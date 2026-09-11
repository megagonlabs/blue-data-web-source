from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from blue_data.source.source import DataSource
from blue_data.source.vector.vector_source import VectorSource
from blue_data_doc_source.source.document.embeddings import EmbeddingModel
from blue_data_doc_source.source.document.enrichment import Enricher
from blue_data_doc_source.source.document.schema.base import DocumentClassifier, FieldExtractor
from blue_data_doc_source.source.document.schema.schema_store import RecordStore

if TYPE_CHECKING:
    from blue_data.source.graph.graph_source import GraphSource

from .freshness import FreshnessStore
from .pipeline import WebPipeline
from .search_provider import WebSearchProvider


class WebDataSource(DataSource):
    """Top-level entry point for web page ingestion and retrieval.

    Exposes an ``execute_query`` interface consistent with Blue's DataSource
    abstraction, dispatching on the ``"op"`` field of the request dict.

    Supported ops:

    - ``WEB.INGEST`` — fetch, extract, chunk, embed, and upsert one URL (``url``) or multiple (``urls``)
    - ``WEB.INGEST_FEED`` — fetch an RSS/Atom feed and ingest each article; optional ``max_items``
    - ``WEB.CRAWL`` — BFS crawl from a seed URL; configurable ``depth`` and ``max_pages``
    - ``WEB.SEARCH_WEB`` — search the web, ingest top-k pages on the fly, return ranked chunks
    - ``WEB.SEARCH`` — embed a query and return matching chunks via KNN
    - ``WEB.ENSURE_INDEX`` — create the vector index for a collection
    - ``WEB.EXTRACT`` — fetch a URL and extract structured fields via a caller-supplied JSON Schema

    Example::

        source = WebDataSource(
            name="news",
            embedding_model=OpenAIEmbeddingModel(),
            vector_source=RedisVectorSource(...),
        )
        records = source.execute_query({"op": "WEB.INGEST", "url": "https://example.com"})
        results = source.execute_query({"op": "WEB.SEARCH", "query": "climate policy", "k": 5})
        results = source.execute_query_nl("search for climate policy articles")
    """

    def __init__(
        self,
        name: str = "web",
        embedding_model: Optional[EmbeddingModel] = None,
        vector_source: Optional[VectorSource] = None,
        enricher: Optional[Enricher] = None,
        chunk_size: int = 1000,
        overlap: int = 150,
        default_collection: str = "web",
        timeout: float = 30.0,
        nl_model: str = "claude-opus-4-8",
        search_provider: Optional[WebSearchProvider] = None,
        freshness_store: Optional[FreshnessStore] = None,
        max_workers: int = 10,
        properties: Optional[Dict[str, Any]] = None,
        classifier: Optional[DocumentClassifier] = None,
        field_extractor: Optional[FieldExtractor] = None,
        record_store: Optional[RecordStore] = None,
        graph_source: Optional["GraphSource"] = None,
        graph_database: str = "web",
    ):
        super().__init__(name, properties)
        self._default_collection = default_collection
        self._nl_model = nl_model
        self._nl_parser = None
        self._search_provider = search_provider
        self._max_workers = max_workers
        self._pipeline = WebPipeline(
            chunk_size=chunk_size,
            overlap=overlap,
            embedding_model=embedding_model,
            vector_source=vector_source,
            enricher=enricher,
            collection=default_collection,
            timeout=timeout,
            freshness_store=freshness_store,
            max_workers=max_workers,
            classifier=classifier,
            field_extractor=field_extractor,
            record_store=record_store,
            graph_source=graph_source,
            graph_database=graph_database,
        )

    def execute_query(self, query, database=None, collection=None, optional_properties=None):
        op = query.get("op")

        if op == "WEB.INGEST":
            return self._ingest(query, collection)
        elif op == "WEB.INGEST_FEED":
            return self._ingest_feed(query, collection)
        elif op == "WEB.CRAWL":
            return self._crawl(query, collection)
        elif op == "WEB.SEARCH_WEB":
            return self._search_web(query, collection)
        elif op == "WEB.SEARCH":
            return self._search(query, collection)
        elif op == "WEB.ENSURE_INDEX":
            return self._ensure_index(query, collection)
        elif op == "WEB.EXTRACT":
            return self._extract(query)
        elif op == "WEB.FIND":
            return self._find(query)
        elif op == "WEB.FIND_THEN_SEARCH":
            return self._find_then_search(query, collection)
        elif op == "WEB.GRAPH_NEIGHBORS":
            return self._graph_neighbors(query)
        elif op == "WEB.GRAPH_QUERY":
            return self._graph_query(query)
        else:
            raise ValueError(
                f"Unsupported op: {op!r}. "
                "Expected WEB.INGEST, WEB.INGEST_FEED, WEB.CRAWL, WEB.SEARCH_WEB, "
                "WEB.SEARCH, WEB.ENSURE_INDEX, WEB.EXTRACT, WEB.FIND, "
                "WEB.FIND_THEN_SEARCH, WEB.GRAPH_NEIGHBORS, or WEB.GRAPH_QUERY"
            )

    def _ingest(self, request: Dict[str, Any], collection_override=None) -> List[Dict[str, Any]]:
        url = request.get("url")
        urls = request.get("urls")
        if not url and not urls:
            raise ValueError("WEB.INGEST requires a 'url' or 'urls' field")
        collection = collection_override or request.get("collection")
        targets = [url] if url else urls
        if len(targets) == 1:
            return self._pipeline.ingest(targets[0], collection=collection)
        return self._pipeline.ingest_many(targets, collection=collection)

    def _ingest_feed(self, request: Dict[str, Any], collection_override=None) -> List[Dict[str, Any]]:
        url = request.get("url")
        if not url:
            raise ValueError("WEB.INGEST_FEED requires a 'url' field")
        collection = collection_override or request.get("collection")
        max_entries = request.get("max_entries")
        since: Optional[datetime] = None
        since_str = request.get("since")
        if since_str:
            since = datetime.fromisoformat(since_str.replace("Z", "+00:00"))
        return self._pipeline.ingest_feed(
            url, max_entries=max_entries, since=since, collection=collection
        )

    def _search(self, request: Dict[str, Any], collection_override=None) -> List[Dict[str, Any]]:
        query_text = request.get("query")
        if not query_text:
            raise ValueError("WEB.SEARCH requires a 'query' field")
        collection = collection_override or request.get("collection")
        k = request.get("k", 5)
        return self._pipeline.search(query_text, k=k, collection=collection)

    def _crawl(self, request: Dict[str, Any], collection_override=None) -> List[Dict[str, Any]]:
        from .crawler import WebCrawler
        url = request.get("url")
        if not url:
            raise ValueError("WEB.CRAWL requires a 'url' field")
        collection = collection_override or request.get("collection")
        crawler = WebCrawler(
            pipeline=self._pipeline,
            fetcher=self._pipeline._fetcher,
            same_domain_only=request.get("same_domain_only", True),
            respect_robots=request.get("respect_robots", True),
            crawl_delay=request.get("crawl_delay", 1.0),
            max_retries=request.get("max_retries", 2),
            max_workers=request.get("max_workers", self._max_workers),
        )
        return crawler.crawl(
            seed_url=url,
            depth=request.get("depth", 1),
            max_pages=request.get("max_pages", 10),
            collection=collection,
        )

    def _search_web(self, request: Dict[str, Any], collection_override=None) -> List[Dict[str, Any]]:
        query = request.get("query")
        if not query:
            raise ValueError("WEB.SEARCH_WEB requires a 'query' field")
        if self._search_provider is None:
            from .search_provider import TavilySearchProvider
            self._search_provider = TavilySearchProvider()
        collection = collection_override or request.get("collection")
        k = request.get("k", 5)
        top_k = request.get("top_k", k)
        urls = self._search_provider.search(query, k=k)
        for url in urls:
            self._pipeline.ingest(url, collection=collection)
        return self._pipeline.search(query, k=top_k, collection=collection)

    def _extract(self, request: Dict[str, Any]) -> Any:
        url = request.get("url")
        urls = request.get("urls")
        schema = request.get("schema")
        if not url and not urls:
            raise ValueError("WEB.EXTRACT requires a 'url' or 'urls' field")
        if not schema:
            raise ValueError("WEB.EXTRACT requires a 'schema' field (JSON Schema dict or Pydantic model)")

        try:
            from blue_data_doc_source.source.document.schema import StructuredFieldExtractor
        except ImportError as exc:
            raise ImportError(
                "blue-data-doc-source with openai is required for WEB.EXTRACT. "
                "Install with: pip install 'blue-data-web-source[openai]'"
            ) from exc

        extractor = StructuredFieldExtractor(schema=schema)
        def _extract_one(target_url: str) -> Dict[str, Any]:
            fetch_result = self._pipeline._fetcher.fetch(target_url)
            extract_result = self._pipeline._extractor.extract(fetch_result.html, fetch_result.final_url)
            if extract_result is None or not extract_result.text.strip():
                return {}
            return extractor.extract(extract_result.text, document_type="web_page")

        if url:
            return _extract_one(url)

        results = []
        for target_url in urls:
            fields = _extract_one(target_url)
            if fields:
                fields["url"] = target_url
            results.append(fields)
        return results

    def _ensure_index(self, request: Dict[str, Any], collection_override=None) -> None:
        collection = collection_override or request.get("collection")
        self._pipeline.ensure_index(collection=collection)

    def _find(self, request: Dict[str, Any]):
        """Implement WEB.FIND — filter web page records by extracted field values.

        Requires the source to have been constructed with a ``classifier``.
        Pass ``"coverage": true`` to receive a :class:`FindResult` with a
        :class:`CoverageReport` instead of a plain list.
        """
        return self._pipeline.find_records(
            request.get("filter"),
            coverage=bool(request.get("coverage", False)),
        )

    def _find_then_search(self, request: Dict[str, Any], collection_override=None) -> List[Dict[str, Any]]:
        """Implement WEB.FIND_THEN_SEARCH.

        Stage 1: filter web page records by ``find_filter`` (deterministic, no LLM).
        Stage 2: vector search scoped to each matched page.
        Results merged, deduped, sorted by score, capped at ``k``.

        Required request fields:
            ``query`` — text to search within matched pages

        Optional request fields:
            ``find_filter`` — field filter dict (same syntax as WEB.FIND)
            ``k`` — number of results (default 5)
            ``collection`` — collection to search in
        """
        query_text = request.get("query")
        if not query_text:
            raise ValueError("WEB.FIND_THEN_SEARCH requires a 'query' field")
        collection = collection_override or request.get("collection")
        return self._pipeline.find_then_search(
            query=query_text,
            find_filter=request.get("find_filter"),
            k=request.get("k", 5),
            collection=collection,
        )

    def _graph_neighbors(self, request: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Implement WEB.GRAPH_NEIGHBORS — entity lookup via graph traversal.

        Required request fields:
            ``entity_name`` — entity text to search for (case-insensitive)

        Optional request fields:
            ``entity_type`` — restrict to one type (PROPER_NOUN, DATE, …)
            ``k`` — maximum results (default 10)
        """
        entity_name = request.get("entity_name")
        if not entity_name:
            raise ValueError("WEB.GRAPH_NEIGHBORS requires an 'entity_name' field")
        return self._pipeline.graph_neighbors(
            entity_name,
            entity_type=request.get("entity_type"),
            k=request.get("k", 10),
        )

    def _graph_query(self, request: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Implement WEB.GRAPH_QUERY — general graph traversal from any node ID.

        Required request fields:
            ``node_id`` — graph node ID to start from

        Optional request fields:
            ``edge_type`` — edge type to follow (default: all types)
            ``direction`` — ``"out"``, ``"in"``, or ``"both"`` (default)
            ``k`` — maximum results (default: all)
        """
        node_id = request.get("node_id")
        if not node_id:
            raise ValueError("WEB.GRAPH_QUERY requires a 'node_id' field")
        return self._pipeline.graph_query(
            node_id,
            edge_type=request.get("edge_type"),
            direction=request.get("direction", "both"),
            k=request.get("k"),
        )

    def execute_query_nl(self, text: str, collection=None):
        """Parse *text* as a natural language query and execute it.

        Uses Claude tool use to classify intent and extract parameters, then
        dispatches to ``execute_query``. Requires the ``anthropic`` package::

            pip install 'blue-data-web-source[nl]'

        Override the Claude model via ``nl_model`` in the constructor.
        """
        from .nl import NLQueryParser
        if self._nl_parser is None:
            self._nl_parser = NLQueryParser(model=self._nl_model)
        query = self._nl_parser.parse(text)
        return self.execute_query(query, collection=collection)
