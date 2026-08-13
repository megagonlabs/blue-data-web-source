"""Pipeline benchmarks: chunk-size sweep and per-stage timing breakdown.

Two benchmarks, mirroring the structure of blue-data-doc-source's bench_pipeline.py
with the addition of the extraction stage unique to web-source.

1. Chunk-size sweep — runs SectionChunker at five chunk_size configs on the
   same extracted text and reports how throughput and chunk count scale.

2. Stage breakdown — times extraction (trafilatura), chunking (SectionChunker),
   enrichment (BasicEnricher), and embedding (DummyEmbeddingModel) independently
   so the relative cost of each stage is visible. DummyEmbeddingModel is used
   because OpenAI calls would dominate with network latency and mask library overhead.

Usage:
    python lib/tests/benchmarks/bench_pipeline.py
    python lib/tests/benchmarks/bench_pipeline.py --size medium --runs 10
"""
import argparse
import os
import statistics
import sys
import time
from dataclasses import dataclass
from typing import List, Optional

from blue_data_doc_source.source.document.chunkers import SectionChunker
from blue_data_doc_source.source.document.embeddings import DummyEmbeddingModel
from blue_data_doc_source.source.document.enrichment import BasicEnricher
from blue_data_web_source.source.web.extractor import WebExtractor

sys.path.insert(0, os.path.dirname(__file__))
from datasets import build_corpus, SMALL, MEDIUM, LARGE

_SWEEP_CONFIGS = [
    {"chunk_size":   50, "overlap":  10},
    {"chunk_size":  100, "overlap":  20},
    {"chunk_size":  200, "overlap":  40},
    {"chunk_size":  500, "overlap": 100},
    {"chunk_size": 1000, "overlap": 150},
]


@dataclass
class SweepResult:
    chunk_size: int
    overlap: int
    chunk_count: int
    avg_words: float
    std_words: float
    words_per_sec: float


@dataclass
class StageResult:
    chunk_count: int
    extract_ms: float    # trafilatura HTML → text (web-source exclusive stage)
    chunk_ms: float
    enrich_ms: float
    embed_ms: float

    @property
    def total_ms(self) -> float:
        return self.extract_ms + self.chunk_ms + self.enrich_ms + self.embed_ms


def benchmark_chunk_size_sweep(
    text: str,
    configs: Optional[List[dict]] = None,
    n_runs: int = 5,
) -> List[SweepResult]:
    """Run SectionChunker at each config and report throughput + chunk stats.

    Operates on pre-extracted text (as produced by WebExtractor) so the sweep
    isolates chunker behaviour independently of extraction cost.
    """
    configs = configs or _SWEEP_CONFIGS
    input_words = len(text.split())
    results = []
    for cfg in configs:
        chunker = SectionChunker(**cfg)
        chunker.chunk_with_titles(text)  # warm-up
        elapsed = []
        chunks = None
        for _ in range(n_runs):
            t0 = time.perf_counter()
            chunks = chunker.chunk_with_titles(text)
            elapsed.append(time.perf_counter() - t0)
        med = statistics.median(elapsed)
        sizes = [len(ct.split()) for _, ct in chunks]
        results.append(SweepResult(
            chunk_size=cfg["chunk_size"],
            overlap=cfg["overlap"],
            chunk_count=len(chunks),
            avg_words=statistics.mean(sizes) if sizes else 0.0,
            std_words=statistics.stdev(sizes) if len(sizes) > 1 else 0.0,
            words_per_sec=input_words / med if med > 0 else 0.0,
        ))
    return results


