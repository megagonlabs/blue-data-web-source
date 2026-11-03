"""Unit tests for WebCrawler."""
from unittest.mock import MagicMock, patch

import httpx
import pytest

from blue_data_web_source.source.web.crawler import WebCrawler
from blue_data_web_source.source.web.fetcher import FetchResult

_BASE = "https://example.com"

_INDEX_HTML = """<!DOCTYPE html><html><head><title>Home</title></head>
<body>
  <article><p>Welcome to the home page with useful content here.</p></article>
  <a href="/about">About</a>
  <a href="/contact">Contact</a>
  <a href="https://external.com/page">External</a>
</body></html>"""

_ABOUT_HTML = """<!DOCTYPE html><html><head><title>About</title></head>
<body><article><p>About page with content about our company and mission.</p></article></body>
</html>"""

_CONTACT_HTML = """<!DOCTYPE html><html><head><title>Contact</title></head>
<body><article><p>Contact us at info@example.com for any questions.</p></article></body>
</html>"""

_EXTERNAL_HTML = """<!DOCTYPE html><html><head><title>External</title></head>
<body><article><p>External site content that should not be crawled by default.</p></article></body>
</html>"""


def _make_fetcher(url_map: dict):
    mock = MagicMock()

    def _fetch(url):
        html = url_map.get(url)
        if html is None:
            raise ValueError(f"No fixture for {url}")
        return FetchResult(url=url, final_url=url, html=html,
                           status_code=200, content_type="text/html")

    mock.fetch.side_effect = _fetch
    return mock


def _make_pipeline(chunks_per_page: int = 1):
    mock = MagicMock()
    mock.ingest.side_effect = lambda url, collection=None, _fetch_result=None: [
        {"url": url, "chunk_id": f"{url}_chunk_0", "text": "text"}
        for _ in range(chunks_per_page)
    ]
    return mock


def _crawler(pipeline, fetcher, **kwargs) -> WebCrawler:
    """Helper: create a WebCrawler with robots and delay disabled by default."""
    kwargs.setdefault("respect_robots", False)
    kwargs.setdefault("crawl_delay", 0)
    kwargs.setdefault("max_retries", 0)
    return WebCrawler(pipeline=pipeline, fetcher=fetcher, **kwargs)


# ---------------------------------------------------------------------------
# Existing crawl behaviour
# ---------------------------------------------------------------------------

def test_crawl_depth_0_only_fetches_seed():
    fetcher = _make_fetcher({_BASE + "/": _INDEX_HTML})
    pipeline = _make_pipeline()
    crawler = _crawler(pipeline, fetcher)

    crawler.crawl(_BASE + "/", depth=0, max_pages=10)

    assert pipeline.ingest.call_count == 1


def test_crawl_depth_1_follows_same_domain_links():
    url_map = {
        _BASE + "/": _INDEX_HTML,
        _BASE + "/about": _ABOUT_HTML,
        _BASE + "/contact": _CONTACT_HTML,
    }
    fetcher = _make_fetcher(url_map)
    pipeline = _make_pipeline()
    crawler = _crawler(pipeline, fetcher)

    crawler.crawl(_BASE + "/", depth=1, max_pages=10)

    crawled = {call.args[0] for call in pipeline.ingest.call_args_list}
    assert _BASE + "/" in crawled
    assert _BASE + "/about" in crawled
    assert _BASE + "/contact" in crawled


def test_crawl_does_not_follow_external_links_by_default():
    url_map = {
        _BASE + "/": _INDEX_HTML,
        _BASE + "/about": _ABOUT_HTML,
        _BASE + "/contact": _CONTACT_HTML,
        "https://external.com/page": _EXTERNAL_HTML,
    }
    fetcher = _make_fetcher(url_map)
    pipeline = _make_pipeline()
    crawler = _crawler(pipeline, fetcher)

    crawler.crawl(_BASE + "/", depth=1, max_pages=10)

    crawled = {call.args[0] for call in pipeline.ingest.call_args_list}
    assert "https://external.com/page" not in crawled


