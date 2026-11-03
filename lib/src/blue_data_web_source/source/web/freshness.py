from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

try:
    import fcntl  # POSIX-only; used for the cross-process freshness-file lock (#74)
except ImportError:  # pragma: no cover - non-POSIX (e.g. Windows)
    fcntl = None


class FreshnessStore(ABC):
    """Pluggable storage for per-(collection, page) content hashes.

    Detects unchanged pages and de-duplicates identical body text *within a
    collection*. Records are keyed by ``(collection, page_id)`` — page_id derives
    from the URL — so the same URL ingested into two collections is tracked
    independently; collections are independent namespaces (#46). Each record holds
    ``content_hash``, ``url``, ``chunk_count``, ``last_ingested_at`` and
    ``last_checked_at``.

    Implementations also keep a **per-collection** reverse index from
    ``content_hash`` to ``page_id`` so cross-URL dedup can tell when the same body
    text was already ingested from a *different* URL in the same collection. The
    :meth:`claim_content_hash` / :meth:`release_content_hash` pair makes that check
    atomic, so two identical-text URLs ingested concurrently cannot both pass it.
    """

    @abstractmethod
    def get(self, collection: str, page_id: str) -> Optional[Dict[str, Any]]:
        """Return the record for *(collection, page_id)*, or ``None``."""

    @abstractmethod
    def set(self, collection: str, page_id: str, record: Dict[str, Any]) -> None:
        """Write or overwrite the record for *(collection, page_id)*."""

    @abstractmethod
    def delete(self, collection: str, page_id: str) -> None:
        """Remove the record for *(collection, page_id)*. No-op if absent."""

    @abstractmethod
    def claim_content_hash(self, collection: str, content_hash: str, page_id: str) -> bool:
        """Atomically claim *content_hash* for *(collection, page_id)*.

        Returns ``True`` when the hash was unclaimed in the collection or already
        owned by *page_id* (and is now owned by it); ``False`` when a *different*
        page in the collection already owns it — i.e. a duplicate the caller should
        skip. The check and the claim happen under one lock, so concurrent callers
        with identical text cannot both receive ``True``.
        """

    @abstractmethod
    def release_content_hash(self, collection: str, content_hash: str, page_id: str) -> None:
        """Release a claim made by *(collection, page_id)*.

        No-op unless *page_id* currently owns *content_hash* and no stored record
        for it carries that hash. Called when an ingest fails after claiming, so a
        half-done ingest does not block the hash permanently.
        """

    def owner_of_hash(self, collection: str, content_hash: str) -> Optional[str]:
        """Return the ``page_id`` owning *content_hash* in *collection*, or ``None``.

        Best-effort read used to report which page a duplicate collides with
        (#99). The base implementation returns ``None``; stores that keep a
        reverse index override it.
        """
        return None


