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


def _in_scope(url: str, netlocs: Set[str]) -> bool:
    """True if *url*'s host is one of the in-scope *netlocs* — the seed host and the
    host it resolves to after redirects (#52)."""
    return urlparse(url).netloc in netlocs


def _is_http(url: str) -> bool:
    return urlparse(url).scheme in ("http", "https")


class WebCrawler:
    """BFS crawler that ingests pages reachable from a seed URL.

    Args:
        pipeline: WebPipeline used to ingest each page.
        fetcher: WebFetcher reused for link-discovery fetches.
        same_domain_only: When True (default) only follow links within the
            seed URL's domain.
        respect_robots: When True (default) skip discovered links disallowed by
            the site's robots.txt. robots.txt is fetched through the same
            ``WebFetcher`` as pages, so it uses the crawler's User-Agent and
            configured timeout; it is cached per domain for the crawl. Falls back
            to allow-all when robots.txt is unreachable, times out, or returns a
            non-2xx status (e.g. a CDN 403, or a 404 meaning "no robots"). The seed
            URL is always crawled regardless of robots.txt (the caller asked for it
            explicitly; see design/06); only discovered links are filtered.
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
        rp: Optional[RobotFileParser] = RobotFileParser()
        rp.set_url(robots_url)
        try:
            # Fetch robots.txt through WebFetcher so it uses the crawler's own
            # User-Agent and configured timeout — not urllib's generic agent with no
            # timeout (#48). A never-answering robots.txt now times out instead of
            # hanging the crawl, and a CDN that 403s a generic agent no longer blocks
            # the whole site.
            result = self._fetcher.fetch(robots_url)
            rp.parse(result.html.splitlines())
        except Exception:
            # Unreachable, timed out, or any non-2xx (a 404 "no robots", or a CDN
            # 401/403): fall back to allow-all, as the crawler contract documents.
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
    ) -> Tuple[List[dict], List[str], Optional[str]]:
        """Fetch one URL, ingest it, and optionally extract outbound links.

        Returns ``(chunks, outbound_links, final_url)``. ``final_url`` is the
        post-redirect URL (``None`` when the fetch failed) — the crawl scopes
        same-domain filtering to the host the seed actually resolves to (#52).
        """
        fetch_result = self._fetch_page(url)
        if fetch_result is None:
            return [], [], None
        chunks = self._pipeline.ingest(url, collection=collection, _fetch_result=fetch_result)
        links: List[str] = []
        if extract_links:
            extractor = _LinkExtractor(fetch_result.final_url)
            extractor.feed(fetch_result.html)
            links = extractor.links
        return chunks, links, fetch_result.final_url

    def crawl(
        self,
        seed_url: str,
        depth: int = 1,
        max_pages: int = 10,
        collection: str | None = None,
        max_attempts: int | None = None,
    ) -> List[dict]:
        """Crawl from *seed_url* and return a flat list of all ingested chunks.

        Pages at each depth level are processed concurrently up to ``max_workers``
        threads. Per-domain locking ensures the configured crawl delay is respected
        regardless of concurrency.

        Args:
            seed_url: Starting URL.
            depth: How many link-hops to follow (0 = seed only, 1 = seed + its links, …).
            max_pages: Maximum number of pages to successfully **import**. Only pages
                that yield chunks count toward it — a fetch that 404s, times out, or
                extracts no content does not consume the budget, so a few dead links
                no longer starve the crawl (#52).
            collection: Vector store collection to upsert into.
            max_attempts: Hard cap on fetch attempts, bounding how many dead links the
                crawl will chase while trying to reach ``max_pages`` imports. Defaults
                to ``max_pages * 10``.

        Same-domain scope follows the host the seed actually *resolves* to: if the
        seed redirects (apex → www, http → https, or an alias host), links on the
        resolved host stay in scope. The original seed host is kept in scope too (#52).
        """
        if max_attempts is None:
            max_attempts = max_pages * 10

        # In-scope hosts: the seed host, plus whatever host the seed resolves to after
        # redirects (added once the seed has been fetched).
        allowed_netlocs: Set[str] = {urlparse(seed_url).netloc}
        seen: Set[str] = {_normalise(seed_url)}
        ingested = 0
        attempts = 0
        all_chunks: List[dict] = []

        current_level = [seed_url]

        for current_depth in range(depth + 1):
            if not current_level or ingested >= max_pages or attempts >= max_attempts:
                break

            # The seed (depth 0) is exempt from the robots allow-check: the caller
            # asked for it explicitly and design/06 documents it as always ingested.
            # Discovered links (depth >= 1) still respect robots.txt (#48).
            is_seed_level = current_depth == 0
            eligible: List[str] = []
            for url in current_level:
                if not _is_http(url):
                    continue
                if self._same_domain_only and not _in_scope(url, allowed_netlocs):
                    continue
                if not is_seed_level and not self._is_allowed(url):
                    continue
                eligible.append(url)

            if not eligible:
                break

            extract_links = current_depth < depth
            next_level_links: List[str] = []
            queue = deque(eligible)

            # Drain this level in budget-sized batches. Only pages that actually
            # import (produce chunks) count toward max_pages, so failed fetches are
            # skipped over rather than consuming page slots (#52); total fetches stay
            # bounded by max_attempts so endless dead links can't hang the crawl.
            while queue and ingested < max_pages and attempts < max_attempts:
                batch_size = min(max_pages - ingested, max_attempts - attempts, len(queue))
                batch = [queue.popleft() for _ in range(batch_size)]
                attempts += len(batch)

                workers = min(self._max_workers, len(batch))
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(self._process_url, url, collection, extract_links): url
                        for url in batch
                    }
                    for future in as_completed(futures):
                        try:
                            chunks, links, final_url = future.result()
                        except Exception:
                            continue
                        if chunks:
                            ingested += 1
                            all_chunks.extend(chunks)
                        next_level_links.extend(links)
                        # Once the seed is fetched, add the host it resolved to so its
                        # (canonical-host) links are kept in scope at the next depth.
                        if is_seed_level and final_url:
                            allowed_netlocs.add(urlparse(final_url).netloc)

            # Build the next level from discovered links, preserving BFS order.
            current_level = []
            for link in next_level_links:
                normalised = _normalise(link)
                if normalised not in seen:
                    seen.add(normalised)
                    current_level.append(normalised)

        return all_chunks
