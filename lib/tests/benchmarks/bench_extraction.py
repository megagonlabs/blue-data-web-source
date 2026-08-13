"""Extraction benchmark: WebExtractor throughput, extraction rate, and metadata coverage.

This is the primary new benchmark area for blue-data-web-source. Trafilatura extraction
is the most CPU-intensive stage in the pipeline — the equivalent of parsing in doc-source
but with much higher per-page cost due to full HTML tree analysis.

Three benchmark areas:

1. Extraction throughput  — pages/sec across all four page types; extraction rate
                           (% of pages yielding non-None); metadata field coverage.
2. Per-type breakdown     — compare throughput between structured, unstructured, mixed,
                           and no-content pages to understand where time is spent.
3. Nav-fragment removal   — how many lines are stripped on mixed-content pages; cost
                           of the post-processing pass vs. raw trafilatura extraction.

Usage:
    python lib/tests/benchmarks/bench_extraction.py
    python lib/tests/benchmarks/bench_extraction.py --size medium --runs 5
"""
import argparse
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass
from typing import List, Optional

sys.path.insert(0, os.path.dirname(__file__))
from datasets import WebCorpus, WebPage, build_corpus, SMALL, MEDIUM, LARGE

# Nav-fragment pattern — mirrors the one in extractor.py
_NAV_FRAGMENT_RE = re.compile(
    r"^(?:Related Story|Read more|See also|Also read|Advertisement|Story continues)(\W|$)",
    re.IGNORECASE,
)


@dataclass
class ExtractionResult:
    page_type: str
    page_count: int
    content_count: int       # pages where extractor returned non-None
    elapsed_ms: float
    pages_per_sec: float
    extract_rate_pct: float  # content_count / page_count * 100
    nav_fragments_found: int # nav-fragment lines found in raw extracted text
    title_coverage_pct: float
    date_coverage_pct: float
    author_coverage_pct: float


@dataclass
class FragmentResult:
    pages_benchmarked: int
    total_nav_fragments: int        # lines matching nav fragment pattern in raw text
    avg_fragments_per_page: float
    strip_overhead_ms: float        # extra ms spent stripping vs. raw extraction
    strip_overhead_pct: float       # strip_ms / (raw_extract_ms + strip_ms) * 100


def _count_nav_fragments(text: Optional[str]) -> int:
    if not text:
        return 0
    return sum(1 for line in text.splitlines() if _NAV_FRAGMENT_RE.match(line.strip()))


def benchmark_extraction(
    pages: List[WebPage],
    page_type: str,
    n_runs: int = 5,
) -> ExtractionResult:
    """Run WebExtractor on all pages of a given type and measure throughput + quality."""
    from blue_data_web_source.source.web.extractor import WebExtractor

    extractor = WebExtractor()
    subset = [p for p in pages if p.page_type == page_type]
    if not subset:
        return ExtractionResult(
            page_type=page_type, page_count=0, content_count=0,
            elapsed_ms=0.0, pages_per_sec=0.0, extract_rate_pct=0.0,
            nav_fragments_found=0,
            title_coverage_pct=0.0, date_coverage_pct=0.0, author_coverage_pct=0.0,
        )

    # Warm-up pass (avoids first-call import / trafilatura model-load penalty)
    for p in subset:
        extractor.extract(p.html, p.url)

    elapsed_runs = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        for p in subset:
            extractor.extract(p.html, p.url)
        elapsed_runs.append(time.perf_counter() - t0)

    med_sec = statistics.median(elapsed_runs)

    # Final pass for quality metrics (use results from one deterministic run)
    results = [extractor.extract(p.html, p.url) for p in subset]
    content_results = [r for r in results if r is not None]

    nav_found = sum(_count_nav_fragments(r.text) for r in content_results)
    titles    = sum(1 for r in content_results if r.title)
    dates     = sum(1 for r in content_results if r.date)
    authors   = sum(1 for r in content_results if r.author)
    n_content = len(content_results)

    return ExtractionResult(
        page_type=page_type,
        page_count=len(subset),
        content_count=n_content,
        elapsed_ms=med_sec * 1000,
        pages_per_sec=len(subset) / med_sec if med_sec > 0 else 0.0,
        extract_rate_pct=100.0 * n_content / len(subset) if subset else 0.0,
        nav_fragments_found=nav_found,
        title_coverage_pct=100.0 * titles  / n_content if n_content else 0.0,
        date_coverage_pct=100.0  * dates   / n_content if n_content else 0.0,
        author_coverage_pct=100.0 * authors / n_content if n_content else 0.0,
    )