def test_crawl_follows_external_links_when_same_domain_false():
    url_map = {
        _BASE + "/": _INDEX_HTML,
        _BASE + "/about": _ABOUT_HTML,
        _BASE + "/contact": _CONTACT_HTML,
        "https://external.com/page": _EXTERNAL_HTML,
    }
    fetcher = _make_fetcher(url_map)
    pipeline = _make_pipeline()
    crawler = _crawler(pipeline, fetcher, same_domain_only=False)

    crawler.crawl(_BASE + "/", depth=1, max_pages=10)

    crawled = {call.args[0] for call in pipeline.ingest.call_args_list}
    assert "https://external.com/page" in crawled


def test_crawl_respects_max_pages():
    url_map = {
        _BASE + "/": _INDEX_HTML,
        _BASE + "/about": _ABOUT_HTML,
        _BASE + "/contact": _CONTACT_HTML,
    }
    fetcher = _make_fetcher(url_map)
    pipeline = _make_pipeline()
    crawler = _crawler(pipeline, fetcher)

    crawler.crawl(_BASE + "/", depth=1, max_pages=2)

    assert pipeline.ingest.call_count <= 2


def test_crawl_does_not_revisit_same_url():
    index_html = """<html><body><article><p>Home content here.</p></article>
    <a href="/about">About 1</a><a href="/about">About 2</a></body></html>"""
    url_map = {
        _BASE + "/": index_html,
        _BASE + "/about": _ABOUT_HTML,
    }
    fetcher = _make_fetcher(url_map)
    pipeline = _make_pipeline()
    crawler = _crawler(pipeline, fetcher)

    crawler.crawl(_BASE + "/", depth=1, max_pages=10)

    crawled = [call.args[0] for call in pipeline.ingest.call_args_list]
    assert crawled.count(_BASE + "/about") == 1


def test_crawl_skips_failed_pages_and_continues():
    url_map = {
        _BASE + "/": _INDEX_HTML,
        _BASE + "/contact": _CONTACT_HTML,
        # /about is intentionally missing — fetcher will raise
    }
    fetcher = _make_fetcher(url_map)
    pipeline = _make_pipeline()
    crawler = _crawler(pipeline, fetcher)

    result = crawler.crawl(_BASE + "/", depth=1, max_pages=10)

    crawled = {call.args[0] for call in pipeline.ingest.call_args_list}
    assert _BASE + "/" in crawled
    assert _BASE + "/contact" in crawled


def test_crawl_returns_flat_chunk_list():
    url_map = {
        _BASE + "/": _INDEX_HTML,
        _BASE + "/about": _ABOUT_HTML,
    }
    fetcher = _make_fetcher(url_map)
    pipeline = _make_pipeline(chunks_per_page=2)
    crawler = _crawler(pipeline, fetcher)

    result = crawler.crawl(_BASE + "/", depth=1, max_pages=10)

    assert all("chunk_id" in r for r in result)


def test_crawl_strips_fragments_from_urls():
    index_html = """<html><body><article><p>Home content.</p></article>
    <a href="/about#section1">About section 1</a>
    <a href="/about#section2">About section 2</a></body></html>"""
    url_map = {
        _BASE + "/": index_html,
        _BASE + "/about": _ABOUT_HTML,
    }
    fetcher = _make_fetcher(url_map)
    pipeline = _make_pipeline()
    crawler = _crawler(pipeline, fetcher)

    crawler.crawl(_BASE + "/", depth=1, max_pages=10)

    crawled = [call.args[0] for call in pipeline.ingest.call_args_list]
    assert crawled.count(_BASE + "/about") == 1


# ---------------------------------------------------------------------------
# robots.txt compliance
# ---------------------------------------------------------------------------

_ROBOTS = _BASE + "/robots.txt"


def _fetched_urls(fetcher):
    return [c.args[0] for c in fetcher.fetch.call_args_list if c.args]


def _ingested_urls(pipeline):
    return {c.args[0] for c in pipeline.ingest.call_args_list if c.args}


def test_crawl_fetches_robots_through_webfetcher():
    # robots.txt must be fetched via WebFetcher (crawler User-Agent + timeout),
    # not urllib with a generic agent and no timeout (#48).
    fetcher = _make_fetcher({_BASE + "/": _INDEX_HTML, _ROBOTS: "User-agent: *\nAllow: /\n"})
    crawler = WebCrawler(pipeline=_make_pipeline(), fetcher=fetcher,
                         respect_robots=True, crawl_delay=0, max_retries=0)
    crawler.crawl(_BASE + "/", depth=0, max_pages=10)
    assert _ROBOTS in _fetched_urls(fetcher)


