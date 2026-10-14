from datetime import datetime, timezone
from hashlib import sha256
from unittest.mock import MagicMock, patch

import pytest

from blue_data_web_source.source.web.extractor import ExtractResult
from blue_data_web_source.source.web.feed import FeedEntry, FeedParseResult
from blue_data_web_source.source.web.fetcher import FetchResult
from blue_data_web_source.source.web.freshness import MemoryFreshnessStore
from blue_data_web_source.source.web.pipeline import WebPipeline

_FEED_URL = "https://example.com/feed.xml"


def _make_feed_result(n: int = 2) -> FeedParseResult:
    return FeedParseResult(
        feed_url=_FEED_URL,
        feed_title="Test Feed",
        entries=[
            FeedEntry(url=f"https://example.com/article-{i}", title=f"Article {i}")
            for i in range(n)
        ],
    )


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


def test_ensure_index_idempotent_when_index_exists():
    # #41: some backends (Redis) raise "Index already exists" on a second call.
    # ensure_index must swallow that specific error so it stays idempotent.
    pipeline = _make_pipeline(collection="web")
    pipeline._embedding_model = MagicMock(dim=8)
    mock_vs = MagicMock()
    mock_vs.create_index.side_effect = Exception("Index already exists")
    pipeline._vector_source = mock_vs

    pipeline.ensure_index()  # must not raise
    pipeline.ensure_index()  # safe to call again
    assert mock_vs.create_index.call_count == 2


def test_ensure_index_propagates_other_errors():
    # A genuine failure (not "already exists") must still surface.
    pipeline = _make_pipeline(collection="web")
    pipeline._embedding_model = MagicMock(dim=8)
    mock_vs = MagicMock()
    mock_vs.create_index.side_effect = Exception("Connection refused")
    pipeline._vector_source = mock_vs

    with pytest.raises(Exception, match="Connection refused"):
        pipeline.ensure_index()


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


# ---------------------------------------------------------------------------
# Feed ingestion
# ---------------------------------------------------------------------------


def test_ingest_feed_ingests_each_article():
    pipeline = _make_pipeline()
    with patch("blue_data_web_source.source.web.feed.FeedParser") as MockParser:
        MockParser.return_value.parse.return_value = _make_feed_result(2)
        chunks = pipeline.ingest_feed(_FEED_URL)
    assert len(chunks) > 0


def test_ingest_feed_max_entries_caps_articles():
    pipeline = _make_pipeline()
    with patch("blue_data_web_source.source.web.feed.FeedParser") as MockParser:
        MockParser.return_value.parse.return_value = _make_feed_result(3)
        pipeline.ingest_feed(_FEED_URL, max_entries=1)
    # 1 call for the feed + 1 for the single article (not 3)
    assert pipeline._fetcher.fetch.call_count == 2


def test_ingest_feed_empty_feed_returns_empty():
    pipeline = _make_pipeline()
    with patch("blue_data_web_source.source.web.feed.FeedParser") as MockParser:
        MockParser.return_value.parse.return_value = FeedParseResult(
            feed_url=_FEED_URL, feed_title=None, entries=[]
        )
        chunks = pipeline.ingest_feed(_FEED_URL)
    assert chunks == []


def test_ingest_feed_article_with_no_content_skipped():
    pipeline = _make_pipeline()
    pipeline._extractor.extract.side_effect = [None, _make_extract_result()]
    with patch("blue_data_web_source.source.web.feed.FeedParser") as MockParser:
        MockParser.return_value.parse.return_value = _make_feed_result(2)
        chunks = pipeline.ingest_feed(_FEED_URL)
    assert len(chunks) > 0


def test_ingest_feed_passes_collection():
    pipeline = _make_pipeline()
    pipeline._embedding_model = MagicMock()
    pipeline._embedding_model.embed_text.return_value = [0.1] * 4
    mock_vs = MagicMock()
    pipeline._vector_source = mock_vs

    with patch("blue_data_web_source.source.web.feed.FeedParser") as MockParser:
        MockParser.return_value.parse.return_value = _make_feed_result(1)
        pipeline.ingest_feed(_FEED_URL, collection="blog")

    upsert_collections = [kw["collection"] for _, kw in mock_vs.upsert.call_args_list]
    assert all(c == "blog" for c in upsert_collections)


