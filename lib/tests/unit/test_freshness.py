import json
import os

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


# ---------------------------------------------------------------------------
# MemoryFreshnessStore
# ---------------------------------------------------------------------------

class TestMemoryFreshnessStore:
    def test_get_unknown_returns_none(self):
        store = MemoryFreshnessStore()
        assert store.get("web_unknown") is None

    def test_set_then_get(self):
        store = MemoryFreshnessStore()
        store.set("web_abc123", _RECORD)
        assert store.get("web_abc123") == _RECORD

    def test_set_overwrites(self):
        store = MemoryFreshnessStore()
        store.set("web_abc123", _RECORD)
        updated = {**_RECORD, "chunk_count": 99}
        store.set("web_abc123", updated)
        assert store.get("web_abc123")["chunk_count"] == 99

    def test_delete_removes_record(self):
        store = MemoryFreshnessStore()
        store.set("web_abc123", _RECORD)
        store.delete("web_abc123")
        assert store.get("web_abc123") is None

    def test_delete_unknown_does_not_raise(self):
        store = MemoryFreshnessStore()
        store.delete("web_nonexistent")  # should not raise

    def test_independent_keys(self):
        store = MemoryFreshnessStore()
        store.set("web_aaa", {**_RECORD, "url": "https://a.com"})
        store.set("web_bbb", {**_RECORD, "url": "https://b.com"})
        assert store.get("web_aaa")["url"] == "https://a.com"
        assert store.get("web_bbb")["url"] == "https://b.com"
        store.delete("web_aaa")
        assert store.get("web_aaa") is None
        assert store.get("web_bbb") is not None


# ---------------------------------------------------------------------------
# FileFreshnessStore
# ---------------------------------------------------------------------------

class TestFileFreshnessStore:
    def test_get_unknown_returns_none(self, tmp_path):
        store = FileFreshnessStore(str(tmp_path / "freshness.json"))
        assert store.get("web_abc123") is None

    def test_missing_file_on_init_does_not_raise(self, tmp_path):
        FileFreshnessStore(str(tmp_path / "nonexistent.json"))  # should not raise

    def test_set_then_get(self, tmp_path):
        store = FileFreshnessStore(str(tmp_path / "freshness.json"))
        store.set("web_abc123", _RECORD)
        assert store.get("web_abc123") == _RECORD

    def test_set_overwrites(self, tmp_path):
        store = FileFreshnessStore(str(tmp_path / "freshness.json"))
        store.set("web_abc123", _RECORD)
        store.set("web_abc123", {**_RECORD, "chunk_count": 99})
        assert store.get("web_abc123")["chunk_count"] == 99

    def test_delete_removes_record(self, tmp_path):
        store = FileFreshnessStore(str(tmp_path / "freshness.json"))
        store.set("web_abc123", _RECORD)
        store.delete("web_abc123")
        assert store.get("web_abc123") is None

    def test_delete_unknown_does_not_raise(self, tmp_path):
        store = FileFreshnessStore(str(tmp_path / "freshness.json"))
        store.delete("web_nonexistent")  # should not raise

    def test_persists_across_instances(self, tmp_path):
        path = str(tmp_path / "freshness.json")
        store1 = FileFreshnessStore(path)
        store1.set("web_abc123", _RECORD)

        store2 = FileFreshnessStore(path)
        assert store2.get("web_abc123") == _RECORD

    def test_delete_persists_across_instances(self, tmp_path):
        path = str(tmp_path / "freshness.json")
        store1 = FileFreshnessStore(path)
        store1.set("web_abc123", _RECORD)
        store1.delete("web_abc123")

        store2 = FileFreshnessStore(path)
        assert store2.get("web_abc123") is None

    def test_atomic_write_no_tmp_file_after_set(self, tmp_path):
        path = str(tmp_path / "freshness.json")
        store = FileFreshnessStore(path)
        store.set("web_abc123", _RECORD)
        assert not os.path.exists(path + ".tmp")
        assert os.path.exists(path)

    def test_file_is_valid_json(self, tmp_path):
        path = str(tmp_path / "freshness.json")
        store = FileFreshnessStore(path)
        store.set("web_abc123", _RECORD)
        with open(path) as fh:
            data = json.load(fh)
        assert "web_abc123" in data

    def test_empty_file_handled_gracefully(self, tmp_path):
        path = str(tmp_path / "freshness.json")
        open(path, "w").close()  # create empty file
        store = FileFreshnessStore(path)  # should not raise
        assert store.get("web_abc123") is None
