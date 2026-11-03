from unittest.mock import MagicMock, patch

import pytest

from blue_data_web_source.source.web.web_source import WebDataSource


def _make_source(**kwargs) -> WebDataSource:
    source = WebDataSource(**kwargs)
    source._pipeline = MagicMock()
    return source


def test_ingest_dispatches_to_pipeline():
    source = _make_source()
    source._pipeline.ingest.return_value = [{"chunk_id": "web_abc_chunk_0000"}]

    result = source.execute_query({"op": "WEB.INGEST", "url": "https://example.com"})

    source._pipeline.ingest.assert_called_once_with("https://example.com", collection=None)
    assert result == [{"chunk_id": "web_abc_chunk_0000"}]


def test_ingest_passes_collection():
    source = _make_source()
    source._pipeline.ingest.return_value = []

    source.execute_query({"op": "WEB.INGEST", "url": "https://example.com", "collection": "news"})
    source._pipeline.ingest.assert_called_once_with("https://example.com", collection="news")


def test_ingest_raises_without_url():
    source = _make_source()
    with pytest.raises(ValueError, match="url"):
        source.execute_query({"op": "WEB.INGEST"})


def test_search_dispatches_to_pipeline():
    source = _make_source()
    source._pipeline.search.return_value = [{"text": "match"}]

    result = source.execute_query({"op": "WEB.SEARCH", "query": "climate", "k": 3})

    source._pipeline.search.assert_called_once_with("climate", k=3, collection=None)
    assert result == [{"text": "match"}]


def test_search_default_k():
    source = _make_source()
    source._pipeline.search.return_value = []

    source.execute_query({"op": "WEB.SEARCH", "query": "test"})
    source._pipeline.search.assert_called_once_with("test", k=5, collection=None)


def test_search_raises_without_query():
    source = _make_source()
    with pytest.raises(ValueError, match="query"):
        source.execute_query({"op": "WEB.SEARCH"})


def test_ensure_index_dispatches():
    source = _make_source()
    source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": "news"})
    source._pipeline.ensure_index.assert_called_once_with(collection="news")


def test_collection_override_takes_priority():
    source = _make_source()
    source._pipeline.ingest.return_value = []

    source.execute_query(
        {"op": "WEB.INGEST", "url": "https://example.com", "collection": "from_request"},
        collection="from_override",
    )
    source._pipeline.ingest.assert_called_once_with(
        "https://example.com", collection="from_override"
    )


def test_ingest_feed_dispatches_to_pipeline():
    source = _make_source()
    source._pipeline.ingest_feed.return_value = [{"chunk_id": "web_abc_chunk_0000"}]

    result = source.execute_query({
        "op": "WEB.INGEST_FEED",
        "url": "https://example.com/feed.xml",
    })

    source._pipeline.ingest_feed.assert_called_once_with(
        "https://example.com/feed.xml", max_entries=None, since=None, collection=None
    )
    assert result == [{"chunk_id": "web_abc_chunk_0000"}]


def test_ingest_feed_raises_without_url():
    source = _make_source()
    with pytest.raises(ValueError, match="url"):
        source.execute_query({"op": "WEB.INGEST_FEED"})


def test_ingest_feed_passes_max_entries_and_collection():
    source = _make_source()
    source._pipeline.ingest_feed.return_value = []

    source.execute_query({
        "op": "WEB.INGEST_FEED",
        "url": "https://example.com/feed.xml",
        "max_entries": 5,
        "collection": "news",
    })
    source._pipeline.ingest_feed.assert_called_once_with(
        "https://example.com/feed.xml", max_entries=5, since=None, collection="news"
    )


def test_ingest_feed_parses_since_string_to_datetime():
    from datetime import datetime, timezone
    source = _make_source()
    source._pipeline.ingest_feed.return_value = []

    source.execute_query({
        "op": "WEB.INGEST_FEED",
        "url": "https://example.com/feed.xml",
        "since": "2026-08-10T00:00:00Z",
    })

    call_kwargs = source._pipeline.ingest_feed.call_args[1]
    since = call_kwargs["since"]
    assert isinstance(since, datetime)
    assert since.tzinfo is not None
    assert since.year == 2026 and since.month == 8 and since.day == 10


