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

    - ``WEB.INGEST`` — fetch, extract, chunk, embed, and upsert one URL (``url``) or multiple (``urls``)
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
        properties: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(name, properties)
        self._default_collection = default_collection
        self._nl_model = nl_model
        self._nl_parser = None
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
        urls = request.get("urls")
        if not url and not urls:
            raise ValueError("WEB.INGEST requires a 'url' or 'urls' field")
        collection = collection_override or request.get("collection")
        targets = [url] if url else urls
        results: List[Dict[str, Any]] = []
        for u in targets:
            results.extend(self._pipeline.ingest(u, collection=collection))
        return results

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
