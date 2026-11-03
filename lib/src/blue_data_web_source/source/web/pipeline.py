from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse

from blue_data.source.vector.vector_source import VectorSource
from blue_data_doc_source import CoverageReport, FindResult
from blue_data_doc_source.source.document.chunkers import Chunker, SectionChunker
from blue_data_doc_source.source.document.embeddings import EmbeddingModel
from blue_data_doc_source.source.document.enrichment import Enricher
from blue_data_doc_source.source.document.graph import GraphIndexer
from blue_data_doc_source.source.document.schema.base import DocumentClassifier, FieldExtractor
from blue_data_doc_source.source.document.schema.schema_store import MemoryRecordStore, RecordStore, _get_nested

from .extractor import WebExtractor
from .fetcher import FetchResult, WebFetcher
from .freshness import FreshnessStore
from .metadata import MetadataExtractor
from .politeness import DomainPoliteness
from .record import WebChunkRecord

_log = logging.getLogger(__name__)


# Ingest outcomes (#99). An empty chunk list no longer conflates "nothing was
# extractable" with the routine, successful "page was unchanged / is a duplicate"
# — callers map these to distinct messages (and exit codes) instead of treating
# every empty result as an error.
INGEST_INGESTED = "ingested"
INGEST_UNCHANGED = "unchanged"
INGEST_DUPLICATE = "duplicate"
INGEST_NO_CONTENT = "no_content"
INGEST_ERROR = "error"


