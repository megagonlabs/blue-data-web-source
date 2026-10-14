"""Tests for platform/web-mcp/src/server.py.

The mcp package is stubbed so these tests run without the MCP SDK installed
and without any network, Redis, or external API connections. The WebDataSource
singleton is patched per-test via unittest.mock.
"""
import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ── Stub the mcp package ──────────────────────────────────────────────────────

def _stub_mcp():
    stub_mcp = types.ModuleType("mcp")
    stub_server = types.ModuleType("mcp.server")
    stub_fastmcp_mod = types.ModuleType("mcp.server.fastmcp")

    class _FakeFastMCP:
        def __init__(self, name):
            self.name = name

        def tool(self, **kwargs):
            return lambda fn: fn

        def run(self, transport="stdio"):
            pass

    stub_fastmcp_mod.FastMCP = _FakeFastMCP
    stub_mcp.server = stub_server
    stub_server.fastmcp = stub_fastmcp_mod
    return {"mcp": stub_mcp, "mcp.server": stub_server, "mcp.server.fastmcp": stub_fastmcp_mod}


_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
_SERVER_PATH = os.path.join(_REPO_ROOT, "platform/web-mcp/src/web_mcp/server.py")


def _load_server():
    with patch.dict(sys.modules, _stub_mcp()):
        spec = importlib.util.spec_from_file_location("_blue_web_mcp_server", _SERVER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


_server = _load_server()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_chunk(url="https://example.com/article", idx=0, section=None, title="Test Article"):
    chunk_id = f"web_abc12345_{idx:04d}"
    c = {
        "url": url,
        "domain": "example.com",
        "chunk_id": chunk_id,
        "chunk_index": idx,
        "text": f"Chunk {idx} text content about the topic.",
        "fetch_timestamp": "2026-08-12T00:00:00+00:00",
    }
    if title:
        c["title"] = title
    if section:
        c["section_title"] = section
    return c


def _mock_source(**execute_return):
    m = MagicMock()
    m.execute_query.side_effect = lambda q, **kw: execute_return.get(q.get("op"), [])
    return m


# ── ensure_index ──────────────────────────────────────────────────────────────

def test_ensure_index_returns_ok():
    source = MagicMock()
    source.execute_query.return_value = None
    with patch.object(_server, "_source", source):
        result = json.loads(_server.ensure_index("news"))
    assert result["collection"] == "news"
    assert "status" not in result
    source.execute_query.assert_called_once_with({"op": "WEB.ENSURE_INDEX", "collection": "news"})


def test_ensure_index_default_collection():
    source = MagicMock()
    source.execute_query.return_value = None
    with patch.object(_server, "_source", source):
        result = json.loads(_server.ensure_index())
    assert result["collection"] == "web"


def test_ensure_index_error_returns_error():
    source = MagicMock()
    source.execute_query.side_effect = Exception("Redis unavailable")
    with patch.object(_server, "_source", source):
        result = json.loads(_server.ensure_index())
    assert "error" in result
    assert "Redis" in result["error"]


# ── ingest_url ────────────────────────────────────────────────────────────────

def test_ingest_url_returns_metadata():
    chunks = [_make_chunk(idx=i) for i in range(4)]
    source = MagicMock()
    source.execute_query.return_value = chunks
    with patch.object(_server, "_source", source):
        result = json.loads(_server.ingest_url("https://example.com/article"))
    assert result["domain"] == "example.com"
    assert result["chunk_count"] == 4
    assert result["title"] == "Test Article"
    assert "page_id" in result
    source.execute_query.assert_called_once_with({
        "op": "WEB.INGEST", "url": "https://example.com/article", "collection": "web"
    })


def test_ingest_url_passes_collection():
    chunks = [_make_chunk()]
    source = MagicMock()
    source.execute_query.return_value = chunks
    with patch.object(_server, "_source", source):
        _server.ingest_url("https://example.com", collection="news")
    source.execute_query.assert_called_once_with({
        "op": "WEB.INGEST", "url": "https://example.com", "collection": "news"
    })


def test_ingest_url_no_content_returns_error():
    source = MagicMock()
    source.execute_query.return_value = []
    with patch.object(_server, "_source", source):
        result = json.loads(_server.ingest_url("https://example.com/login"))
    assert "error" in result
    assert "No content" in result["error"]


def test_ingest_url_exception_returns_error():
    source = MagicMock()
    source.execute_query.side_effect = Exception("Connection refused")
    with patch.object(_server, "_source", source):
        result = json.loads(_server.ingest_url("https://example.com"))
    assert "error" in result


# ── ingest_urls ───────────────────────────────────────────────────────────────

def test_ingest_urls_returns_summary():
    url_a = "https://example.com/a"
    url_b = "https://example.com/b"
    chunks = [_make_chunk(url=url_a, idx=0), _make_chunk(url=url_a, idx=1),
              _make_chunk(url=url_b, idx=0)]
    source = MagicMock()
    source.execute_query.return_value = chunks
    with patch.object(_server, "_source", source):
        result = json.loads(_server.ingest_urls([url_a, url_b]))
    assert result["chunk_count"] == 3
    assert result["ingested"] == 2
    assert result["skipped"] == []


def test_ingest_urls_reports_skipped():
    url_a = "https://example.com/a"
    url_b = "https://example.com/b"
    chunks = [_make_chunk(url=url_a)]
    source = MagicMock()
    source.execute_query.return_value = chunks
    with patch.object(_server, "_source", source):
        result = json.loads(_server.ingest_urls([url_a, url_b]))
    assert result["skipped"] == [url_b]


# ── crawl_site ────────────────────────────────────────────────────────────────

def test_crawl_site_returns_summary():
    chunks = [
        _make_chunk(url="https://example.com/", idx=0),
        _make_chunk(url="https://example.com/", idx=1),
        _make_chunk(url="https://example.com/about", idx=0),
    ]
    source = MagicMock()
    source.execute_query.return_value = chunks
    with patch.object(_server, "_source", source):
        result = json.loads(_server.crawl_site("https://example.com/", depth=1, max_pages=5))
    assert result["chunk_count"] == 3
    assert result["pages_ingested"] == 2
    assert "https://example.com/" in result["urls"]
    source.execute_query.assert_called_once_with({
        "op": "WEB.CRAWL",
        "url": "https://example.com/",
        "depth": 1,
        "max_pages": 5,
        "same_domain_only": True,
        "collection": "web",
    })


def test_crawl_site_passes_same_domain_only_false():
    source = MagicMock()
    source.execute_query.return_value = []
    with patch.object(_server, "_source", source):
        _server.crawl_site("https://example.com/", same_domain_only=False)
    call_arg = source.execute_query.call_args[0][0]
    assert call_arg["same_domain_only"] is False


def test_crawl_site_exception_returns_error():
    source = MagicMock()
    source.execute_query.side_effect = Exception("Timeout")
    with patch.object(_server, "_source", source):
        result = json.loads(_server.crawl_site("https://example.com/"))
    assert "error" in result


# ── search ────────────────────────────────────────────────────────────────────

def test_search_returns_hits():
    chunks = [
        _make_chunk(idx=0, section="Background"),
        _make_chunk(idx=1),
    ]
    source = MagicMock()
    source.execute_query.return_value = chunks
    with patch.object(_server, "_source", source):
        result = json.loads(_server.search("climate policy"))
    assert result["count"] == 2
    assert result["results"][0]["section_title"] == "Background"
    assert "section_title" not in result["results"][1]
    source.execute_query.assert_called_once_with({
        "op": "WEB.SEARCH", "query": "climate policy", "k": 5, "collection": "web"
    })


def test_search_passes_k_and_collection():
    source = MagicMock()
    source.execute_query.return_value = []
    with patch.object(_server, "_source", source):
        _server.search("query", collection="news", k=10)
    source.execute_query.assert_called_once_with({
        "op": "WEB.SEARCH", "query": "query", "k": 10, "collection": "news"
    })


def test_search_no_embedding_model_returns_error():
    source = MagicMock()
    source.execute_query.side_effect = ValueError("An embedding_model is required")
    with patch.object(_server, "_source", source):
        result = json.loads(_server.search("query"))
    assert "error" in result
    assert "embedding_model" in result["error"]


def test_search_omits_none_fields():
    chunk = _make_chunk(idx=0, title=None, section=None)
    source = MagicMock()
    source.execute_query.return_value = [chunk]
    with patch.object(_server, "_source", source):
        result = json.loads(_server.search("query"))
    hit = result["results"][0]
    assert "title" not in hit
    assert "section_title" not in hit


# ── search_web ────────────────────────────────────────────────────────────────

def test_search_web_returns_hits():
    chunks = [_make_chunk(idx=0, section="Methods")]
    source = MagicMock()
    source.execute_query.return_value = chunks
    with patch.object(_server, "_source", source):
        result = json.loads(_server.search_web("AI safety research"))
    assert result["count"] == 1
    assert result["results"][0]["url"] == "https://example.com/article"
    source.execute_query.assert_called_once_with({
        "op": "WEB.SEARCH_WEB", "query": "AI safety research", "k": 5, "collection": "web"
    })


def test_search_web_exception_returns_error():
    source = MagicMock()
    source.execute_query.side_effect = Exception("TAVILY_API_KEY not set")
    with patch.object(_server, "_source", source):
        result = json.loads(_server.search_web("query"))
    assert "error" in result


# ── WebDataSource.from_env() wiring ──────────────────────────────────────────

def test_from_env_uses_dummy_embedding_by_default(monkeypatch):
    monkeypatch.delenv("BLUE_WEB_EMBEDDING", raising=False)
    monkeypatch.delenv("BLUE_WEB_REDIS_HOST", raising=False)
    from blue_data_doc_source.source.document.embeddings.dummy_embedding import DummyEmbeddingModel
    from blue_data_web_source import WebDataSource
    source = WebDataSource.from_env()
    assert isinstance(source._pipeline._embedding_model, DummyEmbeddingModel)


def test_from_env_chunk_size_env(monkeypatch):
    monkeypatch.setenv("BLUE_WEB_CHUNK_SIZE", "500")
    monkeypatch.setenv("BLUE_WEB_OVERLAP", "75")
    monkeypatch.delenv("BLUE_WEB_REDIS_HOST", raising=False)
    from blue_data_web_source import WebDataSource
    source = WebDataSource.from_env()
    assert source._pipeline._chunker._window.chunk_size == 500
    assert source._pipeline._chunker._window.overlap == 75


def test_from_env_timeout_env(monkeypatch):
    monkeypatch.setenv("BLUE_WEB_TIMEOUT", "60")
    monkeypatch.delenv("BLUE_WEB_REDIS_HOST", raising=False)
    from blue_data_web_source import WebDataSource
    source = WebDataSource.from_env()
    assert source._pipeline._fetcher._timeout == 60.0


def test_from_env_collection_env(monkeypatch):
    monkeypatch.setenv("BLUE_WEB_COLLECTION", "articles")
    monkeypatch.delenv("BLUE_WEB_REDIS_HOST", raising=False)
    from blue_data_web_source import WebDataSource
    source = WebDataSource.from_env()
    assert source.default_collection == "articles"


# ── Real SDK import guard (#44) ───────────────────────────────────────────────
#
# The tests above stub the mcp package, so they never exercise the real
# `from mcp.server.fastmcp import FastMCP`. This test loads server.py against the
# *installed* SDK (no stub). It is skipped when mcp is not installed, but when it
# is, it fails loudly if the SDK lacks mcp.server.fastmcp — e.g. mcp 2.x, which
# renamed FastMCP to MCPServer. That is exactly the breakage the `<2` pin guards
# against, and why mocked tests alone could not catch it.

def test_server_imports_against_real_mcp_sdk():
    pytest.importorskip("mcp", reason="mcp SDK not installed")
    spec = importlib.util.spec_from_file_location("_blue_web_mcp_server_real", _SERVER_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Exec with NO stub in sys.modules — raises ModuleNotFoundError on mcp 2.x,
    # which is the failure we want CI to surface.
    spec.loader.exec_module(mod)
    assert mod.mcp is not None
    for tool in (
        "ensure_index", "ingest_url", "ingest_urls", "crawl_site",
        "ingest_feed", "search", "search_web", "extract",
    ):
        assert hasattr(mod, tool), f"MCP tool {tool!r} is not defined in server.py"
