from hashlib import sha256
from unittest.mock import MagicMock

import pytest

from blue_data_web_source.source.web.extractor import ExtractResult
from blue_data_web_source.source.web.fetcher import FetchResult
from blue_data_web_source.source.web.freshness import MemoryFreshnessStore
from blue_data_web_source.source.web.pipeline import WebPipeline


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


def test_ingest_enricher_adds_entities():
    pipeline = _make_pipeline()
    pipeline._enricher = MagicMock()
    pipeline._enricher.enrich.return_value = {
        "entities": [{"text": "Python", "type": "PROPER_NOUN"}]
    }

    records = pipeline.ingest("https://example.com")
    assert all("entities" in r for r in records)
    assert records[0]["entities"] == [{"text": "Python", "type": "PROPER_NOUN"}]


def test_ingest_no_entities_key_when_enricher_returns_empty():
    pipeline = _make_pipeline()
    pipeline._enricher = MagicMock()
    pipeline._enricher.enrich.return_value = {"entities": []}

    records = pipeline.ingest("https://example.com")
    assert all("entities" not in r for r in records)


def test_ingest_description_only_on_first_chunk():
    pipeline = _make_pipeline()
    pipeline._chunker = MagicMock()
    pipeline._chunker.chunk_with_titles.return_value = [
        (None, "First chunk text about the topic."),
        (None, "Second chunk text with more details."),
        (None, "Third chunk text with even more content."),
    ]

    records = pipeline.ingest("https://example.com")
    assert len(records) == 3
    assert records[0].get("description") == "A test page."
    assert "description" not in records[1]
    assert "description" not in records[2]


def test_ingest_section_title_propagated():
    pipeline = _make_pipeline()
    pipeline._chunker = MagicMock()
    pipeline._chunker.chunk_with_titles.return_value = [
        ("Introduction", "First section text here."),
        ("Methods", "Second section text here."),
    ]

    records = pipeline.ingest("https://example.com")
    assert records[0]["section_title"] == "Introduction"
    assert records[1]["section_title"] == "Methods"


def test_ingest_no_section_title_key_when_none():
    pipeline = _make_pipeline()
    pipeline._chunker = MagicMock()
    pipeline._chunker.chunk_with_titles.return_value = [
        (None, "Chunk without a section title."),
    ]

    records = pipeline.ingest("https://example.com")
    assert "section_title" not in records[0]


def test_ingest_uses_final_url_not_original():
    # chunk_id and url should be based on final_url after redirects
    pipeline = _make_pipeline()
    pipeline._fetcher.fetch.return_value = FetchResult(
        url="https://original.example.com",
        final_url="https://redirected.example.com",
        html="<html></html>",
        status_code=200,
        content_type="text/html",
    )
    pipeline._extractor.extract.return_value = _make_extract_result(
        url="https://redirected.example.com"
    )

    records = pipeline.ingest("https://original.example.com")
    assert records[0]["url"] == "https://redirected.example.com"
    assert records[0]["domain"] == "redirected.example.com"


def test_pipeline_default_timeout_is_30_seconds():
    p = WebPipeline()
    assert p._fetcher._timeout == 30.0


def test_ingest_chunk_ids_are_unique():
    pipeline = _make_pipeline()
    pipeline._chunker = MagicMock()
    pipeline._chunker.chunk_with_titles.return_value = [
        (None, "Chunk one text."),
        (None, "Chunk two text."),
        (None, "Chunk three text."),
    ]

    records = pipeline.ingest("https://example.com")
    chunk_ids = [r["chunk_id"] for r in records]
    assert len(chunk_ids) == len(set(chunk_ids))


# ---------------------------------------------------------------------------
# Freshness tracking
# ---------------------------------------------------------------------------

def _page_id(url: str = "https://example.com") -> str:
    return "web_" + sha256(url.encode()).hexdigest()[:16]