def test_crawl_skips_discovered_link_disallowed_by_robots():
    fetcher = _make_fetcher({
        _BASE + "/": _INDEX_HTML,
        _BASE + "/about": _ABOUT_HTML,
        _BASE + "/contact": _CONTACT_HTML,
        _ROBOTS: "User-agent: *\nDisallow: /about\n",
    })
    pipeline = _make_pipeline()
    crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                         respect_robots=True, crawl_delay=0, max_retries=0)
    crawler.crawl(_BASE + "/", depth=1, max_pages=10)

    ingested = _ingested_urls(pipeline)
    assert _BASE + "/about" not in ingested   # disallowed discovered link
    assert _BASE + "/contact" in ingested       # allowed discovered link


def test_crawl_allows_permitted_discovered_links():
    fetcher = _make_fetcher({
        _BASE + "/": _INDEX_HTML,
        _BASE + "/about": _ABOUT_HTML,
        _BASE + "/contact": _CONTACT_HTML,
        _ROBOTS: "User-agent: *\nAllow: /\n",
    })
    pipeline = _make_pipeline()
    crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                         respect_robots=True, crawl_delay=0, max_retries=0)
    crawler.crawl(_BASE + "/", depth=1, max_pages=10)

    assert {_BASE + "/", _BASE + "/about", _BASE + "/contact"} <= _ingested_urls(pipeline)


def test_crawl_seed_ingested_even_when_robots_disallows_all():
    # design/06: the seed is always ingested regardless of robots.txt (#48).
    fetcher = _make_fetcher({_BASE + "/": _INDEX_HTML, _ROBOTS: "User-agent: *\nDisallow: /\n"})
    pipeline = _make_pipeline()
    crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                         respect_robots=True, crawl_delay=0, max_retries=0)
    crawler.crawl(_BASE + "/", depth=0, max_pages=10)
    assert pipeline.ingest.call_count == 1


def test_crawl_allows_all_when_robots_unreachable():
    # robots.txt not served → fetch raises → allow-all, and the crawl still finishes.
    fetcher = _make_fetcher({
        _BASE + "/": _INDEX_HTML,
        _BASE + "/about": _ABOUT_HTML,
        _BASE + "/contact": _CONTACT_HTML,
    })
    pipeline = _make_pipeline()
    crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                         respect_robots=True, crawl_delay=0, max_retries=0)
    crawler.crawl(_BASE + "/", depth=1, max_pages=10)
    assert _BASE + "/about" in _ingested_urls(pipeline)  # allowed despite missing robots


def test_crawl_caches_robots_per_domain():
    fetcher = _make_fetcher({
        _BASE + "/": _INDEX_HTML,
        _BASE + "/about": _ABOUT_HTML,
        _BASE + "/contact": _CONTACT_HTML,
        _ROBOTS: "User-agent: *\nAllow: /\n",
    })
    crawler = WebCrawler(pipeline=_make_pipeline(), fetcher=fetcher,
                         respect_robots=True, crawl_delay=0, max_retries=0)
    crawler.crawl(_BASE + "/", depth=1, max_pages=10)

    # robots.txt is fetched once per domain, not once per page.
    assert _fetched_urls(fetcher).count(_ROBOTS) == 1


def test_crawl_respect_robots_false_skips_robots_fetch():
    fetcher = _make_fetcher({_BASE + "/": _INDEX_HTML})
    pipeline = _make_pipeline()
    crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                         respect_robots=False, crawl_delay=0, max_retries=0)
    crawler.crawl(_BASE + "/", depth=0, max_pages=10)

    assert _ROBOTS not in _fetched_urls(fetcher)
    assert pipeline.ingest.call_count == 1


def test_crawl_honours_robots_crawl_delay():
    fetcher = _make_fetcher({_BASE + "/": _INDEX_HTML, _ROBOTS: "User-agent: *\nCrawl-delay: 5\n"})
    pipeline = _make_pipeline()

    sleep_calls = []
    with patch("blue_data_web_source.source.web.crawler.time.sleep", side_effect=sleep_calls.append), \
         patch("blue_data_web_source.source.web.crawler.time.monotonic", return_value=0.0):
        # robots.txt says Crawl-delay: 5; our default is 1.0 — should use 5.
        crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                             respect_robots=True, crawl_delay=1.0, max_retries=0)
        crawler.crawl(_BASE + "/", depth=0, max_pages=10)

    assert any(s >= 5.0 for s in sleep_calls)


