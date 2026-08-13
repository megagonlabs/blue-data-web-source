from __future__ import annotations

import time
from collections import deque
from html.parser import HTMLParser
from typing import Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from .fetcher import FetchResult, WebFetcher, _DEFAULT_USER_AGENT
from .pipeline import WebPipeline


class _LinkExtractor(HTMLParser):
    """Extracts href values from <a> tags."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._base_url = base_url
        self.links: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "a":
            for attr, val in attrs:
                if attr == "href" and val:
                    self.links.append(urljoin(self._base_url, val))


def _normalise(url: str) -> str:
    """Strip fragment so https://x.com/page#section and /page map to the same key."""
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl()


def _same_domain(url: str, seed_netloc: str) -> bool:
    return urlparse(url).netloc == seed_netloc


def _is_http(url: str) -> bool:
    return urlparse(url).scheme in ("http", "https")


class WebCrawler:
    """BFS crawler that ingests pages reachable from a seed URL.

    Args:
        pipeline: WebPipeline used to ingest each page.
        fetcher: WebFetcher reused for link-discovery fetches.
        same_domain_only: When True (default) only follow links within the
            seed URL's domain.
        respect_robots: When True (default) skip URLs disallowed by the
            site's robots.txt. Caches parsed robots.txt per domain for the
            duration of the crawl. Falls back to allow-all when robots.txt
            is unreachable.
        crawl_delay: Minimum seconds between requests to the same domain.
            The Crawl-delay directive from robots.txt is honoured when it
            exceeds this value.
        max_retries: How many times to retry transient failures (5xx, timeout,
            transport errors) before skipping the page. Uses exponential
            backoff (1s, 2s, …).
    """

    def __init__(
        self,
        pipeline: WebPipeline,
        fetcher: WebFetcher,
        same_domain_only: bool = True,
        respect_robots: bool = True,
        crawl_delay: float = 1.0,
        max_retries: int = 2,
    ) -> None:
        self._pipeline = pipeline
        self._fetcher = fetcher
        self._same_domain_only = same_domain_only
        self._respect_robots = respect_robots
        self._crawl_delay = crawl_delay
        self._max_retries = max_retries
        self._robots_cache: Dict[str, Optional[RobotFileParser]] = {}
        self._last_fetch_time: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # robots.txt helpers
    # ------------------------------------------------------------------

    def _get_robots(self, url: str) -> Optional[RobotFileParser]:
        parsed = urlparse(url)
        domain = parsed.netloc
        if domain not in self._robots_cache:
            robots_url = f"{parsed.scheme}://{domain}/robots.txt"
            rp = RobotFileParser(robots_url)
            try:
                rp.read()
                self._robots_cache[domain] = rp
            except Exception:
                self._robots_cache[domain] = None
        return self._robots_cache[domain]

    def _is_allowed(self, url: str) -> bool:
        if not self._respect_robots:
            return True
        rp = self._get_robots(url)
        return rp is None or rp.can_fetch(_DEFAULT_USER_AGENT, url)

    def _effective_delay(self, url: str) -> float:
        """Crawl delay for this URL's domain, honouring robots.txt Crawl-delay."""
        if not self._respect_robots:
            return self._crawl_delay
        rp = self._get_robots(url)
        if rp is not None:
            robots_delay = rp.crawl_delay(_DEFAULT_USER_AGENT)
            if robots_delay is not None:
                return max(self._crawl_delay, float(robots_delay))
        return self._crawl_delay

    # ------------------------------------------------------------------
    # Politeness
    # ------------------------------------------------------------------

    def _wait_for_domain(self, url: str) -> None:
        domain = urlparse(url).netloc
        delay = self._effective_delay(url)
        elapsed = time.monotonic() - self._last_fetch_time.get(domain, 0.0)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_fetch_time[domain] = time.monotonic()

    # ------------------------------------------------------------------
    # Fetch with retry
    # ------------------------------------------------------------------

    def _fetch_with_retry(self, url: str) -> Optional[FetchResult]:
        for attempt in range(self._max_retries + 1):
            try:
                return self._fetcher.fetch(url)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500 or attempt == self._max_retries:
                    return None
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == self._max_retries:
                    return None
            except Exception:
                return None
            time.sleep(2 ** attempt)
        return None

    # ------------------------------------------------------------------
    # Crawl
    # ------------------------------------------------------------------

    def crawl(
        self,
        seed_url: str,
        depth: int = 1,
        max_pages: int = 10,
        collection: str | None = None,
    ) -> List[dict]:
        """Crawl from *seed_url* and return a flat list of all ingested chunks.

        Args:
            seed_url: Starting URL.
            depth: How many link-hops to follow (0 = seed only, 1 = seed + its links, …).
            max_pages: Hard cap on the total number of pages fetched (robots-filtered
                pages do not count toward this limit).
            collection: Vector store collection to upsert into.
        """
        seed_netloc = urlparse(seed_url).netloc
        seen: Set[str] = set()       # all URLs processed (prevents re-queuing)
        fetch_count = 0              # pages actually attempted (counts toward max_pages)
        queue: deque[tuple[str, int]] = deque([(seed_url, 0)])
        all_chunks: List[dict] = []

        while queue and fetch_count < max_pages:
            url, current_depth = queue.popleft()
            url = _normalise(url)

            if url in seen:
                continue
            seen.add(url)

            if not _is_http(url):
                continue
            if self._same_domain_only and not _same_domain(url, seed_netloc):
                continue
            if not self._is_allowed(url):
                continue

            fetch_count += 1
            self._wait_for_domain(url)

            fetch_result = self._fetch_with_retry(url)
            if fetch_result is None:
                continue

            chunks = self._pipeline.ingest(url, collection=collection, _fetch_result=fetch_result)
            all_chunks.extend(chunks)

            if current_depth < depth:
                extractor = _LinkExtractor(fetch_result.final_url)
                extractor.feed(fetch_result.html)
                for link in extractor.links:
                    normalised = _normalise(link)
                    if normalised not in seen:
                        queue.append((normalised, current_depth + 1))

        return all_chunks
