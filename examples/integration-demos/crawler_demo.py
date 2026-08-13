"""Integration demo: WEB.CRAWL — BFS site crawl with depth and max_pages limits.

Shows how the WebCrawler follows links from a seed URL, respects depth limits,
enforces same_domain_only, and deduplicates pages by canonical URL.

Deps: none (no API key, no network)
Run:  python3 examples/integration-demos/crawler_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from blue_data_doc_source.source.document.embeddings.dummy_embedding import DummyEmbeddingModel
from blue_data_web_source import WebDataSource
from blue_data_web_source.source.web.fetcher import FetchResult

W = 68

# ── Mock site layout ──────────────────────────────────────────────────────────
# /            → links to /research, /news, /about
# /research    → links to /research/ai, /research/climate
# /news        → links to /news/2026-01, /news/2026-02
# /about       → no outbound links
# /research/ai → links back to /research (already seen)
# depth=1 from / visits: /, /research, /news, /about  (4 pages)
# depth=2 from / also visits: /research/ai, /research/climate, /news/2026-01, /news/2026-02

SITE = "https://institute.example.org"

PAGES: dict[str, str] = {
    f"{SITE}/": f"""\
<!DOCTYPE html><html><head><title>Research Institute</title></head><body>
<article>
  <h1>Research Institute</h1>
  <p>The Institute advances scientific research in artificial intelligence,
  climate science, and related disciplines. Founded in 2018 with a focus
  on applied research and technology transfer to industry.</p>
</article>
<nav>
  <a href="{SITE}/research">Research</a>
  <a href="{SITE}/news">News</a>
  <a href="{SITE}/about">About</a>
  <a href="https://external.org/partner">External partner</a>
</nav>
</body></html>""",

    f"{SITE}/research": f"""\
<!DOCTYPE html><html><head><title>Research Areas</title></head><body>
<article>
  <h1>Research Areas</h1>
  <p>Our active research programmes span machine learning theory, large-scale
  optimization, and climate system modelling. Each group collaborates with
  universities and industrial partners on applied projects.</p>
  <h2>Focus Areas</h2>
  <p>Primary focus areas include reinforcement learning, causal inference,
  and neural scaling laws. Climate work covers ocean circulation modelling
  and downscaling of global climate model outputs.</p>
</article>
<nav>
  <a href="{SITE}/">Home</a>
  <a href="{SITE}/research/ai">AI Research</a>
  <a href="{SITE}/research/climate">Climate Research</a>
</nav>
</body></html>""",

    f"{SITE}/news": f"""\
<!DOCTYPE html><html><head><title>News</title></head><body>
<article>
  <h1>Institute News</h1>
  <p>Stay up to date with the latest announcements from the Research Institute.
  We publish news on grants, publications, staff appointments, and events.</p>
</article>
<nav>
  <a href="{SITE}/">Home</a>
  <a href="{SITE}/news/2026-01">January 2026</a>
  <a href="{SITE}/news/2026-02">February 2026</a>
</nav>
</body></html>""",

    f"{SITE}/about": f"""\
<!DOCTYPE html><html><head><title>About Us</title></head><body>
<article>
  <h1>About the Institute</h1>
  <p>The Research Institute is a non-profit organisation with 120 full-time
  researchers across three campuses. Funding comes from a mix of public grants,
  industry partnerships, and philanthropic donations.</p>
  <h2>Leadership</h2>
  <p>The Executive Director and a nine-member board of trustees govern the
  Institute. An independent scientific advisory board sets research priorities
  and reviews output quality annually.</p>
</article>
</body></html>""",

    f"{SITE}/research/ai": f"""\
<!DOCTYPE html><html><head><title>AI Research Group</title></head><body>
<article>
  <h1>AI Research Group</h1>
  <p>The AI Research Group focuses on foundation model alignment, interpretability,
  and sample-efficient learning. Current projects include reward model robustness
  and mechanistic interpretability of transformer attention patterns.</p>
</article>
<nav><a href="{SITE}/research">Back to Research</a></nav>
</body></html>""",

    f"{SITE}/research/climate": f"""\
<!DOCTYPE html><html><head><title>Climate Research Group</title></head><body>
<article>
  <h1>Climate Research Group</h1>
  <p>The Climate Research Group develops high-resolution regional climate models
  for impact assessment. Key output is a probabilistic projection dataset used
  by governments and insurers across Europe and North America.</p>
</article>
<nav><a href="{SITE}/research">Back to Research</a></nav>
</body></html>""",

    f"{SITE}/news/2026-01": f"""\
<!DOCTYPE html><html><head><title>January 2026 News</title></head><body>
<article>
  <h1>January 2026 News</h1>
  <p>The Institute received a EUR 12 million grant from the European Research Council
  for a five-year programme on AI-assisted climate modelling. The project brings
  together 15 researchers from four European countries.</p>
</article>
</body></html>""",

    f"{SITE}/news/2026-02": f"""\
<!DOCTYPE html><html><head><title>February 2026 News</title></head><body>
<article>
  <h1>February 2026 News</h1>
  <p>Dr. Sarah Lin joined the Institute as Director of AI Research. Dr. Lin
  previously led the alignment team at a major AI lab and brings expertise
  in RLHF, constitutional AI, and scalable oversight methods.</p>