# ---------------------------------------------------------------------------
# Crawl delay / politeness
# ---------------------------------------------------------------------------

def test_crawl_delay_applied_between_same_domain_requests():
    url_map = {
        _BASE + "/": _INDEX_HTML,
        _BASE + "/about": _ABOUT_HTML,
    }
    fetcher = _make_fetcher(url_map)
    pipeline = _make_pipeline()

    sleep_calls = []
    with patch("blue_data_web_source.source.web.crawler.time.sleep", side_effect=sleep_calls.append), \
         patch("blue_data_web_source.source.web.crawler.time.monotonic", return_value=0.0):
        crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                             respect_robots=False, crawl_delay=2.0, max_retries=0)
        crawler.crawl(_BASE + "/", depth=1, max_pages=10)

    # Second request to same domain should trigger a sleep
    assert len(sleep_calls) > 0
    assert all(s <= 2.0 for s in sleep_calls)


def test_no_delay_when_crawl_delay_zero():
    url_map = {
        _BASE + "/": _INDEX_HTML,
        _BASE + "/about": _ABOUT_HTML,
    }
    fetcher = _make_fetcher(url_map)
    pipeline = _make_pipeline()

    sleep_calls = []
    with patch("blue_data_web_source.source.web.crawler.time.sleep", side_effect=sleep_calls.append):
        crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                             respect_robots=False, crawl_delay=0, max_retries=0)
        crawler.crawl(_BASE + "/", depth=1, max_pages=10)

    assert len(sleep_calls) == 0


# ---------------------------------------------------------------------------
# Retry with backoff
# ---------------------------------------------------------------------------

def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    response = MagicMock()
    response.status_code = status_code
    return httpx.HTTPStatusError("error", request=MagicMock(), response=response)


def test_crawl_retries_on_5xx_and_succeeds():
    call_count = [0]

    def _fetch(url):
        call_count[0] += 1
        if call_count[0] == 1:
            raise _http_status_error(503)
        return FetchResult(url=url, final_url=url, html=_INDEX_HTML,
                           status_code=200, content_type="text/html")

    fetcher = MagicMock()
    fetcher.fetch.side_effect = _fetch
    pipeline = _make_pipeline()

    with patch("blue_data_web_source.source.web.crawler.time.sleep"):
        crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                             respect_robots=False, crawl_delay=0, max_retries=2)
        crawler.crawl(_BASE + "/", depth=0, max_pages=10)

    assert pipeline.ingest.call_count == 1
    assert call_count[0] == 2


def test_crawl_gives_up_after_max_retries():
    fetcher = MagicMock()
    fetcher.fetch.side_effect = _http_status_error(503)
    pipeline = _make_pipeline()

    with patch("blue_data_web_source.source.web.crawler.time.sleep"):
        crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                             respect_robots=False, crawl_delay=0, max_retries=2)
        crawler.crawl(_BASE + "/", depth=0, max_pages=10)

    assert pipeline.ingest.call_count == 0
    assert fetcher.fetch.call_count == 3  # 1 original + 2 retries


def test_crawl_does_not_retry_4xx():
    fetcher = MagicMock()
    fetcher.fetch.side_effect = _http_status_error(404)
    pipeline = _make_pipeline()

    with patch("blue_data_web_source.source.web.crawler.time.sleep") as mock_sleep:
        crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                             respect_robots=False, crawl_delay=0, max_retries=2)
        crawler.crawl(_BASE + "/", depth=0, max_pages=10)

    assert fetcher.fetch.call_count == 1  # no retry
    mock_sleep.assert_not_called()


