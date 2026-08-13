"""Master benchmark runner — builds the corpus once, runs all benchmarks, prints tables.

Usage:
    python lib/tests/benchmarks/runner.py
    python lib/tests/benchmarks/runner.py --size small --runs 3
    python lib/tests/benchmarks/runner.py --size large --runs 10 --chunk-size 300 --overlap 60
"""
import argparse
import os
import sys
from typing import List

sys.path.insert(0, os.path.dirname(__file__))

from datasets import build_corpus, SMALL, MEDIUM, LARGE
from bench_extraction import (
    ExtractionResult, FragmentResult,
    benchmark_extraction, benchmark_nav_fragment_removal,
)
from bench_pipeline import (
    SweepResult, StageResult,
    benchmark_chunk_size_sweep, benchmark_pipeline_stages,
)

W = 80


# ── Formatting ───────────────────────────────────────────────────────────────

def _rule(char="─", w=W):
    print(char * w)


def _banner(title: str):
    print()
    _rule("═")
    print(f"  {title}")
    _rule("═")


def _section(label: str):
    print()
    tail = "─" * max(0, W - len(label) - 4)
    print(f"── {label} {tail}")


# ── Table: extraction throughput ─────────────────────────────────────────────

def _print_extraction_table(results: List[ExtractionResult]) -> None:
    fmt = "  {:<22} {:>6} {:>8} {:>9} {:>13} {:>9}  {:>8}  {:>9}"
    print(fmt.format(
        "Page type", "Pages", "Content", "Extract%",
        "Pages/sec", "Title%", "Date%", "Author%"
    ))
    print("  " + "─" * 92)
    for r in results:
        print(fmt.format(
            r.page_type,
            r.page_count,
            r.content_count,
            f"{r.extract_rate_pct:.0f}%",
            f"{r.pages_per_sec:,.1f}",
            f"{r.title_coverage_pct:.0f}%",
            f"{r.date_coverage_pct:.0f}%",
            f"{r.author_coverage_pct:.0f}%",
        ))
    print("  " + "─" * 92)


# ── Table: chunk-size sweep ──────────────────────────────────────────────────

def _print_sweep_table(results: List[SweepResult]) -> None:
    fmt = "  {:>10}  {:>7}  {:>6}  {:>8}  {:>8}  {:>15}"
    print(fmt.format("chunk_size", "overlap", "Chunks", "Avg(wds)", "Std(wds)", "Throughput(Kwps)"))
    print("  " + "─" * 62)
    for r in results:
        print(fmt.format(
            r.chunk_size, r.overlap,
            r.chunk_count,
            f"{r.avg_words:.1f}",
            f"{r.std_words:.1f}",
            f"{r.words_per_sec / 1000:,.1f}K",
        ))


# ── Table: pipeline stage breakdown ─────────────────────────────────────────

