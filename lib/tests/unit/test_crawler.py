"""Unit tests for WebCrawler."""
from unittest.mock import MagicMock, patch

import httpx
import pytest

from blue_web_source.source.web.crawler import WebCrawler
from blue_web_source.source.web.fetcher import FetchResult

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

def _mock_robots(allowed: bool, crawl_delay: float | None = None):
    rp = MagicMock()
    rp.can_fetch.return_value = allowed
    rp.crawl_delay.return_value = crawl_delay
    return rp


def test_crawl_skips_url_disallowed_by_robots():
    fetcher = _make_fetcher({_BASE + "/": _INDEX_HTML})
    pipeline = _make_pipeline()

    with patch("blue_web_source.source.web.crawler.RobotFileParser") as mock_rfp:
        mock_rfp.return_value = _mock_robots(allowed=False)
        crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                             respect_robots=True, crawl_delay=0, max_retries=0)
        crawler.crawl(_BASE + "/", depth=0, max_pages=10)

    pipeline.ingest.assert_not_called()


def test_crawl_allows_url_permitted_by_robots():
    fetcher = _make_fetcher({_BASE + "/": _INDEX_HTML})
    pipeline = _make_pipeline()

    with patch("blue_web_source.source.web.crawler.RobotFileParser") as mock_rfp:
        mock_rfp.return_value = _mock_robots(allowed=True)
        crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                             respect_robots=True, crawl_delay=0, max_retries=0)
        crawler.crawl(_BASE + "/", depth=0, max_pages=10)

    assert pipeline.ingest.call_count == 1


def test_crawl_allows_all_when_robots_unreachable():
    fetcher = _make_fetcher({_BASE + "/": _INDEX_HTML})
    pipeline = _make_pipeline()

    with patch("blue_web_source.source.web.crawler.RobotFileParser") as mock_rfp:
        rp = MagicMock()
        rp.read.side_effect = OSError("network error")
        mock_rfp.return_value = rp
        crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                             respect_robots=True, crawl_delay=0, max_retries=0)
        crawler.crawl(_BASE + "/", depth=0, max_pages=10)

    assert pipeline.ingest.call_count == 1


def test_crawl_caches_robots_per_domain():
    url_map = {
        _BASE + "/": _INDEX_HTML,
        _BASE + "/about": _ABOUT_HTML,
    }
    fetcher = _make_fetcher(url_map)
    pipeline = _make_pipeline()

    with patch("blue_web_source.source.web.crawler.RobotFileParser") as mock_rfp:
        mock_rfp.return_value = _mock_robots(allowed=True)
        crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                             respect_robots=True, crawl_delay=0, max_retries=0)
        crawler.crawl(_BASE + "/", depth=1, max_pages=10)

    # RobotFileParser should be instantiated once per domain, not once per page
    assert mock_rfp.call_count == 1


def test_crawl_respect_robots_false_skips_robots_check():
    fetcher = _make_fetcher({_BASE + "/": _INDEX_HTML})
    pipeline = _make_pipeline()

    with patch("blue_web_source.source.web.crawler.RobotFileParser") as mock_rfp:
        crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                             respect_robots=False, crawl_delay=0, max_retries=0)
        crawler.crawl(_BASE + "/", depth=0, max_pages=10)

    mock_rfp.assert_not_called()
    assert pipeline.ingest.call_count == 1


def test_crawl_honours_robots_crawl_delay():
    fetcher = _make_fetcher({_BASE + "/": _INDEX_HTML})
    pipeline = _make_pipeline()

    sleep_calls = []
    with patch("blue_web_source.source.web.crawler.RobotFileParser") as mock_rfp, \
         patch("blue_web_source.source.web.crawler.time.sleep", side_effect=sleep_calls.append), \
         patch("blue_web_source.source.web.crawler.time.monotonic", return_value=0.0):
        # robots.txt says Crawl-delay: 5; our default is 1.0 — should use 5
        mock_rfp.return_value = _mock_robots(allowed=True, crawl_delay=5.0)
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
    with patch("blue_web_source.source.web.crawler.time.sleep", side_effect=sleep_calls.append), \
         patch("blue_web_source.source.web.crawler.time.monotonic", return_value=0.0):
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
    with patch("blue_web_source.source.web.crawler.time.sleep", side_effect=sleep_calls.append):
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

    with patch("blue_web_source.source.web.crawler.time.sleep"):
        crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                             respect_robots=False, crawl_delay=0, max_retries=2)
        crawler.crawl(_BASE + "/", depth=0, max_pages=10)

    assert pipeline.ingest.call_count == 1
    assert call_count[0] == 2


def test_crawl_gives_up_after_max_retries():
    fetcher = MagicMock()
    fetcher.fetch.side_effect = _http_status_error(503)
    pipeline = _make_pipeline()

    with patch("blue_web_source.source.web.crawler.time.sleep"):
        crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                             respect_robots=False, crawl_delay=0, max_retries=2)
        crawler.crawl(_BASE + "/", depth=0, max_pages=10)

    assert pipeline.ingest.call_count == 0
    assert fetcher.fetch.call_count == 3  # 1 original + 2 retries


def test_crawl_does_not_retry_4xx():
    fetcher = MagicMock()
    fetcher.fetch.side_effect = _http_status_error(404)
    pipeline = _make_pipeline()

    with patch("blue_web_source.source.web.crawler.time.sleep") as mock_sleep:
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

    with patch("blue_web_source.source.web.crawler.time.sleep"):
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
    with patch("blue_web_source.source.web.crawler.time.sleep", side_effect=sleep_calls.append):
        crawler = WebCrawler(pipeline=pipeline, fetcher=fetcher,
                             respect_robots=False, crawl_delay=0, max_retries=3)
        crawler.crawl(_BASE + "/", depth=0, max_pages=10)

    # Backoff should be 1s, 2s, 4s (2^0, 2^1, 2^2)
    assert sleep_calls == [1, 2, 4]