def test_ingest_feed_composes_with_freshness():
    store = MemoryFreshnessStore()
    pipeline = _make_pipeline(freshness_store=store)

    with patch("blue_data_web_source.source.web.feed.FeedParser") as MockParser:
        MockParser.return_value.parse.return_value = _make_feed_result(1)
        first = pipeline.ingest_feed(_FEED_URL)
        second = pipeline.ingest_feed(_FEED_URL)

    assert len(first) > 0
    assert second == []


def test_ingest_feed_since_skips_old_entries():
    pipeline = _make_pipeline()
    since = datetime(2026, 8, 10, tzinfo=timezone.utc)
    entries = [
        FeedEntry(url="https://example.com/old", published_parsed=datetime(2026, 8, 1, tzinfo=timezone.utc)),
        FeedEntry(url="https://example.com/new", published_parsed=datetime(2026, 8, 15, tzinfo=timezone.utc)),
    ]
    feed_result = FeedParseResult(feed_url=_FEED_URL, feed_title="Test", entries=entries)

    with patch("blue_data_web_source.source.web.feed.FeedParser") as MockParser:
        MockParser.return_value.parse.return_value = feed_result
        pipeline.ingest_feed(_FEED_URL, since=since)

    # 1 call for feed + 1 for "new" only (old filtered out)
    assert pipeline._fetcher.fetch.call_count == 2


def test_ingest_feed_since_includes_entries_without_date():
    pipeline = _make_pipeline()
    since = datetime(2026, 8, 10, tzinfo=timezone.utc)
    entries = [FeedEntry(url="https://example.com/no-date", published_parsed=None)]
    feed_result = FeedParseResult(feed_url=_FEED_URL, feed_title="Test", entries=entries)

    with patch("blue_data_web_source.source.web.feed.FeedParser") as MockParser:
        MockParser.return_value.parse.return_value = feed_result
        pipeline.ingest_feed(_FEED_URL, since=since)

    # 1 call for feed + 1 for no-date article (always included per spec)
    assert pipeline._fetcher.fetch.call_count == 2