def _print_stage_table(s: StageResult) -> None:
    fmt = "  {:<25}  {:>10}  {:>9}  {:>12}"
    print(fmt.format("Stage", "Time(ms)", "% total", "µs/chunk"))
    print("  " + "─" * 62)
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
    print("  " + "─" * 62)
    per_total = f"{1_000 * s.total_ms / s.chunk_count:.1f}" if s.chunk_count else "n/a"
    print(fmt.format("Total", f"{s.total_ms:.2f}", "100.0%", per_total))


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Run all blue-data-web-source benchmarks")
    ap.add_argument("--size",       choices=["small", "medium", "large"], default="medium",
                    help="Corpus size (small=10 pages, medium=50 pages, large=200 pages)")
    ap.add_argument("--runs",       type=int, default=5,
                    help="Timed repetitions per measurement (median is reported)")
    ap.add_argument("--chunk-size", type=int, default=200,
                    help="chunk_size for chunker sweep and stage breakdown")
    ap.add_argument("--overlap",    type=int, default=40,
                    help="overlap for chunker sweep and stage breakdown")
    args = ap.parse_args()

    n_pages    = {"small": SMALL, "medium": MEDIUM, "large": LARGE}[args.size]
    chunk_size = args.chunk_size
    overlap    = args.overlap
    n_runs     = args.runs

    _banner(
        f"blue-data-web-source BENCHMARK SUITE  |  "
        f"size={args.size}  chunk_size={chunk_size}  overlap={overlap}  runs={n_runs}"
    )

    print(f"\n  Building {args.size} corpus ({n_pages} pages)...", end="", flush=True)
    corpus = build_corpus(n_pages)
    print(" done.\n")
    print(f"  {corpus}")
    by_type = {}
    for p in corpus.pages:
        by_type[p.page_type] = by_type.get(p.page_type, 0) + 1
    for pt, n in sorted(by_type.items()):
        print(f"    {pt:<25} {n:>4} pages")

    # ── 1. Extraction throughput ─────────────────────────────────────────────
    _section("1. EXTRACTION THROUGHPUT")
    print("  WebExtractor (trafilatura) across four page types.")
    print("  Extract%: pages returning non-None (content vs no-content).")
    print("  Title/Date/Author%: metadata field coverage on content pages.")
    print()

    page_types = ["article_structured", "article_unstructured", "article_mixed", "no_content"]
    ext_results = [
        benchmark_extraction(corpus.pages, pt, n_runs)
        for pt in page_types
    ]
    _print_extraction_table(ext_results)

    total_content = sum(r.content_count for r in ext_results)
    total_pages   = sum(r.page_count for r in ext_results)
    total_nav     = sum(r.nav_fragments_found for r in ext_results)
    print(f"\n  Overall extraction rate: {100.0 * total_content / total_pages:.0f}%"
          f"  ({total_content}/{total_pages} pages yielded content)")
    print(f"  Nav fragments found in extracted text: {total_nav}")

    content_results = [r for r in ext_results if r.page_type != "no_content"]
    avg_pps = sum(r.pages_per_sec for r in content_results) / len(content_results) if content_results else 0
    print(f"  Average throughput (content pages):   {avg_pps:,.1f} pages/sec")

    # ── 2. Nav-fragment removal overhead ────────────────────────────────────
    _section("2. NAV-FRAGMENT REMOVAL OVERHEAD")
    print("  Cost of the post-extraction stripping pass on mixed-content pages.")
    print()
    frag = benchmark_nav_fragment_removal(corpus.pages, n_runs)
    print(f"  Pages benchmarked:          {frag.pages_benchmarked}")
    print(f"  Total nav fragments found:  {frag.total_nav_fragments}")
    print(f"  Avg fragments per page:     {frag.avg_fragments_per_page:.1f}")
    print(f"  Strip overhead:             {frag.strip_overhead_ms:.2f} ms total")
    print(f"  Strip overhead (%):         {frag.strip_overhead_pct:.1f}% of extraction+strip time")

    # ── 3. Chunk-size sweep ──────────────────────────────────────────────────
    structured_pages = corpus.by_type("article_structured")
    if not structured_pages:
        print("\n  (no structured pages — skipping chunk-size sweep)")
    else:
        ref_page = structured_pages[0]

        _section(f"3. CHUNK SIZE SWEEP  (SectionChunker, structured article, {ref_page.word_count:,} words)")
        print("  How chunk_size affects chunk count, average size, and throughput.")
        print("  Operates on pre-extracted text — excludes extraction cost.")
        print()

        from blue_web_source.source.web.extractor import WebExtractor
        ext = WebExtractor()
        extract_result = ext.extract(ref_page.html, ref_page.url)
        if extract_result:
            sweep = benchmark_chunk_size_sweep(extract_result.text, n_runs=n_runs)
            _print_sweep_table(sweep)
        else:
            print("  (extraction returned None for reference page — skipping sweep)")

    # ── 4. Pipeline stage breakdown ──────────────────────────────────────────
    if not structured_pages:
        print("\n  (no structured pages — skipping stage breakdown)")
    else:
        ref_page = structured_pages[0]
        _section(
            f"4. PIPELINE STAGE BREAKDOWN  "
            f"(structured article, {ref_page.word_count:,} words)"
        )
        print(f"  chunk_size={chunk_size}  overlap={overlap}")
        print("  Embedding uses DummyEmbeddingModel (SHA-256, no network) to isolate library cost.")
        print()
        stages = benchmark_pipeline_stages(
            ref_page.html, ref_page.url,
            chunk_size=chunk_size, overlap=overlap, n_runs=n_runs,
        )
        _print_stage_table(stages)

    print()
    _rule("═")
    print("  Benchmark complete.")
    _rule("═")
    print()


if __name__ == "__main__":
    main()
