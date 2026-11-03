"""CLI tests — no external services; WebDataSource mocked via get_source patch."""
from __future__ import annotations

import json
import logging
import sys
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
        "page_id": "web_abc123",
        "url": url,
        "domain": domain,
        "title": title,
        "date": "2026-08-17",
        "section_title": "Introduction",
        "fetch_timestamp": "2026-08-17T10:00:00+00:00",
        "text": "This is chunk text for testing. " * 5,
    }


def _ingested(url="https://example.com/a", n=1):
    """An ingested-outcome dict as WebDataSource.ingest_result returns it (#99)."""
    records = [_chunk(url=url, n=i) for i in range(n)]
    return {"status": "ingested", "url": url, "chunk_count": n,
            "records": records, "page_id": "web_abc123"}


def _outcome(status, url="https://example.com/a", **extra):
    """A non-ingested outcome dict (unchanged / duplicate / no_content / error)."""
    return {"status": status, "url": url, "chunk_count": 0, "records": [], **extra}


# ── ensure-index ──────────────────────────────────────────────────────────────

class TestEnsureIndex:
    def test_ok(self, runner, mock_source):
        mock_source.execute_query.return_value = None
        result = runner.invoke(cli, ["index", "ensure", "--collection", "test"])
        assert result.exit_code == 0
        assert "test" in result.output

    def test_json_output(self, runner, mock_source):
        mock_source.execute_query.return_value = None
        result = runner.invoke(cli, ["--format", "json", "index", "ensure", "--collection", "test"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["collection"] == "test"
        assert "status" not in data

    def test_idempotent(self, runner, mock_source):
        mock_source.execute_query.return_value = None
        runner.invoke(cli, ["index", "ensure", "--collection", "test"])
        result = runner.invoke(cli, ["index", "ensure", "--collection", "test"])
        assert result.exit_code == 0

    def test_error_propagates(self, runner, mock_source):
        mock_source.execute_query.side_effect = RuntimeError("Redis not available")
        result = runner.invoke(cli, ["index", "ensure"])
        assert result.exit_code == 1


# ── ingest ────────────────────────────────────────────────────────────────────

class TestIngest:
    def test_single_url(self, runner, mock_source):
        mock_source.ingest_result.return_value = _ingested(n=1)
        result = runner.invoke(cli, ["page", "import", "https://example.com/a"])
        assert result.exit_code == 0
        assert "1 chunk" in result.output
        assert "example.com" in result.output

    def test_single_url_json(self, runner, mock_source):
        mock_source.ingest_result.return_value = _ingested(n=2)
        result = runner.invoke(cli, ["--format", "json", "page", "import", "https://example.com/a"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["chunk_count"] == 2
        assert data["url"] == "https://example.com/a"
        assert data["page_id"] == "web_abc123"

    def test_multi_url(self, runner, mock_source):
        mock_source.ingest_result.side_effect = [
            _ingested(url="https://example.com/a"),
            _ingested(url="https://example.com/b"),
        ]
        result = runner.invoke(cli, ["page", "import", "https://example.com/a", "https://example.com/b"])
        assert result.exit_code == 0
        assert "2 pages" in result.output
        assert "✓" in result.output

    def test_multi_url_json(self, runner, mock_source):
        mock_source.ingest_result.side_effect = [
            _ingested(url="https://example.com/a"),
            _ingested(url="https://example.com/b"),
        ]
        result = runner.invoke(cli, ["--format", "json", "page", "import", "https://example.com/a", "https://example.com/b"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 2

    def test_unchanged_single_url_exits_zero(self, runner, mock_source):
        # Re-ingesting an unchanged page is a success, not an error (#99).
        mock_source.ingest_result.return_value = _outcome("unchanged", "https://example.com/u")
        result = runner.invoke(cli, ["page", "import", "https://example.com/u"])
        assert result.exit_code == 0
        assert "Unchanged" in result.output

    def test_duplicate_single_url_exits_zero(self, runner, mock_source):
        mock_source.ingest_result.return_value = _outcome(
            "duplicate", "https://example.com/d", duplicate_of="web_other"
        )
        result = runner.invoke(cli, ["page", "import", "https://example.com/d"])
        assert result.exit_code == 0
        assert "Duplicate" in result.output

    def test_no_content_single_url_exits_nonzero(self, runner, mock_source):
        mock_source.ingest_result.return_value = _outcome("no_content", "https://example.com/empty")
        result = runner.invoke(cli, ["page", "import", "https://example.com/empty"])
        assert result.exit_code == 1

    def test_skipped_url_shown_in_multi(self, runner, mock_source):
        mock_source.ingest_result.side_effect = [
            _ingested(url="https://example.com/a"),
            _outcome("no_content", "https://example.com/b"),
        ]
        result = runner.invoke(cli, ["page", "import", "https://example.com/a", "https://example.com/b"])
        assert result.exit_code == 1
        assert "✗" in result.output

    def test_unchanged_url_shown_in_multi(self, runner, mock_source):
        mock_source.ingest_result.side_effect = [
            _ingested(url="https://example.com/a"),
            _outcome("unchanged", "https://example.com/b"),
        ]
        result = runner.invoke(cli, ["page", "import", "https://example.com/a", "https://example.com/b"])
        assert result.exit_code == 0  # unchanged is not a failure
        assert "unchanged" in result.output.lower()

    def test_skipped_url_json_exits_nonzero(self, runner, mock_source):
        mock_source.ingest_result.side_effect = [
            _ingested(url="https://example.com/a"),
            _outcome("no_content", "https://example.com/b"),
        ]
        result = runner.invoke(cli, ["--format", "json", "page", "import", "https://example.com/a", "https://example.com/b"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert any(item.get("error") for item in data)

    def test_collection_passed_in_request(self, runner, mock_source):
        mock_source.ingest_result.return_value = _ingested()
        runner.invoke(cli, ["page", "import", "https://example.com/a", "--collection", "news"])
        assert mock_source.ingest_result.call_args.kwargs["collection"] == "news"


# ── crawl ─────────────────────────────────────────────────────────────────────

class TestCrawl:
    def test_basic(self, runner, mock_source):
        mock_source.execute_query.return_value = [
            _chunk(url="https://example.com/"),
            _chunk(url="https://example.com/about"),
        ]
        result = runner.invoke(cli, ["page", "crawl", "https://example.com"])
        assert result.exit_code == 0
        assert "2 pages" in result.output

    def test_json_output(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        result = runner.invoke(cli, ["--format", "json", "page", "crawl", "https://example.com"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "pages_imported" in data
        assert "chunk_count" in data
        assert "urls" in data
        assert "seed" not in data

    def test_depth_and_max_pages_passed(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        runner.invoke(cli, ["page", "crawl", "https://example.com", "--depth", "3", "--max-pages", "50"])
        call_args = mock_source.execute_query.call_args[0][0]
        assert call_args["depth"] == 3
        assert call_args["max_pages"] == 50

    def test_same_domain_only_default(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        runner.invoke(cli, ["page", "crawl", "https://example.com"])
        call_args = mock_source.execute_query.call_args[0][0]
        assert call_args["same_domain_only"] is True

    def test_allow_external_flag(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        runner.invoke(cli, ["page", "crawl", "https://example.com", "--allow-external"])
        call_args = mock_source.execute_query.call_args[0][0]
        assert call_args["same_domain_only"] is False


# ── feed ─────────────────────────────────────────────────────────────────────

class TestFeed:
    def test_basic(self, runner, mock_source):
        mock_source.execute_query.return_value = [
            _chunk(url="https://example.com/a1"),
            _chunk(url="https://example.com/a2"),
        ]
        result = runner.invoke(cli, ["feed", "import", "https://example.com/feed.xml"])
        assert result.exit_code == 0
        assert "2 articles" in result.output

    def test_json_output(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        result = runner.invoke(cli, ["--format", "json", "feed", "import", "https://example.com/feed.xml"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "articles_imported" in data
        assert "chunk_count" in data
        assert "urls" in data
        assert "feed" not in data

    def test_since_passed(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        runner.invoke(cli, ["feed", "import", "https://example.com/feed.xml", "--since", "2026-08-01T00:00:00Z"])
        call_args = mock_source.execute_query.call_args[0][0]
        assert call_args["since"] == "2026-08-01T00:00:00Z"

    def test_since_absent_when_not_provided(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        runner.invoke(cli, ["feed", "import", "https://example.com/feed.xml"])
        call_args = mock_source.execute_query.call_args[0][0]
        assert "since" not in call_args

    def test_max_entries_passed(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        runner.invoke(cli, ["feed", "import", "https://example.com/feed.xml", "--max-entries", "10"])
        call_args = mock_source.execute_query.call_args[0][0]
        assert call_args["max_entries"] == 10

    def test_empty_feed(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        result = runner.invoke(cli, ["feed", "import", "https://example.com/feed.xml"])
        assert result.exit_code == 0
        assert "0 articles" in result.output

    def test_single_article(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk(url="https://example.com/a1")]
        result = runner.invoke(cli, ["feed", "import", "https://example.com/feed.xml"])
        assert result.exit_code == 0
        assert "1 article " in result.output


# ── search ─────────────────────────────────────────────────────────────────────

class TestSearch:
    def test_results(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        result = runner.invoke(cli, ["page", "search", "climate change"])
        assert result.exit_code == 0
        assert "1 result" in result.output
        assert "example.com" in result.output

    def test_json_output(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        result = runner.invoke(cli, ["--format", "json", "page", "search", "climate change"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "results" in data
        assert data["count"] == 1
        assert data["results"][0]["url"] == "https://example.com/a"

    def test_no_results(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        result = runner.invoke(cli, ["page", "search", "nothing"])
        assert result.exit_code == 0
        assert "No results" in result.output

    def test_limit_passed(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        runner.invoke(cli, ["page", "search", "query", "--limit", "10"])
        call_args = mock_source.execute_query.call_args[0][0]
        assert call_args["k"] == 10

    def test_limit_short_flag(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        runner.invoke(cli, ["page", "search", "query", "-k", "7"])
        call_args = mock_source.execute_query.call_args[0][0]
        assert call_args["k"] == 7

    def test_section_title_shown(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        result = runner.invoke(cli, ["page", "search", "anything"])
        assert "Introduction" in result.output

    def test_error_propagates(self, runner, mock_source):
        mock_source.execute_query.side_effect = ValueError("No vector store configured")
        result = runner.invoke(cli, ["page", "search", "query"])
        assert result.exit_code == 1


# ── extract ───────────────────────────────────────────────────────────────────

_SCHEMA_JSON = '{"properties": {"title": {"type": "string"}, "price": {"type": "number"}}}'


class TestExtract:
    def test_basic(self, runner, mock_source):
        mock_source.execute_query.return_value = {"title": "Widget", "price": 29.99}
        result = runner.invoke(cli, ["page", "extract", "https://example.com", "--schema", _SCHEMA_JSON])
        assert result.exit_code == 0
        assert "Widget" in result.output

    def test_json_output(self, runner, mock_source):
        mock_source.execute_query.return_value = {"title": "Widget", "price": 29.99}
        result = runner.invoke(cli, ["--format", "json", "page", "extract", "https://example.com", "--schema", _SCHEMA_JSON])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["title"] == "Widget"
        assert data["price"] == 29.99

    def test_schema_passed_in_request(self, runner, mock_source):
        mock_source.execute_query.return_value = {}
        runner.invoke(cli, ["page", "extract", "https://example.com", "--schema", _SCHEMA_JSON])
        call_args = mock_source.execute_query.call_args[0][0]
        assert call_args["op"] == "WEB.EXTRACT"
        assert call_args["url"] == "https://example.com"
        assert isinstance(call_args["schema"], dict)

    def test_schema_file(self, runner, mock_source, tmp_path):
        schema_file = tmp_path / "schema.json"
        schema_file.write_text(_SCHEMA_JSON)
        mock_source.execute_query.return_value = {"title": "Widget"}
        result = runner.invoke(cli, ["page", "extract", "https://example.com", "--schema-file", str(schema_file)])
        assert result.exit_code == 0
        assert "Widget" in result.output

    def test_no_schema_exits_2(self, runner, mock_source):
        result = runner.invoke(cli, ["page", "extract", "https://example.com"])
        assert result.exit_code == 2

    def test_invalid_schema_json_exits_2(self, runner, mock_source):
        result = runner.invoke(cli, ["page", "extract", "https://example.com", "--schema", "not-json"])
        assert result.exit_code == 2

    def test_empty_result_shown(self, runner, mock_source):
        mock_source.execute_query.return_value = {}
        result = runner.invoke(cli, ["page", "extract", "https://example.com", "--schema", _SCHEMA_JSON])
        assert result.exit_code == 0
        assert "No fields" in result.output

    def test_error_propagates(self, runner, mock_source):
        mock_source.execute_query.side_effect = RuntimeError("fetch failed")
        result = runner.invoke(cli, ["page", "extract", "https://example.com", "--schema", _SCHEMA_JSON])
        assert result.exit_code == 1


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
        result = runner.invoke(cli, ["live", "search", "offshore wind energy"])
        assert result.exit_code == 0
        assert "1 result" in result.output

    def test_json_output(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        result = runner.invoke(cli, ["--format", "json", "live", "search", "offshore wind"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "results" in data
        assert data["count"] == 1

    def test_no_results(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        result = runner.invoke(cli, ["live", "search", "nothing"])
        assert result.exit_code == 0
        assert "No results" in result.output

    def test_urls_passed(self, runner, mock_source):
        mock_source.execute_query.return_value = []
        runner.invoke(cli, ["live", "search", "query", "--urls", "3"])
        call_args = mock_source.execute_query.call_args[0][0]
        assert call_args["k"] == 3

    def test_error_propagates(self, runner, mock_source):
        mock_source.execute_query.side_effect = RuntimeError("TAVILY_API_KEY not set")
        result = runner.invoke(cli, ["live", "search", "query"])
        assert result.exit_code == 1


# ── --log / --log-limit ───────────────────────────────────────────────────────

class TestLog:
    """--log routes diagnostics to stderr; JSON stdout stays clean."""

    @pytest.fixture(autouse=True)
    def _clean_loggers(self):
        """Remove any handlers added to the web-source logger between tests."""
        logger = logging.getLogger("blue_data_web_source")
        yield
        logger.handlers.clear()
        logger.setLevel(logging.WARNING)

    # --log is accepted on every network command
    def test_ingest_accepts_log(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        result = runner.invoke(cli, ["page", "import", "--log", "https://example.com/a"])
        assert result.exit_code == 0

    def test_crawl_accepts_log(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        result = runner.invoke(cli, ["page", "crawl", "--log", "https://example.com/"])
        assert result.exit_code == 0

    def test_feed_accepts_log(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        result = runner.invoke(cli, ["feed", "import", "--log", "https://example.com/rss.xml"])
        assert result.exit_code == 0

    def test_search_web_accepts_log(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        result = runner.invoke(cli, ["live", "search", "--log", "climate policy"])
        assert result.exit_code == 0

    def test_extract_accepts_log(self, runner, mock_source):
        mock_source.execute_query.return_value = {"title": "x"}
        result = runner.invoke(cli, [
            "page", "extract", "--log",
            "--schema", '{"properties": {"title": {"type": "string"}}}',
            "https://example.com/",
        ])
        assert result.exit_code == 0

    # -l is the short alias
    def test_short_flag(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        result = runner.invoke(cli, ["page", "import", "-l", "https://example.com/a"])
        assert result.exit_code == 0

    # --log does not corrupt JSON stdout (source is mocked → no log events fire)
    def test_log_does_not_corrupt_json_stdout(self, runner, mock_source):
        mock_source.ingest_result.return_value = _ingested(n=2)
        result = runner.invoke(cli, ["--format", "json", "page", "import", "--log", "https://example.com/a"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["chunk_count"] == 2

    # _configure_logging unit tests
    def test_configure_logging_adds_stderr_handler(self):
        from blue_data_web_cli.main import _configure_logging
        logger = logging.getLogger("blue_data_web_source")
        assert not logger.handlers
        _configure_logging(True, 200)
        assert any(isinstance(h, logging.StreamHandler) for h in logger.handlers)

    def test_configure_logging_noop_when_false(self):
        from blue_data_web_cli.main import _configure_logging
        logger = logging.getLogger("blue_data_web_source")
        _configure_logging(False, 200)
        assert not logger.handlers

    def test_configure_logging_routes_to_stderr(self):
        from blue_data_web_cli.main import _configure_logging
        logger = logging.getLogger("blue_data_web_source")
        _configure_logging(True, 200)
        handler = logger.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        assert handler.stream is sys.stderr

    def test_log_limit_truncates(self):
        from blue_data_web_cli.main import _TruncatingFormatter
        record = logging.LogRecord(
            name="blue_data_web_source.pipeline",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="ingesting " + "x" * 300,
            args=(),
            exc_info=None,
        )
        formatter = _TruncatingFormatter(50)
        line = formatter.format(record)
        assert len(line) == 51  # 50 chars + "…"
        assert line.endswith("…")

    def test_log_limit_option(self, runner, mock_source):
        mock_source.execute_query.return_value = [_chunk()]
        result = runner.invoke(cli, [
            "page", "import", "--log", "--log-limit", "100", "https://example.com/a",
        ])
        assert result.exit_code == 0
