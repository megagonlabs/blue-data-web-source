"""Integration demo: WEB.SEARCH_WEB — live web search via Tavily.

WEB.SEARCH_WEB uses Tavily to find relevant URLs for a query, ingests each
page on the fly, then returns semantically ranked chunks from the live results.

Unlike WEB.SEARCH (which queries an existing index), SEARCH_WEB fetches
fresh content at call time — no prior ingest step required.

Deps: TAVILY_API_KEY, OPENAI_API_KEY (for real semantic embeddings)
      pip install tavily-python
Run:  TAVILY_API_KEY=tvly-... OPENAI_API_KEY=sk-... python3 examples/integration-demos/search_web_demo.py

Skip OpenAI: TAVILY_API_KEY=tvly-... python3 examples/integration-demos/search_web_demo.py --dummy
  (uses DummyEmbeddingModel — Tavily still fetches URLs, but scores are hash-based)
"""
from __future__ import annotations

import os
import sys

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TAVILY_API_KEY:
    print("ERROR: TAVILY_API_KEY environment variable not set.")
    print()
    print("Get a free API key at https://tavily.com/")
    print("Then run: TAVILY_API_KEY=tvly-... python3 examples/integration-demos/search_web_demo.py")
    sys.exit(1)

USE_DUMMY = "--dummy" in sys.argv
if not OPENAI_API_KEY and not USE_DUMMY:
    print("WARN: OPENAI_API_KEY not set — falling back to DummyEmbeddingModel.")
    print("      Pass --dummy to suppress this warning, or set OPENAI_API_KEY.")
    USE_DUMMY = True

from blue_data.source.vector.memory_vector_source import MemoryVectorSource
from blue_web_source import WebDataSource
from blue_web_source.source.web.search_provider import TavilySearchProvider

if USE_DUMMY:
    from blue_doc_source.source.document.embeddings.dummy_embedding import DummyEmbeddingModel
    embedding_model = DummyEmbeddingModel()
else:
    from blue_doc_source.source.document.embeddings.openai_embedding import OpenAIEmbeddingModel
    embedding_model = OpenAIEmbeddingModel(model="text-embedding-3-small")

COLLECTION = "live_search"
W = 68
QUERIES = [
    "latest advances in solid-state batteries 2026",
    "AI regulation EU Act 2025 compliance",
]


def _hr():
    print("─" * W)


def _ok(msg: str):
    print(f"  ✓  {msg}")


def _excerpt(text: str, n: int = 140) -> str:
    text = text.replace("\n", " ").strip()
    return text[:n] + ("…" if len(text) > n else "")


def main():
    print()
    print("═" * W)
    print("  blue-data-web-source — Live Web Search (WEB.SEARCH_WEB)")
    print("═" * W)

    embedding_label = "DummyEmbeddingModel (hash-based)" if USE_DUMMY else \
                      f"OpenAIEmbeddingModel (text-embedding-3-small, dim={embedding_model.dim})"

    # ── Setup ────────────────────────────────────────────────
    print()
    print("  SETUP")
    _hr()

    vector_store     = MemoryVectorSource()
    search_provider  = TavilySearchProvider()  # reads TAVILY_API_KEY from env

    source = WebDataSource(
        name="live-search",
        embedding_model=embedding_model,
        vector_source=vector_store,
        search_provider=search_provider,
        default_collection=COLLECTION,
    )
    source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": COLLECTION})

    _ok(f"EmbeddingModel  : {embedding_label}")
    _ok( "VectorStore     : MemoryVectorSource (in-process)")
    _ok( "SearchProvider  : TavilySearchProvider (TAVILY_API_KEY loaded)")

    # ── Live search ──────────────────────────────────────────
    for qi, query in enumerate(QUERIES, 1):
        print()
        print(f"  QUERY {qi}: \"{query}\"")
        _hr()
        print("  Fetching from Tavily and ingesting top results…")

        results = source.execute_query({
            "op": "WEB.SEARCH_WEB",
            "query": query,
            "k": 4,
            "collection": COLLECTION,
        })

        if not results:
            print("  (no results returned)")
            continue

        print(f"  Returned {len(results)} ranked chunks:\n")
        for i, r in enumerate(results, 1):
            score = r.get("score")
            score_str = f"{score:.4f}" if isinstance(score, float) else "?"
            domain = r.get("domain", "?")
            title  = (r.get("title") or "—")[:50]
            sec    = f"  § {r['section_title']}" if r.get("section_title") else ""
            print(f"  #{i}  score={score_str}  {domain}{sec}")
            print(f"       title : {title}")
            print(f"       url   : {r.get('url', '?')}")
            print(f"       text  : {_excerpt(r.get('text', ''), 130)}")
            print()

    # ── Compare SEARCH_WEB vs SEARCH ─────────────────────────
    print()
    print("  HOW SEARCH_WEB DIFFERS FROM SEARCH")
    _hr()
    comparison = [
        ("WEB.SEARCH",     "Queries pre-built vector index",  "Instant  (indexed)",     "Requires prior WEB.INGEST"),
        ("WEB.SEARCH_WEB", "Tavily → ingest on the fly",       "Slower   (live fetch)",  "No prior ingest needed"),
    ]
    print(f"  {'Op':<18} {'Flow':<30} {'Latency':<20} {'Requires'}")
    print(f"  {'─' * 18} {'─' * 30} {'─' * 20} {'─' * 22}")
    for op, flow, latency, requires in comparison:
        print(f"  {op:<18} {flow:<30} {latency:<20} {requires}")

    print()
    print("  Use SEARCH_WEB for:")
    print("   • Queries about events after your last crawl")
    print("   • Exploratory research — no curated URL list needed")
    print("   • Combining live web context with an existing index")
    print()
    print("  Use SEARCH for:")
    print("   • Low-latency retrieval over a curated document set")
    print("   • Controlled content — only pages you chose to ingest")
    print()

    stats = vector_store.fetch_source_stats()
    _ok(f"Chunks ingested by SEARCH_WEB: {stats['total_docs']}")
    print()
    print("═" * W)
    print()


if __name__ == "__main__":
    main()