def test_unknown_op_raises():
    source = _make_source()
    with pytest.raises(ValueError, match="Unsupported op"):
        source.execute_query({"op": "WEB.UNKNOWN"})


def test_web_data_source_default_timeout_is_30_seconds():
    source = WebDataSource()
    assert source._pipeline._fetcher._timeout == 30.0


# ---------------------------------------------------------------------------
# WEB.EXTRACT
# ---------------------------------------------------------------------------

_SCHEMA = {"properties": {"title": {"type": "string"}, "price": {"type": "number"}}}


def _make_source_with_real_pipeline() -> WebDataSource:
    """Source where pipeline internals are individually mocked (not the whole pipeline)."""
    source = WebDataSource()
    source._pipeline._fetcher = MagicMock()
    source._pipeline._extractor = MagicMock()
    return source


def _make_fetch_result(url: str = "https://example.com") -> MagicMock:
    fr = MagicMock()
    fr.html = "<html><body><p>Some product content here.</p></body></html>"
    fr.final_url = url
    return fr


def _make_extract_result(text: str = "Product title is Widget. Price is 29.99.") -> MagicMock:
    er = MagicMock()
    er.text = text
    return er


def test_extract_raises_without_url():
    source = _make_source()
    with pytest.raises(ValueError, match="url"):
        source.execute_query({"op": "WEB.EXTRACT", "schema": _SCHEMA})


def test_extract_raises_without_schema():
    source = _make_source()
    with pytest.raises(ValueError, match="schema"):
        source.execute_query({"op": "WEB.EXTRACT", "url": "https://example.com"})


def test_extract_rejects_too_many_urls():
    # One LLM call per URL — bound the batch to limit model cost / fetch fan-out (#112).
    source = _make_source()
    too_many = [f"https://example.com/{i}" for i in range(source._MAX_EXTRACT_URLS + 1)]
    with pytest.raises(ValueError, match="at most"):
        source.execute_query({"op": "WEB.EXTRACT", "urls": too_many, "schema": _SCHEMA})


def test_extract_calls_structured_field_extractor():
    source = _make_source_with_real_pipeline()
    source._pipeline._fetcher.fetch.return_value = _make_fetch_result()
    source._pipeline._extractor.extract.return_value = _make_extract_result()

    mock_extractor_instance = MagicMock()
    mock_extractor_instance.extract.return_value = {"title": "Widget", "price": 29.99}

    with patch(
        "blue_data_doc_source.source.document.schema.StructuredFieldExtractor",
        return_value=mock_extractor_instance,
    ) as MockSFE:
        result = source.execute_query({
            "op": "WEB.EXTRACT",
            "url": "https://example.com",
            "schema": _SCHEMA,
        })

    MockSFE.assert_called_once_with(schema=_SCHEMA)
    mock_extractor_instance.extract.assert_called_once()
    assert result == {"title": "Widget", "price": 29.99}


def test_extract_returns_empty_when_no_content():
    source = _make_source_with_real_pipeline()
    source._pipeline._fetcher.fetch.return_value = _make_fetch_result()
    source._pipeline._extractor.extract.return_value = None

    result = source.execute_query({
        "op": "WEB.EXTRACT",
        "url": "https://example.com",
        "schema": _SCHEMA,
    })

    assert result == {}


def test_extract_returns_empty_when_text_blank():
    source = _make_source_with_real_pipeline()
    source._pipeline._fetcher.fetch.return_value = _make_fetch_result()
    source._pipeline._extractor.extract.return_value = _make_extract_result(text="   ")

    result = source.execute_query({
        "op": "WEB.EXTRACT",
        "url": "https://example.com",
        "schema": _SCHEMA,
    })

    assert result == {}


def test_extract_urls_returns_list():
    source = _make_source_with_real_pipeline()
    source._pipeline._fetcher.fetch.return_value = _make_fetch_result()
    source._pipeline._extractor.extract.return_value = _make_extract_result()

    mock_extractor_instance = MagicMock()
    mock_extractor_instance.extract.return_value = {"title": "Widget", "price": 29.99}

    with patch(
        "blue_data_doc_source.source.document.schema.StructuredFieldExtractor",
        return_value=mock_extractor_instance,
    ):
        result = source.execute_query({
            "op": "WEB.EXTRACT",
            "urls": ["https://example.com/a", "https://example.com/b"],
            "schema": _SCHEMA,
        })

    assert isinstance(result, list)
    assert len(result) == 2
    assert all("url" in r for r in result)


