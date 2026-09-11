"""Tests for WEB.FIND, classification/field-extraction wiring, enrichment storage, and graph indexing."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from blue_data.source.graph.memory_graph_source import MemoryGraphSource
from blue_data_doc_source import CoverageReport, FindResult
from blue_data_doc_source.source.document.schema.schema_store import MemoryRecordStore
from blue_data_web_source.source.web.extractor import ExtractResult
from blue_data_web_source.source.web.fetcher import FetchResult
from blue_data_web_source.source.web.pipeline import WebPipeline
from blue_data_web_source.source.web.record import WebChunkRecord
from blue_data_web_source.source.web.web_source import WebDataSource

_URL = "https://example.com/article"
_TEXT = "Apple announced new MacBook Pro models today. The price starts at $1999. " * 20


def _fetch(url: str = _URL) -> FetchResult:
    return FetchResult(url=url, final_url=url, html="<html></html>", status_code=200, content_type="text/html")


def _extract(text: str = _TEXT) -> ExtractResult:
    return ExtractResult(url=_URL, text=text, title="Apple MacBook", author="Jane", date="2026-09-10")


def _pipeline(**kwargs) -> WebPipeline:
    return WebPipeline(chunk_size=50, overlap=5, **kwargs)


# ---------------------------------------------------------------------------
# WebChunkRecord — entities + semantic_hints fields
# ---------------------------------------------------------------------------

class TestWebChunkRecordEnrichmentFields:
    def test_entities_default_empty(self):
        r = WebChunkRecord(url=_URL, domain="example.com", chunk_id="c1", chunk_index=0, text="hello")
        assert r.entities == []

    def test_semantic_hints_default_empty(self):
        r = WebChunkRecord(url=_URL, domain="example.com", chunk_id="c1", chunk_index=0, text="hello")
        assert r.semantic_hints == {}

    def test_entities_in_to_dict_when_present(self):
        r = WebChunkRecord(
            url=_URL, domain="example.com", chunk_id="c1", chunk_index=0, text="hello",
            entities=[{"text": "Apple", "type": "PROPER_NOUN"}],
        )
        d = r.to_dict()
        assert d["entities"] == [{"text": "Apple", "type": "PROPER_NOUN"}]

    def test_entities_omitted_from_to_dict_when_empty(self):
        r = WebChunkRecord(url=_URL, domain="example.com", chunk_id="c1", chunk_index=0, text="hello")
        assert "entities" not in r.to_dict()

    def test_semantic_hints_in_to_dict_when_present(self):
        r = WebChunkRecord(
            url=_URL, domain="example.com", chunk_id="c1", chunk_index=0, text="hello",
            semantic_hints={"word_count": 1},
        )
        d = r.to_dict()
        assert d["semantic_hints"] == {"word_count": 1}

    def test_semantic_hints_omitted_from_to_dict_when_empty(self):
        r = WebChunkRecord(url=_URL, domain="example.com", chunk_id="c1", chunk_index=0, text="hello")
        assert "semantic_hints" not in r.to_dict()


# ---------------------------------------------------------------------------
# Enrichment wiring — entities + semantic_hints stored on record
# ---------------------------------------------------------------------------

class TestEnrichmentWiring:
    def _mock_enricher(self):
        enricher = MagicMock()
        enricher.enrich.return_value = {
            "entities": [{"text": "Apple", "type": "PROPER_NOUN"}],
            "word_count": 42,
            "keyphrases": ["MacBook Pro"],
        }
        return enricher

    @patch("blue_data_web_source.source.web.pipeline.WebFetcher")
    @patch("blue_data_web_source.source.web.pipeline.WebExtractor")
    @patch("blue_data_web_source.source.web.pipeline.MetadataExtractor")
    def test_entities_stored_on_chunk(self, mock_meta, mock_ext, mock_fetcher):
        mock_fetcher.return_value.fetch.return_value = _fetch()
        mock_ext.return_value.extract.return_value = _extract()
        mock_meta.return_value.extract.return_value = MagicMock(
            schema_type=None, keywords=[], publisher=None, canonical_url=None,
            author=None, date_published=None, description=None,
        )
        pipeline = _pipeline(enricher=self._mock_enricher())
        results = pipeline.ingest(_URL)
        assert len(results) > 0
        assert results[0]["entities"] == [{"text": "Apple", "type": "PROPER_NOUN"}]

    @patch("blue_data_web_source.source.web.pipeline.WebFetcher")
    @patch("blue_data_web_source.source.web.pipeline.WebExtractor")
    @patch("blue_data_web_source.source.web.pipeline.MetadataExtractor")
    def test_semantic_hints_stored_on_chunk(self, mock_meta, mock_ext, mock_fetcher):
        mock_fetcher.return_value.fetch.return_value = _fetch()
        mock_ext.return_value.extract.return_value = _extract()
        mock_meta.return_value.extract.return_value = MagicMock(
            schema_type=None, keywords=[], publisher=None, canonical_url=None,
            author=None, date_published=None, description=None,
        )
        pipeline = _pipeline(enricher=self._mock_enricher())
        results = pipeline.ingest(_URL)
        assert results[0]["semantic_hints"] == {"word_count": 42, "keyphrases": ["MacBook Pro"]}


# ---------------------------------------------------------------------------
# Classification + field extraction wiring
# ---------------------------------------------------------------------------

class TestClassificationWiring:
    def _mock_classifier(self, page_type="news_article", confidence=0.85):
        c = MagicMock()
        c.classify.return_value = {"document_type": page_type, "confidence": confidence, "classifier": "keyword"}
        return c

    def _mock_extractor(self, fields=None):
        e = MagicMock()
        e.extract.return_value = fields or {"author": "Jane", "publish_date": "2026-09-10"}
        return e

    @patch("blue_data_web_source.source.web.pipeline.WebFetcher")
    @patch("blue_data_web_source.source.web.pipeline.WebExtractor")
    @patch("blue_data_web_source.source.web.pipeline.MetadataExtractor")
    def test_record_written_to_store_after_ingest(self, mock_meta, mock_ext, mock_fetcher):
        mock_fetcher.return_value.fetch.return_value = _fetch()
        mock_ext.return_value.extract.return_value = _extract()
        mock_meta.return_value.extract.return_value = MagicMock(
            schema_type=None, keywords=[], publisher=None, canonical_url=None,
            author=None, date_published=None, description=None,
        )
        store = MemoryRecordStore()
        pipeline = _pipeline(classifier=self._mock_classifier(), record_store=store)
        pipeline.ingest(_URL)
        records = store.find(None)
        assert len(records) == 1
        assert records[0]["document_type"] == "news_article"
        assert records[0]["url"] == _URL

    @patch("blue_data_web_source.source.web.pipeline.WebFetcher")
    @patch("blue_data_web_source.source.web.pipeline.WebExtractor")
    @patch("blue_data_web_source.source.web.pipeline.MetadataExtractor")
    def test_fields_stored_when_field_extractor_set(self, mock_meta, mock_ext, mock_fetcher):
        mock_fetcher.return_value.fetch.return_value = _fetch()
        mock_ext.return_value.extract.return_value = _extract()
        mock_meta.return_value.extract.return_value = MagicMock(
            schema_type=None, keywords=[], publisher=None, canonical_url=None,
            author=None, date_published=None, description=None,
        )
        store = MemoryRecordStore()
        pipeline = _pipeline(
            classifier=self._mock_classifier(),
            field_extractor=self._mock_extractor(),
            record_store=store,
        )
        pipeline.ingest(_URL)
        records = store.find(None)
        assert records[0]["fields"]["author"] == "Jane"

    @patch("blue_data_web_source.source.web.pipeline.WebFetcher")
    @patch("blue_data_web_source.source.web.pipeline.WebExtractor")
    @patch("blue_data_web_source.source.web.pipeline.MetadataExtractor")
    def test_no_record_written_without_classifier(self, mock_meta, mock_ext, mock_fetcher):
        mock_fetcher.return_value.fetch.return_value = _fetch()
        mock_ext.return_value.extract.return_value = _extract()
        mock_meta.return_value.extract.return_value = MagicMock(
            schema_type=None, keywords=[], publisher=None, canonical_url=None,
            author=None, date_published=None, description=None,
        )
        store = MemoryRecordStore()
        pipeline = _pipeline(record_store=store)
        pipeline.ingest(_URL)
        assert store.find(None) == []

    @patch("blue_data_web_source.source.web.pipeline.WebFetcher")
    @patch("blue_data_web_source.source.web.pipeline.WebExtractor")
    @patch("blue_data_web_source.source.web.pipeline.MetadataExtractor")
    def test_memory_record_store_auto_created_when_classifier_set(self, mock_meta, mock_ext, mock_fetcher):
        mock_fetcher.return_value.fetch.return_value = _fetch()
        mock_ext.return_value.extract.return_value = _extract()
        mock_meta.return_value.extract.return_value = MagicMock(
            schema_type=None, keywords=[], publisher=None, canonical_url=None,
            author=None, date_published=None, description=None,
        )
        pipeline = _pipeline(classifier=self._mock_classifier())
        assert pipeline._record_store is not None
        pipeline.ingest(_URL)
        assert len(pipeline._record_store.find(None)) == 1


# ---------------------------------------------------------------------------
# find_records on WebPipeline
# ---------------------------------------------------------------------------

class TestWebPipelineFindRecords:
    def _store_with_records(self):
        store = MemoryRecordStore()
        store.upsert({
            "document_id": "page_a",
            "document_type": "news_article",
            "confidence": 0.9,
            "classifier": "keyword",
            "url": "https://example.com/a",
            "fields": {"author": "Jane", "topic": "AI"},
        })
        store.upsert({
            "document_id": "page_b",
            "document_type": "product_page",
            "confidence": 0.8,
            "classifier": "keyword",
            "url": "https://example.com/b",
            "fields": {"price": "$999", "brand": "Apple"},
        })
        return store

    def test_find_records_returns_list_by_default(self):
        pipeline = _pipeline(record_store=self._store_with_records())
        result = pipeline.find_records({"document_type": "news_article"})
        assert isinstance(result, list)
        assert len(result) == 1

    def test_find_records_returns_find_result_with_coverage(self):
        pipeline = _pipeline(record_store=self._store_with_records())
        result = pipeline.find_records({"fields.author": "Jane"}, coverage=True)
        assert isinstance(result, FindResult)
        assert result.status == "partial"   # product_page missing author
        assert result.coverage.documents_missing_field == 1

    def test_find_records_raises_without_record_store(self):
        pipeline = _pipeline()
        with pytest.raises(ValueError, match="record_store"):
            pipeline.find_records()

    def test_find_records_filter_by_field(self):
        pipeline = _pipeline(record_store=self._store_with_records())
        result = pipeline.find_records({"fields.brand": "Apple"})
        assert len(result) == 1
        assert result[0]["document_type"] == "product_page"

    def test_find_records_no_filter_returns_all(self):
        pipeline = _pipeline(record_store=self._store_with_records())
        assert len(pipeline.find_records()) == 2


# ---------------------------------------------------------------------------
# WEB.FIND op on WebDataSource
# ---------------------------------------------------------------------------

class TestWebFindOp:
    def _source_with_records(self):
        store = MemoryRecordStore()
        store.upsert({
            "document_id": "page_a",
            "document_type": "news_article",
            "confidence": 0.9,
            "classifier": "keyword",
            "url": "https://example.com/a",
            "fields": {"author": "Jane"},
        })
        store.upsert({
            "document_id": "page_b",
            "document_type": "product_page",
            "confidence": 0.8,
            "classifier": "keyword",
            "url": "https://example.com/b",
            "fields": {"price": "$999"},
        })
        return WebDataSource(name="test", record_store=store)

    def test_web_find_returns_list(self):
        source = self._source_with_records()
        result = source.execute_query({"op": "WEB.FIND", "filter": {"document_type": "news_article"}})
        assert isinstance(result, list)
        assert len(result) == 1

    def test_web_find_with_coverage_returns_find_result(self):
        source = self._source_with_records()
        result = source.execute_query({
            "op": "WEB.FIND",
            "filter": {"fields.author": "Jane"},
            "coverage": True,
        })
        assert isinstance(result, FindResult)
        assert result.status == "partial"

    def test_web_find_no_filter_returns_all(self):
        source = self._source_with_records()
        result = source.execute_query({"op": "WEB.FIND"})
        assert len(result) == 2

    def test_web_find_raises_without_record_store(self):
        source = WebDataSource(name="test")
        with pytest.raises(ValueError, match="record_store"):
            source.execute_query({"op": "WEB.FIND"})


# ---------------------------------------------------------------------------
# page_id on WebChunkRecord
# ---------------------------------------------------------------------------

class TestPageIdOnRecord:
    def test_page_id_stored_in_to_dict(self):
        r = WebChunkRecord(
            url=_URL, domain="example.com", chunk_id="c1", chunk_index=0, text="hello",
            page_id="web_abc123",
        )
        assert r.to_dict()["page_id"] == "web_abc123"

    def test_page_id_omitted_when_none(self):
        r = WebChunkRecord(url=_URL, domain="example.com", chunk_id="c1", chunk_index=0, text="hello")
        assert "page_id" not in r.to_dict()

    @patch("blue_data_web_source.source.web.pipeline.WebFetcher")
    @patch("blue_data_web_source.source.web.pipeline.WebExtractor")
    @patch("blue_data_web_source.source.web.pipeline.MetadataExtractor")
    def test_page_id_set_on_ingested_chunks(self, mock_meta, mock_ext, mock_fetcher):
        mock_fetcher.return_value.fetch.return_value = _fetch()
        mock_ext.return_value.extract.return_value = _extract()
        mock_meta.return_value.extract.return_value = MagicMock(
            schema_type=None, keywords=[], publisher=None, canonical_url=None,
            author=None, date_published=None, description=None,
        )
        pipeline = _pipeline()
        results = pipeline.ingest(_URL)
        assert all("page_id" in r for r in results)
        # all chunks from the same page share the same page_id
        page_ids = {r["page_id"] for r in results}
        assert len(page_ids) == 1


# ---------------------------------------------------------------------------
# find_then_search on WebPipeline
# ---------------------------------------------------------------------------

class TestFindThenSearch:
    def _store_with(self, *records):
        store = MemoryRecordStore()
        for r in records:
            store.upsert(r)
        return store

    def test_returns_empty_when_no_pages_match_filter(self):
        store = self._store_with({
            "document_id": "page_a",
            "document_type": "news_article",
            "confidence": 0.9,
            "classifier": "keyword",
            "url": "https://example.com/a",
            "fields": {"author": "Jane"},
        })
        pipeline = _pipeline(record_store=store)
        result = pipeline.find_then_search("AI news", find_filter={"document_type": "product_page"})
        assert result == []

    def test_raises_without_record_store(self):
        pipeline = _pipeline()
        with pytest.raises(ValueError, match="record_store"):
            pipeline.find_then_search("AI news")

    def test_calls_search_with_page_id_filter(self):
        store = self._store_with({
            "document_id": "page_abc",
            "document_type": "news_article",
            "confidence": 0.9,
            "classifier": "keyword",
            "url": "https://example.com/a",
            "fields": {},
        })
        pipeline = _pipeline(record_store=store)
        pipeline.search = MagicMock(return_value=[])
        pipeline.find_then_search("AI news", find_filter={"document_type": "news_article"})
        pipeline.search.assert_called_once()
        call_kwargs = pipeline.search.call_args
        assert call_kwargs.kwargs.get("filters") == {"page_id": "page_abc"} or \
               (call_kwargs.args and call_kwargs.args[-1] == {"page_id": "page_abc"}) or \
               call_kwargs.kwargs.get("filters", {}).get("page_id") == "page_abc"

    def test_results_deduped_and_sorted_by_score(self):
        store = self._store_with(
            {"document_id": "p1", "document_type": "news_article", "confidence": 0.9,
             "classifier": "keyword", "url": "https://a.com", "fields": {}},
            {"document_id": "p2", "document_type": "news_article", "confidence": 0.8,
             "classifier": "keyword", "url": "https://b.com", "fields": {}},
        )
        pipeline = _pipeline(record_store=store)
        pipeline.search = MagicMock(side_effect=[
            [{"chunk_id": "c1", "score": 0.9}, {"chunk_id": "c2", "score": 0.7}],
            [{"chunk_id": "c2", "score": 0.7}, {"chunk_id": "c3", "score": 0.5}],
        ])
        results = pipeline.find_then_search("AI", find_filter={"document_type": "news_article"}, k=10)
        ids = [r["chunk_id"] for r in results]
        assert ids == ["c1", "c2", "c3"]   # c2 deduped; sorted by score

    def test_results_capped_at_k(self):
        store = self._store_with(
            {"document_id": "p1", "document_type": "news_article", "confidence": 0.9,
             "classifier": "keyword", "url": "https://a.com", "fields": {}},
        )
        pipeline = _pipeline(record_store=store)
        pipeline.search = MagicMock(return_value=[
            {"chunk_id": f"c{i}", "score": 1.0 - i * 0.1} for i in range(10)
        ])
        results = pipeline.find_then_search("AI", k=3)
        assert len(results) == 3


# ---------------------------------------------------------------------------
# WEB.FIND_THEN_SEARCH op on WebDataSource
# ---------------------------------------------------------------------------

class TestWebFindThenSearchOp:
    def test_requires_query_field(self):
        source = WebDataSource(name="test", record_store=MemoryRecordStore())
        with pytest.raises(ValueError, match="query"):
            source.execute_query({"op": "WEB.FIND_THEN_SEARCH"})

    def test_dispatches_to_pipeline(self):
        store = MemoryRecordStore()
        source = WebDataSource(name="test", record_store=store)
        source._pipeline.find_then_search = MagicMock(return_value=[{"chunk_id": "c1"}])
        result = source.execute_query({
            "op": "WEB.FIND_THEN_SEARCH",
            "query": "battery life",
            "find_filter": {"document_type": "product_page"},
            "k": 3,
        })
        source._pipeline.find_then_search.assert_called_once_with(
            query="battery life",
            find_filter={"document_type": "product_page"},
            k=3,
            collection=None,
        )
        assert result == [{"chunk_id": "c1"}]


# ---------------------------------------------------------------------------
# Graph indexing
# ---------------------------------------------------------------------------

class TestGraphIndexing:
    def _meta(self):
        return MagicMock(
            schema_type=None, keywords=[], publisher=None, canonical_url=None,
            author=None, date_published=None, description=None,
        )

    @patch("blue_data_web_source.source.web.pipeline.WebFetcher")
    @patch("blue_data_web_source.source.web.pipeline.WebExtractor")
    @patch("blue_data_web_source.source.web.pipeline.MetadataExtractor")
    def test_document_node_created_after_ingest(self, mock_meta, mock_ext, mock_fetcher):
        mock_fetcher.return_value.fetch.return_value = _fetch()
        mock_ext.return_value.extract.return_value = _extract()
        mock_meta.return_value.extract.return_value = self._meta()

        graph = MemoryGraphSource("test")
        pipeline = _pipeline(graph_source=graph)
        pipeline.ingest(_URL)

        # page_id is sha256(url)[:16] prefixed with "web_"
        from hashlib import sha256
        page_id = "web_" + sha256(_URL.encode()).hexdigest()[:16]
        node = graph.get_node("web", f"doc:{page_id}")
        assert node is not None
        assert node["properties"]["file_name"] == _URL

    @patch("blue_data_web_source.source.web.pipeline.WebFetcher")
    @patch("blue_data_web_source.source.web.pipeline.WebExtractor")
    @patch("blue_data_web_source.source.web.pipeline.MetadataExtractor")
    def test_chunk_nodes_created_after_ingest(self, mock_meta, mock_ext, mock_fetcher):
        mock_fetcher.return_value.fetch.return_value = _fetch()
        mock_ext.return_value.extract.return_value = _extract()
        mock_meta.return_value.extract.return_value = self._meta()

        graph = MemoryGraphSource("test")
        pipeline = _pipeline(graph_source=graph)
        results = pipeline.ingest(_URL)

        for chunk in results:
            node = graph.get_node("web", f"chunk:{chunk['chunk_id']}")
            assert node is not None

    @patch("blue_data_web_source.source.web.pipeline.WebFetcher")
    @patch("blue_data_web_source.source.web.pipeline.WebExtractor")
    @patch("blue_data_web_source.source.web.pipeline.MetadataExtractor")
    def test_entity_nodes_created_when_enricher_set(self, mock_meta, mock_ext, mock_fetcher):
        mock_fetcher.return_value.fetch.return_value = _fetch()
        mock_ext.return_value.extract.return_value = _extract()
        mock_meta.return_value.extract.return_value = self._meta()

        enricher = MagicMock()
        enricher.enrich.return_value = {
            "entities": [{"text": "Apple Inc", "type": "PROPER_NOUN"}],
        }
        graph = MemoryGraphSource("test")
        pipeline = _pipeline(enricher=enricher, graph_source=graph)
        pipeline.ingest(_URL)

        node = graph.get_node("web", "entity:PROPER_NOUN:apple inc")
        assert node is not None
        assert node["properties"]["name"] == "Apple Inc"

    @patch("blue_data_web_source.source.web.pipeline.WebFetcher")
    @patch("blue_data_web_source.source.web.pipeline.WebExtractor")
    @patch("blue_data_web_source.source.web.pipeline.MetadataExtractor")
    def test_no_graph_indexing_without_graph_source(self, mock_meta, mock_ext, mock_fetcher):
        mock_fetcher.return_value.fetch.return_value = _fetch()
        mock_ext.return_value.extract.return_value = _extract()
        mock_meta.return_value.extract.return_value = self._meta()

        pipeline = _pipeline()
        assert pipeline._graph_indexer is None
        results = pipeline.ingest(_URL)
        assert len(results) > 0   # ingest still works fine

    def test_graph_neighbors_raises_without_graph_source(self):
        pipeline = _pipeline()
        with pytest.raises(ValueError, match="graph_source"):
            pipeline.graph_neighbors("Apple")

    def test_graph_query_raises_without_graph_source(self):
        pipeline = _pipeline()
        with pytest.raises(ValueError, match="graph_source"):
            pipeline.graph_query("doc:web_abc")

    @patch("blue_data_web_source.source.web.pipeline.WebFetcher")
    @patch("blue_data_web_source.source.web.pipeline.WebExtractor")
    @patch("blue_data_web_source.source.web.pipeline.MetadataExtractor")
    def test_graph_neighbors_returns_chunks_mentioning_entity(self, mock_meta, mock_ext, mock_fetcher):
        mock_fetcher.return_value.fetch.return_value = _fetch()
        mock_ext.return_value.extract.return_value = _extract()
        mock_meta.return_value.extract.return_value = self._meta()

        enricher = MagicMock()
        enricher.enrich.return_value = {"entities": [{"text": "Apple Inc", "type": "PROPER_NOUN"}]}
        graph = MemoryGraphSource("test")
        pipeline = _pipeline(enricher=enricher, graph_source=graph)
        pipeline.ingest(_URL)

        results = pipeline.graph_neighbors("Apple Inc", entity_type="PROPER_NOUN")
        assert len(results) > 0
        assert results[0]["entity"] == "Apple Inc"


# ---------------------------------------------------------------------------
# WEB.GRAPH_NEIGHBORS and WEB.GRAPH_QUERY ops
# ---------------------------------------------------------------------------

class TestGraphOps:
    def test_graph_neighbors_op_dispatches(self):
        source = WebDataSource(name="test", graph_source=MemoryGraphSource("test"))
        source._pipeline.graph_neighbors = MagicMock(return_value=[{"chunk_id": "c1"}])
        result = source.execute_query({
            "op": "WEB.GRAPH_NEIGHBORS",
            "entity_name": "Apple Inc",
            "entity_type": "PROPER_NOUN",
            "k": 5,
        })
        source._pipeline.graph_neighbors.assert_called_once_with(
            "Apple Inc", entity_type="PROPER_NOUN", k=5
        )
        assert result == [{"chunk_id": "c1"}]

    def test_graph_neighbors_op_requires_entity_name(self):
        source = WebDataSource(name="test", graph_source=MemoryGraphSource("test"))
        with pytest.raises(ValueError, match="entity_name"):
            source.execute_query({"op": "WEB.GRAPH_NEIGHBORS"})

    def test_graph_query_op_dispatches(self):
        source = WebDataSource(name="test", graph_source=MemoryGraphSource("test"))
        source._pipeline.graph_query = MagicMock(return_value=[])
        source.execute_query({
            "op": "WEB.GRAPH_QUERY",
            "node_id": "doc:web_abc",
            "edge_type": "HAS_CHUNK",
            "direction": "out",
            "k": 10,
        })
        source._pipeline.graph_query.assert_called_once_with(
            "doc:web_abc", edge_type="HAS_CHUNK", direction="out", k=10
        )

    def test_graph_query_op_requires_node_id(self):
        source = WebDataSource(name="test", graph_source=MemoryGraphSource("test"))
        with pytest.raises(ValueError, match="node_id"):
            source.execute_query({"op": "WEB.GRAPH_QUERY"})
