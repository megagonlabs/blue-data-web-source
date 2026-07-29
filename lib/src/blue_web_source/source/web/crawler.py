from __future__ import annotations

from collections import deque
from html.parser import HTMLParser
from typing import List, Set
from urllib.parse import urljoin, urlparse

from .fetcher import FetchResult, WebFetcher
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
    """

    def __init__(
        self,
        pipeline: WebPipeline,
        fetcher: WebFetcher,
        same_domain_only: bool = True,
    ) -> None:
        self._pipeline = pipeline
        self._fetcher = fetcher
        self._same_domain_only = same_domain_only

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
            max_pages: Hard cap on the total number of pages fetched.
            collection: Vector store collection to upsert into.
        """
        seed_netloc = urlparse(seed_url).netloc
        visited: Set[str] = set()
        # queue entries: (url, current_depth)
        queue: deque[tuple[str, int]] = deque([(seed_url, 0)])
        all_chunks: List[dict] = []

        while queue and len(visited) < max_pages:
            url, current_depth = queue.popleft()
            url = _normalise(url)

            if url in visited:
                continue
            if not _is_http(url):
                continue
            if self._same_domain_only and not _same_domain(url, seed_netloc):
                continue

            visited.add(url)

            try:
                fetch_result: FetchResult = self._fetcher.fetch(url)
            except Exception:
                continue

            chunks = self._pipeline.ingest(url, collection=collection, _fetch_result=fetch_result)
            all_chunks.extend(chunks)

            if current_depth < depth:
                extractor = _LinkExtractor(fetch_result.final_url)
                extractor.feed(fetch_result.html)
                for link in extractor.links:
                    normalised = _normalise(link)
                    if normalised not in visited:
                        queue.append((normalised, current_depth + 1))

        return all_chunks
