from __future__ import annotations

import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from .fetcher import FetchResult, WebFetcher, _DEFAULT_USER_AGENT
from .pipeline import WebPipeline
from .politeness import DomainPoliteness


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
        max_workers: Thread pool size for concurrent page fetches within a
            crawl level. Defaults to 1 (serial). Per-domain locking ensures
            politeness regardless of worker count.
    """

    def __init__(
        self,
        pipeline: WebPipeline,
        fetcher: WebFetcher,
        same_domain_only: bool = True,
        respect_robots: bool = True,
        crawl_delay: float = 1.0,
        max_retries: int = 2,
        max_workers: int = 1,
    ) -> None:
        self._pipeline = pipeline
        self._fetcher = fetcher
        self._same_domain_only = same_domain_only
        self._respect_robots = respect_robots
        self._crawl_delay = crawl_delay
        self._max_retries = max_retries
        self._max_workers = max_workers
        self._robots_cache: Dict[str, Optional[RobotFileParser]] = {}
        self._robots_cache_lock = threading.Lock()
        # Per-domain politeness: serialize same-host fetches and honour the
        # (robots-aware) crawl delay. Shared with WebPipeline.ingest_many.
        self._politeness = DomainPoliteness(delay=crawl_delay, delay_fn=self._effective_delay)

    # ------------------------------------------------------------------
    # robots.txt helpers
    # ------------------------------------------------------------------

    def _get_robots(self, url: str) -> Optional[RobotFileParser]:
        parsed = urlparse(url)
        domain = parsed.netloc
        with self._robots_cache_lock:
            if domain in self._robots_cache:
                return self._robots_cache[domain]
        robots_url = f"{parsed.scheme}://{domain}/robots.txt"
        rp = RobotFileParser(robots_url)
        try:
            rp.read()
        except Exception:
            rp = None
        with self._robots_cache_lock:
            if domain not in self._robots_cache:
                self._robots_cache[domain] = rp
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

    def _fetch_page(self, url: str) -> Optional[FetchResult]:
        """Fetch a page while holding the per-domain politeness lock."""
        with self._politeness.hold(url):
            return self._fetch_with_retry(url)

    # ------------------------------------------------------------------
    # Crawl
    # ------------------------------------------------------------------

    def _process_url(
        self,
        url: str,
        collection: Optional[str],
        extract_links: bool,
    ) -> Tuple[List[dict], List[str]]:
        """Fetch one URL, ingest it, and optionally extract outbound links."""
        fetch_result = self._fetch_page(url)
        if fetch_result is None:
            return [], []
        chunks = self._pipeline.ingest(url, collection=collection, _fetch_result=fetch_result)
        links: List[str] = []
        if extract_links:
            extractor = _LinkExtractor(fetch_result.final_url)
            extractor.feed(fetch_result.html)
            links = extractor.links
        return chunks, links

    def crawl(
        self,
        seed_url: str,
        depth: int = 1,
        max_pages: int = 10,
        collection: str | None = None,
    ) -> List[dict]:
        """Crawl from *seed_url* and return a flat list of all ingested chunks.

        Pages at each depth level are processed concurrently up to ``max_workers``
        threads. Per-domain locking ensures the configured crawl delay is respected
        regardless of concurrency.

        Args:
            seed_url: Starting URL.
            depth: How many link-hops to follow (0 = seed only, 1 = seed + its links, …).
            max_pages: Hard cap on the total number of pages fetched (robots-filtered
                pages do not count toward this limit).
            collection: Vector store collection to upsert into.
        """
        seed_netloc = urlparse(seed_url).netloc
        seen: Set[str] = {_normalise(seed_url)}
        fetch_count = 0
        all_chunks: List[dict] = []

        current_level = [seed_url]

        for current_depth in range(depth + 1):
            if not current_level or fetch_count >= max_pages:
                break

            # Filter to eligible URLs for this level, respecting max_pages cap
            to_process: List[str] = []
            for url in current_level:
                if fetch_count + len(to_process) >= max_pages:
                    break
                if not _is_http(url):
                    continue
                if self._same_domain_only and not _same_domain(url, seed_netloc):
                    continue
                if not self._is_allowed(url):
                    continue
                to_process.append(url)

            if not to_process:
                break

            fetch_count += len(to_process)
            extract_links = current_depth < depth
            next_level_links: List[str] = []

            workers = min(self._max_workers, len(to_process))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(self._process_url, url, collection, extract_links): url
                    for url in to_process
                }
                for future in as_completed(futures):
                    try:
                        chunks, links = future.result()
                        all_chunks.extend(chunks)
                        next_level_links.extend(links)
                    except Exception:
                        pass

            # Build the next level from discovered links, preserving BFS order
            current_level = []
            for link in next_level_links:
                normalised = _normalise(link)
                if normalised not in seen:
                    seen.add(normalised)
                    current_level.append(normalised)

        return all_chunks
