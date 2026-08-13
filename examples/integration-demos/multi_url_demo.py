"""Integration demo: Ingest a batch of URLs in one WEB.INGEST call.

WEB.INGEST accepts either a single `url` or a `urls` list. This demo ingests
three thematically different articles at once, then searches across all of them.

Deps: none (no API key, no network)
Run:  python3 examples/integration-demos/multi_url_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from blue_data.source.vector.memory_vector_source import MemoryVectorSource
from blue_doc_source.source.document.embeddings.dummy_embedding import DummyEmbeddingModel
from blue_web_source import WebDataSource
from mock_fetcher import (
    CLIMATE_HTML, CLIMATE_URL, NPR_URL, POLICY_HTML, POLICY_URL,
    build_mock_fetcher, load_npr_html,
)

COLLECTION = "multi_url"
W = 68


def _hr():
    print("─" * W)


def _ok(msg: str):
    print(f"  ✓  {msg}")


def _excerpt(text: str, n: int = 110) -> str:
    text = text.replace("\n", " ").strip()
    return text[:n] + ("…" if len(text) > n else "")


def main():
    npr_html = load_npr_html()
    if npr_html is None:
        print("ERROR: data/fixtures/npr_article.html not found — run from repo root.")
        sys.exit(1)

    print()
    print("═" * W)
    print("  blue-data-web-source — Multi-URL Batch Ingest")
    print("═" * W)

    # ── Setup ────────────────────────────────────────────────
    print()
    print("  SETUP")
    _hr()

    vector_store = MemoryVectorSource()
    source = WebDataSource(
        name="multi-url",
        embedding_model=DummyEmbeddingModel(),
        vector_source=vector_store,
        default_collection=COLLECTION,
    )
    source._pipeline._fetcher = build_mock_fetcher({
        CLIMATE_URL: CLIMATE_HTML,
        NPR_URL: npr_html,
        POLICY_URL: POLICY_HTML,
    })
    source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": COLLECTION})
    _ok(f"Collection '{COLLECTION}' ready")
    _ok("3 fixture URLs registered (no real HTTP)")

    # ── Single vs batch ingestion comparison ─────────────────
    print()
    print("  APPROACH COMPARISON")
    _hr()
    print()
    print("  Option A — three separate WEB.INGEST calls:")
    print("    source.execute_query({'op': 'WEB.INGEST', 'url': url1, ...})")
    print("    source.execute_query({'op': 'WEB.INGEST', 'url': url2, ...})")
    print("    source.execute_query({'op': 'WEB.INGEST', 'url': url3, ...})")
    print()
    print("  Option B — one WEB.INGEST call with urls list:")
    print("    source.execute_query({'op': 'WEB.INGEST', 'urls': [url1, url2, url3], ...})")
    print()
    print("  Both produce identical results. Use urls list for cleaner code when")
    print("  you have a batch of URLs ready at the same time.")

    # ── Batch ingest ─────────────────────────────────────────
    print()
    print("  STEP 1 — Batch ingest (urls list)")
    _hr()

    urls = [CLIMATE_URL, NPR_URL, POLICY_URL]
    records = source.execute_query({
        "op": "WEB.INGEST",
        "urls": urls,
        "collection": COLLECTION,
    })

    _ok(f"Total chunks returned: {len(records)}")
    print()

    # Summarise per URL
    url_chunks: dict[str, list[dict]] = {}
    for r in records:
        url_chunks.setdefault(r["url"], []).append(r)

    for url, chunks in url_chunks.items():
        title = chunks[0].get("title", "—")[:50]
        domain = chunks[0].get("domain", "?")
        print(f"    {len(chunks):2d} chunks  |  {domain}  |  {title}")
        print(f"           chunk IDs: {chunks[0]['chunk_id']} … {chunks[-1]['chunk_id']}")

    # ── Per-URL verification ─────────────────────────────────
    print()
    print("  STEP 2 — Verify chunk structure for each article")
    _hr()

    for url, chunks in url_chunks.items():
        first = chunks[0]
        last  = chunks[-1]
        print(f"\n  {first.get('domain', url)}")
        print(f"    title    : {first.get('title', '—')}")
        print(f"    date     : {first.get('date') or '—'}")
        print(f"    chunks   : {len(chunks)}  (index 0 … {last['chunk_index']})")
        print(f"    chunk 0  : {first['chunk_id']}")
        desc = first.get("description")
        if desc:
            print(f"    desc (c0): {desc[:70]}…")
        # description must be absent from non-first chunks
        missing_desc = all("description" not in c for c in chunks[1:])
        print(f"    desc on later chunks: {'none ✓' if missing_desc else 'PRESENT (unexpected)'}")

    # ── Search across all articles ───────────────────────────
    print()
    print("  STEP 3 — Search across all ingested articles")
    _hr()
    print("  Note: DummyEmbeddingModel scores are hash-based, not semantic.")
    print()

    query = "policy and government commitments"
    print(f"  Query: \"{query}\"")
    results = source.execute_query({
        "op": "WEB.SEARCH",
        "query": query,
        "k": 4,
        "collection": COLLECTION,
    })
    for i, r in enumerate(results, 1):
        score = r.get("score")
        score_str = f"{score:.4f}" if isinstance(score, float) else "?"
        sec = f"  § {r['section_title']}" if r.get("section_title") else ""
        print(f"\n  #{i}  score={score_str}  {r.get('domain', '?')}{sec}")
        print(f"       {_excerpt(r.get('text', ''), 110)}")

    # ── Stats ────────────────────────────────────────────────
    print()
    print("  SUMMARY")
    _hr()
    stats = vector_store.fetch_source_stats()
    _ok(f"URLs ingested   : {len(url_chunks)}")
    _ok(f"Chunks stored   : {stats['total_docs']}")
    _ok( "Batch ingest ('urls' list) ≡ N individual ingest calls")
    print()
    print("═" * W)
    print()


if __name__ == "__main__":
    main()
