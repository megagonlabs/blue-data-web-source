"""
blue-data-web-source  —  End-to-End Demo
=========================================
Demonstrates the full pipeline: ingest web articles → store as embeddings → semantic search → NL queries.

HTTP is intercepted and served from local fixture files (no network), but this
demo uses real models: OpenAIEmbeddingModel for embeddings and Claude for the
natural-language step. It therefore requires the openai and llm extras and both
API keys:

    pip install -e 'lib/[openai,llm]'
    export OPENAI_API_KEY=sk-...
    export ANTHROPIC_API_KEY=sk-ant-...

Usage:
    python examples/demo.py

For a fully offline, no-key demo see examples/backends/memory_quickstart.py.
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

from blue_data.source.vector.memory_vector_source import MemoryVectorSource
from blue_data_doc_source.source.document.embeddings.openai_embedding import OpenAIEmbeddingModel

from blue_data_web_source import WebDataSource
from blue_data_web_source.source.web.fetcher import FetchResult
from blue_data_web_source.source.web.search_provider import WebSearchProvider

# ---------------------------------------------------------------------------
# Article fixtures (served in place of live HTTP)
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent.parent / "data" / "fixtures"

_CLIMATE_URL = "https://climate.example.com/report"
_CLIMATE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Climate Policy Report 2026</title>
  <meta name="author" content="Research Team">
  <meta name="description" content="An overview of global climate policy developments in 2026.">
</head>
<body>
  <article>
    <h1>Climate Policy Report 2026</h1>
    <p>Global greenhouse gas emissions continued to rise in 2025 despite international
    agreements. The Paris Accord signatories convened in March to discuss revised targets.
    Scientists warned that without immediate action, warming could exceed 1.5°C by 2035.</p>

    <h2>National Commitments</h2>
    <p>Several major economies announced updated nationally determined contributions.
    The European Union pledged a 65 percent reduction by 2030. The United States committed
    to net-zero by 2050. China indicated peak emissions would occur no later than 2028.</p>

    <h2>Technology Outlook</h2>
    <p>Renewable energy capacity additions reached a record high globally in 2025. Solar
    PV costs dropped twelve percent year-on-year. Battery storage deployment doubled.
    Carbon capture projects received substantial new investment across G7 nations.</p>
  </article>
</body>
</html>"""

_NPR_URL = "https://text.npr.org/nx-s1-5884537"

_ARTICLES = [
    (_CLIMATE_URL, _CLIMATE_HTML),
    (_NPR_URL, None),  # loaded from fixture file
]

_QUERIES = [
    "greenhouse gas emissions and climate targets",
    "ballistic missile launch in the Pacific",
]


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------

W = 64  # line width

def _hr(char: str = "─") -> None:
    print(char * W)

def _header(title: str) -> None:
    print()
    _hr("═")
    print(f"  {title}")
    _hr("═")

def _section(title: str) -> None:
    print()
    print(f"  {title}")
    _hr()

def _ok(msg: str) -> None:
    print(f"  ✓  {msg}")

def _item(label: str, value: str, indent: int = 7) -> None:
    pad = " " * indent
    prefix = f"{pad}{label:<10}"
    wrap_width = W - len(prefix)
    lines = textwrap.wrap(str(value), wrap_width)
    print(f"{prefix}{lines[0]}")
    for line in lines[1:]:
        print(" " * len(prefix) + line)

def _excerpt(text: str, max_chars: int = 140) -> str:
    text = text.replace("\n", " ").strip()
    return text[:max_chars].rstrip() + ("…" if len(text) > max_chars else "")


# ---------------------------------------------------------------------------
# HTTP interception — serve local HTML instead of real network calls
# ---------------------------------------------------------------------------

def _build_mock_fetcher(url_map: dict[str, str]):
    """Return a patched WebFetcher that serves HTML from url_map."""
    mock_fetcher = MagicMock()

    def _fetch(url: str) -> FetchResult:
        html = url_map.get(url)
        if html is None:
            raise ValueError(f"No fixture registered for URL: {url}")
        return FetchResult(
            url=url,
            final_url=url,
            html=html,
            status_code=200,
            content_type="text/html; charset=utf-8",
        )

    mock_fetcher.fetch.side_effect = _fetch
    return mock_fetcher