def test_crawl_retries_on_timeout():
    call_count = [0]

    def _fetch(url):
        call_count[0] += 1
        if call_count[0] < 3:
            raise httpx.TimeoutException("timed out")
        return FetchResult(url=url, final_url=url, html=_INDEX_HTML,
                           status_code=200, content_type="text/html")

    fetcher = MagicMock()
    fetcher.fetch.side_effect = _fetch
    pipeline = _make_pipeline()

    with patch("blue_data_web_source.source.web.crawler.time.sleep"):
        crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                             respect_robots=False, crawl_delay=0, max_retries=2)
        crawler.crawl(_BASE + "/", depth=0, max_pages=10)

    assert pipeline.ingest.call_count == 1
    assert call_count[0] == 3


def test_crawl_retry_uses_exponential_backoff():
    fetcher = MagicMock()
    fetcher.fetch.side_effect = _http_status_error(502)
    pipeline = _make_pipeline()

    sleep_calls = []
    with patch("blue_data_web_source.source.web.crawler.time.sleep", side_effect=sleep_calls.append):
        crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                             respect_robots=False, crawl_delay=0, max_retries=3)
        crawler.crawl(_BASE + "/", depth=0, max_pages=10)

    # Backoff should be 1s, 2s, 4s (2^0, 2^1, 2^2)
    assert sleep_calls == [1, 2, 4]


# ---------------------------------------------------------------------------
# Concurrent crawl (max_workers)
# ---------------------------------------------------------------------------

def test_crawler_accepts_max_workers_param():
    fetcher = _make_fetcher({_BASE + "/": _INDEX_HTML})
    pipeline = _make_pipeline()
    crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                         respect_robots=False, crawl_delay=0, max_retries=0,
                         max_workers=4)
    assert crawler._max_workers == 4
    crawler.crawl(_BASE + "/", depth=0, max_pages=10)
    assert pipeline.ingest.call_count == 1


def test_crawler_multi_worker_ingests_all_pages():
    url_map = {
        _BASE + "/": _INDEX_HTML,
        _BASE + "/about": _ABOUT_HTML,
        _BASE + "/contact": _CONTACT_HTML,
    }
    fetcher = _make_fetcher(url_map)
    pipeline = _make_pipeline()
    crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                         respect_robots=False, crawl_delay=0, max_retries=0,
                         max_workers=3)
    crawler.crawl(_BASE + "/", depth=1, max_pages=10)

    crawled = {call.args[0] for call in pipeline.ingest.call_args_list}
    assert _BASE + "/" in crawled
    assert _BASE + "/about" in crawled
    assert _BASE + "/contact" in crawled


def test_crawler_multi_worker_respects_max_pages():
    url_map = {
        _BASE + "/": _INDEX_HTML,
        _BASE + "/about": _ABOUT_HTML,
        _BASE + "/contact": _CONTACT_HTML,
    }
    fetcher = _make_fetcher(url_map)
    pipeline = _make_pipeline()
    crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                         respect_robots=False, crawl_delay=0, max_retries=0,
                         max_workers=3)
    crawler.crawl(_BASE + "/", depth=1, max_pages=2)

    assert pipeline.ingest.call_count <= 2


# ---------------------------------------------------------------------------
# #52: seed-redirect scope and max_pages counting
# ---------------------------------------------------------------------------

def _redirect_fetcher(responses: dict):
    """Fetcher whose responses may redirect: url -> (final_url, html)."""
    mock = MagicMock()

    def _fetch(url):
        if url not in responses:
            raise ValueError(f"No fixture for {url}")
        final_url, html = responses[url]
        return FetchResult(url=url, final_url=final_url, html=html,
                           status_code=200, content_type="text/html")

    mock.fetch.side_effect = _fetch
    return mock


def test_crawl_scope_follows_seed_redirect_to_canonical_host():
    # Seed is on an alias host that 30x-redirects to the canonical host; links on
    # the canonical page must stay in scope with same_domain_only=True (#52).
    alias = "https://alias.example.com"
    home = ('<html><body><article><p>home</p>'
            '<a href="/docs/1">1</a><a href="/docs/2">2</a><a href="/docs/3">3</a>'
            '</article></body></html>')
    doc = "<html><body><article><p>doc</p></article></body></html>"
    responses = {
        alias + "/start": (_BASE + "/", home),      # seed resolves alias → canonical
        _BASE + "/docs/1": (_BASE + "/docs/1", doc),
        _BASE + "/docs/2": (_BASE + "/docs/2", doc),
        _BASE + "/docs/3": (_BASE + "/docs/3", doc),
    }
    pipeline = _make_pipeline()
    crawler = WebCrawler(pipeline=pipeline, fetcher=_redirect_fetcher(responses),
                         same_domain_only=True, respect_robots=False,
                         crawl_delay=0, max_retries=0)
    crawler.crawl(alias + "/start", depth=1, max_pages=10)

    ingested = {c.args[0] for c in pipeline.ingest.call_args_list}
    assert ingested == {alias + "/start", _BASE + "/docs/1", _BASE + "/docs/2", _BASE + "/docs/3"}


