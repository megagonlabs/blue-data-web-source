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
    """

    @abstractmethod
    def get(self, page_id: str) -> Optional[Dict[str, Any]]:
        """Return the stored record for *page_id*, or ``None`` if not seen before."""

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
        self._lock = threading.Lock()

    def get(self, page_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._store.get(page_id)

    def set(self, page_id: str, record: Dict[str, Any]) -> None:
        with self._lock:
            self._store[page_id] = record

    def delete(self, page_id: str) -> None:
        with self._lock:
            self._store.pop(page_id, None)


class FileFreshnessStore(FreshnessStore):
    """JSON-file-backed freshness store — survives process restarts.

    Writes atomically (write to ``<path>.tmp`` then ``os.replace``) so a
    crash mid-write never corrupts the file.

    Args:
        path: Path to the JSON file. Created on first write if absent.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._store: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if os.path.exists(self._path):
            with open(self._path, "r", encoding="utf-8") as fh:
                content = fh.read().strip()
            if content:
                self._store = json.loads(content)

    def _save(self) -> None:
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._store, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self._path)

    def get(self, page_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._store.get(page_id)

    def set(self, page_id: str, record: Dict[str, Any]) -> None:
        with self._lock:
            self._store[page_id] = record
            self._save()

    def delete(self, page_id: str) -> None:
        with self._lock:
            if page_id in self._store:
                del self._store[page_id]
                self._save()
