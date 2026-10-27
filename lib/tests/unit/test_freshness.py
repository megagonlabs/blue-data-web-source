import json
import threading

import pytest

from blue_data_web_source.source.web.freshness import (
    FileFreshnessStore,
    MemoryFreshnessStore,
)

_RECORD = {
    "page_id": "web_abc123",
    "content_hash": "deadbeef",
    "url": "https://example.com",
    "chunk_count": 3,
    "last_ingested_at": "2026-08-18T00:00:00+00:00",
    "last_checked_at": "2026-08-18T00:00:00+00:00",
}


@pytest.fixture(params=["memory", "file"])
def store(request, tmp_path):
    """Run every store test against both the in-memory and file-backed stores."""
    if request.param == "memory":
        return MemoryFreshnessStore()
    return FileFreshnessStore(str(tmp_path / "freshness.json"))


# ── get / set / delete, keyed by (collection, page_id) ────────────────────────

def test_get_unknown_returns_none(store):
    assert store.get("web", "web_unknown") is None


def test_set_then_get(store):
    store.set("web", "web_abc123", _RECORD)
    assert store.get("web", "web_abc123") == _RECORD


def test_set_overwrites(store):
    store.set("web", "web_abc123", _RECORD)
    store.set("web", "web_abc123", {**_RECORD, "chunk_count": 99})
    assert store.get("web", "web_abc123")["chunk_count"] == 99


def test_delete_removes_record(store):
    store.set("web", "web_abc123", _RECORD)
    store.delete("web", "web_abc123")
    assert store.get("web", "web_abc123") is None


def test_delete_unknown_does_not_raise(store):
    store.delete("web", "web_nonexistent")  # no-op


# ── collections are independent namespaces (#46) ──────────────────────────────

def test_same_page_id_independent_across_collections(store):
    # The same URL (→ same page_id) tracked in two collections must not collide.
    store.set("news", "web_abc123", {**_RECORD, "url": "https://x.com", "chunk_count": 1})
    store.set("archive", "web_abc123", {**_RECORD, "url": "https://x.com", "chunk_count": 2})
    assert store.get("news", "web_abc123")["chunk_count"] == 1
    assert store.get("archive", "web_abc123")["chunk_count"] == 2
    store.delete("news", "web_abc123")
    assert store.get("news", "web_abc123") is None
    assert store.get("archive", "web_abc123") is not None


# ── atomic content-hash claim / release ───────────────────────────────────────

def test_claim_unclaimed_returns_true(store):
    assert store.claim_content_hash("web", "h1", "page_a") is True


def test_claim_by_same_page_is_idempotent(store):
    assert store.claim_content_hash("web", "h1", "page_a") is True
    assert store.claim_content_hash("web", "h1", "page_a") is True


def test_claim_by_different_page_returns_false(store):
    assert store.claim_content_hash("web", "h1", "page_a") is True
    assert store.claim_content_hash("web", "h1", "page_b") is False


def test_claim_is_per_collection(store):
    # The same hash can be claimed independently in different collections.
    assert store.claim_content_hash("news", "h1", "page_a") is True
    assert store.claim_content_hash("archive", "h1", "page_b") is True


def test_release_lets_another_page_claim(store):
    assert store.claim_content_hash("web", "h1", "page_a") is True
    store.release_content_hash("web", "h1", "page_a")
    assert store.claim_content_hash("web", "h1", "page_b") is True


def test_release_noop_when_not_owner(store):
    store.claim_content_hash("web", "h1", "page_a")
    store.release_content_hash("web", "h1", "page_b")  # page_b doesn't own it
    assert store.claim_content_hash("web", "h1", "page_a") is True   # still owned by a
    assert store.claim_content_hash("web", "h1", "page_c") is False


