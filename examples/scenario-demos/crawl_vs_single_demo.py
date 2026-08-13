"""Scenario: Single-URL ingest vs BFS crawl — when to use each.

Single-URL ingest processes exactly one page. BFS crawl follows links up to
`depth` hops and ingests every reachable page up to `max_pages`. This demo
shows both approaches on the same mock site so their chunk counts and URL sets
can be compared directly.

Deps: none (no API key, no network)
Run:  python3 examples/scenario-demos/crawl_vs_single_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from blue_doc_source.source.document.embeddings.dummy_embedding import DummyEmbeddingModel
from blue_web_source import WebDataSource
from blue_web_source.source.web.fetcher import FetchResult

# ── Mock site: index page + 3 articles ────────────────────────────────────────

SITE = "https://blog.example.com"

PAGES: dict[str, str] = {
    f"{SITE}/": f"""\
<!DOCTYPE html><html><head><title>Tech Blog</title></head>
<body>
<nav><a href="{SITE}/ai-trends">AI Trends</a>
     <a href="{SITE}/quantum">Quantum Computing</a>
     <a href="{SITE}/robotics">Robotics</a></nav>
<article><h1>Tech Blog</h1>
<p>Welcome to our technology blog covering artificial intelligence, quantum computing,
and robotics. New articles published every week by our research team.</p></article>
</body></html>""",

    f"{SITE}/ai-trends": f"""\
<!DOCTYPE html><html><head><title>AI Trends 2026</title></head>
<body>
<article>
<h1>AI Trends 2026</h1>
<p>Large language models continued to improve in 2026, with new architectures pushing
reasoning capabilities to new heights. Multimodal systems became mainstream in enterprise
workflows, enabling document understanding and code generation at scale.</p>
<h2>Agentic Systems</h2>
<p>AI agents capable of multi-step planning and tool use gained traction in software
development. Code review, test generation, and deployment automation became key use cases.
Investment in AI infrastructure exceeded $200 billion globally.</p>
<a href="{SITE}/">Back to home</a>
</article></body></html>""",

    f"{SITE}/quantum": f"""\
<!DOCTYPE html><html><head><title>Quantum Computing Update</title></head>
<body>
<article>
<h1>Quantum Computing Update</h1>
<p>Qubit counts crossed the 1,000-qubit threshold at multiple research labs. Error
correction techniques improved stability, moving practical quantum advantage closer
for optimization and materials simulation tasks.</p>
<h2>Commercial Timeline</h2>
<p>Industry analysts predict fault-tolerant quantum computing will become commercially
viable for specific domains by 2030. Pharmaceutical and financial services sectors
are early adopters of quantum simulation capabilities.</p>
<a href="{SITE}/">Back to home</a>
</article></body></html>""",

    f"{SITE}/robotics": f"""\
<!DOCTYPE html><html><head><title>Robotics in 2026</title></head>
<body>
<article>
<h1>Robotics in 2026</h1>
<p>Humanoid robots saw rapid deployment in warehouse and logistics settings. General-purpose
manipulation systems achieved human-level dexterity for common assembly tasks, reducing
the need for bespoke automation for each product line.</p>
<h2>Safety Standards</h2>
<p>New ISO standards for collaborative robots operating alongside humans were finalized.
Sensor fusion and real-time obstacle avoidance became mandatory for warehouse deployments
in the EU and California.</p>
<a href="{SITE}/">Back to home</a>
</article></body></html>""",
}

EXTERNAL_URL = "https://external.example.org/news"
PAGES[EXTERNAL_URL] = """\
<!DOCTYPE html><html><head><title>External News</title></head>
<body><article><h1>External News</h1>
<p>This page is on a different domain and should not be crawled when
same_domain_only=True is set.</p></article></body></html>"""


def _build_mock_fetcher(url_map: dict[str, str]) -> MagicMock:
    mock = MagicMock()

    def _fetch(url: str) -> FetchResult:
        html = url_map.get(url)
        if html is None:
            raise ValueError(f"No fixture for: {url}")
        return FetchResult(url=url, final_url=url, html=html,
                           status_code=200, content_type="text/html")

    mock.fetch.side_effect = _fetch
    return mock


W = 68


def _hr():
    print("─" * W)


def _ok(msg: str):
    print(f"  ✓  {msg}")


def _build_source(name: str) -> WebDataSource:
    source = WebDataSource(
        name=name,
        embedding_model=DummyEmbeddingModel(),
        default_collection="demo",
    )
    source._pipeline._fetcher = _build_mock_fetcher(PAGES)
    return source


def main():
    print()
    print("═" * W)
    print("  blue-data-web-source — Single-URL vs BFS Crawl")
    print("═" * W)

    # ── Single-URL ingest ────────────────────────────────────
    print()
    print("  APPROACH A — Single-URL ingest (WEB.INGEST)")
    _hr()
    single_source = _build_source("single")
    records = single_source.execute_query({
        "op": "WEB.INGEST",
        "url": f"{SITE}/",
        "collection": "single",
    })
    print(f"  Ingested 1 URL: {SITE}/")
    print(f"  Chunks produced: {len(records)}")
    print()
    for r in records:
        sec = f"  § {r['section_title']}" if r.get("section_title") else ""
        print(f"    chunk {r['chunk_index']:02d}{sec}")
        print(f"           {r['text'][:70].replace(chr(10), ' ')}…")
    print()
    print("  Observation: only the index page is processed.")
    print("  Linked article pages are NOT followed.")

    # ── BFS crawl ────────────────────────────────────────────
    print()
    print("  APPROACH B — BFS crawl (WEB.CRAWL, depth=1, same_domain_only=True)")
    _hr()
    crawl_source = _build_source("crawl")
    records = crawl_source.execute_query({
        "op": "WEB.CRAWL",
        "url": f"{SITE}/",
        "depth": 1,
        "max_pages": 10,
        "same_domain_only": True,
        "collection": "crawled",
    })
    ingested_urls = sorted({r["url"] for r in records})
    print(f"  Seed URL: {SITE}/")
    print(f"  Pages crawled: {len(ingested_urls)}")
    print(f"  Chunks produced: {len(records)}")
    print()
    for url in ingested_urls:
        url_records = [r for r in records if r["url"] == url]
        title = url_records[0].get("title", "—") if url_records else "—"
        print(f"    {len(url_records):2d} chunks  |  {url}  ({title})")
    print()
    print(f"  External domain '{EXTERNAL_URL}' was NOT crawled (same_domain_only=True).")

    # ── Comparison ───────────────────────────────────────────
    print()
    print("  COMPARISON")
    _hr()
    single_records = single_source.execute_query({
        "op": "WEB.INGEST", "url": f"{SITE}/", "collection": "cmp"
    })
    print()
    print(f"  {'Approach':<25} {'Pages':>6} {'Chunks':>7}")
    print(f"  {'─' * 25} {'─' * 6} {'─' * 7}")
    print(f"  {'WEB.INGEST (single URL)':<25} {'1':>6} {len(single_records):>7}")
    print(f"  {'WEB.CRAWL (depth=1)':<25} {len(ingested_urls):>6} {len(records):>7}")
    print()
    print("  When to use each:")
    print("   • WEB.INGEST  — known URL, single article, or API response page")
    print("   • WEB.CRAWL   — documentation sites, blogs, multi-page reports")
    print("     depth=1 follows links from seed only; depth=2 follows links of links")
    print("     max_pages limits total pages regardless of depth")
    print("     same_domain_only=True (default) prevents crawling external sites")
    print()
    print("═" * W)
    print()


if __name__ == "__main__":
    main()