def test_extract_urls_includes_url_in_each_result():
    source = _make_source_with_real_pipeline()
    source._pipeline._fetcher.fetch.return_value = _make_fetch_result()
    source._pipeline._extractor.extract.return_value = _make_extract_result()

    mock_extractor_instance = MagicMock()
    mock_extractor_instance.extract.return_value = {"title": "Widget"}

    with patch(
        "blue_data_doc_source.source.document.schema.StructuredFieldExtractor",
        return_value=mock_extractor_instance,
    ):
        result = source.execute_query({
            "op": "WEB.EXTRACT",
            "urls": ["https://example.com/a"],
            "schema": _SCHEMA,
        })

    assert isinstance(result, list)
    assert result[0]["url"] == "https://example.com/a"


def test_extract_import_error_when_doc_source_missing():
    source = _make_source_with_real_pipeline()
    source._pipeline._fetcher.fetch.return_value = _make_fetch_result()
    source._pipeline._extractor.extract.return_value = _make_extract_result()

    with patch.dict("sys.modules", {"blue_data_doc_source.source.document.schema": None}):
        with pytest.raises((ImportError, TypeError)):
            source.execute_query({
                "op": "WEB.EXTRACT",
                "url": "https://example.com",
                "schema": _SCHEMA,
            })


# ── from_env() freshness-store configuration (#32 follow-up) ──────────────────
#
# The CLI, REST API, and MCP server all build their source via from_env(). It
# configures a freshness store so those surfaces skip re-embedding unchanged pages
# and delete stale surplus chunks when a page shrinks (#32). The freshness store's
# persistence is paired to the vector store's: in-memory vectors → in-memory
# freshness (#94), persistent (Redis) vectors → file freshness.

import os

from blue_data_web_source.source.web.freshness import FileFreshnessStore, MemoryFreshnessStore


@pytest.fixture(autouse=True)
def _clear_freshness_env(monkeypatch):
    for var in ("BLUE_WEB_FRESHNESS", "BLUE_WEB_FRESHNESS_PATH", "BLUE_WEB_REDIS_HOST"):
        monkeypatch.delenv(var, raising=False)
    yield


def test_from_env_defaults_to_memory_freshness_with_memory_vectors(monkeypatch):
    # #94: the default config (no Redis) uses an in-memory vector store, so the
    # freshness store must be in-memory too — a persistent freshness store would,
    # after a restart, skip re-ingesting a known page while its vectors are gone.
    source = WebDataSource.from_env()
    assert type(source._pipeline._vector_source).__name__ == "MemoryVectorSource"
    assert isinstance(source._pipeline._freshness_store, MemoryFreshnessStore)


def test_from_env_uses_file_freshness_with_redis_vectors(monkeypatch, tmp_path):
    # With a persistent (Redis) vector store, freshness stays file-backed.
    monkeypatch.setenv("BLUE_WEB_REDIS_HOST", "localhost")
    path = tmp_path / "freshness.json"
    monkeypatch.setenv("BLUE_WEB_FRESHNESS_PATH", str(path))
    source = WebDataSource.from_env()
    store = source._pipeline._freshness_store
    assert isinstance(store, FileFreshnessStore)
    assert store._path == str(path)


def test_from_env_explicit_file_freshness_overrides_pairing(monkeypatch, tmp_path):
    # An explicit BLUE_WEB_FRESHNESS always wins, even with memory vectors.
    monkeypatch.setenv("BLUE_WEB_FRESHNESS", "file")
    monkeypatch.setenv("HOME", str(tmp_path))
    source = WebDataSource.from_env()
    store = source._pipeline._freshness_store
    assert isinstance(store, FileFreshnessStore)
    assert store._path == str(tmp_path / ".blue-web" / "freshness.json")
    assert (tmp_path / ".blue-web").is_dir()


def test_from_env_freshness_none_disables(monkeypatch):
    monkeypatch.setenv("BLUE_WEB_FRESHNESS", "none")
    source = WebDataSource.from_env()
    assert source._pipeline._freshness_store is None


