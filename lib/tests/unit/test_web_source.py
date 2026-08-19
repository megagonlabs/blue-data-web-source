from unittest.mock import MagicMock

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
        "https://example.com/feed.xml", max_items=None, collection=None
    )
    assert result == [{"chunk_id": "web_abc_chunk_0000"}]


def test_ingest_feed_raises_without_url():
    source = _make_source()
    with pytest.raises(ValueError, match="url"):
        source.execute_query({"op": "WEB.INGEST_FEED"})


def test_ingest_feed_passes_max_items_and_collection():
    source = _make_source()
    source._pipeline.ingest_feed.return_value = []

    source.execute_query({
        "op": "WEB.INGEST_FEED",
        "url": "https://example.com/feed.xml",
        "max_items": 5,
        "collection": "news",
    })
    source._pipeline.ingest_feed.assert_called_once_with(
        "https://example.com/feed.xml", max_items=5, collection="news"
    )


def test_unknown_op_raises():
    source = _make_source()
    with pytest.raises(ValueError, match="Unsupported op"):
        source.execute_query({"op": "WEB.UNKNOWN"})


def test_web_data_source_default_timeout_is_30_seconds():
    source = WebDataSource()
    assert source._pipeline._fetcher._timeout == 30.0
