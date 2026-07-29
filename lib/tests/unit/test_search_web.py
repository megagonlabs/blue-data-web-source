"""Unit tests for WEB.SEARCH_WEB and WebSearchProvider."""
from unittest.mock import MagicMock, patch

import pytest

from blue_data.source.vector.memory_vector_source import MemoryVectorSource
from blue_doc_source.source.document.embeddings.dummy_embedding import DummyEmbeddingModel

from blue_web_source import WebDataSource
from blue_web_source.source.web.search_provider import TavilySearchProvider, WebSearchProvider


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


def test_search_web_uses_tavily_by_default():
    source = _make_source(search_provider=None)
    assert source._search_provider is None
    source.execute_query({"op": "WEB.ENSURE_INDEX"})
    with patch("blue_web_source.source.web.search_provider.TavilySearchProvider.search", return_value=[]):
        try:
            source.execute_query({"op": "WEB.SEARCH_WEB", "query": "test"})
        except Exception:
            pass
    assert isinstance(source._search_provider, TavilySearchProvider)