def test_from_env_freshness_memory(monkeypatch):
    monkeypatch.setenv("BLUE_WEB_FRESHNESS", "memory")
    source = WebDataSource.from_env()
    assert isinstance(source._pipeline._freshness_store, MemoryFreshnessStore)


def test_from_env_file_store_creates_missing_parent_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("BLUE_WEB_FRESHNESS", "file")
    path = tmp_path / "nested" / "dir" / "freshness.json"
    monkeypatch.setenv("BLUE_WEB_FRESHNESS_PATH", str(path))
    source = WebDataSource.from_env()
    assert isinstance(source._pipeline._freshness_store, FileFreshnessStore)
    assert path.parent.is_dir()


def test_from_env_file_store_falls_back_to_memory_when_unwritable(monkeypatch, tmp_path):
    # Point at a path whose parent cannot be created (a file is in the way), so
    # makedirs raises OSError — from_env must not crash; it degrades to memory.
    monkeypatch.setenv("BLUE_WEB_FRESHNESS", "file")
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.setenv("BLUE_WEB_FRESHNESS_PATH", str(blocker / "freshness.json"))
    source = WebDataSource.from_env()
    assert isinstance(source._pipeline._freshness_store, MemoryFreshnessStore)


# ── from_env() vector-store configuration (#39) ──────────────────────────────
#
# Exercised against the REAL blue-data vector classes (not mocks): from_env()
# must build a searchable source in both configurations — RedisVectorSource when
# BLUE_WEB_REDIS_HOST is set (correct constructor), MemoryVectorSource otherwise.

from blue_data.source.vector.memory_vector_source import MemoryVectorSource


def test_from_env_without_redis_uses_memory_vector_source(monkeypatch):
    monkeypatch.setenv("BLUE_WEB_FRESHNESS", "none")  # keep the test off the home dir
    source = WebDataSource.from_env()
    assert isinstance(source._pipeline._vector_source, MemoryVectorSource)


def test_from_env_without_redis_search_returns_empty_not_error(monkeypatch):
    # The reported symptom: without Redis, search raised "A vector_source is
    # required for search". It must now return an empty list on an empty store.
    monkeypatch.setenv("BLUE_WEB_FRESHNESS", "none")
    source = WebDataSource.from_env()
    source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": "web"})
    results = source.execute_query({"op": "WEB.SEARCH", "query": "hello", "collection": "web"})
    assert results == []


def test_from_env_with_redis_builds_redis_vector_source(monkeypatch):
    # Real RedisVectorSource; no server needed — the client is lazy and we only
    # inspect the connection properties. Before the fix this raised TypeError.
    redis_source = pytest.importorskip(
        "blue_data.source.vector.redis.redis_vector_source"
    )
    monkeypatch.setenv("BLUE_WEB_FRESHNESS", "none")
    monkeypatch.setenv("BLUE_WEB_REDIS_HOST", "127.0.0.1")
    monkeypatch.setenv("BLUE_WEB_REDIS_PORT", "6380")
    source = WebDataSource.from_env()
    vs = source._pipeline._vector_source
    assert isinstance(vs, redis_source.RedisVectorSource)
    conn = vs.properties["connection"]
    assert conn["host"] == "127.0.0.1"
    assert conn["port"] == 6380
    assert conn["scan_prefix"] == "web:"


def test_from_env_redis_prefix_override(monkeypatch):
    pytest.importorskip("blue_data.source.vector.redis.redis_vector_source")
    monkeypatch.setenv("BLUE_WEB_FRESHNESS", "none")
    monkeypatch.setenv("BLUE_WEB_REDIS_HOST", "127.0.0.1")
    monkeypatch.setenv("BLUE_WEB_REDIS_PREFIX", "myapp:")
    source = WebDataSource.from_env()
    assert source._pipeline._vector_source.properties["connection"]["scan_prefix"] == "myapp:"


def test_ensure_index_returns_list_of_dict():
    # #50: execute_query must return list[dict]; ENSURE_INDEX used to return None.
    source = _make_source()
    result = source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": "web"})
    assert result == [{"status": "ok", "collection": "web"}]


def test_ensure_index_default_collection_in_result():
    source = _make_source()
    result = source.execute_query({"op": "WEB.ENSURE_INDEX"})
    assert isinstance(result, list) and result[0]["status"] == "ok"
    assert result[0]["collection"] == source.default_collection
