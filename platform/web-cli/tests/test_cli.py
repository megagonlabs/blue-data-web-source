"""CLI tests — no external services; WebDataSource mocked via get_source patch."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from blue_data_web_cli.config import reset_source
from blue_data_web_source import WebDataSource
from blue_data_web_cli.main import cli


@pytest.fixture(autouse=True)
def _reset():
    reset_source()
    yield
    reset_source()


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def mock_source():
    source = MagicMock()
    with patch("blue_data_web_cli.main.get_source", return_value=source):
        yield source


def _chunk(url="https://example.com/a", domain="example.com", title="Example Article", n=0):
    return {
        "chunk_id": f"web_abc_{n:04d}",
        "url": url,
        "domain": domain,
        "title": title,
        "date": "2026-08-17",
        "section_title": "Introduction",
        "fetch_timestamp": "2026-08-17T10:00:00+00:00",
        "text": "This is chunk text for testing. " * 5,
    }


# ── ensure-index ──────────────────────────────────────────────────────────────

class TestEnsureIndex:
    def test_ok(self, runner, mock_source):
        mock_source.execute_query.return_value = None
        result = runner.invoke(cli, ["ensure-index", "--collection", "test"])
        assert result.exit_code == 0
        assert "test" in result.output

    def test_json_output(self, runner, mock_source):
        mock_source.execute_query.return_value = None
        result = runner.invoke(cli, ["--json", "ensure-index", "--collection", "test"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["collection"] == "test"

    def test_idempotent(self, runner, mock_source):
        mock_source.execute_query.return_value = None
        runner.invoke(cli, ["ensure-index", "--collection", "test"])
        result = runner.invoke(cli, ["ensure-index", "--collection", "test"])
        assert result.exit_code == 0

    def test_error_propagates(self, runner, mock_source):
        mock_source.execute_query.side_effect = RuntimeError("Redis not available")
        result = runner.invoke(cli, ["ensure-index"])
        assert result.exit_code != 0


# ── ingest ────────────────────────────────────────────────────────────────────

class TestIngest:
    def test_single_url(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        result = runner.invoke(cli, ["ingest", "https://example.com/a"])
        assert result.exit_code == 0
        assert "1 chunk" in result.output
        assert "example.com" in result.output

    def test_single_url_json(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk(), _chunk(n=1)]
        result = runner.invoke(cli, ["--json", "ingest", "https://example.com/a"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["chunk_count"] == 2
        assert "url" in data

    def test_multi_url(self, runner, mock_source):
        mock_source.execute_query.side_effect = [
            [_chunk(url="https://example.com/a")],
            [_chunk(url="https://example.com/b")],
        ]
        result = runner.invoke(cli, ["ingest", "https://example.com/a", "https://example.com/b"])
        assert result.exit_code == 0
        assert "2 pages" in result.output
        assert "✓" in result.output

    def test_multi_url_json(self, runner, mock_source):
        mock_source.execute_query.side_effect = [
            [_chunk(url="https://example.com/a")],
            [_chunk(url="https://example.com/b")],
        ]
        result = runner.invoke(cli, ["--json", "ingest", "https://example.com/a", "https://example.com/b"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 2

    def test_no_content_single_url_exits_nonzero(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        result = runner.invoke(cli, ["ingest", "https://example.com/empty"])
        assert result.exit_code != 0

    def test_skipped_url_shown_in_multi(self, runner, mock_source):
        mock_source.execute_query.side_effect = [
            [_chunk(url="https://example.com/a")],
            [],
        ]
        result = runner.invoke(cli, ["ingest", "https://example.com/a", "https://example.com/b"])
        assert result.exit_code == 0
        assert "✗" in result.output

    def test_collection_passed_in_request(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        runner.invoke(cli, ["ingest", "https://example.com/a", "--collection", "news"])
        call_args = mock_source.execute_query.call_args[0][0]
        assert call_args["collection"] == "news"


# ── crawl ─────────────────────────────────────────────────────────────────────

class TestCrawl:
    def test_basic(self, runner, mock_source):
        mock_source.execute_query.return_value = [
            _chunk(url="https://example.com/"),
            _chunk(url="https://example.com/about"),
        ]
        result = runner.invoke(cli, ["crawl", "https://example.com"])
        assert result.exit_code == 0
        assert "2 pages" in result.output

    def test_json_output(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        result = runner.invoke(cli, ["--json", "crawl", "https://example.com"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "pages_ingested" in data
        assert data["seed"] == "https://example.com"

    def test_depth_and_max_pages_passed(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        runner.invoke(cli, ["crawl", "https://example.com", "--depth", "3", "--max-pages", "50"])
        call_args = mock_source.execute_query.call_args[0][0]
        assert call_args["depth"] == 3
        assert call_args["max_pages"] == 50

    def test_same_domain_only_default(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        runner.invoke(cli, ["crawl", "https://example.com"])
        call_args = mock_source.execute_query.call_args[0][0]
        assert call_args["same_domain_only"] is True

    def test_allow_external_flag(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        runner.invoke(cli, ["crawl", "https://example.com", "--allow-external"])
        call_args = mock_source.execute_query.call_args[0][0]
        assert call_args["same_domain_only"] is False


# ── feed ─────────────────────────────────────────────────────────────────────

class TestFeed:
    def test_basic(self, runner, mock_source):
        mock_source.execute_query.return_value = [
            _chunk(url="https://example.com/a1"),
            _chunk(url="https://example.com/a2"),
        ]
        result = runner.invoke(cli, ["feed", "https://example.com/feed.xml"])
        assert result.exit_code == 0
        assert "2 articles" in result.output

    def test_json_output(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        result = runner.invoke(cli, ["--json", "feed", "https://example.com/feed.xml"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "articles_ingested" in data
        assert data["feed"] == "https://example.com/feed.xml"

    def test_since_passed(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        runner.invoke(cli, ["feed", "https://example.com/feed.xml", "--since", "2026-08-01T00:00:00Z"])
        call_args = mock_source.execute_query.call_args[0][0]
        assert call_args["since"] == "2026-08-01T00:00:00Z"

    def test_since_absent_when_not_provided(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        runner.invoke(cli, ["feed", "https://example.com/feed.xml"])
        call_args = mock_source.execute_query.call_args[0][0]
        assert "since" not in call_args

    def test_max_entries_passed(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        runner.invoke(cli, ["feed", "https://example.com/feed.xml", "--max-entries", "10"])
        call_args = mock_source.execute_query.call_args[0][0]
        assert call_args["max_entries"] == 10

    def test_empty_feed(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        result = runner.invoke(cli, ["feed", "https://example.com/feed.xml"])
        assert result.exit_code == 0
        assert "0 articles" in result.output

    def test_single_article(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk(url="https://example.com/a1")]
        result = runner.invoke(cli, ["feed", "https://example.com/feed.xml"])
        assert result.exit_code == 0
        assert "1 article " in result.output


# ── search ─────────────────────────────────────────────────────────────────────

class TestSearch:
    def test_results(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        result = runner.invoke(cli, ["search", "climate change"])
        assert result.exit_code == 0
        assert "1 result" in result.output
        assert "example.com" in result.output

    def test_json_output(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        result = runner.invoke(cli, ["--json", "search", "climate change"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["url"] == "https://example.com/a"

    def test_no_results(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        result = runner.invoke(cli, ["search", "nothing"])
        assert result.exit_code == 0
        assert "No results" in result.output

    def test_k_passed(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        runner.invoke(cli, ["search", "query", "-k", "10"])
        call_args = mock_source.execute_query.call_args[0][0]
        assert call_args["k"] == 10

    def test_section_title_shown(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        result = runner.invoke(cli, ["search", "anything"])
        assert "Introduction" in result.output

    def test_error_propagates(self, runner, mock_source):
        mock_source.execute_query.side_effect = ValueError("No vector store configured")
        result = runner.invoke(cli, ["search", "query"])
        assert result.exit_code != 0


# ── extract ───────────────────────────────────────────────────────────────────

_SCHEMA_JSON = '{"properties": {"title": {"type": "string"}, "price": {"type": "number"}}}'


class TestExtract:
    def test_basic(self, runner, mock_source):
        mock_source.execute_query.return_value = {"title": "Widget", "price": 29.99}
        result = runner.invoke(cli, ["extract", "https://example.com", "--schema", _SCHEMA_JSON])
        assert result.exit_code == 0
        assert "Widget" in result.output

    def test_json_output(self, runner, mock_source):
        mock_source.execute_query.return_value = {"title": "Widget", "price": 29.99}
        result = runner.invoke(cli, ["--json", "extract", "https://example.com", "--schema", _SCHEMA_JSON])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["title"] == "Widget"
        assert data["price"] == 29.99

    def test_schema_passed_in_request(self, runner, mock_source):
        mock_source.execute_query.return_value = {}
        runner.invoke(cli, ["extract", "https://example.com", "--schema", _SCHEMA_JSON])
        call_args = mock_source.execute_query.call_args[0][0]
        assert call_args["op"] == "WEB.EXTRACT"
        assert call_args["url"] == "https://example.com"
        assert isinstance(call_args["schema"], dict)

    def test_schema_file(self, runner, mock_source, tmp_path):
        schema_file = tmp_path / "schema.json"
        schema_file.write_text(_SCHEMA_JSON)
        mock_source.execute_query.return_value = {"title": "Widget"}
        result = runner.invoke(cli, ["extract", "https://example.com", "--schema-file", str(schema_file)])
        assert result.exit_code == 0
        assert "Widget" in result.output

    def test_no_schema_exits_nonzero(self, runner, mock_source):
        result = runner.invoke(cli, ["extract", "https://example.com"])
        assert result.exit_code != 0

    def test_invalid_schema_json_exits_nonzero(self, runner, mock_source):
        result = runner.invoke(cli, ["extract", "https://example.com", "--schema", "not-json"])
        assert result.exit_code != 0

    def test_empty_result_shown(self, runner, mock_source):
        mock_source.execute_query.return_value = {}
        result = runner.invoke(cli, ["extract", "https://example.com", "--schema", _SCHEMA_JSON])
        assert result.exit_code == 0
        assert "No fields" in result.output

    def test_error_propagates(self, runner, mock_source):
        mock_source.execute_query.side_effect = RuntimeError("fetch failed")
        result = runner.invoke(cli, ["extract", "https://example.com", "--schema", _SCHEMA_JSON])
        assert result.exit_code != 0


# ── BLUE_WEB_ENRICHER config ──────────────────────────────────────────────────

class TestEnricherConfig:
    def _enricher(self, enricher_env: str, monkeypatch):
        monkeypatch.setenv("BLUE_WEB_ENRICHER", enricher_env)
        return WebDataSource.from_env()._pipeline._enricher

    def test_default_is_basic_enricher(self, monkeypatch):
        monkeypatch.delenv("BLUE_WEB_ENRICHER", raising=False)
        from blue_data_doc_source.source.document.enrichment import BasicEnricher
        assert isinstance(WebDataSource.from_env()._pipeline._enricher, BasicEnricher)

    def test_none_enricher(self, monkeypatch):
        assert self._enricher("none", monkeypatch) is None

    def test_basic_enricher(self, monkeypatch):
        from blue_data_doc_source.source.document.enrichment import BasicEnricher
        assert isinstance(self._enricher("basic", monkeypatch), BasicEnricher)

    def test_llm_enricher(self, monkeypatch):
        from blue_data_doc_source.source.document.enrichment import LLMEnricher
        assert isinstance(self._enricher("llm", monkeypatch), LLMEnricher)

    def test_llm_enricher_has_basic_fallback(self, monkeypatch):
        from blue_data_doc_source.source.document.enrichment import BasicEnricher, LLMEnricher
        enricher = self._enricher("llm", monkeypatch)
        assert isinstance(enricher, LLMEnricher)
        assert isinstance(enricher._fallback, BasicEnricher)

    def test_llm_model_env_var(self, monkeypatch):
        monkeypatch.setenv("BLUE_WEB_ENRICHER", "llm")
        monkeypatch.setenv("BLUE_WEB_LLM_MODEL", "gpt-4o")
        from blue_data_doc_source.source.document.enrichment import LLMEnricher
        enricher = WebDataSource.from_env()._pipeline._enricher
        assert isinstance(enricher, LLMEnricher)
        assert enricher._llm_fn is not None


# ── search-web ────────────────────────────────────────────────────────────────

class TestSearchWeb:
    def test_results(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        result = runner.invoke(cli, ["search-web", "offshore wind energy"])
        assert result.exit_code == 0
        assert "1 result" in result.output

    def test_json_output(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        result = runner.invoke(cli, ["--json", "search-web", "offshore wind"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)

    def test_no_results(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        result = runner.invoke(cli, ["search-web", "nothing"])
        assert result.exit_code == 0
        assert "No results" in result.output

    def test_k_passed(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        runner.invoke(cli, ["search-web", "query", "-k", "3"])
        call_args = mock_source.execute_query.call_args[0][0]
        assert call_args["k"] == 3

    def test_error_propagates(self, runner, mock_source):
        mock_source.execute_query.side_effect = RuntimeError("TAVILY_API_KEY not set")
        result = runner.invoke(cli, ["search-web", "query"])
        assert result.exit_code != 0