class _DictFreshnessStore(FreshnessStore):
    """Shared in-memory implementation over nested dicts, guarded by one lock.

    ``_store``:      ``collection -> page_id -> record``
    ``_hash_index``: ``collection -> content_hash -> page_id``

    Subclasses override :meth:`_persist` (called inside the lock after a record
    mutation) to make writes durable.
    """

    def __init__(self) -> None:
        self._store: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._hash_index: Dict[str, Dict[str, str]] = {}
        self._lock = threading.Lock()

    def _persist(self) -> None:
        """Durability hook; no-op by default (in-memory)."""

    def _rebuild_hash_index(self) -> None:
        self._hash_index = {}
        for collection, pages in self._store.items():
            idx = self._hash_index.setdefault(collection, {})
            for page_id, rec in pages.items():
                content_hash = rec.get("content_hash")
                if content_hash:
                    idx[content_hash] = page_id

    def get(self, collection: str, page_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._store.get(collection, {}).get(page_id)

    def set(self, collection: str, page_id: str, record: Dict[str, Any]) -> None:
        with self._lock:
            self._apply_set(collection, page_id, record)
            self._persist()

    def _apply_set(self, collection: str, page_id: str, record: Dict[str, Any]) -> bool:
        """Apply a set to the in-memory state (no lock, no persist). Returns True."""
        pages = self._store.setdefault(collection, {})
        idx = self._hash_index.setdefault(collection, {})
        old = pages.get(page_id)
        old_hash = old.get("content_hash") if old else None
        new_hash = record.get("content_hash")
        if old_hash and old_hash != new_hash and idx.get(old_hash) == page_id:
            idx.pop(old_hash, None)
        pages[page_id] = record
        if new_hash:
            idx[new_hash] = page_id
        return True

    def delete(self, collection: str, page_id: str) -> None:
        with self._lock:
            if self._apply_delete(collection, page_id):
                self._persist()

    def _apply_delete(self, collection: str, page_id: str) -> bool:
        """Apply a delete to the in-memory state (no lock, no persist).

        Returns True if a record was removed (so the caller persists), False if the
        delete was a no-op.
        """
        pages = self._store.get(collection)
        if not pages:
            return False
        old = pages.pop(page_id, None)
        if old is None:
            return False
        old_hash = old.get("content_hash")
        if old_hash:
            idx = self._hash_index.get(collection, {})
            if idx.get(old_hash) == page_id:
                idx.pop(old_hash, None)
        return True

    def claim_content_hash(self, collection: str, content_hash: str, page_id: str) -> bool:
        with self._lock:
            idx = self._hash_index.setdefault(collection, {})
            owner = idx.get(content_hash)
            if owner is None or owner == page_id:
                idx[content_hash] = page_id
                return True
            return False

    def release_content_hash(self, collection: str, content_hash: str, page_id: str) -> None:
        with self._lock:
            idx = self._hash_index.get(collection)
            if not idx or idx.get(content_hash) != page_id:
                return
            # Keep the index if a stored record already owns this hash (the ingest
            # completed before an unrelated error) so it stays consistent with the
            # store; only drop a claim left by a half-done ingest.
            rec = self._store.get(collection, {}).get(page_id)
            if not rec or rec.get("content_hash") != content_hash:
                idx.pop(content_hash, None)

    def owner_of_hash(self, collection: str, content_hash: str) -> Optional[str]:
        with self._lock:
            return self._hash_index.get(collection, {}).get(content_hash)


class MemoryFreshnessStore(_DictFreshnessStore):
    """In-process freshness store — default, zero dependencies, lost on restart."""


class FileFreshnessStore(_DictFreshnessStore):
    """JSON-file-backed freshness store — survives process restarts, safe to share
    across processes.

    On disk the store is ``{collection: {page_id: record}}``; the per-collection
    content-hash index is derived on load and kept in memory (claims are transient
    and intentionally not persisted).

    Concurrency (#74): a fixed ``<path>.tmp`` + no lock let two processes sharing one
    freshness file (e.g. the CLI and the REST/MCP server on one data directory)
    clobber each other's writes. Every read-modify-write now runs under an exclusive
    cross-process lock (``fcntl.flock`` on a ``<path>.lock`` sidecar) and **re-reads**
    the file first, so each writer merges onto the latest state instead of
    overwriting it. The write goes to a unique temp file in the same directory, then
    ``os.replace`` swaps it in atomically — so the file is always readable and no two
    writers share a temp name. Reads also re-read, so a long-lived process sees other
    processes' updates. (``fcntl`` is POSIX-only; without it the cross-process lock is
    a no-op and only in-process safety remains.)

    Args:
        path: Path to the JSON file. Created on first write if absent.
    """

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = path
        self._lock_path = path + ".lock"
        self._load()

    @contextlib.contextmanager
    def _file_lock(self):
        """Hold an exclusive cross-process lock on the ``.lock`` sidecar."""
        if fcntl is None:  # pragma: no cover - non-POSIX
            yield
            return
        parent = os.path.dirname(self._lock_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self._lock_path, "w", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_fh, fcntl.LOCK_UN)

    def _reload_preserving_claims(self) -> None:
        """Re-read the file, keeping transient (not-yet-persisted) claims.

        ``_load`` rebuilds the hash index from stored records only, which would drop
        in-flight claims made by :meth:`claim_content_hash` before their record is
        written. Snapshot those claim-only entries and restore them after the reload.
        """
        claims = [
            (collection, content_hash, page_id)
            for collection, hashes in self._hash_index.items()
            for content_hash, page_id in hashes.items()
            if (self._store.get(collection, {}).get(page_id) or {}).get("content_hash") != content_hash
        ]
        self._load()
        for collection, content_hash, page_id in claims:
            self._hash_index.setdefault(collection, {}).setdefault(content_hash, page_id)

    def get(self, collection: str, page_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            # Re-read so a long-lived process sees records written by other processes
            # (os.replace makes writes atomic, so no lock is needed for the read).
            self._reload_preserving_claims()
            return self._store.get(collection, {}).get(page_id)

    def set(self, collection: str, page_id: str, record: Dict[str, Any]) -> None:
        with self._lock, self._file_lock():
            self._reload_preserving_claims()
            self._apply_set(collection, page_id, record)
            self._persist()

    def delete(self, collection: str, page_id: str) -> None:
        with self._lock, self._file_lock():
            self._reload_preserving_claims()
            if self._apply_delete(collection, page_id):
                self._persist()

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        with open(self._path, "r", encoding="utf-8") as fh:
            content = fh.read().strip()
        if not content:
            return
        data = json.loads(content)
        # Legacy format was flat ``{page_id: record}`` with no collection. The
        # collection cannot be recovered, so discard it — freshness is a cache and
        # pages are simply re-ingested once into the new per-collection layout.
        if isinstance(data, dict) and any(
            isinstance(v, dict) and ("content_hash" in v or "url" in v)
            for v in data.values()
        ):
            return
        self._store = data
        self._rebuild_hash_index()

    def _persist(self) -> None:
        # Unique temp file in the same directory (two writers never share a name),
        # then atomic os.replace so the file is always readable (#74).
        directory = os.path.dirname(self._path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".freshness-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._store, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