def benchmark_nav_fragment_removal(
    pages: List[WebPage],
    n_runs: int = 5,
) -> FragmentResult:
    """Measure the cost of the nav-fragment stripping pass on mixed-content pages."""
    import trafilatura
    from blue_data_web_source.source.web.extractor import _strip_nav_fragments

    mixed_pages = [p for p in pages if p.page_type == "article_mixed"]
    if not mixed_pages:
        return FragmentResult(0, 0, 0.0, 0.0, 0.0)

    # Extract raw trafilatura text for each page (not stripped)
    raw_texts = []
    for p in mixed_pages:
        raw = trafilatura.extract(
            p.html, url=p.url, include_comments=False, output_format="txt"
        )
        raw_texts.append(raw or "")

    total_fragments = sum(_count_nav_fragments(t) for t in raw_texts)

    # Time raw extraction alone
    raw_elapsed = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        for p in mixed_pages:
            trafilatura.extract(p.html, url=p.url, include_comments=False, output_format="txt")
        raw_elapsed.append(time.perf_counter() - t0)
    raw_med = statistics.median(raw_elapsed) * 1000

    # Time raw extraction + stripping pass
    full_elapsed = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        for p in mixed_pages:
            raw = trafilatura.extract(p.html, url=p.url, include_comments=False, output_format="txt")
            if raw:
                _strip_nav_fragments(raw)
        full_elapsed.append(time.perf_counter() - t0)
    full_med = statistics.median(full_elapsed) * 1000

    strip_ms = max(0.0, full_med - raw_med)
    overhead_pct = 100.0 * strip_ms / full_med if full_med > 0 else 0.0

    return FragmentResult(
        pages_benchmarked=len(mixed_pages),
        total_nav_fragments=total_fragments,
        avg_fragments_per_page=total_fragments / len(mixed_pages) if mixed_pages else 0.0,
        strip_overhead_ms=strip_ms,
        strip_overhead_pct=overhead_pct,
    )


# ── Standalone entry point ───────────────────────────────────────────────────

def _print_extraction_table(results: List[ExtractionResult]) -> None:
    fmt = "{:<22} {:>6} {:>8} {:>9} {:>13} {:>9}  {:>9}  {:>9}"
    print(fmt.format(
        "Page type", "Pages", "Content", "Extract%",
        "Pages/sec", "Title%", "Date%", "Author%"
    ))
    print("─" * 92)
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
    print("─" * 92)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Run blue-data-web-source extraction benchmarks")
    ap.add_argument("--size",  choices=["small", "medium", "large"], default="medium",
                    help="Corpus size (small=10 pages, medium=50, large=200)")
    ap.add_argument("--runs",  type=int, default=5,
                    help="Timed repetitions per measurement (median is reported)")
    args = ap.parse_args()

    n_pages = {"small": SMALL, "medium": MEDIUM, "large": LARGE}[args.size]
    print(f"Building {args.size} corpus ({n_pages} pages)...", flush=True)
    corpus = build_corpus(n_pages)
    print(f"  {corpus}\n")

    page_types = ["article_structured", "article_unstructured", "article_mixed", "no_content"]

    print(f"── Extraction throughput  ({args.runs} runs, median reported) ──")
    results = [benchmark_extraction(corpus.pages, pt, args.runs) for pt in page_types]
    _print_extraction_table(results)

    total_content = sum(r.content_count for r in results)
    total_pages   = sum(r.page_count for r in results)
    total_nav     = sum(r.nav_fragments_found for r in results)
    print(f"\n  Overall extraction rate: {100.0 * total_content / total_pages:.0f}%  "
          f"({total_content}/{total_pages} pages)")
    print(f"  Nav fragments found in extracted text: {total_nav}")

    print(f"\n── Nav-fragment removal overhead  ({args.runs} runs, mixed-content pages) ──")
    frag = benchmark_nav_fragment_removal(corpus.pages, args.runs)
    print(f"  Pages:                   {frag.pages_benchmarked}")
    print(f"  Total fragments found:   {frag.total_nav_fragments}")
    print(f"  Avg per page:            {frag.avg_fragments_per_page:.1f}")
    print(f"  Strip overhead:          {frag.strip_overhead_ms:.2f} ms")
    print(f"  Strip overhead (%):      {frag.strip_overhead_pct:.1f}%")
