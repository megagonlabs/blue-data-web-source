"""Unit tests for NLQueryParser and WebDataSource.execute_query_nl."""
from unittest.mock import MagicMock

import pytest

from blue_web_source.source.web.nl import NLQueryParser
from blue_web_source.source.web.web_source import WebDataSource


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

    result = source.execute_query_nl("ingest https://example.com")

    source._pipeline.ingest.assert_called_once_with("https://example.com", collection=None)
    assert result == [{"chunk_id": "abc"}]


def test_execute_query_nl_passes_collection_override():
    source = _make_source()
    source._pipeline.ingest.return_value = []

    mock_parser = MagicMock()
    mock_parser.parse.return_value = {"op": "WEB.INGEST", "url": "https://example.com"}
    source._nl_parser = mock_parser

    source.execute_query_nl("ingest https://example.com", collection="news")

    source._pipeline.ingest.assert_called_once_with("https://example.com", collection="news")


def test_execute_query_nl_creates_parser_lazily():
    source = _make_source(nl_model="claude-haiku-4-5")
    assert source._nl_parser is None

    mock_parser = MagicMock()
    mock_parser.parse.return_value = {"op": "WEB.ENSURE_INDEX"}
    source._nl_parser = mock_parser

    source.execute_query_nl("create index")
    assert source._nl_parser is mock_parser


def test_execute_query_nl_reuses_parser_across_calls():
    source = _make_source()
    source._pipeline.search.return_value = []

    mock_parser = MagicMock()
    mock_parser.parse.return_value = {"op": "WEB.SEARCH", "query": "test"}
    source._nl_parser = mock_parser

    source.execute_query_nl("first call")
    source.execute_query_nl("second call")

    assert mock_parser.parse.call_count == 2
