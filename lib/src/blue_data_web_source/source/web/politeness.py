from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Callable, Dict, Iterator, Optional
from urllib.parse import urlparse


class DomainPoliteness:
    """Per-host politeness for concurrent fetching.

    Serializes requests to the same host (one at a time) and optionally enforces
    a minimum delay between them. Requests to *different* hosts are unaffected
    and proceed concurrently, so a thread pool still parallelizes across hosts.

    Used by both :class:`WebCrawler` and :meth:`WebPipeline.ingest_many` so batch
    ingest, feed ingest, and web search get the same per-domain politeness the
    crawler has always had.

    Args:
        delay: Minimum seconds between requests to the same host. ``0`` (the
            default) means serialize only — no artificial wait.
        delay_fn: Optional ``callable(url) -> float`` returning a per-URL delay,
            used when the delay is not a fixed constant (the crawler passes this
            to honour robots.txt ``Crawl-delay``). Takes precedence over ``delay``.
    """

    def __init__(self, delay: float = 0.0, delay_fn: Optional[Callable[[str], float]] = None) -> None:
        self._delay = delay
        self._delay_fn = delay_fn
        self._locks: Dict[str, threading.Lock] = {}
        self._locks_mutex = threading.Lock()
        self._last_fetch: Dict[str, float] = {}

    def _lock_for(self, domain: str) -> threading.Lock:
        with self._locks_mutex:
            lock = self._locks.get(domain)
            if lock is None:
                lock = self._locks[domain] = threading.Lock()
            return lock

    def _delay_for(self, url: str) -> float:
        if self._delay_fn is not None:
            return self._delay_fn(url)
        return self._delay

    @contextmanager
    def hold(self, url: str) -> Iterator[None]:
        """Hold *url*'s per-host lock for the duration of the ``with`` block.

        Waits out the configured delay (measured from the start of the previous
        same-host request) before yielding. Different hosts use different locks,
        so they never block each other.
        """
        domain = urlparse(url).netloc
        with self._lock_for(domain):
            delay = self._delay_for(url)
            if delay > 0:
                elapsed = time.monotonic() - self._last_fetch.get(domain, 0.0)
                if elapsed < delay:
                    time.sleep(delay - elapsed)
            self._last_fetch[domain] = time.monotonic()
            yield
