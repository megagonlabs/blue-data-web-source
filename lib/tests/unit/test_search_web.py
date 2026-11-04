"""Unit tests for WEB.SEARCH_WEB and WebSearchProvider."""
from unittest.mock import MagicMock, patch

import pytest

from blue_data.source.vector.memory_vector_source import MemoryVectorSource
from blue_data_doc_source.source.document.embeddings.dummy_embedding import DummyEmbeddingModel

from blue_data_web_source import WebDataSource
from blue_data_web_source.source.web.freshness import MemoryFreshnessStore
from blue_data_web_source.source.web.search_provider import TavilySearchProvider, WebSearchProvider


# ---------------------------------------------------------------------------
# WebSearchProvider ABC
# ---------------------------------------------------------------------------

def test_web_search_provider_is_abstract():
    with pytest.raises(TypeError):
        WebSearchProvider()


# ---------------------------------------------------------------------------
# TavilySearchProvider
# ---------------------------------------------------------------------------

def test_tavily_search_provider_returns_urls():
    fake_response = {
        "results": [
            {"url": "https://example.com/a"},
            {"url": "https://example.com/b"},
        ]
    }
    mock_client = MagicMock()
    mock_client.search.return_value = fake_response

    mock_tavily_module = MagicMock()
    mock_tavily_module.TavilyClient.return_value = mock_client
    with patch.dict("sys.modules", {"tavily": mock_tavily_module}):
        provider = TavilySearchProvider(api_key="fake-key")
        urls = provider.search("climate targets", k=2)

    assert urls == ["https://example.com/a", "https://example.com/b"]
    mock_client.search.assert_called_once_with("climate targets", max_results=2)


def test_tavily_search_provider_missing_package():
    with patch.dict("sys.modules", {"tavily": None}):
        provider = TavilySearchProvider(api_key="fake-key")
        with pytest.raises(ImportError, match="tavily-python"):
            provider.search("query")


def test_tavily_uses_env_api_key(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "env-key")
    provider = TavilySearchProvider()
    assert provider._api_key == "env-key"


# ---------------------------------------------------------------------------
# WEB.SEARCH_WEB op
# ---------------------------------------------------------------------------

def _make_source(search_provider=None):
    return WebDataSource(
        name="test",
        embedding_model=DummyEmbeddingModel(dim=8),
        vector_source=MemoryVectorSource(),
        default_collection="web",
        search_provider=search_provider,
    )


def _mock_provider(urls):
    provider = MagicMock(spec=WebSearchProvider)
    provider.search.return_value = urls
    return provider


def test_search_web_requires_query():
    source = _make_source(search_provider=_mock_provider([]))
    source.execute_query({"op": "WEB.ENSURE_INDEX"})
    with pytest.raises(ValueError, match="query"):
        source.execute_query({"op": "WEB.SEARCH_WEB"})


def test_search_web_calls_provider_with_query_and_k(httpx_mock):
    httpx_mock.add_response(
        url="https://example.com/a", status_code=200,
        text="<html><body><article><p>Climate change content about greenhouse gas.</p></article></body></html>",
        headers={"content-type": "text/html"},
    )
    provider = _mock_provider(["https://example.com/a"])
    source = _make_source(search_provider=provider)
    source.execute_query({"op": "WEB.ENSURE_INDEX"})

    source.execute_query({"op": "WEB.SEARCH_WEB", "query": "climate targets", "k": 3})

    provider.search.assert_called_once_with("climate targets", k=3)


def test_search_web_ingests_returned_urls(httpx_mock):
    httpx_mock.add_response(
        url="https://example.com/a", status_code=200,
        text="<html><body><article><p>Article A about climate policy.</p></article></body></html>",
        headers={"content-type": "text/html"},
    )
    httpx_mock.add_response(
        url="https://example.com/b", status_code=200,
        text="<html><body><article><p>Article B about renewable energy.</p></article></body></html>",
        headers={"content-type": "text/html"},
    )
    provider = _mock_provider(["https://example.com/a", "https://example.com/b"])
    source = _make_source(search_provider=provider)
    source.execute_query({"op": "WEB.ENSURE_INDEX"})

    results = source.execute_query({"op": "WEB.SEARCH_WEB", "query": "climate", "k": 2})

    domains = {r["domain"] for r in results}
    assert "example.com" in domains


def test_search_web_returns_semantic_results(httpx_mock):
    httpx_mock.add_response(
        url="https://example.com/a", status_code=200,
        text="<html><body><article><p>Greenhouse gas emissions rose in 2025.</p></article></body></html>",
        headers={"content-type": "text/html"},
    )
    provider = _mock_provider(["https://example.com/a"])
    source = _make_source(search_provider=provider)
    source.execute_query({"op": "WEB.ENSURE_INDEX"})

    results = source.execute_query({"op": "WEB.SEARCH_WEB", "query": "emissions", "k": 1})

    assert len(results) > 0
    assert "score" in results[0]
    assert "text" in results[0]
    assert "url" in results[0]


def test_search_web_skips_failing_url_and_returns_rest(httpx_mock):
    # #49: one result URL 403s; the op must skip it and still return chunks from
    # the good URL rather than letting the exception abort the whole search.
    good_html = (
        "<html><head><title>Tidal</title></head><body><article><h1>Tidal</h1><p>"
        + "tidal energy turbine output renewable power generation " * 30
        + "</p></article></body></html>"
    )
    httpx_mock.add_response(url="https://example.com/forbidden", status_code=403)
    httpx_mock.add_response(
        url="https://example.com/good", status_code=200,
        text=good_html, headers={"content-type": "text/html"},
    )
    provider = _mock_provider(["https://example.com/forbidden", "https://example.com/good"])
    source = _make_source(search_provider=provider)
    source.execute_query({"op": "WEB.ENSURE_INDEX"})

    results = source.execute_query({"op": "WEB.SEARCH_WEB", "query": "tidal energy", "k": 2})

    assert len(results) > 0
    assert {r["url"] for r in results} == {"https://example.com/good"}