def benchmark_pipeline_stages(
    html: str,
    url: str,
    chunk_size: int = 200,
    overlap: int = 40,
    n_runs: int = 5,
) -> StageResult:
    """Time each pipeline stage independently on the same page HTML.

    Stages are timed over the full page to capture real overhead.
    DummyEmbeddingModel produces SHA-256 vectors — zero network latency.

    Args:
        html: Raw HTML of a structured article (from datasets.generate_article_structured).
        url:  URL to pass to WebExtractor (used for metadata extraction).
    """
    extractor  = WebExtractor()
    chunker    = SectionChunker(chunk_size=chunk_size, overlap=overlap)
    enricher   = BasicEnricher()
    embed_model = DummyEmbeddingModel(dim=8)

    # Warm-up: extract once to avoid trafilatura's first-call overhead
    extract_result = extractor.extract(html, url)
    if extract_result is None:
        raise ValueError("Benchmark HTML produced no extractable content")
    text = extract_result.text
    chunks = chunker.chunk_with_titles(text)
    texts  = [ct for _, ct in chunks]

    def _median_ms(fn, n: int) -> float:
        t = []
        for _ in range(n):
            t0 = time.perf_counter()
            fn()
            t.append(time.perf_counter() - t0)
        return statistics.median(t) * 1000

    extract_ms = _median_ms(lambda: extractor.extract(html, url), n_runs)
    chunk_ms   = _median_ms(lambda: chunker.chunk_with_titles(text), n_runs)
    enrich_ms  = _median_ms(lambda: [enricher.enrich(ct) for ct in texts], n_runs)
    embed_ms   = _median_ms(lambda: [embed_model.embed_text(ct) for ct in texts], n_runs)

    return StageResult(
        chunk_count=len(chunks),
        extract_ms=extract_ms,
        chunk_ms=chunk_ms,
        enrich_ms=enrich_ms,
        embed_ms=embed_ms,
    )


# ── Standalone entry point ───────────────────────────────────────────────────

def _print_sweep(results: List[SweepResult]) -> None:
    fmt = "{:>10}  {:>7}  {:>6}  {:>8}  {:>8}  {:>15}"
    print(fmt.format("chunk_size", "overlap", "Chunks", "Avg(wds)", "Std(wds)", "Throughput(Kwps)"))
    print("─" * 62)
    for r in results:
        print(fmt.format(
            r.chunk_size, r.overlap, r.chunk_count,
            f"{r.avg_words:.1f}", f"{r.std_words:.1f}",
            f"{r.words_per_sec / 1000:,.1f}K",
        ))


def _print_stages(s: StageResult) -> None:
    fmt = "{:<25}  {:>10}  {:>9}  {:>12}"
    print(fmt.format("Stage", "Time(ms)", "% total", "µs/chunk"))
    print("─" * 62)
    rows = [
        ("Extraction (trafilatura)", s.extract_ms),
        ("Chunking",                 s.chunk_ms),
        ("Enrichment",               s.enrich_ms),
        ("Embedding (dummy)",        s.embed_ms),
    ]
    for name, ms in rows:
        pct = f"{100 * ms / s.total_ms:.1f}%" if s.total_ms > 0 else "n/a"
        per = f"{1_000 * ms / s.chunk_count:.1f}" if s.chunk_count else "n/a"
        print(fmt.format(name, f"{ms:.2f}", pct, per))
    print("─" * 62)
    per_total = f"{1_000 * s.total_ms / s.chunk_count:.1f}" if s.chunk_count else "n/a"
    print(fmt.format("Total", f"{s.total_ms:.2f}", "100.0%", per_total))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--size",       choices=["small", "medium", "large"], default="medium")
    ap.add_argument("--runs",       type=int, default=5)
    ap.add_argument("--chunk-size", type=int, default=200)
    ap.add_argument("--overlap",    type=int, default=40)
    args = ap.parse_args()

    n_pages = {"small": SMALL, "medium": MEDIUM, "large": LARGE}[args.size]
    print(f"Building corpus ({args.size}, {n_pages} pages)...", flush=True)
    corpus = build_corpus(n_pages)

    structured = corpus.by_type("article_structured")[0]
    print(f"  Using structured page: {structured.name!r} ({structured.word_count:,} words)\n")

    print(f"── Chunk-size sweep  ({structured.word_count:,} content words, SectionChunker) ──")
    from blue_data_web_source.source.web.extractor import WebExtractor
    extractor = WebExtractor()
    extract_result = extractor.extract(structured.html, structured.url)
    sweep = benchmark_chunk_size_sweep(extract_result.text, n_runs=args.runs)
    _print_sweep(sweep)

    print(f"\n── Stage breakdown  (chunk_size={args.chunk_size}, overlap={args.overlap}) ──")
    stages = benchmark_pipeline_stages(
        structured.html, structured.url,
        args.chunk_size, args.overlap, args.runs,
    )
    _print_stages(stages)