@dataclass
class IngestResult:
    """Outcome of ingesting a single URL.

    ``status`` is one of :data:`INGEST_INGESTED`, :data:`INGEST_UNCHANGED`,
    :data:`INGEST_DUPLICATE`, :data:`INGEST_NO_CONTENT`, :data:`INGEST_ERROR`.
    ``records`` holds the chunk dicts and is non-empty only when the page was
    actually ``ingested``. ``duplicate_of`` is the ``page_id`` of the page that
    already owns this text (when ``status == "duplicate"`` and the freshness
    store can report it). ``error`` carries the message when ``status ==
    "error"``.
    """

    status: str
    url: str
    records: List[Dict[str, Any]] = field(default_factory=list)
    page_id: Optional[str] = None
    duplicate_of: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        """True for every non-failure outcome (ingested/unchanged/duplicate)."""
        return self.status in (INGEST_INGESTED, INGEST_UNCHANGED, INGEST_DUPLICATE)


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
        freshness_store: Optional[FreshnessStore] = None,
        max_workers: int = 10,
        classifier: Optional[DocumentClassifier] = None,
        field_extractor: Optional[FieldExtractor] = None,
        record_store: Optional[RecordStore] = None,
        graph_source: Optional[Any] = None,
        graph_database: str = "web",
        ingest_delay: float = 0.0,
    ):
        self._fetcher = WebFetcher(timeout=timeout)
        self._extractor = WebExtractor()
        self._metadata_extractor = MetadataExtractor()
        self._chunker = chunker or SectionChunker(chunk_size=chunk_size, overlap=overlap)
        self._embedding_model = embedding_model
        self._vector_source = vector_source
        self._enricher = enricher
        self._collection = collection
        self._freshness_store = freshness_store
        self._max_workers = max_workers
        self._classifier = classifier
        self._field_extractor = field_extractor
        self._record_store = record_store if record_store is not None else (
            MemoryRecordStore() if (classifier is not None or field_extractor is not None) else None
        )
        self._graph_indexer: Optional[GraphIndexer] = None
        if graph_source is not None:
            self._graph_indexer = GraphIndexer(graph_source, database=graph_database)
            self._graph_indexer.ensure_database()
        # Per-domain politeness for ingest_many: serialize same-host fetches so a
        # batch of URLs on one host is not hit with max_workers concurrent
        # requests. Different hosts still run concurrently. ingest_delay=0 (the
        # default) serializes without an artificial wait.
        self._politeness = DomainPoliteness(delay=ingest_delay)

    def ingest(
        self,
        url: str,
        collection: Optional[str] = None,
        _fetch_result: Optional[FetchResult] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch, extract, chunk, and embed a URL; return the chunk dicts.

        Thin wrapper over :meth:`ingest_result` that returns just the records
        (empty for unchanged/duplicate/no-content outcomes). Use
        :meth:`ingest_result` when the caller needs to tell those outcomes apart
        (#99).
        """
        return self.ingest_result(url, collection=collection, _fetch_result=_fetch_result).records

    def ingest_result(
        self,
        url: str,
        collection: Optional[str] = None,
        _fetch_result: Optional[FetchResult] = None,
    ) -> IngestResult:
        """Fetch, extract, chunk, and embed a URL, reporting the outcome.

        Args:
            url: Web page URL to ingest.
            collection: Override the instance-level collection for this call only.
            _fetch_result: Pre-fetched result to skip the HTTP fetch (used by crawler).

        Returns:
            An :class:`IngestResult` whose ``status`` distinguishes a successful
            ingest from the routine ``unchanged``/``duplicate`` skips and a
            genuine ``no_content`` extraction — so a caller no longer has to read
            an empty list as a failure (#99). ``records`` is non-empty only when
            ``status == "ingested"``.
        """
        effective_collection = collection or self._collection

        _log.info("ingesting %s", url)
        fetch_result = _fetch_result if _fetch_result is not None else self._fetcher.fetch(url)
        page_id = "web_" + sha256(fetch_result.final_url.encode()).hexdigest()[:16]
        extract_result = self._extractor.extract(fetch_result.html, fetch_result.final_url)
        if extract_result is None or not extract_result.text.strip():
            _log.info("no content: %s", url)
            return IngestResult(INGEST_NO_CONTENT, url, page_id=page_id)

        meta = self._metadata_extractor.extract(fetch_result.html)

        domain = urlparse(fetch_result.final_url).netloc
        content_hash = sha256(extract_result.text.strip().encode()).hexdigest()

        # Previous chunk count for this page, if known. Used after re-chunking to
        # delete stale surplus chunks when a page shrinks. Only a freshness_store
        # persists this across ingests, so it stays 0 when none is configured.
        prev_chunk_count = 0

        claimed_hash = False
        if self._freshness_store is not None:
            stored = self._freshness_store.get(effective_collection, page_id)
            if stored and stored.get("content_hash") == content_hash:
                _log.info("unchanged: %s", url)
                self._freshness_store.set(effective_collection, page_id, {
                    **stored,
                    "last_checked_at": datetime.now(timezone.utc).isoformat(),
                })
                return IngestResult(INGEST_UNCHANGED, url, page_id=page_id)

            # Cross-URL dedup within this collection, made atomic: claim the content
            # hash before the expensive chunk/embed/upsert so two identical-text URLs
            # ingested concurrently cannot both pass a check-then-act test (#46). A
            # False claim means another page in this collection already owns the text.
            if not self._freshness_store.claim_content_hash(effective_collection, content_hash, page_id):
                owner = self._freshness_store.owner_of_hash(effective_collection, content_hash)
                _log.debug("duplicate content: %s (collection %r)", url, effective_collection)
                # Another page in this collection already owns this text. If THIS
                # page previously held its own (different) content, that old content
                # is now stale: without cleanup its chunks, graph nodes, record and
                # freshness entry would stay and keep search returning the superseded
                # text indefinitely (#96). Purge them before returning. The old
                # freshness record's content-hash claim is released by delete(), so
                # the prior text can be ingested again elsewhere.
                # (Recording this URL as an alias of the owning page is #34.)
                if stored is not None and stored.get("content_hash") != content_hash:
                    self._purge_page(
                        effective_collection, page_id, stored.get("chunk_count", 0)
                    )
                return IngestResult(
                    INGEST_DUPLICATE, url, page_id=page_id, duplicate_of=owner
                )
            claimed_hash = True

            prev_chunk_count = stored.get("chunk_count", 0) if stored else 0

        try:
            # Classification and field extraction — once per page.
            # doc-source's DocumentClassifier.classify() returns a (document_type,
            # confidence) tuple (not a dict), matching how doc-source's own pipeline
            # unpacks it.
            page_type: Optional[str] = None
            if self._classifier is not None:
                document_type, confidence = self._classifier.classify(extract_result.text)
                page_type = document_type
                if self._record_store is not None:
                    fields: Dict[str, Any] = {}
                    if self._field_extractor is not None and page_type is not None:
                        fields = self._field_extractor.extract(extract_result.text, document_type=page_type)
                    self._record_store.upsert({
                        "document_id": page_id,
                        "document_type": page_type,
                        "confidence": confidence,
                        "classifier": type(self._classifier).__name__,
                        "url": fetch_result.final_url,
                        "fields": fields,
                    })

            chunks = self._chunker.chunk_with_titles(extract_result.text)
            records = []

            for idx, (section_title, chunk_text) in enumerate(chunks):
                chunk_id = f"{page_id}_chunk_{idx:04d}"

                embedding = None
                if self._embedding_model is not None:
                    embedding = self._embedding_model.embed_text(chunk_text)

                entities: list = []
                semantic_hints: dict = {}
                if self._enricher is not None:
                    enriched = self._enricher.enrich(chunk_text)
                    entities = enriched.get("entities", [])
                    semantic_hints = {k: v for k, v in enriched.items() if k != "entities"}

                record = WebChunkRecord(
                    url=fetch_result.final_url,
                    domain=domain,
                    chunk_id=chunk_id,
                    chunk_index=idx,
                    text=chunk_text,
                    title=extract_result.title,
                    author=extract_result.author or meta.author,
                    date=extract_result.date or meta.date_published,
                    description=(extract_result.description or meta.description) if idx == 0 else None,
                    language=extract_result.language,
                    collection=effective_collection,
                    section_title=section_title,
                    page_id=page_id,
                    embedding=embedding,
                    entities=entities,
                    semantic_hints=semantic_hints,
                    schema_type=meta.schema_type,
                    keywords=meta.keywords,
                    publisher=meta.publisher,
                    canonical_url=meta.canonical_url,
                )
                record_dict = record.to_dict()
                records.append(record_dict)

                if self._vector_source is not None and embedding is not None:
                    self._vector_source.upsert(
                        collection=effective_collection,
                        doc_id=chunk_id,
                        doc=record_dict,
                    )

            # Delete stale surplus chunks. Chunk IDs are positional
            # ({page_id}_chunk_{idx}); the upsert above overwrites indices
            # 0..len(records)-1, but when a page shrinks the old higher-indexed
            # chunks are left orphaned and still searchable. Remove them.
            # prev_chunk_count is 0 without a freshness_store, so this is a no-op
            # in that case (see docs: surplus cleanup requires a freshness_store).
            if self._vector_source is not None and self._embedding_model is not None:
                for idx in range(len(records), prev_chunk_count):
                    self._vector_source.delete(
                        collection=effective_collection,
                        doc_id=f"{page_id}_chunk_{idx:04d}",
                    )

            if self._graph_indexer is not None and records:
                # index_document is a no-op when the Document node already exists, so
                # a changed page would otherwise keep its stale graph. Delete the
                # prior document first to refresh it — this also drops section and
                # chunk nodes removed when a page shrinks. Shared entity nodes are
                # retained by delete_document. Safe no-op on first ingest.
                self._graph_indexer.delete_document(page_id)
                self._graph_indexer.index_document(
                    document_id=page_id,
                    file_name=fetch_result.final_url,
                    document_type=page_type,
                    records=records,
                )

            if self._freshness_store is not None:
                now = datetime.now(timezone.utc).isoformat()
                self._freshness_store.set(effective_collection, page_id, {
                    "page_id": page_id,
                    "content_hash": content_hash,
                    "url": fetch_result.final_url,
                    "chunk_count": len(records),
                    "last_ingested_at": now,
                    "last_checked_at": now,
                })

            _log.info("ingested %s: %d chunk%s", url, len(records), "s" if len(records) != 1 else "")
            return IngestResult(INGEST_INGESTED, url, records=records, page_id=page_id)
        except Exception:
            # An ingest that fails after claiming the content hash must release
            # the claim so the text is not blocked from a later ingest (#46).
            if claimed_hash and self._freshness_store is not None:
                self._freshness_store.release_content_hash(
                    effective_collection, content_hash, page_id
                )
            raise

    def _purge_page(self, collection: str, page_id: str, chunk_count: int) -> None:
        """Remove all stored state for a page whose content is now stale (#96).

        Deletes the page's chunks (``0..chunk_count-1``) from the vector store,
        drops its graph document, its structured record, and its freshness entry.
        Deleting the freshness entry also releases the page's old content-hash
        claim (see ``_apply_delete``), so the superseded text is free to be
        ingested again under another URL.

        Used when a re-ingested page's text has become identical to another
        page's: cross-URL dedup rejects the claim, and this page's prior chunks
        would otherwise stay searchable forever.
        """
        if self._vector_source is not None:
            for idx in range(chunk_count):
                self._vector_source.delete(
                    collection=collection,
                    doc_id=f"{page_id}_chunk_{idx:04d}",
                )
        if self._graph_indexer is not None:
            self._graph_indexer.delete_document(page_id)
        if self._record_store is not None:
            self._record_store.delete(page_id)
        if self._freshness_store is not None:
            self._freshness_store.delete(collection, page_id)

    def ingest_many(
        self,
        urls: List[str],
        collection: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Ingest multiple URLs concurrently.

        Uses a thread pool sized to ``max_workers``. Pages that fail to fetch or
        yield no content are silently skipped. Result order is not guaranteed.

        Per-domain politeness applies: URLs on the same host are processed one at
        a time (optionally spaced by ``ingest_delay``), while URLs on different
        hosts run concurrently. This prevents a batch of same-host URLs from
        being hit with ``max_workers`` simultaneous requests.
        """
        records: List[Dict[str, Any]] = []
        for result in self.ingest_many_results(urls, collection=collection):
            records.extend(result.records)
        return records

    def ingest_many_results(
        self,
        urls: List[str],
        collection: Optional[str] = None,
    ) -> List[IngestResult]:
        """Ingest multiple URLs concurrently, reporting each URL's outcome.

        Like :meth:`ingest_many` but returns one :class:`IngestResult` per URL so
        callers can tell ``unchanged``/``duplicate`` skips apart from genuine
        ``no_content`` / ``error`` failures (#99). A URL that raises is captured
        as an ``error`` result rather than silently dropped. Result order is not
        guaranteed.

        Per-domain politeness applies: URLs on the same host are processed one at
        a time (optionally spaced by ``ingest_delay``), while URLs on different
        hosts run concurrently.
        """
        if not urls:
            return []
        results: List[IngestResult] = []
        workers = min(self._max_workers, len(urls))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(self._ingest_result_polite, u, collection): u for u in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append(IngestResult(INGEST_ERROR, url, error=str(exc)))
        return results

    def _ingest_result_polite(self, url: str, collection: Optional[str]) -> IngestResult:
        """Ingest one URL while holding its per-host politeness lock."""
        with self._politeness.hold(url):
            return self.ingest_result(url, collection=collection)

    def ingest_feed(
        self,
        url: str,
        max_entries: Optional[int] = None,
        since: Optional[datetime] = None,
        collection: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch an RSS or Atom feed and ingest each article.

        Fetches the feed XML using the same ``WebFetcher`` as page ingestion,
        then calls ``ingest()`` on each article URL. Articles that fail to fetch
        or yield no content are silently skipped.

        Composes naturally with freshness tracking: if a ``freshness_store`` is
        set, articles whose content has not changed since the last ingest return
        no chunks without re-embedding.

        Args:
            url: RSS or Atom feed URL.
            max_entries: Cap on articles to process after date filtering. ``None``
                processes all entries that pass the ``since`` filter.
            since: Only ingest entries published/updated at or after this datetime
                (must be timezone-aware). Entries with no publication date are
                always included regardless of this filter.
            collection: Override the instance-level collection for this call only.

        Returns:
            Flat list of chunk dicts from all successfully ingested articles.
        """
        from .feed import FeedParser

        fetch_result = self._fetcher.fetch(url)
        # Parse the *fetched bytes* as data. Never hand feedparser a string it could
        # treat as a URL or file path (#43); FeedParser wraps the body in a stream.
        # Prefer the raw bytes; fall back to the decoded body if a caller/fetcher
        # supplied only that.
        body = fetch_result.content or fetch_result.html
        parsed = FeedParser().parse(body, url, content_type=fetch_result.content_type)

        entries = parsed.entries

        if since is not None:
            entries = [
                e for e in entries
                if e.published_parsed is None or e.published_parsed >= since
            ]

        if max_entries is not None:
            entries = entries[:max_entries]

        article_urls = [e.url for e in entries]
        return self.ingest_many(article_urls, collection=collection)

    def search(
        self,
        query: str,
        k: int = 5,
        collection: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Embed a query and return the k nearest chunks from the vector store.

        Args:
            query: Natural language query string.
            k: Number of results to return.
            collection: Override the instance-level collection for this call only.
            filters: Optional field equality pre-filter, e.g. ``{"page_id": ...}``.
                ``page_id`` is always stored on web chunks and is used to scope
                search to a specific page. Translated to blue-data's TAG filter
                expression (``@field:{value}``) and passed to ``knn`` as
                ``filter_expr``.

        Raises:
            ValueError: If ``embedding_model`` or ``vector_source`` is not set.
        """
        if self._embedding_model is None:
            raise ValueError("An embedding_model is required for search")
        if self._vector_source is None:
            raise ValueError("A vector_source is required for search")

        query_vector = self._embedding_model.embed_text(query)
        kwargs: Dict[str, Any] = {
            "index_name": collection or self._collection,
            "query_vector": query_vector,
            "k": k,
        }
        if filters:
            # blue-data's knn takes a RediSearch-style filter_expr, not a dict.
            # Build a TAG filter understood by both Memory and Redis backends.
            kwargs["filter_expr"] = " ".join(
                f"@{field}:{{{value}}}" for field, value in filters.items()
            )
        return [self._flatten_result(row) for row in self._vector_source.knn(**kwargs)]

    @staticmethod
    def _flatten_result(row: Dict[str, Any]) -> Dict[str, Any]:
        """Normalise a knn result row to a flat chunk dict.

        blue-data's Redis backend returns the stored chunk under a ``document``
        key (it issues ``RETURN $`` when no return_fields are given), while
        ``MemoryVectorSource`` returns the chunk fields flat. Merge the document
        up so callers always get flat fields (``url``, ``text``, ``chunk_id``,
        ``page_id``, ``title`` …) alongside the top-level ``id``/``score``.
        """
        doc = row.get("document")
        if not isinstance(doc, dict):
            return row
        flat = dict(doc)
        # Drop the stored embedding vector — Redis' RETURN $ includes it, but
        # MemoryVectorSource omits it from results; keep the two backends aligned
        # and results lean.
        flat.pop("embedding", None)
        for key, value in row.items():
            if key != "document":
                flat[key] = value
        return flat

    def find_then_search(
        self,
        query: str,
        find_filter: Optional[Dict[str, Any]] = None,
        k: int = 5,
        collection: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Filter web page records by fields, then search text within matched pages.

        Stage 1: ``find_records(find_filter)`` — deterministic field filter, no LLM.
        Stage 2: For each matched page, ``search(query, filters={"page_id": ...})``.
        Results are merged, deduplicated, sorted by score, and capped at ``k``.

        Requires both a ``record_store`` (for Stage 1) and an ``embedding_model``
        + ``vector_source`` (for Stage 2).

        Args:
            query: Text to search within matched pages.
            find_filter: Field filter dict — same syntax as ``WEB.FIND``.
            k: Maximum results to return.
            collection: Override the instance-level collection for this call only.

        Raises:
            ValueError: If no ``record_store`` is configured.
        """
        matched_pages = self.find_records(find_filter)
        if not matched_pages:
            return []
        page_ids = [p.get("document_id") for p in matched_pages]
        return self.search_in_pages(query, page_ids, k=k, collection=collection)

    def search_in_pages(
        self,
        query: str,
        page_ids: Sequence[Optional[str]],
        k: int = 5,
        collection: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Embed *query* and KNN-rank only within the given *page_ids*.

        Searches each page under a ``page_id`` pre-filter, then merges, de-duplicates
        by ``chunk_id``, sorts by score (ascending cosine distance = closest first)
        and caps at *k*. Falsy page IDs are skipped; an empty list yields ``[]``.

        Used to scope retrieval to a specific set of pages rather than the whole
        collection: the matches of a ``WEB.FIND`` filter (:meth:`find_then_search`)
        or the result set of the current live web search (``WEB.SEARCH_WEB`` with
        ``scope="current_search_results"``).
        """
        seen: set = set()
        all_results: List[Dict[str, Any]] = []
        for page_id in page_ids:
            if not page_id:
                continue
            for chunk in self.search(query, k=k, collection=collection, filters={"page_id": page_id}):
                chunk_id = chunk.get("chunk_id", "")
                if chunk_id not in seen:
                    seen.add(chunk_id)
                    all_results.append(chunk)
        # blue-data knn returns cosine distance in "score" (lower = closer), so
        # sort ascending to put the most similar chunks first.
        all_results.sort(key=lambda c: c.get("score") if c.get("score") is not None else float("inf"))
        return all_results[:k]

    def graph_neighbors(
        self,
        entity_name: str,
        entity_type: Optional[str] = None,
        k: int = 10,
    ) -> List[Dict[str, Any]]:
        """Find chunks that mention *entity_name* via graph traversal.

        Requires ``graph_source`` to have been set at construction time.

        Args:
            entity_name: Entity text to search for (case-insensitive).
            entity_type: Restrict to one entity type (PROPER_NOUN, DATE, …).
                When ``None``, all known entity types are tried.
            k: Maximum number of chunk results to return.

        Raises:
            ValueError: If no ``graph_source`` was configured.
        """
        if self._graph_indexer is None:
            raise ValueError(
                "graph_neighbors requires a graph_source. "
                "Pass graph_source= when constructing WebPipeline."
            )
        return self._graph_indexer.graph_neighbors(entity_name, entity_type=entity_type, k=k)

    def graph_query(
        self,
        node_id: str,
        edge_type: Optional[str] = None,
        direction: str = "both",
        k: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """General graph traversal from *node_id*.

        Args:
            node_id: Graph node ID (e.g. ``doc:web_abc123``,
                ``entity:PROPER_NOUN:apple inc``).
            edge_type: Edge type to follow. ``None`` traverses all types.
            direction: ``"out"``, ``"in"``, or ``"both"`` (default).
            k: Maximum number of results. ``None`` returns all.

        Raises:
            ValueError: If no ``graph_source`` was configured.
        """
        if self._graph_indexer is None:
            raise ValueError(
                "graph_query requires a graph_source. "
                "Pass graph_source= when constructing WebPipeline."
            )
        return self._graph_indexer.get_neighbors(
            node_id, edge_type=edge_type, direction=direction, k=k
        )

    def ensure_index(self, collection: Optional[str] = None) -> None:
        """Create the vector index for a collection if it does not already exist.

        Idempotent: a second call is a no-op. Some backends (e.g. Redis) raise
        "Index already exists" from ``create_index`` rather than ignoring the
        duplicate, so that specific error is swallowed here — calling this at
        every deploy or session start is safe, as the docs promise.
        """
        if self._vector_source is None or self._embedding_model is None:
            return
        name = collection or self._collection
        dim = self._embedding_model.dim
        # Index page_id as a TAG so FIND_THEN_SEARCH can scope a vector search to
        # specific pages via a @page_id:{...} filter on Redis. Not every backend
        # accepts extra_fields (MemoryVectorSource filters chunk dicts directly
        # and omits the kwarg), so fall back to a plain create_index there.
        page_id_tag = [{"path": "$.page_id", "name": "page_id", "field_type": "TAG"}]
        try:
            try:
                self._vector_source.create_index(
                    index_name=name, collection=name, dim=dim, extra_fields=page_id_tag,
                )
            except TypeError:
                self._vector_source.create_index(index_name=name, collection=name, dim=dim)
        except Exception as exc:  # noqa: BLE001 — backends raise varied error types
            if "already exists" in str(exc).lower():
                _log.debug("index %r already exists; ensure_index is a no-op", name)
                return
            raise

    def find_records(
        self,
        filter: Optional[Dict[str, Any]] = None,
        coverage: bool = False,
    ) -> "List[Dict[str, Any]] | FindResult":
        """Return web page records matching *filter* from the record store.

        Requires a ``classifier`` (and optionally a ``field_extractor``) to have
        been set — records are only written during ingest when a classifier is
        present.

        Args:
            filter: Structured filter dict (dot notation + operators). ``None``
                returns all records. Same syntax as ``DOCUMENT.FIND``.
            coverage: When ``True``, return a :class:`FindResult` with a
                :class:`CoverageReport` indicating how many pages were missing
                the queried ``fields.*`` keys.

        Raises:
            ValueError: If no ``record_store`` is configured.
        """
        if self._record_store is None:
            raise ValueError(
                "find_records requires a record_store. "
                "Set classifier= on WebPipeline to enable automatic record storage."
            )
        if not coverage:
            return self._record_store.find(filter)

        all_records = self._record_store.find(None)
        matched = self._record_store.find(filter)

        field_keys = [k for k in (filter or {}) if k.startswith("fields.")]
        documents_missing_field = (
            sum(
                1
                for r in all_records
                if any(_get_nested(r, k) is None for k in field_keys)
            )
            if field_keys
            else 0
        )

        documents_considered = len(all_records)
        field_coverage_rate = (
            (documents_considered - documents_missing_field) / documents_considered
            if documents_considered > 0
            else 0.0
        )

        if documents_considered == 0:
            status = "no_data"
        elif documents_missing_field == 0:
            status = "ok"
        else:
            status = "partial"

        warning: Optional[str] = None
        if status == "partial":
            names = ", ".join(f"'{k}'" for k in field_keys)
            warning = (
                f"Result may be incomplete: {documents_missing_field} of "
                f"{documents_considered} pages are missing the field(s) {names}."
            )

        return FindResult(
            records=matched,
            status=status,
            coverage=CoverageReport(
                documents_considered=documents_considered,
                documents_matched=len(matched),
                documents_missing_field=documents_missing_field,
                field_coverage_rate=field_coverage_rate,
            ),
            warning=warning,
        )
