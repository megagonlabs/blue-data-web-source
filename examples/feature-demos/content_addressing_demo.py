"""Feature demo: URL-hash content addressing and deduplication.

WebChunkRecord IDs are derived from sha256(final_url)[:16] — the same URL
always produces the same chunk IDs, so re-ingesting is a safe upsert rather
than a duplicate append.

This demo shows:
  1. Deterministic chunk_id from URL hash
  2. Re-ingest same URL → same IDs, no duplicate chunks
  3. Redirect handling — final_url (post-redirect) drives the hash
  4. Different URL, same content → different IDs

Deps: none (no API key, no network)
Run:  python3 examples/feature-demos/content_addressing_demo.py
"""
from __future__ import annotations

import sys
from hashlib import sha256
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from blue_doc_source.source.document.embeddings.dummy_embedding import DummyEmbeddingModel
from blue_web_source import WebDataSource
from blue_web_source.source.web.fetcher import FetchResult

W = 68

ARTICLE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Battery Storage Breakthrough</title>
  <meta name="description" content="New solid-state batteries achieve 500 Wh/kg density.">
</head>
<body>
  <article>
    <h1>Battery Storage Breakthrough</h1>
    <p>Researchers at several university labs announced solid-state battery prototypes
    achieving energy density of 500 Wh/kg — more than double current lithium-ion
    cells. The advance addresses range anxiety for electric vehicles and grid storage.</p>

    <h2>Manufacturing Readiness</h2>
    <p>Scale-up challenges remain significant. Thin-film deposition of solid electrolytes
    at commercial throughput is the primary engineering bottleneck. Pilot production lines
    are expected by 2027, with volume manufacturing following in 2029.</p>

    <h2>Grid Applications</h2>
    <p>At 500 Wh/kg, stationary storage would become cost-competitive with pumped hydro,
    the current low-cost benchmark for long-duration storage. Utilities in Germany and
    California have already issued pre-commercial procurement requests.</p>
  </article>