def test_release_keeps_claim_backed_by_stored_record(store):
    # A completed ingest writes a record owning the hash; a later release (e.g. from
    # an unrelated error path) must not drop a claim the stored record still backs.
    store.claim_content_hash("web", "deadbeef", "web_abc123")
    store.set("web", "web_abc123", _RECORD)  # record carries content_hash deadbeef
    store.release_content_hash("web", "deadbeef", "web_abc123")
    assert store.claim_content_hash("web", "deadbeef", "web_other") is False


def test_set_registers_hash_for_cross_url_dedup(store):
    # After set(), a different page with the same content hash is refused.
    store.set("web", "web_abc123", _RECORD)
    assert store.claim_content_hash("web", "deadbeef", "web_other") is False


def test_changed_content_releases_old_hash(store):
    store.set("web", "web_abc123", _RECORD)  # hash deadbeef
    store.set("web", "web_abc123", {**_RECORD, "content_hash": "feedface"})
    # The page's old hash is freed; a different page may claim it.
    assert store.claim_content_hash("web", "deadbeef", "web_other") is True
    # ...but the new hash is held by the page.
    assert store.claim_content_hash("web", "feedface", "web_other") is False


# ── concurrency: the claim is atomic (no check-then-act race) ─────────────────

def test_concurrent_claims_only_one_wins(store):
    results = []
    barrier = threading.Barrier(8)

    def worker(page_id):
        barrier.wait()
        results.append(store.claim_content_hash("web", "race", page_id))

    threads = [threading.Thread(target=worker, args=(f"page_{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for r in results if r) == 1  # exactly one True


# ── FileFreshnessStore persistence ────────────────────────────────────────────

def test_file_store_persists_across_instances(tmp_path):
    path = str(tmp_path / "freshness.json")
    FileFreshnessStore(path).set("news", "web_abc123", _RECORD)
    reopened = FileFreshnessStore(path)
    assert reopened.get("news", "web_abc123") == _RECORD
    # the derived hash index is rebuilt on load → cross-URL dedup still works
    assert reopened.claim_content_hash("news", "deadbeef", "web_other") is False


def test_file_store_on_disk_is_nested_by_collection(tmp_path):
    path = tmp_path / "freshness.json"
    FileFreshnessStore(str(path)).set("news", "web_abc123", _RECORD)
    data = json.loads(path.read_text())
    assert data == {"news": {"web_abc123": _RECORD}}


def test_file_store_discards_legacy_flat_format(tmp_path):
    # Pre-#46 files were flat {page_id: record} with no collection; they are
    # discarded (freshness is a cache) rather than mis-read as collections.
    path = tmp_path / "freshness.json"
    path.write_text(json.dumps({"web_abc123": _RECORD}))
    store = FileFreshnessStore(str(path))
    assert store.get("web", "web_abc123") is None


# ── #74: concurrent writers must not lose each other's updates ────────────────

def _writer_worker(path: str, i: int) -> None:
    """Separate-process writer: set one distinct record in the shared file."""
    from blue_data_web_source.source.web.freshness import FileFreshnessStore

    FileFreshnessStore(path).set("web", f"web_page_{i:03d}", {
        "page_id": f"web_page_{i:03d}",
        "content_hash": f"hash{i:03d}",
        "url": f"https://example.com/{i}",
        "chunk_count": 1,
        "last_ingested_at": "2026-10-26T00:00:00+00:00",
        "last_checked_at": "2026-10-26T00:00:00+00:00",
    })


def test_file_store_concurrent_processes_keep_all_updates(tmp_path):
    pytest.importorskip("fcntl", reason="cross-process file lock needs POSIX fcntl")
    import multiprocessing as mp

    path = str(tmp_path / "freshness.json")
    n = 16
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_writer_worker, args=(path, i)) for i in range(n)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)

    # Every writer's update survived — none clobbered — and the file is valid JSON.
    reopened = FileFreshnessStore(path)
    kept = sum(1 for i in range(n) if reopened.get("web", f"web_page_{i:03d}") is not None)
    assert kept == n
    with open(path, encoding="utf-8") as fh:
        json.load(fh)  # must be readable / uncorrupted
