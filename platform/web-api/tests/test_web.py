"""Tests for web-api routes."""
from unittest.mock import MagicMock


# ── /web/health ───────────────────────────────────────────────────────────────

class TestHealth:
    def test_returns_ok(self, client):
        r = client.get("/web/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


# ── /web/ensure-index ─────────────────────────────────────────────────────────

class TestEnsureIndex:
    def test_calls_execute_query(self, client, mock_source):
        mock_source.execute_query.return_value = None
        r = client.post("/web/ensure-index", json={})
        assert r.status_code == 200
        mock_source.execute_query.assert_called_once_with({"op": "WEB.ENSURE_INDEX", "collection": None})

    def test_returns_collection(self, client, mock_source):
        mock_source.execute_query.return_value = None
        r = client.post("/web/ensure-index", json={"collection": "news"})
        assert r.json()["collection"] == "news"

    def test_defaults_to_pipeline_collection(self, client, mock_source):
        mock_source.execute_query.return_value = None
        r = client.post("/web/ensure-index", json={})
        assert r.json()["collection"] == "web"


# ── /web/ingest ───────────────────────────────────────────────────────────────

_CHUNK = {
    "url": "https://example.com/article",
    "domain": "example.com",
    "chunk_id": "web_abc_chunk_0000",
    "chunk_index": 0,
    "text": "article body",
    "fetch_timestamp": "2026-08-20T00:00:00+00:00",
}


class TestIngest:
    def test_single_url(self, client, mock_source):
        mock_source.execute_query.return_value = [_CHUNK]
        r = client.post("/web/ingest", json={"url": "https://example.com/article"})
        assert r.status_code == 200
        body = r.json()
        assert body["chunk_count"] == 1
        assert body["pages_ingested"] == 1
        assert body["urls"] == ["https://example.com/article"]

    def test_passes_url_to_execute_query(self, client, mock_source):
        mock_source.execute_query.return_value = [_CHUNK]
        client.post("/web/ingest", json={"url": "https://example.com/article"})
        call_arg = mock_source.execute_query.call_args[0][0]
        assert call_arg["op"] == "WEB.INGEST"
        assert call_arg["url"] == "https://example.com/article"

    def test_urls_list(self, client, mock_source):
        chunk2 = {**_CHUNK, "url": "https://example.com/b", "chunk_id": "web_def_chunk_0000"}
        mock_source.execute_query.return_value = [_CHUNK, chunk2]
        r = client.post("/web/ingest", json={"urls": ["https://example.com/article", "https://example.com/b"]})
        assert r.json()["pages_ingested"] == 2

    def test_passes_urls_to_execute_query(self, client, mock_source):
        mock_source.execute_query.return_value = []
        client.post("/web/ingest", json={"urls": ["https://a.com", "https://b.com"]})
        call_arg = mock_source.execute_query.call_args[0][0]
        assert call_arg["urls"] == ["https://a.com", "https://b.com"]
        assert "url" not in call_arg

    def test_collection_passed_through(self, client, mock_source):
        mock_source.execute_query.return_value = [_CHUNK]
        client.post("/web/ingest", json={"url": "https://example.com/article", "collection": "news"})
        call_arg = mock_source.execute_query.call_args[0][0]
        assert call_arg["collection"] == "news"

    def test_missing_url_and_urls_returns_422(self, client, mock_source):
        r = client.post("/web/ingest", json={})
        assert r.status_code == 422

    def test_empty_result_returns_zero_chunks(self, client, mock_source):
        mock_source.execute_query.return_value = []
        r = client.post("/web/ingest", json={"url": "https://example.com/nav-only"})
        assert r.json()["chunk_count"] == 0
        assert r.json()["pages_ingested"] == 0


# ── /web/crawl ────────────────────────────────────────────────────────────────

class TestCrawl:
    def test_passes_params_to_execute_query(self, client, mock_source):
        mock_source.execute_query.return_value = [_CHUNK]
        client.post("/web/crawl", json={"url": "https://docs.example.com/", "depth": 2, "max_pages": 20})
        call_arg = mock_source.execute_query.call_args[0][0]
        assert call_arg["op"] == "WEB.CRAWL"
        assert call_arg["depth"] == 2
        assert call_arg["max_pages"] == 20
        assert call_arg["same_domain_only"] is True

    def test_returns_summary(self, client, mock_source):
        mock_source.execute_query.return_value = [_CHUNK, {**_CHUNK, "chunk_id": "web_abc_chunk_0001"}]
        r = client.post("/web/crawl", json={"url": "https://docs.example.com/"})
        body = r.json()
        assert body["chunk_count"] == 2
        assert body["pages_ingested"] == 1

    def test_same_domain_only_false(self, client, mock_source):
        mock_source.execute_query.return_value = []
        client.post("/web/crawl", json={"url": "https://example.com/", "same_domain_only": False})
        call_arg = mock_source.execute_query.call_args[0][0]
        assert call_arg["same_domain_only"] is False

    def test_collection_passed_through(self, client, mock_source):
        mock_source.execute_query.return_value = []
        client.post("/web/crawl", json={"url": "https://example.com/", "collection": "docs"})
        call_arg = mock_source.execute_query.call_args[0][0]
        assert call_arg["collection"] == "docs"


# ── /web/feed ─────────────────────────────────────────────────────────────────

class TestFeed:
    def test_passes_url_to_execute_query(self, client, mock_source):
        mock_source.execute_query.return_value = [_CHUNK]
        client.post("/web/feed", json={"url": "https://example.com/feed.xml"})
        call_arg = mock_source.execute_query.call_args[0][0]
        assert call_arg["op"] == "WEB.INGEST_FEED"
        assert call_arg["url"] == "https://example.com/feed.xml"

    def test_since_passed_through(self, client, mock_source):
        mock_source.execute_query.return_value = []
        client.post("/web/feed", json={"url": "https://example.com/feed.xml", "since": "2026-08-01T00:00:00Z"})
        call_arg = mock_source.execute_query.call_args[0][0]
        assert call_arg["since"] == "2026-08-01T00:00:00Z"

    def test_max_entries_passed_through(self, client, mock_source):
        mock_source.execute_query.return_value = []
        client.post("/web/feed", json={"url": "https://example.com/feed.xml", "max_entries": 10})
        call_arg = mock_source.execute_query.call_args[0][0]
        assert call_arg["max_entries"] == 10

    def test_since_omitted_when_not_provided(self, client, mock_source):
        mock_source.execute_query.return_value = []
        client.post("/web/feed", json={"url": "https://example.com/feed.xml"})
        call_arg = mock_source.execute_query.call_args[0][0]
        assert "since" not in call_arg

    def test_returns_summary(self, client, mock_source):
        mock_source.execute_query.return_value = [_CHUNK]
        r = client.post("/web/feed", json={"url": "https://example.com/feed.xml"})
        body = r.json()
        assert body["chunk_count"] == 1
        assert body["articles_ingested"] == 1


# ── /web/extract ──────────────────────────────────────────────────────────────

_SCHEMA = {"type": "object", "properties": {"headline": {"type": "string"}}}


class TestExtract:
    def test_single_url_returns_dict(self, client, mock_source):
        mock_source.execute_query.return_value = {"headline": "Test Article"}
        r = client.post("/web/extract", json={"url": "https://example.com/article", "schema": _SCHEMA})
        assert r.status_code == 200
        assert r.json() == {"headline": "Test Article"}

    def test_passes_url_and_schema(self, client, mock_source):
        mock_source.execute_query.return_value = {}
        client.post("/web/extract", json={"url": "https://example.com/article", "schema": _SCHEMA})
        call_arg = mock_source.execute_query.call_args[0][0]
        assert call_arg["op"] == "WEB.EXTRACT"
        assert call_arg["url"] == "https://example.com/article"
        assert call_arg["schema"] == _SCHEMA

    def test_urls_list_returns_list(self, client, mock_source):
        mock_source.execute_query.return_value = [
            {"headline": "A", "url": "https://example.com/a"},
            {"headline": "B", "url": "https://example.com/b"},
        ]
        r = client.post("/web/extract", json={
            "urls": ["https://example.com/a", "https://example.com/b"],
            "schema": _SCHEMA,
        })
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_passes_urls_not_url(self, client, mock_source):
        mock_source.execute_query.return_value = []
        client.post("/web/extract", json={"urls": ["https://a.com"], "schema": _SCHEMA})
        call_arg = mock_source.execute_query.call_args[0][0]
        assert "urls" in call_arg
        assert "url" not in call_arg

    def test_missing_url_returns_422(self, client, mock_source):
        r = client.post("/web/extract", json={"schema": _SCHEMA})
        assert r.status_code == 422

    def test_missing_schema_returns_422(self, client, mock_source):
        r = client.post("/web/extract", json={"url": "https://example.com/article"})
        assert r.status_code == 422

    def test_value_error_returns_400(self, client, mock_source):
        mock_source.execute_query.side_effect = ValueError("bad schema")
        r = client.post("/web/extract", json={"url": "https://example.com/article", "schema": _SCHEMA})
        assert r.status_code == 400
        assert "bad schema" in r.json()["detail"]


# ── /web/search ───────────────────────────────────────────────────────────────

_RESULT = {"chunk_id": "web_abc_chunk_0000", "text": "article body", "url": "https://example.com/article", "score": 0.9}


class TestSearch:
    def test_passes_query_to_execute_query(self, client, mock_source):
        mock_source.execute_query.return_value = [_RESULT]
        client.post("/web/search", json={"query": "climate policy"})
        call_arg = mock_source.execute_query.call_args[0][0]
        assert call_arg["op"] == "WEB.SEARCH"
        assert call_arg["query"] == "climate policy"

    def test_default_k_is_5(self, client, mock_source):
        mock_source.execute_query.return_value = []
        client.post("/web/search", json={"query": "test"})
        call_arg = mock_source.execute_query.call_args[0][0]
        assert call_arg["k"] == 5

    def test_custom_k(self, client, mock_source):
        mock_source.execute_query.return_value = []
        client.post("/web/search", json={"query": "test", "k": 10})
        call_arg = mock_source.execute_query.call_args[0][0]
        assert call_arg["k"] == 10

    def test_returns_results_and_count(self, client, mock_source):
        mock_source.execute_query.return_value = [_RESULT, _RESULT]
        r = client.post("/web/search", json={"query": "test"})
        body = r.json()
        assert body["count"] == 2
        assert len(body["results"]) == 2

    def test_value_error_returns_400(self, client, mock_source):
        mock_source.execute_query.side_effect = ValueError("no embedding model")
        r = client.post("/web/search", json={"query": "test"})
        assert r.status_code == 400

    def test_collection_passed_through(self, client, mock_source):
        mock_source.execute_query.return_value = []
        client.post("/web/search", json={"query": "test", "collection": "news"})
        call_arg = mock_source.execute_query.call_args[0][0]
        assert call_arg["collection"] == "news"


# ── /web/search-web ───────────────────────────────────────────────────────────

class TestSearchWeb:
    def test_passes_query_to_execute_query(self, client, mock_source):
        mock_source.execute_query.return_value = [_RESULT]
        client.post("/web/search-web", json={"query": "LLM alignment 2026"})
        call_arg = mock_source.execute_query.call_args[0][0]
        assert call_arg["op"] == "WEB.SEARCH_WEB"
        assert call_arg["query"] == "LLM alignment 2026"

    def test_default_k_and_top_k(self, client, mock_source):
        mock_source.execute_query.return_value = []
        client.post("/web/search-web", json={"query": "test"})
        call_arg = mock_source.execute_query.call_args[0][0]
        assert call_arg["k"] == 5
        assert call_arg["top_k"] == 5

    def test_custom_k_and_top_k(self, client, mock_source):
        mock_source.execute_query.return_value = []
        client.post("/web/search-web", json={"query": "test", "k": 8, "top_k": 3})
        call_arg = mock_source.execute_query.call_args[0][0]
        assert call_arg["k"] == 8
        assert call_arg["top_k"] == 3

    def test_returns_results_and_count(self, client, mock_source):
        mock_source.execute_query.return_value = [_RESULT]
        r = client.post("/web/search-web", json={"query": "test"})
        body = r.json()
        assert body["count"] == 1
        assert body["results"][0]["url"] == "https://example.com/article"

    def test_value_error_returns_400(self, client, mock_source):
        mock_source.execute_query.side_effect = ValueError("no search provider")
        r = client.post("/web/search-web", json={"query": "test"})
        assert r.status_code == 400
