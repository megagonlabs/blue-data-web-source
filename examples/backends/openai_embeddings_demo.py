"""Compare semantic quality: OpenAIEmbeddingModel vs DummyEmbeddingModel.

Deps: OPENAI_API_KEY
Run:  OPENAI_API_KEY=sk-... python3 examples/backends/openai_embeddings_demo.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from blue_data.source.vector.memory_vector_source import MemoryVectorSource
from blue_data_doc_source.source.document.embeddings.dummy_embedding import DummyEmbeddingModel
from blue_data_doc_source.source.document.embeddings.openai_embedding import OpenAIEmbeddingModel
from blue_data_web_source import WebDataSource
from mock_fetcher import (
    CLIMATE_HTML, CLIMATE_URL, NPR_URL,
    build_mock_fetcher, load_npr_html,
)

if not os.getenv("OPENAI_API_KEY"):
    print("ERROR: OPENAI_API_KEY environment variable not set.")
    sys.exit(1)

COLLECTION = "embedding_cmp"
W = 68


def _hr():
    print("─" * W)


def _ok(msg: str):
    print(f"  ✓  {msg}")


def _excerpt(text: str, n: int = 120) -> str:
    text = text.replace("\n", " ").strip()
    return text[:n] + ("…" if len(text) > n else "")


def _run_search(source: WebDataSource, query: str, collection: str, label: str):
    print(f"\n  Query: \"{query}\"")
    print(f"  {'─' * (W - 2)}")
    results = source.execute_query({"op": "WEB.SEARCH", "query": query, "k": 3, "collection": collection})
    for i, r in enumerate(results, 1):
        score = r.get("score")
        score_str = f"{score:.4f}" if isinstance(score, float) else "?"
        domain = r.get("domain", "?")
        title  = r.get("title", "—")[:40]
        section = r.get("section_title", "")
        sec_str = f"  § {section}" if section else ""
        print(f"\n  {label} #{i}  score={score_str}  {domain}{sec_str}")
        print(f"       title  : {title}")
        print(f"       text   : {_excerpt(r.get('text', ''), 120)}")


def main():
    npr_html = load_npr_html()
    if npr_html is None:
        print("ERROR: data/fixtures/npr_article.html not found — run from repo root.")
        sys.exit(1)

    print()
    print("═" * W)
    print("  blue-data-web-source — OpenAI vs Dummy Embeddings")
    print("═" * W)

    url_map = {CLIMATE_URL: CLIMATE_HTML, NPR_URL: npr_html}

    # ── OpenAI source ────────────────────────────────────────
    print()
    print("  SETUP — OpenAIEmbeddingModel (text-embedding-3-small)")
    _hr()

    openai_model  = OpenAIEmbeddingModel(model="text-embedding-3-small")
    openai_store  = MemoryVectorSource()
    openai_source = WebDataSource(
        name="openai",
        embedding_model=openai_model,
        vector_source=openai_store,
        default_collection=COLLECTION,
    )
    openai_source._pipeline._fetcher = build_mock_fetcher(url_map)
    openai_source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": COLLECTION})
    _ok(f"OpenAI model ready  (dim={openai_model.dim})")

    # ── Dummy source ─────────────────────────────────────────
    dummy_model  = DummyEmbeddingModel()
    dummy_store  = MemoryVectorSource()
    dummy_source = WebDataSource(
        name="dummy",
        embedding_model=dummy_model,
        vector_source=dummy_store,
        default_collection=COLLECTION,
    )
    dummy_source._pipeline._fetcher = build_mock_fetcher(url_map)
    dummy_source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": COLLECTION})
    _ok(f"Dummy model ready   (dim={dummy_model.dim}, hash-based)")

    # ── Ingest into both ─────────────────────────────────────
    print()
    print("  INGEST (same articles into both stores)")
    _hr()
    for url in [CLIMATE_URL, NPR_URL]:
        openai_records = openai_source.execute_query({"op": "WEB.INGEST", "url": url, "collection": COLLECTION})
        dummy_records  = dummy_source.execute_query({"op": "WEB.INGEST", "url": url, "collection": COLLECTION})
        title = (openai_records[0].get("title", url) if openai_records else url)[:50]
        _ok(f"{len(openai_records):2d} chunks  |  {title}")

    # ── Compare search results ────────────────────────────────
    queries = [
        "greenhouse gas emissions and international climate targets",
        "missile test in the Pacific",
    ]

    print()
    print("  SEMANTIC SEARCH COMPARISON")
    _hr()
    print("  OpenAI: real semantic similarity | Dummy: hash-based (random-like)")

    for q in queries:
        print()
        print(f"  ╔ Query: \"{q}\"")
        print(f"  ╟ {'─' * (W - 4)}")
        openai_results = openai_source.execute_query({"op": "WEB.SEARCH", "query": q, "k": 3, "collection": COLLECTION})
        dummy_results  = dummy_source.execute_query({"op": "WEB.SEARCH", "query": q, "k": 3, "collection": COLLECTION})

        print(f"  ╟ OpenAI top-3:")
        for i, r in enumerate(openai_results, 1):
            score = r.get("score")
            score_str = f"{score:.4f}" if isinstance(score, float) else "?"
            sec = f"  § {r['section_title']}" if r.get("section_title") else ""
            print(f"  ║   #{i}  score={score_str}  {r.get('domain', '?')}{sec}")
            print(f"  ║        {_excerpt(r.get('text', ''), 110)}")

        print(f"  ╟ Dummy top-3:")
        for i, r in enumerate(dummy_results, 1):
            score = r.get("score")
            score_str = f"{score:.4f}" if isinstance(score, float) else "?"
            print(f"  ║   #{i}  score={score_str}  {r.get('domain', '?')}")
            print(f"  ║        {_excerpt(r.get('text', ''), 110)}")
        print(f"  ╚{'─' * (W - 3)}")

    # ── Dimension analysis ───────────────────────────────────
    print()
    print("  EMBEDDING DIMENSIONS")
    _hr()
    _ok(f"OpenAIEmbeddingModel   dim={openai_model.dim}  (text-embedding-3-small)")
    _ok(f"DummyEmbeddingModel    dim={dummy_model.dim}  (SHA-256 hash; test only)")
    print()
    print("  Note: OpenAI scores reflect true semantic proximity — paraphrase queries")
    print("        match topically relevant chunks even without shared keywords.")
    print("        Dummy scores are hash-based and uncorrelated with meaning.")
    print()
    print("═" * W)
    print()


if __name__ == "__main__":
    main()