def test_crawl_max_pages_counts_only_imported_pages():
    # Home lists 3 dead links before 3 live docs; with max_pages=4 the dead links
    # must be skipped without consuming the budget (#52).
    home = ('<html><body><article><p>home</p>'
            + "".join(f'<a href="/{p}">x</a>'
                      for p in ("dead/1", "dead/2", "dead/3", "docs/1", "docs/2", "docs/3"))
            + '</article></body></html>')
    doc = "<html><body><article><p>doc</p></article></body></html>"
    url_map = {
        _BASE + "/": home,
        _BASE + "/docs/1": doc, _BASE + "/docs/2": doc, _BASE + "/docs/3": doc,
        # /dead/* are absent → fetch fails → they must not count toward max_pages
    }
    pipeline = _make_pipeline()
    crawler = WebCrawler(pipeline=pipeline, fetcher=_make_fetcher(url_map),
                         same_domain_only=True, respect_robots=False,
                         crawl_delay=0, max_retries=0)
    chunks = crawler.crawl(_BASE + "/", depth=1, max_pages=4)

    assert {c["url"] for c in chunks} == {
        _BASE + "/", _BASE + "/docs/1", _BASE + "/docs/2", _BASE + "/docs/3",
    }


def test_crawl_max_attempts_bounds_dead_link_fetches():
    # A page of dead links must not fetch forever: max_attempts caps total fetches.
    home = ('<html><body><article><p>home</p>'
            + "".join(f'<a href="/dead/{i}">x</a>' for i in range(50))
            + '</article></body></html>')
    pipeline = _make_pipeline()
    fetcher = _make_fetcher({_BASE + "/": home})  # every /dead/* 404s
    crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher, same_domain_only=True,
                         respect_robots=False, crawl_delay=0, max_retries=0)
    crawler.crawl(_BASE + "/", depth=1, max_pages=4, max_attempts=10)

    # seed + at most (max_attempts - 1) dead-link attempts, never all 50.
    assert fetcher.fetch.call_count <= 10


# #100: re-crawling an unchanged site must stay bounded to ~max_pages


def test_recrawl_unchanged_stays_bounded_to_max_pages():
    # Seed links to many pages; a freshness store makes re-ingest yield no chunks.
    # A successful fetch counts toward max_pages even with no new chunks, so each
    # re-crawl fetches ~max_pages instead of climbing toward max_attempts (#100).
    n = 30
    home = ('<html><body><article><p>home</p>'
            + "".join(f'<a href="/p{i}">x</a>' for i in range(n))
            + '</article></body></html>')
    doc = "<html><body><article><p>doc</p></article></body></html>"
    url_map = {_BASE + "/": home}
    for i in range(n):
        url_map[_BASE + f"/p{i}"] = doc

    # Simulate a freshness store: a URL yields chunks the first time it is ingested
    # and nothing on every later ingest.
    seen: set = set()

    def _ingest(url, collection=None, _fetch_result=None):
        if url in seen:
            return []
        seen.add(url)
        return [{"url": url, "chunk_id": f"{url}_0", "text": "t"}]

    pipeline = MagicMock()
    pipeline.ingest.side_effect = _ingest
    fetcher = _make_fetcher(url_map)
    crawler = _crawler(pipeline, fetcher)

    crawler.crawl(_BASE + "/", depth=1, max_pages=5)
    first = fetcher.fetch.call_count
    assert first == 5  # seed + 4 links

    fetcher.fetch.reset_mock()
    crawler.crawl(_BASE + "/", depth=1, max_pages=5)
    second = fetcher.fetch.call_count

    # The re-crawl of the now-unchanged site must not escalate (pre-fix it would
    # fetch far more, chasing chunk-producing pages up to max_attempts).
    assert second == first