class _OfflineSearchProvider(WebSearchProvider):
    """Offline stand-in for a live web-search backend (issue #60).

    Step 5 drives the pipeline through ``execute_query_nl``, and the NL parser
    may classify a plain question as ``WEB.SEARCH_WEB`` (live search), which
    would fetch arbitrary live URLs the mock fetcher cannot serve — crashing the
    demo. Returning the already-ingested fixture URLs keeps that path inside the
    offline fixtures, so the NL step works deterministically whether the parser
    picks ``search`` or ``search_web``.
    """

    def search(self, query: str, k: int = 5) -> list[str]:
        return [_CLIMATE_URL, _NPR_URL][:k]


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def main() -> None:
    _header("blue-data-web-source  —  End-to-End Demo")

    # Load NPR fixture from disk
    try:
        npr_html = (_FIXTURES_DIR / "npr_article.html").read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"\n  ERROR: fixture not found at {_FIXTURES_DIR / 'npr_article.html'}")
        print("  Run from the repo root or check data/fixtures/.")
        sys.exit(1)

    url_map = {
        _CLIMATE_URL: _CLIMATE_HTML,
        _NPR_URL: npr_html,
    }

    # ── Setup ──────────────────────────────────────────────────────────────
    _section("SETUP")

    embedding_model = OpenAIEmbeddingModel()  # text-embedding-3-small, dim=1536
    vector_store = MemoryVectorSource()
    source = WebDataSource(
        name="demo",
        embedding_model=embedding_model,
        vector_source=vector_store,
        default_collection="news",
        search_provider=_OfflineSearchProvider(),  # keep WEB.SEARCH_WEB offline (issue #60)
    )

    # Inject mock fetcher so no real HTTP occurs
    mock_fetcher = _build_mock_fetcher(url_map)
    source._pipeline._fetcher = mock_fetcher

    _ok(f"EmbeddingModel  : OpenAIEmbeddingModel (text-embedding-3-small, dim={embedding_model.dim})")
    _ok(f"VectorStore     : MemoryVectorSource (in-process, no Redis needed)")
    _ok(f"SearchProvider  : offline stub (returns the ingested fixtures)")
    _ok(f"WebDataSource   : ready  (collection='news')")

    # ── Step 1: Create index ───────────────────────────────────────────────
    _section("STEP 1  —  Create vector index")

    source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": "news"})
    _ok(f"Index 'news' created  (dim={embedding_model.dim}, COSINE)")

    # ── Step 2: Ingest articles ────────────────────────────────────────────
    _section("STEP 2  —  Ingest articles")

    all_chunks: list[dict] = []
    for i, (url, _html) in enumerate(_ARTICLES, 1):
        print(f"\n  [{i}/{len(_ARTICLES)}]  {url}")
        records = source.execute_query({"op": "WEB.INGEST", "url": url, "collection": "news"})

        if not records:
            print("         (no extractable content)")
            continue

        first = records[0]
        _item("Title    :", first.get("title", "—"))
        _item("Domain   :", first.get("domain", "—"))
        _item("Date     :", first.get("date") or "—")
        _item("Chunks   :", str(len(records)))

        all_chunks.extend(records)

    print()
    _ok(f"Total chunks ingested and stored: {len(all_chunks)}")

    # ── Step 3: Show ingested chunk sample ────────────────────────────────
    _section("STEP 3  —  Sample chunk (first chunk of each article)")

    seen_urls: set[str] = set()
    for record in all_chunks:
        if record["url"] not in seen_urls and record["chunk_index"] == 0:
            seen_urls.add(record["url"])
            print(f"\n  chunk_id : {record['chunk_id']}")
            _item("url      :", record["url"])
            _item("text     :", _excerpt(record["text"]))
            if "section_title" in record:
                _item("section  :", record["section_title"])

    # ── Step 4: Semantic search ────────────────────────────────────────────
    _section("STEP 4  —  Semantic search")

    for query in _QUERIES:
        print(f"\n  Query: \"{query}\"")
        print(f"  {'─' * (W - 2)}")

        results = source.execute_query(
            {"op": "WEB.SEARCH", "query": query, "k": 3, "collection": "news"}
        )

        if not results:
            print("  (no results)")
            continue

        for rank, r in enumerate(results, 1):
            score = r.get("score", "?")
            score_str = f"{score:.4f}" if isinstance(score, float) else str(score)
            print(f"\n  Result {rank}  (cosine distance: {score_str})")
            _item("url      :", r.get("url", "—"))
            _item("title    :", r.get("title", "—"))
            _item("text     :", _excerpt(r.get("text", ""), 160))

    # ── Step 5: Natural language queries ──────────────────────
    _section("STEP 5  —  Natural language queries (Claude tool use)")

    _nl_questions = [
        "What did scientists say about global warming targets?",
        "Find news about China's missile test in the Pacific",
    ]

    for question in _nl_questions:
        print(f"\n  Q: \"{question}\"")
        print(f"  {'─' * (W - 2)}")
        results = source.execute_query_nl(question, collection="news")
        if not results:
            print("  (no results)")
            continue
        for rank, r in enumerate(results, 1):
            score = r.get("score", "?")
            score_str = f"{score:.4f}" if isinstance(score, float) else str(score)
            print(f"\n  Result {rank}  (cosine distance: {score_str})")
            _item("url      :", r.get("url", "—"))
            _item("title    :", r.get("title", "—"))
            _item("text     :", _excerpt(r.get("text", ""), 160))

    # ── Summary ───────────────────────────────────────────────────────────
    _section("SUMMARY")

    stats = vector_store.fetch_source_stats()
    _ok(f"Articles ingested : {len(seen_urls)}")
    _ok(f"Chunks stored     : {stats['total_docs']}")
    _ok(f"Searches run      : {len(_QUERIES)} structured + {len(_nl_questions)} natural language")
    _ok(f"Vector store      : MemoryVectorSource (in-process, no external services)")

    print()
    print("  Note: OpenAIEmbeddingModel produces real semantic vectors — search")
    print("        results are ranked by genuine semantic similarity.")
    print()
    _hr("═")
    print()


if __name__ == "__main__":
    main()
