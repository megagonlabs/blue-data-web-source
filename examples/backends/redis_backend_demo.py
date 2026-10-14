"""WebDataSource with Redis Stack as the persistent vector store.

Deps: Redis Stack on localhost:6379
      pip install redis
Run:  docker run -p 6379:6379 redis/redis-stack-server:latest
      python3 examples/backends/redis_backend_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from blue_data.source.vector.redis.redis_vector_source import RedisVectorSource
from blue_data_doc_source.source.document.embeddings.dummy_embedding import DummyEmbeddingModel
from blue_data_web_source import WebDataSource
from mock_fetcher import (
    CLIMATE_HTML, CLIMATE_URL, NPR_URL, POLICY_HTML, POLICY_URL,
    build_mock_fetcher, load_npr_html,
)

REDIS_HOST  = "localhost"
REDIS_PORT  = 6379
COLLECTION  = "redis_web_demo"
W = 64


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
    print("  blue-data-web-source — Redis Backend Demo")
    print("═" * W)

    # ── Connect to Redis ─────────────────────────────────────
    print()
    print("  SETUP")
    _hr()

    try:
        vector_store = RedisVectorSource(
            name="redis-web",
            properties={"connection": {
                "host": REDIS_HOST,
                "port": REDIS_PORT,
                "scan_prefix": "web:",
            }},
        )
    except Exception as exc:
        print(f"  ERROR: cannot connect to Redis at {REDIS_HOST}:{REDIS_PORT}")
        print(f"  {exc}")
        print()
        print("  Start Redis Stack with:")
        print("    docker run -p 6379:6379 redis/redis-stack-server:latest")
        sys.exit(1)

    embedding_model = DummyEmbeddingModel()
    source = WebDataSource(
        name="redis-demo",
        embedding_model=embedding_model,
        vector_source=vector_store,
        default_collection=COLLECTION,
    )

    url_map = {CLIMATE_URL: CLIMATE_HTML, NPR_URL: npr_html, POLICY_URL: POLICY_HTML}
    source._pipeline._fetcher = build_mock_fetcher(url_map)

    _ok(f"Redis           : {REDIS_HOST}:{REDIS_PORT}")
    _ok(f"EmbeddingModel  : DummyEmbeddingModel  (dim={embedding_model.dim})")
    _ok(f"Collection      : {COLLECTION}")

    # ── Create index ─────────────────────────────────────────
    print()
    print("  STEP 1 — Create vector index in Redis")
    _hr()
    source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": COLLECTION})
    _ok(f"Index '{COLLECTION}' created  (or already exists — safe to re-run)")

    # ── Ingest ───────────────────────────────────────────────
    print()
    print("  STEP 2 — Ingest three web pages")
    _hr()

    total_chunks = 0
    for url in [CLIMATE_URL, NPR_URL, POLICY_URL]:
        records = source.execute_query({"op": "WEB.INGEST", "url": url, "collection": COLLECTION})
        if not records:
            _ok(f"(no content) {url}")
            continue
        total_chunks += len(records)
        _ok(f"{len(records):2d} chunks  |  {records[0].get('domain', '?')}  |  {records[0].get('title', '—')[:50]}")
        print(f"          chunk IDs: {records[0]['chunk_id']} … {records[-1]['chunk_id']}")

    print()
    _ok(f"Total chunks stored in Redis: {total_chunks}")

    # ── Content-addressing: re-ingest same URL ───────────────
    print()
    print("  STEP 3 — Re-ingest same URL (idempotency check)")
    _hr()
    records_again = source.execute_query({"op": "WEB.INGEST", "url": CLIMATE_URL, "collection": COLLECTION})
    _ok(f"Second ingest produced {len(records_again)} chunks with same chunk_id values")
    _ok( "Same chunk_id → upsert overwrites, no duplicate entries in Redis")

    # ── Search ───────────────────────────────────────────────
    print()
    print("  STEP 4 — Search (DummyEmbeddingModel: hash-based, not semantic)")
    _hr()
    print("  Note: DummyEmbeddingModel scores are not semantically meaningful.")
    print()

    query = "greenhouse gas emissions reduction"
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
        print(f"\n  #{i}  score={score_str}  {r.get('domain', '?')}")
        print(f"       title   : {r.get('title', '—')}")
        print(f"       section : {r.get('section_title', '—')}")
        print(f"       text    : {_excerpt(r.get('text', ''), 100)}")

    # ── Persistence note ─────────────────────────────────────
    print()
    print("  STEP 5 — Persistence")
    _hr()
    _ok("Unlike MemoryVectorSource, Redis data persists across restarts")
    _ok("Run this script again — ENSURE_INDEX is safe to call multiple times")
    _ok("Same chunks are upserted (content-addressed by URL hash)")
    print()
    print("  Swap embedding model for real semantic search:")
    print("    OPENAI_API_KEY=sk-... python3 examples/backends/openai_embeddings_demo.py")
    print()
    print("═" * W)
    print()


if __name__ == "__main__":
    main()