def test_ingest_feed_since_none_includes_all():
    pipeline = _make_pipeline()
    entries = [
        FeedEntry(url="https://example.com/old", published_parsed=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        FeedEntry(url="https://example.com/new", published_parsed=datetime(2026, 8, 15, tzinfo=timezone.utc)),
    ]
    feed_result = FeedParseResult(feed_url=_FEED_URL, feed_title="Test", entries=entries)

    with patch("blue_data_web_source.source.web.feed.FeedParser") as MockParser:
        MockParser.return_value.parse.return_value = feed_result
        pipeline.ingest_feed(_FEED_URL, since=None)

    # 1 call for feed + 2 for both articles
    assert pipeline._fetcher.fetch.call_count == 3


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


# ---------------------------------------------------------------------------
# ingest_many
# ---------------------------------------------------------------------------


def test_ingest_many_empty_returns_empty():
    pipeline = _make_pipeline()
    assert pipeline.ingest_many([]) == []


def test_ingest_many_ingests_all_urls():
    pipeline = _make_pipeline()
    results = pipeline.ingest_many([
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ])
    assert pipeline._fetcher.fetch.call_count == 3
    assert len(results) > 0


def test_ingest_many_skips_failed_urls():
    pipeline = _make_pipeline()
    pipeline._fetcher.fetch.side_effect = [
        _make_fetch_result("https://example.com/a"),
        Exception("connection refused"),
        _make_fetch_result("https://example.com/c"),
    ]
    results = pipeline.ingest_many([
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ])
    fetched_urls = {
        call.args[0] for call in pipeline._fetcher.fetch.call_args_list
    }
    assert fetched_urls == {
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    }
    # Only two succeeded; at least those chunks are present
    assert len(results) > 0


def test_ingest_many_respects_max_workers():
    pipeline = _make_pipeline(max_workers=2)
    assert pipeline._max_workers == 2
    results = pipeline.ingest_many(["https://example.com/a", "https://example.com/b"])
    assert pipeline._fetcher.fetch.call_count == 2
    assert len(results) > 0


def test_cross_url_dedup_skips_duplicate_content():
    store = MemoryFreshnessStore()
    pipeline = _make_pipeline(freshness_store=store)

    # Ingest URL A
    pipeline._fetcher.fetch.return_value = _make_fetch_result("https://site-a.com/story")
    pipeline._extractor.extract.return_value = _make_extract_result(url="https://site-a.com/story")
    first = pipeline.ingest("https://site-a.com/story")
    assert len(first) > 0

    # Ingest URL B with identical extracted text
    pipeline._fetcher.fetch.return_value = _make_fetch_result("https://site-b.com/story")
    pipeline._extractor.extract.return_value = _make_extract_result(url="https://site-b.com/story")
    second = pipeline.ingest("https://site-b.com/story")
    assert second == []


def test_cross_url_dedup_allows_unique_content():
    store = MemoryFreshnessStore()
    pipeline = _make_pipeline(freshness_store=store)

    pipeline._fetcher.fetch.return_value = _make_fetch_result("https://site-a.com/story")
    pipeline._extractor.extract.return_value = _make_extract_result(url="https://site-a.com/story")
    pipeline.ingest("https://site-a.com/story")

    pipeline._fetcher.fetch.return_value = _make_fetch_result("https://site-b.com/other")
    pipeline._extractor.extract.return_value = _make_extract_result(
        url="https://site-b.com/other",
        text="Completely different article about another topic altogether. " * 30,
    )
    second = pipeline.ingest("https://site-b.com/other")
    assert len(second) > 0


def test_cross_url_dedup_inactive_without_freshness_store():
    pipeline = _make_pipeline()  # no freshness_store

    pipeline._fetcher.fetch.return_value = _make_fetch_result("https://site-a.com/story")
    pipeline._extractor.extract.return_value = _make_extract_result(url="https://site-a.com/story")
    pipeline.ingest("https://site-a.com/story")

    pipeline._fetcher.fetch.return_value = _make_fetch_result("https://site-b.com/story")
    pipeline._extractor.extract.return_value = _make_extract_result(url="https://site-b.com/story")
    second = pipeline.ingest("https://site-b.com/story")
    assert len(second) > 0


def test_same_url_reingest_not_blocked_by_cross_url_dedup():
    store = MemoryFreshnessStore()
    pipeline = _make_pipeline(freshness_store=store)

    pipeline._fetcher.fetch.return_value = _make_fetch_result("https://example.com")
    pipeline._extractor.extract.return_value = _make_extract_result(url="https://example.com")
    first = pipeline.ingest("https://example.com")
    assert len(first) > 0

    # Same URL, same content → handled by same-URL freshness check, not cross-URL dedup
    second = pipeline.ingest("https://example.com")
    assert second == []  # still skipped, just by the right path


def test_ingest_many_single_url_behaves_like_ingest():
    pipeline = _make_pipeline()
    many = pipeline.ingest_many(["https://example.com"])
    pipeline2 = _make_pipeline()
    single = pipeline2.ingest("https://example.com")
    assert len(many) == len(single)


# ── Stale surplus-chunk cleanup on re-ingest (#32) ────────────────────────────

def _chunks(n: int):
    return [(None, f"Chunk {i} unique token {i * 97}. " * 5) for i in range(n)]


def _reingest_pipeline(**kwargs):
    """Pipeline with a mocked vector store + embedding model for chunk-count tests."""
    pipeline = _make_pipeline(**kwargs)
    pipeline._embedding_model = MagicMock()
    pipeline._embedding_model.embed_text.return_value = [0.1, 0.2, 0.3]
    pipeline._vector_source = MagicMock()
    pipeline._chunker = MagicMock()
    return pipeline


def _deleted_ids(vector_source) -> list:
    return [c.kwargs["doc_id"] for c in vector_source.delete.call_args_list]


def test_reingest_shrink_deletes_surplus_vector_chunks():
    store = MemoryFreshnessStore()
    pipeline = _reingest_pipeline(freshness_store=store, collection="web")

    pipeline._chunker.chunk_with_titles.return_value = _chunks(5)
    pipeline.ingest("https://example.com")
    assert store.get(_page_id())["chunk_count"] == 5

    # Page shrinks to 2 chunks; content changed so it is re-ingested
    pipeline._chunker.chunk_with_titles.return_value = _chunks(2)
    pipeline._extractor.extract.return_value = _make_extract_result(text="Shrunk content. " * 30)
    pipeline.ingest("https://example.com")

    pid = _page_id()
    assert _deleted_ids(pipeline._vector_source) == [
        f"{pid}_chunk_0002", f"{pid}_chunk_0003", f"{pid}_chunk_0004",
    ]
    for call in pipeline._vector_source.delete.call_args_list:
        assert call.kwargs["collection"] == "web"
    assert store.get(pid)["chunk_count"] == 2


def test_reingest_grow_deletes_nothing():
    store = MemoryFreshnessStore()
    pipeline = _reingest_pipeline(freshness_store=store)

    pipeline._chunker.chunk_with_titles.return_value = _chunks(2)
    pipeline.ingest("https://example.com")

    pipeline._chunker.chunk_with_titles.return_value = _chunks(4)
    pipeline._extractor.extract.return_value = _make_extract_result(text="Grown content. " * 30)
    pipeline.ingest("https://example.com")

    pipeline._vector_source.delete.assert_not_called()


def test_first_ingest_deletes_nothing():
    store = MemoryFreshnessStore()
    pipeline = _reingest_pipeline(freshness_store=store)
    pipeline._chunker.chunk_with_titles.return_value = _chunks(3)
    pipeline.ingest("https://example.com")
    pipeline._vector_source.delete.assert_not_called()


def test_reingest_shrink_without_freshness_store_skips_cleanup():
    # Documented limitation (option a): without a freshness_store the pipeline has
    # no record of the previous chunk count, so surplus cleanup cannot run.
    pipeline = _reingest_pipeline()  # no freshness_store
    pipeline._chunker.chunk_with_titles.return_value = _chunks(5)
    pipeline.ingest("https://example.com")
    pipeline._chunker.chunk_with_titles.return_value = _chunks(2)
    pipeline._extractor.extract.return_value = _make_extract_result(text="Shrunk content. " * 30)
    pipeline.ingest("https://example.com")
    pipeline._vector_source.delete.assert_not_called()


def test_reingest_refreshes_graph():
    store = MemoryFreshnessStore()
    pipeline = _reingest_pipeline(freshness_store=store)
    pipeline._graph_indexer = MagicMock()

    pipeline._chunker.chunk_with_titles.return_value = _chunks(5)
    pipeline.ingest("https://example.com")

    pipeline._chunker.chunk_with_titles.return_value = _chunks(2)
    pipeline._extractor.extract.return_value = _make_extract_result(text="Refreshed content. " * 30)
    pipeline.ingest("https://example.com")

    pid = _page_id()
    # delete_document is called before index_document on every ingest so the
    # graph is rebuilt rather than left stale (index_document is a no-op when
    # the Document node already exists).
    assert pipeline._graph_indexer.delete_document.call_count == 2
    assert all(
        c.args == (pid,) for c in pipeline._graph_indexer.delete_document.call_args_list
    )
    assert pipeline._graph_indexer.index_document.call_count == 2


# ── Per-domain politeness in ingest_many (#51) ────────────────────────────────

def _concurrency_tracking_pipeline():
    """Pipeline whose fetcher records peak concurrent in-flight fetches."""
    import threading
    import time

    pipeline = _make_pipeline()
    state = {"inflight": 0, "peak": 0}
    lock = threading.Lock()

    def _fetch(url):
        with lock:
            state["inflight"] += 1
            state["peak"] = max(state["peak"], state["inflight"])
        time.sleep(0.05)
        with lock:
            state["inflight"] -= 1
        return _make_fetch_result(url=url)

    pipeline._fetcher.fetch.side_effect = _fetch
    return pipeline, state


def test_ingest_many_serializes_requests_to_one_host():
    # #51: a batch of same-host URLs must not hit the host with max_workers
    # concurrent requests — the per-domain lock serializes them (peak == 1).
    pipeline, state = _concurrency_tracking_pipeline()
    urls = [f"http://samehost.example/p{i}" for i in range(6)]
    pipeline.ingest_many(urls)
    assert state["peak"] == 1


def test_ingest_many_runs_different_hosts_concurrently():
    # Different hosts use different locks, so they still run in parallel.
    pipeline, state = _concurrency_tracking_pipeline()
    urls = [f"http://host{i}.example/page" for i in range(6)]
    pipeline.ingest_many(urls)
    assert state["peak"] > 1
