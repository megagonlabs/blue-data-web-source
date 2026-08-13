"""Minimal quickstart: WebDataSource with in-process vector store and no network.

Deps: none (no API key, no Redis, no real HTTP)
Run:  python3 examples/backends/memory_quickstart.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from blue_data.source.vector.memory_vector_source import MemoryVectorSource
from blue_data_doc_source.source.document.embeddings.dummy_embedding import DummyEmbeddingModel
from blue_data_web_source import WebDataSource
from mock_fetcher import (
    CLIMATE_HTML, CLIMATE_URL, NPR_URL,
    build_mock_fetcher, load_npr_html,
)

COLLECTION = "quickstart"
W = 60


def _hr():
    print("─" * W)


def _ok(msg: str):
    print(f"  ✓  {msg}")


def _item(label: str, value: str):
    print(f"       {label:<10} {value}")


def _excerpt(text: str, n: int = 100) -> str:
    text = text.replace("\n", " ").strip()
    return text[:n] + ("…" if len(text) > n else "")


def main():
    # ── NPR fixture ──────────────────────────────────────────
    npr_html = load_npr_html()
    if npr_html is None:
        print("ERROR: data/fixtures/npr_article.html not found — run from repo root.")
        sys.exit(1)

    print()
    print("═" * W)
    print("  blue-data-web-source — Memory Quickstart")
    print("═" * W)

    # ── Build source ─────────────────────────────────────────
    print()
    print("  SETUP")
    _hr()

    embedding_model = DummyEmbeddingModel()
    vector_store    = MemoryVectorSource()
    source          = WebDataSource(
        name="quickstart",
        embedding_model=embedding_model,
        vector_source=vector_store,
        default_collection=COLLECTION,
    )

    # Intercept HTTP with local HTML fixtures
    source._pipeline._fetcher = build_mock_fetcher({
        CLIMATE_URL: CLIMATE_HTML,
        NPR_URL: npr_html,
    })

    _ok(f"EmbeddingModel  : DummyEmbeddingModel  (dim={embedding_model.dim})")
    _ok( "VectorStore     : MemoryVectorSource   (in-process)")
    _ok( "Fetcher         : mocked               (no real HTTP)")

    # ── Ensure index ─────────────────────────────────────────
    print()
    print("  STEP 1 — Create vector index")
    _hr()

    source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": COLLECTION})
    _ok(f"Index '{COLLECTION}' created  (dim={embedding_model.dim})")

    # ── Ingest pages ─────────────────────────────────────────
    print()
    print("  STEP 2 — Ingest articles")
    _hr()

    all_chunks: list[dict] = []
    for url in [CLIMATE_URL, NPR_URL]:
        records = source.execute_query({"op": "WEB.INGEST", "url": url, "collection": COLLECTION})
        if not records:
            print(f"  (no content: {url})")
            continue
        all_chunks.extend(records)
        _ok(f"{len(records):2d} chunks  |  {records[0].get('domain', '?')}  |  {records[0].get('title', '—')}")

    print()
    _ok(f"Total chunks stored: {len(all_chunks)}")

    # ── Chunk sample ─────────────────────────────────────────
    print()
    print("  STEP 3 — Chunk sample (chunk 0 of each article)")
    _hr()

    seen = set()
    for rec in all_chunks:
        if rec["url"] not in seen and rec["chunk_index"] == 0:
            seen.add(rec["url"])
            print(f"  chunk_id : {rec['chunk_id']}")
            _item("url      :", rec["url"])
            _item("domain   :", rec["domain"])
            _item("title    :", rec.get("title", "—"))
            _item("text     :", _excerpt(rec["text"]))
            print()

    # ── Search ───────────────────────────────────────────────
    print()
    print("  STEP 4 — Search (DummyEmbeddingModel: hash-based, not semantic)")
    _hr()
    print("  Note: DummyEmbeddingModel produces hash-based vectors;")
    print("        cosine scores are not meaningful for this query.")
    print()

    results = source.execute_query({
        "op": "WEB.SEARCH",
        "query": "climate policy emissions",
        "k": 3,
        "collection": COLLECTION,
    })

    for i, r in enumerate(results, 1):
        score = r.get("score")
        score_str = f"{score:.4f}" if isinstance(score, float) else str(score)
        print(f"  #{i}  score={score_str}  |  {r.get('domain', '?')}")
        _item("title :", r.get("title", "—"))
        _item("text  :", _excerpt(r.get("text", ""), 80))
        print()

    # ── Stats ────────────────────────────────────────────────
    print()
    print("  SUMMARY")
    _hr()
    stats = vector_store.fetch_source_stats()
    _ok(f"Articles ingested : {len(seen)}")
    _ok(f"Chunks stored     : {stats['total_docs']}")
    _ok( "No API key required — fully offline")
    print()
    print("  Next step: swap DummyEmbeddingModel → OpenAIEmbeddingModel")
    print("  for real semantic search similarity scores.")
    print()
    print("═" * W)
    print()


if __name__ == "__main__":
    main()
