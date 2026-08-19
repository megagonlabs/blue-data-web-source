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