</body>
</html>"""

ORIGINAL_URL  = "https://tech.example.com/batteries"
REDIRECT_URL  = "https://tech.example.com/batteries?utm_source=newsletter"
CANONICAL_URL = "https://tech.example.com/batteries"  # redirect target (final_url)
DIFFERENT_URL = "https://other.example.com/batteries"


def _expected_page_id(url: str) -> str:
    return "web_" + sha256(url.encode()).hexdigest()[:16]


def _build_source() -> WebDataSource:
    return WebDataSource(
        name="dedup-demo",
        embedding_model=DummyEmbeddingModel(),
        default_collection="dedup",
    )


def _mock_direct(url_map: dict[str, str]) -> MagicMock:
    """Fetcher where final_url == url (no redirect)."""
    m = MagicMock()

    def _fetch(url: str) -> FetchResult:
        html = url_map.get(url)
        if html is None:
            raise ValueError(f"No fixture: {url}")
        return FetchResult(url=url, final_url=url, html=html,
                           status_code=200, content_type="text/html")

    m.fetch.side_effect = _fetch
    return m


def _mock_redirect(source_url: str, final_url: str, html: str) -> MagicMock:
    """Fetcher that simulates a redirect: source_url → final_url."""
    m = MagicMock()

    def _fetch(url: str) -> FetchResult:
        if url == source_url:
            return FetchResult(url=url, final_url=final_url, html=html,
                               status_code=200, content_type="text/html")
        raise ValueError(f"No fixture: {url}")

    m.fetch.side_effect = _fetch
    return m


def _hr():
    print("─" * W)


def _ok(msg: str):
    print(f"  ✓  {msg}")


def main():
    print()
    print("═" * W)
    print("  blue-data-web-source — Content Addressing & Dedup")
    print("═" * W)

    # ── 1: Deterministic IDs ─────────────────────────────────
    print()
    print("  PART 1 — Deterministic chunk_id from URL")
    _hr()
    page_id = _expected_page_id(ORIGINAL_URL)
    print(f"  URL      : {ORIGINAL_URL}")
    print(f"  sha256   : {sha256(ORIGINAL_URL.encode()).hexdigest()}")
    print(f"  page_id  : {page_id}  (first 16 hex chars)")
    print(f"  chunk_00 : {page_id}_chunk_0000")
    print(f"  chunk_01 : {page_id}_chunk_0001")
    print()

    source = _build_source()
    source._pipeline._fetcher = _mock_direct({ORIGINAL_URL: ARTICLE_HTML})
    records = source.execute_query({"op": "WEB.INGEST", "url": ORIGINAL_URL, "collection": "dedup"})
    _ok(f"Ingest produced {len(records)} chunks")
    for r in records:
        _ok(f"  {r['chunk_id']}")

    # ── 2: Re-ingest same URL → same IDs ────────────────────
    print()
    print("  PART 2 — Re-ingest same URL (safe idempotency)")
    _hr()
    source2 = _build_source()
    source2._pipeline._fetcher = _mock_direct({ORIGINAL_URL: ARTICLE_HTML})
    records2 = source2.execute_query({"op": "WEB.INGEST", "url": ORIGINAL_URL, "collection": "dedup"})

    ids_first  = [r["chunk_id"] for r in records]
    ids_second = [r["chunk_id"] for r in records2]

    _ok(f"First ingest : {ids_first}")
    _ok(f"Second ingest: {ids_second}")
    assert ids_first == ids_second, "IDs differ — dedup is broken"
    _ok("chunk_id values are identical — upsert overwrites, no duplicates")

    # ── 3: Redirect handling ─────────────────────────────────
    print()
    print("  PART 3 — Redirect: UTM-tracked URL → canonical URL")
    _hr()
    print(f"  Source URL (fetched) : {REDIRECT_URL}")
    print(f"  Final URL (canonical): {CANONICAL_URL}")
    print()

    source3 = _build_source()
    source3._pipeline._fetcher = _mock_redirect(
        source_url=REDIRECT_URL, final_url=CANONICAL_URL, html=ARTICLE_HTML
    )
    records3 = source3.execute_query({"op": "WEB.INGEST", "url": REDIRECT_URL, "collection": "redir"})

    expected_page_id = _expected_page_id(CANONICAL_URL)
    actual_page_id   = records3[0]["chunk_id"].rsplit("_chunk_", 1)[0]

    _ok(f"Records produced: {len(records3)}")
    _ok(f"page_id  (actual)  : {actual_page_id}")
    _ok(f"page_id  (expected): {expected_page_id}  ← sha256(final_url)")
    assert actual_page_id == expected_page_id, "page_id does not match sha256(final_url)"
    _ok("page_id derived from final_url, not original URL")
    _ok("Fetching with UTM params produces the same chunk IDs as the canonical URL")

    # ── 4: Different URL → different IDs ────────────────────
    print()
    print("  PART 4 — Different domain URL → different chunk IDs")
    _hr()
    source4 = _build_source()
    source4._pipeline._fetcher = _mock_direct({DIFFERENT_URL: ARTICLE_HTML})
    records4 = source4.execute_query({"op": "WEB.INGEST", "url": DIFFERENT_URL, "collection": "diff"})

    id_orig = records[0]["chunk_id"]
    id_diff = records4[0]["chunk_id"]
    print(f"  URL A chunk_id : {id_orig}")
    print(f"  URL B chunk_id : {id_diff}")
    assert id_orig != id_diff, "Different URLs should produce different IDs"
    _ok("Same content, different URL → different chunk IDs")
    _ok("URL is the identity key, not content hash (unlike doc-source which hashes file bytes)")

    # ── Summary table ────────────────────────────────────────
    print()
    print("  ID SCHEME COMPARISON: web-source vs doc-source")
    _hr()
    print(f"  {'Property':<30} {'web-source':<22} {'doc-source'}")
    print(f"  {'─' * 30} {'─' * 22} {'─' * 20}")
    rows = [
        ("Hash input",      "sha256(final_url)",    "sha256(file bytes)"),
        ("ID format",       "web_{h16}_chunk_NNNN", "doc_{h16}_chunk_NNNNNN"),
        ("Chunk # padding", "4 digits (0000)",       "6 digits (000000)"),
        ("Re-ingest same",  "same IDs (upsert)",    "same IDs (upsert)"),
        ("UTM redirect",    "→ canonical final_url", "N/A"),
    ]
    for prop, web, doc in rows:
        print(f"  {prop:<30} {web:<22} {doc}")
    print()
    print("═" * W)
    print()


if __name__ == "__main__":
    main()
