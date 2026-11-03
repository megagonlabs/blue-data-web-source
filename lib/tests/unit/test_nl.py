"""Unit tests for NLQueryParser and WebDataSource.execute_query_nl."""
import http.server
import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from blue_data_web_source.source.web.nl import NLQueryParser
from blue_data_web_source.source.web.web_source import WebDataSource


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_response(tool_name: str, args: dict) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = args
    resp = MagicMock()
    resp.content = [block]
    return resp


def _parser(tool_name: str, args: dict) -> NLQueryParser:
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response(tool_name, args)
    return NLQueryParser(client=mock_client)


# ---------------------------------------------------------------------------
# NLQueryParser._to_op
# ---------------------------------------------------------------------------

class TestToOp:
    def test_ingest_url_minimal(self):
        p = NLQueryParser()
        assert p._to_op("ingest_url", {"url": "https://example.com"}) == {
            "op": "WEB.INGEST",
            "url": "https://example.com",
        }

    def test_ingest_url_with_collection(self):
        p = NLQueryParser()
        assert p._to_op("ingest_url", {"url": "https://example.com", "collection": "news"}) == {
            "op": "WEB.INGEST",
            "url": "https://example.com",
            "collection": "news",
        }

    def test_search_minimal(self):
        p = NLQueryParser()
        assert p._to_op("search", {"query": "climate"}) == {
            "op": "WEB.SEARCH",
            "query": "climate",
        }

    def test_search_with_k_and_collection(self):
        p = NLQueryParser()
        assert p._to_op("search", {"query": "climate", "k": 3, "collection": "science"}) == {
            "op": "WEB.SEARCH",
            "query": "climate",
            "k": 3,
            "collection": "science",
        }

    def test_ensure_index_no_collection(self):
        p = NLQueryParser()
        assert p._to_op("ensure_index", {}) == {"op": "WEB.ENSURE_INDEX"}

    def test_ensure_index_with_collection(self):
        p = NLQueryParser()
        assert p._to_op("ensure_index", {"collection": "news"}) == {
            "op": "WEB.ENSURE_INDEX",
            "collection": "news",
        }

    def test_crawl_minimal(self):
        p = NLQueryParser()
        assert p._to_op("crawl", {"url": "https://docs.example.com/"}) == {
            "op": "WEB.CRAWL",
            "url": "https://docs.example.com/",
        }

    def test_crawl_with_depth_and_max_pages(self):
        p = NLQueryParser()
        assert p._to_op("crawl", {"url": "https://docs.example.com/", "depth": 2, "max_pages": 20}) == {
            "op": "WEB.CRAWL",
            "url": "https://docs.example.com/",
            "depth": 2,
            "max_pages": 20,
        }

    def test_crawl_with_collection(self):
        p = NLQueryParser()
        assert p._to_op("crawl", {"url": "https://docs.example.com/", "collection": "docs"}) == {
            "op": "WEB.CRAWL",
            "url": "https://docs.example.com/",
            "collection": "docs",
        }

    def test_search_web_minimal(self):
        p = NLQueryParser()
        assert p._to_op("search_web", {"query": "climate news"}) == {
            "op": "WEB.SEARCH_WEB",
            "query": "climate news",
        }

    def test_search_web_with_k_and_top_k(self):
        p = NLQueryParser()
        assert p._to_op("search_web", {"query": "AI policy", "k": 10, "top_k": 3}) == {
            "op": "WEB.SEARCH_WEB",
            "query": "AI policy",
            "k": 10,
            "top_k": 3,
        }

    def test_search_web_with_collection(self):
        p = NLQueryParser()
        assert p._to_op("search_web", {"query": "elections", "collection": "news"}) == {
            "op": "WEB.SEARCH_WEB",
            "query": "elections",
            "collection": "news",
        }

    def test_unknown_tool_raises(self):
        p = NLQueryParser()
        with pytest.raises(ValueError, match="Unexpected tool"):
            p._to_op("unknown_tool", {})


# ---------------------------------------------------------------------------
# NLQueryParser.parse
# ---------------------------------------------------------------------------