def test_search_web_all_urls_failing_returns_empty(httpx_mock):
    # Every result URL fails — the op must return an empty list, not raise.
    httpx_mock.add_response(url="https://example.com/a", status_code=403)
    httpx_mock.add_response(url="https://example.com/b", status_code=500)
    provider = _mock_provider(["https://example.com/a", "https://example.com/b"])
    source = _make_source(search_provider=provider)
    source.execute_query({"op": "WEB.ENSURE_INDEX"})

    results = source.execute_query({"op": "WEB.SEARCH_WEB", "query": "anything", "k": 2})

    assert results == []


def test_search_web_uses_tavily_by_default():
    source = _make_source(search_provider=None)
    assert source._search_provider is None
    source.execute_query({"op": "WEB.ENSURE_INDEX"})
    with patch("blue_data_web_source.source.web.search_provider.TavilySearchProvider.search", return_value=[]):
        try:
            source.execute_query({"op": "WEB.SEARCH_WEB", "query": "test"})
        except Exception:
            pass
    assert isinstance(source._search_provider, TavilySearchProvider)


# ── #35: scope — current search results vs whole collection ───────────────────

_OLD_HTML = (
    "<html><body><article><h1>Stale</h1>"
    "<p>An older page about solar energy that was indexed long ago and is not in "
    "the current web search result set at all.</p></article></body></html>"
)
_NEW_HTML = (
    "<html><body><article><h1>Fresh</h1>"
    "<p>A fresh page about solar energy returned by the current live web search "
    "and ingested on the fly for this request.</p></article></body></html>"
)
_OLD_URL = "https://old.example.com/stale"
_NEW_URL = "https://new.example.com/fresh"


def _seed_and_search(httpx_mock, scope=None):
    """Pre-seed an old page, then WEB.SEARCH_WEB a query that matches both pages."""
    httpx_mock.add_response(url=_OLD_URL, text=_OLD_HTML,
                            headers={"content-type": "text/html"})
    httpx_mock.add_response(url=_NEW_URL, text=_NEW_HTML,
                            headers={"content-type": "text/html"})
    source = _make_source(search_provider=_mock_provider([_NEW_URL]))
    source.execute_query({"op": "WEB.ENSURE_INDEX"})
    source.execute_query({"op": "WEB.INGEST", "url": _OLD_URL})  # old page, not a current result
    op = {"op": "WEB.SEARCH_WEB", "query": "solar energy", "k": 10}
    if scope is not None:
        op["scope"] = scope
    return source.execute_query(op)


def test_search_web_default_scope_excludes_old_collection_pages(httpx_mock):
    # Default scope = current_search_results: an old indexed page that is NOT in
    # this search's results must not surface (#35).
    results = _seed_and_search(httpx_mock)
    domains = {r["domain"] for r in results}
    assert "new.example.com" in domains
    assert "old.example.com" not in domains


def test_search_web_collection_scope_includes_old_pages(httpx_mock):
    # Opt-in scope = collection: rank over the whole corpus, old page included.
    results = _seed_and_search(httpx_mock, scope="collection")
    domains = {r["domain"] for r in results}
    assert "new.example.com" in domains
    assert "old.example.com" in domains


def test_search_web_rejects_unknown_scope():
    source = _make_source(search_provider=_mock_provider([]))
    source.execute_query({"op": "WEB.ENSURE_INDEX"})
    with pytest.raises(ValueError, match="scope"):
        source.execute_query({"op": "WEB.SEARCH_WEB", "query": "x", "scope": "bogus"})


# ── #122: with a freshness store, result pages with no new chunks must still rank ──

_LENS_HTML = (
    "<html><body><article><p>Fresnel lenses project a narrow beam across the sea.</p>"
    "</article></body></html>"
)


def _fresh_source(urls):
    return WebDataSource(
        name="test",
        embedding_model=DummyEmbeddingModel(dim=8),
        vector_source=MemoryVectorSource(),
        default_collection="web",
        search_provider=_mock_provider(urls),
        freshness_store=MemoryFreshnessStore(),
    )


def test_search_web_repeated_still_returns_result_page(httpx_mock):
    # The 2nd identical search re-fetches the page, the freshness store reports it
    # unchanged (no new chunks), but it is still a result page and must rank (#122).
    for _ in range(2):
        httpx_mock.add_response(
            url="https://example.com/a", status_code=200,
            text=_LENS_HTML, headers={"content-type": "text/html"},
        )
    source = _fresh_source(["https://example.com/a"])
    source.execute_query({"op": "WEB.ENSURE_INDEX"})
    op = {"op": "WEB.SEARCH_WEB", "query": "fresnel lens", "k": 3}
    assert len(source.execute_query(dict(op))) > 0
    assert len(source.execute_query(dict(op))) > 0  # pre-fix: 0


def test_search_web_includes_already_imported_result_page(httpx_mock):
    # A result page already in the collection (unchanged) must still be ranked (#122).
    for _ in range(2):  # one for the pre-import INGEST, one for the search fetch
        httpx_mock.add_response(
            url="https://example.com/a", status_code=200,
            text=_LENS_HTML, headers={"content-type": "text/html"},
        )
    source = _fresh_source(["https://example.com/a"])
    source.execute_query({"op": "WEB.ENSURE_INDEX"})
    source.execute_query({"op": "WEB.INGEST", "url": "https://example.com/a"})
    results = source.execute_query({"op": "WEB.SEARCH_WEB", "query": "fresnel lens", "k": 3})
    assert len(results) > 0