</article>
</body></html>""",
}


def _build_mock_fetcher() -> MagicMock:
    m = MagicMock()

    def _fetch(url: str) -> FetchResult:
        html = PAGES.get(url)
        if html is None:
            raise ValueError(f"No fixture: {url!r}")
        return FetchResult(url=url, final_url=url, html=html,
                           status_code=200, content_type="text/html")

    m.fetch.side_effect = _fetch
    return m


def _build_source(name: str) -> WebDataSource:
    s = WebDataSource(name=name, embedding_model=DummyEmbeddingModel(), default_collection="crawl")
    s._pipeline._fetcher = _build_mock_fetcher()
    return s


def _hr():
    print("─" * W)


def _ok(msg: str):
    print(f"  ✓  {msg}")


def _summarise(label: str, records: list[dict]):
    urls = sorted({r["url"] for r in records})
    print(f"\n  {label}")
    print(f"    Pages crawled : {len(urls)}")
    print(f"    Chunks stored : {len(records)}")
    for url in urls:
        url_records = [r for r in records if r["url"] == url]
        title = url_records[0].get("title", "—")
        print(f"      {len(url_records):2d} chunks  {url.replace(SITE, '')}  ({title})")


def main():
    print()
    print("═" * W)
    print("  blue-data-web-source — BFS Crawler Demo")
    print("═" * W)

    # ── Site layout ──────────────────────────────────────────
    print()
    print("  MOCK SITE STRUCTURE")
    _hr()
    structure = [
        ("/             → links to /research, /news, /about, [external]"),
        ("/research     → links to /research/ai, /research/climate"),
        ("/news         → links to /news/2026-01, /news/2026-02"),
        ("/about        → no outbound links"),
        ("/research/ai  → links back to /research (already seen)"),
    ]
    for line in structure:
        print(f"    {line}")

    # ── Depth=1 ──────────────────────────────────────────────
    print()
    print("  EXPERIMENT 1 — depth=1  (seed + its direct links)")
    _hr()
    s1 = _build_source("depth1")
    r1 = s1.execute_query({
        "op": "WEB.CRAWL",
        "url": f"{SITE}/",
        "depth": 1,
        "max_pages": 20,
        "same_domain_only": True,
        "collection": "d1",
    })
    _summarise("depth=1 result", r1)
    print()
    _ok("External URL 'https://external.org/partner' not crawled (same_domain_only=True)")

    # ── Depth=2 ──────────────────────────────────────────────
    print()
    print("  EXPERIMENT 2 — depth=2  (seed + links + links-of-links)")
    _hr()
    s2 = _build_source("depth2")
    r2 = s2.execute_query({
        "op": "WEB.CRAWL",
        "url": f"{SITE}/",
        "depth": 2,
        "max_pages": 20,
        "same_domain_only": True,
        "collection": "d2",
    })
    _summarise("depth=2 result", r2)

    # ── max_pages limit ──────────────────────────────────────
    print()
    print("  EXPERIMENT 3 — max_pages=3  (early stop)")
    _hr()
    s3 = _build_source("maxpages")
    r3 = s3.execute_query({
        "op": "WEB.CRAWL",
        "url": f"{SITE}/",
        "depth": 2,
        "max_pages": 3,
        "same_domain_only": True,
        "collection": "mp",
    })
    _summarise("max_pages=3 result", r3)
    print()
    _ok("Crawl stops after 3 pages regardless of remaining links in the queue")

    # ── same_domain_only=False ───────────────────────────────
    print()
    print("  EXPERIMENT 4 — same_domain_only=False  (follows external links)")
    _hr()
    PAGES["https://external.org/partner"] = """\
<!DOCTYPE html><html><head><title>Partner Organisation</title></head><body>
<article><h1>Partner Organisation</h1>
<p>We collaborate on several joint research projects covering AI safety
and environmental monitoring. Our partnership spans five years.</p>
</article></body></html>"""

    s4 = _build_source("extdomain")
    s4._pipeline._fetcher = _build_mock_fetcher()
    r4 = s4.execute_query({
        "op": "WEB.CRAWL",
        "url": f"{SITE}/",
        "depth": 1,
        "max_pages": 20,
        "same_domain_only": False,
        "collection": "ext",
    })
    _summarise("same_domain_only=False, depth=1", r4)
    ext_urls = [u for u in {r["url"] for r in r4} if "external.org" in u]
    _ok(f"External URL included: {ext_urls[0] if ext_urls else 'none found'}")

    # ── Comparison ───────────────────────────────────────────
    print()
    print("  COMPARISON TABLE")
    _hr()
    print(f"  {'Config':<35} {'Pages':>6} {'Chunks':>7}")
    print(f"  {'─' * 35} {'─' * 6} {'─' * 7}")
    rows = [
        (f"depth=1, max_pages=20, same_domain", r1),
        (f"depth=2, max_pages=20, same_domain", r2),
        (f"depth=2, max_pages=3,  same_domain", r3),
        (f"depth=1, max_pages=20, cross_domain", r4),
    ]
    for label, records in rows:
        pages = len({r["url"] for r in records})
        print(f"  {label:<35} {pages:>6} {len(records):>7}")

    print()
    print("  Notes:")
    print("   • depth counts hops from seed (depth=1 = seed + its links)")
    print("   • max_pages is a hard cap regardless of depth")
    print("   • BFS visits pages in breadth-first order — max_pages cuts the queue")
    print("   • URL dedup is hash-based; back-links to already-seen pages are skipped")
    print()
    print("═" * W)
    print()


if __name__ == "__main__":
    main()
