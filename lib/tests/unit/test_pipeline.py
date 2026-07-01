from unittest.mock import MagicMock, patch

import pytest

from blue_web_source.source.web.fetcher import FetchResult
from blue_web_source.source.web.extractor import ExtractResult
from blue_web_source.source.web.pipeline import WebPipeline


def _make_fetch_result(url: str = "https://example.com") -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        html="<html>ignored</html>",
        status_code=200,
        content_type="text/html",
    )


def _make_extract_result(url: str = "https://example.com", text: str = None) -> ExtractResult:
    if text is None:
        text = "First paragraph with some words. " * 30
    return ExtractResult(
        url=url,
        text=text,
        title="Test Page",
        author="Jane Doe",
        date="2026-06-01",
        description="A test page.",
        language="en",
    )


def _make_pipeline(**kwargs) -> WebPipeline:
    p = WebPipeline(**kwargs)
    p._fetcher = MagicMock()
    p._fetcher.fetch.return_value = _make_fetch_result()
    p._extractor = MagicMock()
    p._extractor.extract.return_value = _make_extract_result()
    return p


def test_ingest_returns_list_of_dicts():
    pipeline = _make_pipeline()
    records = pipeline.ingest("https://example.com")
    assert isinstance(records, list)
    assert len(records) > 0
    assert all(isinstance(r, dict) for r in records)


def test_ingest_chunk_fields():
    pipeline = _make_pipeline()
    records = pipeline.ingest("https://example.com")
    first = records[0]
    assert first["url"] == "https://example.com"
    assert first["domain"] == "example.com"
    assert "chunk_id" in first
    assert first["chunk_index"] == 0
    assert "text" in first
    assert "fetch_timestamp" in first


def test_ingest_empty_when_extractor_returns_none():
    pipeline = _make_pipeline()
    pipeline._extractor.extract.return_value = None
    records = pipeline.ingest("https://example.com")
    assert records == []


def test_ingest_empty_when_text_blank():
    pipeline = _make_pipeline()
    pipeline._extractor.extract.return_value = _make_extract_result(text="   ")
    records = pipeline.ingest("https://example.com")
    assert records == []


def test_ingest_embeds_when_model_set():
    pipeline = _make_pipeline()
    mock_model = MagicMock()
    mock_model.embed_text.return_value = [0.1] * 8
    pipeline._embedding_model = mock_model

    records = pipeline.ingest("https://example.com")
    assert all("embedding" in r for r in records)
    assert records[0]["embedding"] == [0.1] * 8


def test_ingest_upserts_to_vector_source():
    pipeline = _make_pipeline()
    mock_model = MagicMock()
    mock_model.embed_text.return_value = [0.1] * 8
    pipeline._embedding_model = mock_model
    mock_vs = MagicMock()
    pipeline._vector_source = mock_vs

    records = pipeline.ingest("https://example.com", collection="news")
    assert mock_vs.upsert.call_count == len(records)
    call_kwargs = mock_vs.upsert.call_args_list[0][1]
    assert call_kwargs["collection"] == "news"


def test_ingest_no_upsert_without_embedding_model():
    pipeline = _make_pipeline()
    mock_vs = MagicMock()
    pipeline._vector_source = mock_vs

    pipeline.ingest("https://example.com")
    mock_vs.upsert.assert_not_called()


def test_ingest_collection_override():
    pipeline = _make_pipeline(collection="default")
    mock_model = MagicMock()
    mock_model.embed_text.return_value = [0.0]
    pipeline._embedding_model = mock_model
    mock_vs = MagicMock()
    pipeline._vector_source = mock_vs

    pipeline.ingest("https://example.com", collection="override")
    call_kwargs = mock_vs.upsert.call_args_list[0][1]
    assert call_kwargs["collection"] == "override"


def test_search_raises_without_embedding_model():
    pipeline = _make_pipeline()
    with pytest.raises(ValueError, match="embedding_model"):
        pipeline.search("query")


def test_search_raises_without_vector_source():
    pipeline = _make_pipeline()
    pipeline._embedding_model = MagicMock()
    with pytest.raises(ValueError, match="vector_source"):
        pipeline.search("query")


def test_search_calls_knn():
    pipeline = _make_pipeline()
    mock_model = MagicMock()
    mock_model.embed_text.return_value = [0.5] * 4
    pipeline._embedding_model = mock_model
    mock_vs = MagicMock()
    mock_vs.knn.return_value = [{"text": "result"}]
    pipeline._vector_source = mock_vs

    results = pipeline.search("what is this?", k=3, collection="news")
    mock_vs.knn.assert_called_once_with(
        index_name="news",
        query_vector=[0.5] * 4,
        k=3,
    )
    assert results == [{"text": "result"}]


def test_ensure_index_noop_without_model_or_vs():
    pipeline = _make_pipeline()
    pipeline.ensure_index()  # should not raise


def test_ensure_index_calls_create_index():
    pipeline = _make_pipeline(collection="web")
    mock_model = MagicMock()
    mock_model.dim = 16
    pipeline._embedding_model = mock_model
    mock_vs = MagicMock()
    pipeline._vector_source = mock_vs

    pipeline.ensure_index()
    mock_vs.create_index.assert_called_once_with(
        index_name="web",
        collection="web",
        dim=16,
    )
