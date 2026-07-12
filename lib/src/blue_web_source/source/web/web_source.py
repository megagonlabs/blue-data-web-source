from __future__ import annotations

from typing import Any, Dict, List, Optional

from blue_data.source.source import DataSource
from blue_data.source.vector.vector_source import VectorSource
from blue_doc_source.source.document.embeddings import EmbeddingModel
from blue_doc_source.source.document.enrichment import Enricher

from .pipeline import WebPipeline


class WebDataSource(DataSource):
    """Top-level entry point for web page ingestion and retrieval.

    Exposes an ``execute_query`` interface consistent with Blue's DataSource
    abstraction, dispatching on the ``"op"`` field of the request dict.

    Supported ops:

    - ``WEB.INGEST`` — fetch, extract, chunk, embed, and upsert a URL
    - ``WEB.SEARCH`` — embed a query and return matching chunks via KNN
    - ``WEB.ENSURE_INDEX`` — create the vector index for a collection

    Example::

        source = WebDataSource(
            name="news",
            embedding_model=OpenAIEmbeddingModel(),
            vector_source=RedisVectorSource(...),
        )
        records = source.execute_query({"op": "WEB.INGEST", "url": "https://example.com"})
        results = source.execute_query({"op": "WEB.SEARCH", "query": "climate policy", "k": 5})
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
        properties: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(name, properties)
        self._default_collection = default_collection
        self._pipeline = WebPipeline(
            chunk_size=chunk_size,
            overlap=overlap,
            embedding_model=embedding_model,
            vector_source=vector_source,
            enricher=enricher,
            collection=default_collection,
            timeout=timeout,
        )

    def execute_query(self, query, database=None, collection=None, optional_properties=None):
        op = query.get("op")

        if op == "WEB.INGEST":
            return self._ingest(query, collection)
        elif op == "WEB.SEARCH":
            return self._search(query, collection)
        elif op == "WEB.ENSURE_INDEX":
            return self._ensure_index(query, collection)
        else:
            raise ValueError(
                f"Unsupported op: {op!r}. "
                "Expected WEB.INGEST, WEB.SEARCH, or WEB.ENSURE_INDEX"
            )

    def _ingest(self, request: Dict[str, Any], collection_override=None) -> List[Dict[str, Any]]:
        url = request.get("url")
        if not url:
            raise ValueError("WEB.INGEST requires a 'url' field")
        collection = collection_override or request.get("collection")
        return self._pipeline.ingest(url, collection=collection)

    def _search(self, request: Dict[str, Any], collection_override=None) -> List[Dict[str, Any]]:
        query_text = request.get("query")
        if not query_text:
            raise ValueError("WEB.SEARCH requires a 'query' field")
        collection = collection_override or request.get("collection")
        k = request.get("k", 5)
        return self._pipeline.search(query_text, k=k, collection=collection)

    def _ensure_index(self, request: Dict[str, Any], collection_override=None) -> None:
        collection = collection_override or request.get("collection")
        self._pipeline.ensure_index(collection=collection)