class TestParse:
    def test_parse_ingest(self):
        p = _parser("ingest_url", {"url": "https://example.com"})
        assert p.parse("ingest https://example.com") == {
            "op": "WEB.INGEST",
            "url": "https://example.com",
        }

    def test_parse_ingest_with_collection(self):
        p = _parser("ingest_url", {"url": "https://example.com", "collection": "news"})
        assert p.parse("ingest https://example.com into news") == {
            "op": "WEB.INGEST",
            "url": "https://example.com",
            "collection": "news",
        }

    def test_parse_search(self):
        p = _parser("search", {"query": "climate policy"})
        assert p.parse("search for articles about climate policy") == {
            "op": "WEB.SEARCH",
            "query": "climate policy",
        }

    def test_parse_search_with_k(self):
        p = _parser("search", {"query": "emissions", "k": 10})
        result = p.parse("find top 10 results about emissions")
        assert result == {"op": "WEB.SEARCH", "query": "emissions", "k": 10}

    def test_parse_ensure_index(self):
        p = _parser("ensure_index", {"collection": "news"})
        assert p.parse("create the news index") == {
            "op": "WEB.ENSURE_INDEX",
            "collection": "news",
        }

    def test_parse_uses_specified_model(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _fake_response("search", {"query": "test"})
        p = NLQueryParser(model="claude-haiku-4-5", client=mock_client)
        p.parse("search for test")
        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-haiku-4-5"

    def test_parse_passes_tool_choice_any(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _fake_response("search", {"query": "test"})
        p = NLQueryParser(client=mock_client)
        p.parse("search for test")
        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["tool_choice"] == {"type": "any"}

    def test_parse_no_tool_call_raises(self):
        text_block = MagicMock()
        text_block.type = "text"
        resp = MagicMock()
        resp.content = [text_block]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = resp

        p = NLQueryParser(client=mock_client)
        with pytest.raises(ValueError, match="tool call"):
            p.parse("hello world")

    def test_missing_anthropic_raises_import_error(self):
        import sys
        p = NLQueryParser()  # no client injected
        with pytest.raises(ImportError, match="anthropic"):
            with pytest.MonkeyPatch().context() as m:
                m.setitem(sys.modules, "anthropic", None)  # type: ignore[arg-type]
                p._get_client()


# ---------------------------------------------------------------------------
# WebDataSource.execute_query_nl
# ---------------------------------------------------------------------------

def _make_source(**kwargs) -> WebDataSource:
    source = WebDataSource(**kwargs)
    source._pipeline = MagicMock()
    return source


def test_execute_query_nl_dispatches_search():
    source = _make_source()
    source._pipeline.search.return_value = [{"text": "result"}]

    mock_parser = MagicMock()
    mock_parser.parse.return_value = {"op": "WEB.SEARCH", "query": "climate"}
    source._nl_parser = mock_parser

    result = source.execute_query_nl("search for climate articles")

    mock_parser.parse.assert_called_once_with("search for climate articles")
    source._pipeline.search.assert_called_once_with("climate", k=5, collection=None)
    assert result == [{"text": "result"}]


def test_execute_query_nl_dispatches_ingest():
    source = _make_source()
    source._pipeline.ingest.return_value = [{"chunk_id": "abc"}]

    mock_parser = MagicMock()
    mock_parser.parse.return_value = {"op": "WEB.INGEST", "url": "https://example.com"}
    source._nl_parser = mock_parser

    result = source.execute_query_nl("ingest https://example.com", read_only=False)

    source._pipeline.ingest.assert_called_once_with("https://example.com", collection=None)
    assert result == [{"chunk_id": "abc"}]


def test_execute_query_nl_passes_collection_override():
    source = _make_source()
    source._pipeline.ingest.return_value = []

    mock_parser = MagicMock()
    mock_parser.parse.return_value = {"op": "WEB.INGEST", "url": "https://example.com"}
    source._nl_parser = mock_parser

    source.execute_query_nl("ingest https://example.com", collection="news", read_only=False)

    source._pipeline.ingest.assert_called_once_with("https://example.com", collection="news")


def test_execute_query_nl_creates_parser_lazily():
    source = _make_source(nl_model="claude-haiku-4-5")
    assert source._nl_parser is None

    mock_parser = MagicMock()
    mock_parser.parse.return_value = {"op": "WEB.ENSURE_INDEX"}
    source._nl_parser = mock_parser

    source.execute_query_nl("create index", read_only=False)
    assert source._nl_parser is mock_parser


def test_execute_query_nl_dispatches_crawl():
    source = _make_source()

    mock_parser = MagicMock()
    mock_parser.parse.return_value = {
        "op": "WEB.CRAWL",
        "url": "https://docs.example.com/",
        "depth": 2,
    }
    source._nl_parser = mock_parser
    source._crawl = MagicMock(return_value=[{"chunk_id": "x"}])

    result = source.execute_query_nl("crawl the docs site at docs.example.com", read_only=False)

    mock_parser.parse.assert_called_once_with("crawl the docs site at docs.example.com")
    source._crawl.assert_called_once()
    request_arg = source._crawl.call_args.args[0]
    assert request_arg["url"] == "https://docs.example.com/"
    assert request_arg["depth"] == 2
    assert result == [{"chunk_id": "x"}]


def test_execute_query_nl_dispatches_search_web():
    source = _make_source()
    source._pipeline.ingest.return_value = []
    source._pipeline.search.return_value = [{"text": "live result"}]
    source._search_provider = MagicMock()
    source._search_provider.search.return_value = []

    mock_parser = MagicMock()
    mock_parser.parse.return_value = {"op": "WEB.SEARCH_WEB", "query": "latest climate news", "k": 3}
    source._nl_parser = mock_parser

    result = source.execute_query_nl("find me the latest climate news", read_only=False)

    mock_parser.parse.assert_called_once_with("find me the latest climate news")
    source._search_provider.search.assert_called_once_with("latest climate news", k=3)


def test_execute_query_nl_reuses_parser_across_calls():
    source = _make_source()
    source._pipeline.search.return_value = []

    mock_parser = MagicMock()
    mock_parser.parse.return_value = {"op": "WEB.SEARCH", "query": "test"}
    source._nl_parser = mock_parser

    source.execute_query_nl("first call")
    source.execute_query_nl("second call")

    assert mock_parser.parse.call_count == 2


# ---------------------------------------------------------------------------
# #50: NLQueryParser.parse_with_llm_fn + base-signature execute_query_nl
# ---------------------------------------------------------------------------

class TestParseWithLlmFn:
    def test_maps_json_tool_call_to_op(self):
        p = NLQueryParser()
        op = p.parse_with_llm_fn(
            "find tides",
            lambda prompt: '{"name": "search", "input": {"query": "tides", "k": 3}}',
        )
        assert op == {"op": "WEB.SEARCH", "query": "tides", "k": 3}

    def test_tolerates_prose_and_code_fences(self):
        p = NLQueryParser()
        op = p.parse_with_llm_fn(
            "ingest it",
            lambda prompt: 'Sure:\n```json\n{"name": "ingest_url", "input": {"url": "https://e.com"}}\n```',
        )
        assert op == {"op": "WEB.INGEST", "url": "https://e.com"}

    def test_prompt_lists_the_tools(self):
        p = NLQueryParser()
        captured = {}

        def llm_fn(prompt):
            captured["prompt"] = prompt
            return '{"name": "ensure_index", "input": {}}'

        p.parse_with_llm_fn("make the index", llm_fn)
        assert "ingest_url" in captured["prompt"]
        assert "search_web" in captured["prompt"]

    def test_raises_on_non_json_response(self):
        p = NLQueryParser()
        with pytest.raises(ValueError):
            p.parse_with_llm_fn("x", lambda prompt: "no json here")


def test_execute_query_nl_registry_signature_with_llm_fn():
    # The exact keyword call DataRegistry.execute_query_nl makes — must not
    # TypeError, and must route through llm_fn (no Anthropic client needed).
    source = _make_source()
    source._pipeline.search.return_value = [{"chunk_id": "c1"}]
    result = source.execute_query_nl(
        query="search for tides",
        database=None,
        collection="web",
        schema=None,
        llm_fn=lambda prompt: '{"name": "search", "input": {"query": "tides", "k": 2}}',
        optional_properties={},
    )
    source._pipeline.search.assert_called_once_with("tides", k=2, collection="web")
    assert result == [{"chunk_id": "c1"}]


# ---------------------------------------------------------------------------
# #95: read_only (the default) refuses a translated write before it runs
# ---------------------------------------------------------------------------

@pytest.fixture
def loopback_page():
    """A throwaway HTTP server on 127.0.0.1 that records the paths it is asked for."""
    hits = []

    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><article><p>Placeholder page about garden snails for this test.</p>"
                b"</article></body></html>"
            )

        def log_message(self, *args):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/snails", hits
    srv.shutdown()
    srv.server_close()


def _ingest_llm_fn(url):
    # What a model returns for "ingest this page".
    return lambda prompt: json.dumps({"name": "ingest_url", "input": {"url": url}})


def _memory_source():
    # These tests fetch a throwaway 127.0.0.1 server, so opt in to loopback — the
    # fetch destination policy blocks internal addresses by default (#112).
    source = WebDataSource(properties={
        "embedding": "dummy", "vector": "memory", "freshness": "none",
        "fetch_allow_private": True,
    })
    source.execute_query({"op": "WEB.ENSURE_INDEX"})
    return source


def test_execute_query_nl_read_only_refuses_translated_write(loopback_page):
    url, hits = loopback_page
    source = _memory_source()
    with pytest.raises(ValueError, match="write operation"):
        source.execute_query_nl("what do I have about snails?", llm_fn=_ingest_llm_fn(url))
    assert hits == []
    assert source.execute_query({"op": "WEB.SEARCH", "query": "snails", "k": 1}) == []


def test_execute_query_nl_runs_translated_write_when_not_read_only(loopback_page):
    url, hits = loopback_page
    source = _memory_source()
    out = source.execute_query_nl("ingest the snails page", llm_fn=_ingest_llm_fn(url), read_only=False)
    assert out
    assert hits == ["/snails"]
    assert len(source.execute_query({"op": "WEB.SEARCH", "query": "snails", "k": 1})) == 1