def test_freshness_first_ingest_writes_store():
    store = MemoryFreshnessStore()
    pipeline = _make_pipeline(freshness_store=store)
    records = pipeline.ingest("https://example.com")

    assert len(records) > 0
    entry = store.get(_page_id())
    assert entry is not None
    assert "content_hash" in entry
    assert entry["url"] == "https://example.com"
    assert entry["chunk_count"] == len(records)
    assert "last_ingested_at" in entry
    assert "last_checked_at" in entry


def test_freshness_unchanged_content_returns_empty():
    store = MemoryFreshnessStore()
    pipeline = _make_pipeline(freshness_store=store)

    first = pipeline.ingest("https://example.com")
    assert len(first) > 0

    second = pipeline.ingest("https://example.com")
    assert second == []


def test_freshness_unchanged_updates_last_checked_not_ingested():
    store = MemoryFreshnessStore()
    pipeline = _make_pipeline(freshness_store=store)
    pipeline.ingest("https://example.com")

    # Backdate last_checked_at so we can verify it gets updated
    pid = _page_id()
    old_entry = store.get(pid)
    original_ingested_at = old_entry["last_ingested_at"]
    store.set(pid, {**old_entry, "last_checked_at": "2020-01-01T00:00:00+00:00"})

    pipeline.ingest("https://example.com")

    updated = store.get(pid)
    assert updated["last_ingested_at"] == original_ingested_at
    assert updated["last_checked_at"] != "2020-01-01T00:00:00+00:00"


def test_freshness_changed_content_reingests():
    store = MemoryFreshnessStore()
    pipeline = _make_pipeline(freshness_store=store)

    pipeline.ingest("https://example.com")
    first_hash = store.get(_page_id())["content_hash"]

    pipeline._extractor.extract.return_value = _make_extract_result(
        text="Completely different content here. " * 30
    )
    second = pipeline.ingest("https://example.com")

    assert len(second) > 0
    assert store.get(_page_id())["content_hash"] != first_hash


def test_freshness_changed_content_updates_chunk_count():
    store = MemoryFreshnessStore()
    pipeline = _make_pipeline(freshness_store=store)
    pipeline.ingest("https://example.com")

    # Return more chunks on second ingest
    pipeline._chunker = MagicMock()
    pipeline._chunker.chunk_with_titles.return_value = [
        (None, f"Chunk {i} with unique content token {i * 99}." * 5)
        for i in range(5)
    ]
    pipeline._extractor.extract.return_value = _make_extract_result(
        text="New content that is definitely different. " * 30
    )
    pipeline.ingest("https://example.com")

    assert store.get(_page_id())["chunk_count"] == 5


def test_freshness_no_store_reingest_always_processes():
    pipeline = _make_pipeline()  # no freshness_store
    first = pipeline.ingest("https://example.com")
    second = pipeline.ingest("https://example.com")
    assert len(first) > 0
    assert len(second) > 0


def test_freshness_extractor_returns_none_does_not_write_store():
    store = MemoryFreshnessStore()
    pipeline = _make_pipeline(freshness_store=store)
    pipeline._extractor.extract.return_value = None

    pipeline.ingest("https://example.com")
    assert store.get(_page_id()) is None


def test_freshness_store_keyed_by_final_url_not_original():
    store = MemoryFreshnessStore()
    pipeline = _make_pipeline(freshness_store=store)
    pipeline._fetcher.fetch.return_value = FetchResult(
        url="https://original.example.com",
        final_url="https://canonical.example.com",
        html="<html></html>",
        status_code=200,
        content_type="text/html",
    )
    pipeline._extractor.extract.return_value = _make_extract_result(
        url="https://canonical.example.com"
    )

    pipeline.ingest("https://original.example.com")

    canonical_pid = _page_id("https://canonical.example.com")
    original_pid = _page_id("https://original.example.com")
    assert store.get(canonical_pid) is not None
    assert store.get(original_pid) is None
