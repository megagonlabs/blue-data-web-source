from __future__ import annotations

import json
import os
import threading
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class FreshnessStore(ABC):
    """Pluggable storage for per-page content hashes used to detect unchanged pages.

    Each record keyed by ``page_id`` contains:
    ``content_hash``, ``url``, ``chunk_count``,
    ``last_ingested_at`` (when content last changed and was re-processed),
    ``last_checked_at`` (when the page was last fetched, even if unchanged).

    Implementations must also maintain a reverse index from ``content_hash``
    to ``page_id`` so that :meth:`get_by_content_hash` can detect when the
    same body text has already been ingested from a different URL.
    """

    @abstractmethod
    def get(self, page_id: str) -> Optional[Dict[str, Any]]:
        """Return the stored record for *page_id*, or ``None`` if not seen before."""

    @abstractmethod
    def get_by_content_hash(self, content_hash: str) -> Optional[Dict[str, Any]]:
        """Return the record whose ``content_hash`` matches, or ``None``.

        Used for cross-URL deduplication: if the same extracted text has
        already been ingested from a different URL, the pipeline can skip
        re-chunking and re-embedding the duplicate.
        """

    @abstractmethod
    def set(self, page_id: str, record: Dict[str, Any]) -> None:
        """Write or overwrite the record for *page_id*."""

    @abstractmethod
    def delete(self, page_id: str) -> None:
        """Remove the record for *page_id*. No-op if not present."""


class MemoryFreshnessStore(FreshnessStore):
    """In-process freshness store — default, zero dependencies, lost on restart."""

    def __init__(self) -> None:
        self._store: Dict[str, Dict[str, Any]] = {}
        self._hash_index: Dict[str, str] = {}  # content_hash → page_id
        self._lock = threading.Lock()

    def get(self, page_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._store.get(page_id)

    def get_by_content_hash(self, content_hash: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            page_id = self._hash_index.get(content_hash)
            return self._store.get(page_id) if page_id else None

    def set(self, page_id: str, record: Dict[str, Any]) -> None:
        with self._lock:
            old = self._store.get(page_id)
            if old and "content_hash" in old:
                self._hash_index.pop(old["content_hash"], None)
            if "content_hash" in record:
                self._hash_index[record["content_hash"]] = page_id
            self._store[page_id] = record

    def delete(self, page_id: str) -> None:
        with self._lock:
            old = self._store.pop(page_id, None)
            if old and "content_hash" in old:
                self._hash_index.pop(old["content_hash"], None)


class FileFreshnessStore(FreshnessStore):
    """JSON-file-backed freshness store — survives process restarts.

    Writes atomically (write to ``<path>.tmp`` then ``os.replace``) so a
    crash mid-write never corrupts the file.

    The content-hash reverse index is derived from the store on load and
    maintained in memory; it does not need to be persisted separately.

    Args:
        path: Path to the JSON file. Created on first write if absent.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._store: Dict[str, Dict[str, Any]] = {}
        self._hash_index: Dict[str, str] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if os.path.exists(self._path):
            with open(self._path, "r", encoding="utf-8") as fh:
                content = fh.read().strip()
            if content:
                self._store = json.loads(content)
        self._hash_index = {
            rec["content_hash"]: page_id
            for page_id, rec in self._store.items()
            if "content_hash" in rec
        }

    def _save(self) -> None:
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._store, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self._path)

    def get(self, page_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._store.get(page_id)

    def get_by_content_hash(self, content_hash: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            page_id = self._hash_index.get(content_hash)
            return self._store.get(page_id) if page_id else None

    def set(self, page_id: str, record: Dict[str, Any]) -> None:
        with self._lock:
            old = self._store.get(page_id)
            if old and "content_hash" in old:
                self._hash_index.pop(old["content_hash"], None)
            if "content_hash" in record:
                self._hash_index[record["content_hash"]] = page_id
            self._store[page_id] = record
            self._save()

    def delete(self, page_id: str) -> None:
        with self._lock:
            old = self._store.pop(page_id, None)
            if old and "content_hash" in old:
                self._hash_index.pop(old["content_hash"], None)
            if old is not None:
                self._save()
