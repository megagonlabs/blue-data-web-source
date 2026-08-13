from __future__ import annotations

from hashlib import sha256
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from blue_data.source.vector.vector_source import VectorSource
from blue_data_doc_source.source.document.chunkers import Chunker, SectionChunker
from blue_data_doc_source.source.document.embeddings import EmbeddingModel
from blue_data_doc_source.source.document.enrichment import Enricher

from .extractor import WebExtractor
from .fetcher import FetchResult, WebFetcher
from .record import WebChunkRecord


class WebPipeline:
    """Orchestrates the full web ingestion pipeline.

    Chains fetch → extract → chunk → enrich → embed → WebChunkRecord
    construction → optional vector store upsert.

    All optional components (``embedding_model``, ``vector_source``,
    ``enricher``) can be omitted. Upsert is skipped unless both
    ``embedding_model`` and ``vector_source`` are set.
    """

    def __init__(
        self,
        chunk_size: int = 1000,
        overlap: int = 150,
        chunker: Optional[Chunker] = None,
        embedding_model: Optional[EmbeddingModel] = None,
        vector_source: Optional[VectorSource] = None,
        enricher: Optional[Enricher] = None,
        collection: str = "web",
        timeout: float = 30.0,
    ):
        self._fetcher = WebFetcher(timeout=timeout)
        self._extractor = WebExtractor()
        self._chunker = chunker or SectionChunker(chunk_size=chunk_size, overlap=overlap)
        self._embedding_model = embedding_model
        self._vector_source = vector_source
        self._enricher = enricher
        self._collection = collection

    def ingest(
        self,
        url: str,
        collection: Optional[str] = None,
        _fetch_result: Optional[FetchResult] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch, extract, chunk, and embed a URL.

        Args:
            url: Web page URL to ingest.
            collection: Override the instance-level collection for this call only.
            _fetch_result: Pre-fetched result to skip the HTTP fetch (used by crawler).

        Returns:
            List of ``WebChunkRecord.to_dict()`` dicts, one per chunk.
            Returns an empty list if the page yields no extractable text.
        """
        effective_collection = collection or self._collection

        fetch_result = _fetch_result if _fetch_result is not None else self._fetcher.fetch(url)
        extract_result = self._extractor.extract(fetch_result.html, fetch_result.final_url)
        if extract_result is None or not extract_result.text.strip():
            return []

        domain = urlparse(fetch_result.final_url).netloc
        page_id = "web_" + sha256(fetch_result.final_url.encode()).hexdigest()[:16]
        chunks = self._chunker.chunk_with_titles(extract_result.text)
        records = []

        for idx, (section_title, chunk_text) in enumerate(chunks):
            chunk_id = f"{page_id}_chunk_{idx:04d}"

            embedding = None
            if self._embedding_model is not None:
                embedding = self._embedding_model.embed_text(chunk_text)

            entities: list = []
            if self._enricher is not None:
                enriched = self._enricher.enrich(chunk_text)
                entities = enriched.get("entities", [])

            record = WebChunkRecord(
                url=fetch_result.final_url,
                domain=domain,
                chunk_id=chunk_id,
                chunk_index=idx,
                text=chunk_text,
                title=extract_result.title,
                author=extract_result.author,
                date=extract_result.date,
                description=extract_result.description if idx == 0 else None,
                language=extract_result.language,
                collection=effective_collection,
                section_title=section_title,
                embedding=embedding,
            )
            record_dict = record.to_dict()
            if entities:
                record_dict["entities"] = entities
            records.append(record_dict)

            if self._vector_source is not None and embedding is not None:
                self._vector_source.upsert(
                    collection=effective_collection,
                    doc_id=chunk_id,
                    doc=record_dict,
                )

        return records

    def search(self, query: str, k: int = 5, collection: Optional[str] = None) -> List[Dict[str, Any]]:
        """Embed a query and return the k nearest chunks from the vector store.

        Args:
            query: Natural language query string.
            k: Number of results to return.
            collection: Override the instance-level collection for this call only.

        Raises:
            ValueError: If ``embedding_model`` or ``vector_source`` is not set.
        """
        if self._embedding_model is None:
            raise ValueError("An embedding_model is required for search")
        if self._vector_source is None:
            raise ValueError("A vector_source is required for search")

        query_vector = self._embedding_model.embed_text(query)
        return self._vector_source.knn(
            index_name=collection or self._collection,
            query_vector=query_vector,
            k=k,
        )

    def ensure_index(self, collection: Optional[str] = None) -> None:
        """Create the vector index for a collection if it does not already exist."""
        if self._vector_source is None or self._embedding_model is None:
            return
        self._vector_source.create_index(
            index_name=collection or self._collection,
            collection=collection or self._collection,
            dim=self._embedding_model.dim,
        )
