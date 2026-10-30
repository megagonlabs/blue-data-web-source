from __future__ import annotations

import logging
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

_log = logging.getLogger(__name__)


class WebDataSource(DataSource):
    """Top-level entry point for web page ingestion and retrieval.

    Exposes an ``execute_query`` interface consistent with Blue's DataSource
    abstraction, dispatching on the ``"op"`` field of the request dict.

    Supported ops:

    - ``WEB.INGEST`` — fetch, extract, chunk, embed, and upsert one URL (``url``) or multiple (``urls``)
    - ``WEB.INGEST_FEED`` — fetch an RSS/Atom feed and ingest each article; optional ``max_items``
    - ``WEB.CRAWL`` — BFS crawl from a seed URL; configurable ``depth`` and ``max_pages``
    - ``WEB.SEARCH_WEB`` — search the web, ingest top-k pages on the fly, return ranked chunks.
      ``scope`` controls what is ranked: ``"current_search_results"`` (default) ranks only the
      pages this search returned; ``"collection"`` ranks over the whole ingested corpus (#35)
    - ``WEB.SEARCH`` — embed a query and return matching chunks via KNN
    - ``WEB.ENSURE_INDEX`` — create the vector index for a collection
    - ``WEB.EXTRACT`` — fetch a URL and extract structured fields via a caller-supplied JSON Schema

    Return types: ``execute_query`` returns ``list[dict]`` for every op, with two
    documented, opt-in exceptions that the generic (e.g. ``DataRegistry``) path
    never triggers:

    - ``WEB.EXTRACT`` with a single ``url`` returns one ``dict`` of fields (a
      ``urls`` batch returns ``list[dict]``);
    - ``WEB.FIND`` with ``coverage=True`` returns a :class:`FindResult` carrying a
      :class:`CoverageReport` (plain ``WEB.FIND`` returns ``list[dict]``).

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

    # Every op is in exactly one of these sets. blue-data's registry refuses an
    # op without ``optional_properties={"commit": True}`` unless it is a read
    # (#106). WEB.SEARCH_WEB is a write: it ingests the pages it fetches.
    _WRITE_OPS = frozenset({
        "WEB.INGEST",
        "WEB.INGEST_FEED",
        "WEB.CRAWL",
        "WEB.SEARCH_WEB",
        "WEB.ENSURE_INDEX",
    })
    _READ_OPS = frozenset({
        "WEB.SEARCH",
        "WEB.EXTRACT",
        "WEB.FIND",
        "WEB.FIND_THEN_SEARCH",
        "WEB.GRAPH_NEIGHBORS",
        "WEB.GRAPH_QUERY",
    })

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
        ingest_delay: float = 0.0,
    ):
        super().__init__(name, properties)
        props = self.properties

        # DataSource contract §4 (design/14): a source must be reconstructible from
        # (name, properties) alone — the registry's connect_source rebuilds it from
        # the stored record, with no Python-object collaborators to hand in. So the
        # embedding model, vector store, enricher and freshness store are built from
        # ``properties`` here. A collaborator passed explicitly still wins (direct,
        # in-process use); otherwise it is built from the matching properties key when
        # present; otherwise it stays unset (the historical no-properties default, so
        # existing callers are unaffected). Scalars take the properties value when set,
        # else the keyword default — so a rebuilt source reproduces the same config.
        default_collection = props.get("collection", default_collection)
        chunk_size = props.get("chunk_size", chunk_size)
        overlap = props.get("overlap", overlap)
        timeout = props.get("timeout", timeout)
        max_workers = props.get("max_workers", max_workers)
        ingest_delay = props.get("ingest_delay", ingest_delay)
        nl_model = props.get("nl_model", nl_model)
        graph_database = props.get("graph_database", graph_database)

        if embedding_model is None:
            embedding_model = self._build_embedding(props)
        if vector_source is None:
            vector_source = self._build_vector_source(props)
        if enricher is None:
            enricher = self._build_enricher(props)
        if freshness_store is None:
            freshness_store = self._build_freshness_store(props)

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
            ingest_delay=ingest_delay,
        )

    # ---------------------------------------------------------------- builders
    # Build collaborators from a ``properties`` dict so the source is
    # reconstructible from its stored record (DataSource contract §4). Each
    # returns ``None`` when its backend key is absent, preserving the historical
    # "no properties → no collaborator" constructor default.

    @staticmethod
    def _build_embedding(props: Dict[str, Any]) -> Optional[EmbeddingModel]:
        backend = props.get("embedding")
        if not backend:
            return None
        backend = str(backend).lower()
        if backend == "openai":
            from blue_data_doc_source.source.document.embeddings.openai_embedding import OpenAIEmbeddingModel
            return OpenAIEmbeddingModel(
                model=props.get("openai_embedding_model", "text-embedding-3-small")
            )
        if backend == "dummy":
            from blue_data_doc_source.source.document.embeddings.dummy_embedding import DummyEmbeddingModel
            return DummyEmbeddingModel()
        raise ValueError(f"Unknown embedding backend {backend!r} (expected 'openai' or 'dummy')")

    @staticmethod
    def _build_vector_source(props: Dict[str, Any]) -> Optional[VectorSource]:
        backend = props.get("vector")
        if not backend:
            return None
        backend = str(backend).lower()
        if backend == "memory":
            from blue_data.source.vector.memory_vector_source import MemoryVectorSource
            return MemoryVectorSource(name="memory")
        if backend == "redis":
            from blue_data.source.vector.redis.redis_vector_source import RedisVectorSource
            conn = dict(props.get("connection", {}))
            return RedisVectorSource(name="redis", properties={"connection": {
                "host": conn.get("host", "localhost"),
                "port": int(conn.get("port", 6379)),
                "scan_prefix": conn.get("scan_prefix", "web:"),
            }})
        raise ValueError(f"Unknown vector backend {backend!r} (expected 'memory' or 'redis')")

    @staticmethod
    def _build_enricher(props: Dict[str, Any]) -> Optional[Enricher]:
        backend = props.get("enricher")
        if not backend:
            return None
        backend = str(backend).lower()
        if backend == "none":
            return None
        if backend == "basic":
            from blue_data_doc_source.source.document.enrichment import BasicEnricher
            return BasicEnricher()
        if backend == "llm":
            from blue_data_doc_source.source.document.enrichment import BasicEnricher, LLMEnricher
            llm_model = props.get("llm_model", "gpt-4o-mini")

            def _llm_fn(prompt: str) -> str:
                from openai import OpenAI
                return OpenAI().chat.completions.create(
                    model=llm_model,
                    messages=[{"role": "user", "content": prompt}],
                ).choices[0].message.content or ""

            return LLMEnricher(llm_fn=_llm_fn, fallback=BasicEnricher())
        raise ValueError(f"Unknown enricher backend {backend!r} (expected 'none', 'basic' or 'llm')")

    @staticmethod
    def _build_freshness_store(props: Dict[str, Any]) -> Optional[FreshnessStore]:
        backend = props.get("freshness")
        if not backend:
            return None
        backend = str(backend).lower()
        if backend == "none":
            return None
        if backend == "memory":
            from .freshness import MemoryFreshnessStore
            return MemoryFreshnessStore()
        # "file" (default) and any unknown value fall back to the file store.
        import os
        from .freshness import FileFreshnessStore, MemoryFreshnessStore
        default_path = os.path.join(os.path.expanduser("~"), ".blue-web", "freshness.json")
        path = props.get("freshness_path", default_path)
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            return FileFreshnessStore(path)
        except OSError:
            _log.warning(
                "freshness=file but %r is not writable; falling back to an in-memory "
                "freshness store (no cleanup across separate runs).", path,
            )
            return MemoryFreshnessStore()

    @property
    def default_collection(self) -> str:
        return self._default_collection

    @classmethod
    def from_env(cls, name: str = "web") -> "WebDataSource":
        """Build a WebDataSource from standard BLUE_WEB_* environment variables.

        Environment variables
        ---------------------
        BLUE_WEB_EMBEDDING        openai | dummy  (default: dummy)
        OPENAI_API_KEY            required when BLUE_WEB_EMBEDDING=openai
        OPENAI_EMBEDDING_MODEL    default: text-embedding-3-small
        BLUE_WEB_REDIS_HOST       Redis host; omit to fall back to MemoryVectorSource
        BLUE_WEB_REDIS_PORT       default: 6379
        BLUE_WEB_REDIS_PREFIX     Redis key prefix for this source; default: "web:"
        BLUE_WEB_COLLECTION       default collection name (default: web)
        BLUE_WEB_CHUNK_SIZE       default: 1000
        BLUE_WEB_OVERLAP          default: 150
        BLUE_WEB_TIMEOUT          HTTP timeout in seconds; default: 30
        BLUE_WEB_MAX_WORKERS      concurrent fetch threads; default: 10
        BLUE_WEB_INGEST_DELAY     min seconds between same-host batch fetches; default: 0
        BLUE_WEB_ENRICHER         none | basic | llm  (default: basic)
        BLUE_WEB_LLM_MODEL        OpenAI model for llm enricher; default: gpt-4o-mini
        BLUE_WEB_FRESHNESS        file | memory | none  (default: file)
        BLUE_WEB_FRESHNESS_PATH   path for the file freshness store;
                                  default: ~/.blue-web/freshness.json
        TAVILY_API_KEY            required for WEB.SEARCH_WEB (read by TavilySearchProvider on first call)

        A freshness store is configured by default (``file``) so that the CLI,
        REST API, and MCP server — which all build their source via this
        factory — skip re-embedding unchanged pages and, crucially, delete
        stale surplus chunks when a page shrinks on re-ingest. Set
        ``BLUE_WEB_FRESHNESS=none`` to opt out.
        """
        import os

        # Translate BLUE_WEB_* env vars into a ``properties`` dict and let the
        # constructor build the collaborators from it. Routing through properties
        # (rather than passing Python objects) means a source built by this factory
        # is reconstructible from its stored record too — DataSource contract §4.
        props: Dict[str, Any] = {
            "embedding": os.getenv("BLUE_WEB_EMBEDDING", "dummy").lower(),
            "enricher": os.getenv("BLUE_WEB_ENRICHER", "basic").lower(),
            "collection": os.getenv("BLUE_WEB_COLLECTION", "web"),
            "chunk_size": int(os.getenv("BLUE_WEB_CHUNK_SIZE", "1000")),
            "overlap": int(os.getenv("BLUE_WEB_OVERLAP", "150")),
            "timeout": float(os.getenv("BLUE_WEB_TIMEOUT", "30")),
            "max_workers": int(os.getenv("BLUE_WEB_MAX_WORKERS", "10")),
            "ingest_delay": float(os.getenv("BLUE_WEB_INGEST_DELAY", "0")),
        }
        if props["embedding"] == "openai":
            props["openai_embedding_model"] = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        if props["enricher"] == "llm":
            props["llm_model"] = os.getenv("BLUE_WEB_LLM_MODEL", "gpt-4o-mini")

        redis_host = os.getenv("BLUE_WEB_REDIS_HOST")
        if redis_host:
            props["vector"] = "redis"
            props["connection"] = {
                "host": redis_host,
                "port": int(os.getenv("BLUE_WEB_REDIS_PORT", "6379")),
                "scan_prefix": os.getenv("BLUE_WEB_REDIS_PREFIX", "web:"),
            }
        else:
            # No Redis configured — fall back to an in-memory vector store so the
            # source is still searchable (data is lost on restart), as documented.
            props["vector"] = "memory"

        # Default the freshness store to the vector store's volatility. A persistent
        # (file) freshness store paired with an in-memory vector store would, after a
        # restart, skip re-ingesting a known-unchanged page while its vectors are gone
        # — leaving it permanently unsearchable (#94). So with an in-memory vector
        # store, default freshness to memory too; with a persistent (Redis) vector
        # store it stays file-backed. An explicit BLUE_WEB_FRESHNESS always wins.
        default_freshness = "memory" if props["vector"] == "memory" else "file"
        props["freshness"] = os.getenv("BLUE_WEB_FRESHNESS", default_freshness).lower()

        freshness_path = os.getenv("BLUE_WEB_FRESHNESS_PATH")
        if freshness_path:
            props["freshness_path"] = freshness_path

        return cls(name=name, properties=props)

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
            max_attempts=request.get("max_attempts"),
        )

    def _search_web(self, request: Dict[str, Any], collection_override=None) -> List[Dict[str, Any]]:
        query = request.get("query")
        if not query:
            raise ValueError("WEB.SEARCH_WEB requires a 'query' field")
        scope = request.get("scope", "current_search_results")
        if scope not in ("current_search_results", "collection"):
            raise ValueError(
                "WEB.SEARCH_WEB 'scope' must be 'current_search_results' (default, "
                "rank only this search's result pages) or 'collection' (rank over "
                f"the whole collection). Got {scope!r}."
            )
        if self._search_provider is None:
            from .search_provider import TavilySearchProvider
            self._search_provider = TavilySearchProvider()
        collection = collection_override or request.get("collection")
        k = request.get("k", 5)
        top_k = request.get("top_k", k)
        urls = self._search_provider.search(query, k=k)
        # Ingest via ingest_many so a single bad result URL (403/404/timeout/no
        # content) is skipped instead of aborting the whole op — ingest_many
        # isolates per-URL failures (and ingests concurrently). A plain loop here
        # would let one exception propagate and lose every other result.
        records = self._pipeline.ingest_many(urls, collection=collection)
        if urls:
            ingested_urls = {r["url"] for r in records}
            if len(ingested_urls) < len(urls):
                _log.info(
                    "WEB.SEARCH_WEB: ingested chunks from %d of %d result URL(s); "
                    "the rest failed, were empty, or were unchanged",
                    len(ingested_urls), len(urls),
                )

        if scope == "collection":
            # Rank over everything ever ingested into the collection.
            return self._pipeline.search(query, k=top_k, collection=collection)

        # Default: rank only within the pages this search actually ingested, so a
        # live web search never surfaces an old, unrelated page that merely happens
        # to be in the collection (#35). Scope is the distinct page_ids from this
        # call's records; a result page skipped as unchanged by freshness tracking
        # is not re-added here — pass scope="collection" to rank over the corpus.
        page_ids: List[str] = []
        seen: set = set()
        for record in records:
            page_id = record.get("page_id")
            if page_id and page_id not in seen:
                seen.add(page_id)
                page_ids.append(page_id)
        return self._pipeline.search_in_pages(query, page_ids, k=top_k, collection=collection)

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

    def _ensure_index(self, request: Dict[str, Any], collection_override=None) -> List[Dict[str, Any]]:
        collection = collection_override or request.get("collection")
        self._pipeline.ensure_index(collection=collection)
        # Return a list[dict] rather than None so the op honours the DataSource
        # execute_query -> list[dict] contract (#50).
        return [{"status": "ok", "collection": collection or self._default_collection}]

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

    def translate_nl_to_native_query(self, query: str, schema=None, llm_fn=None):
        """Translate a natural-language request into a ``WEB.*`` op dict.

        This is the blue-data ``DataSource`` extension hook. Overriding it (rather
        than ``execute_query_nl``) means NL queries go through the base
        ``execute_query_nl(query, database, collection, schema, llm_fn,
        optional_properties, read_only)`` — the exact signature
        ``DataRegistry.execute_query_nl`` calls (#50).

        Uses ``llm_fn`` (a ``(prompt) -> str`` callable, e.g. supplied by the
        registry) when given; otherwise falls back to the built-in Claude
        tool-use parser, which needs the ``anthropic`` extra and
        ``ANTHROPIC_API_KEY``. Override the Claude model via ``nl_model`` in the
        constructor.
        """
        from .nl import NLQueryParser
        if self._nl_parser is None:
            self._nl_parser = NLQueryParser(model=self._nl_model)
        if llm_fn is not None:
            return self._nl_parser.parse_with_llm_fn(query, llm_fn)
        return self._nl_parser.parse(query)

    def execute_query_nl(
        self,
        query: str,
        database=None,
        collection=None,
        schema=None,
        llm_fn=None,
        optional_properties=None,
        read_only: bool = True,
    ):
        """Translate a natural-language *query* and execute it.

        Matches blue-data's ``DataSource.execute_query_nl`` signature, which is
        exactly how ``DataRegistry.execute_query_nl`` invokes a source (#50). The
        translation goes through :meth:`translate_nl_to_native_query`, which uses
        ``llm_fn`` when supplied and otherwise the built-in Claude tool-use parser.

        Defined explicitly (rather than relying on the inherited base method) so
        it works regardless of the installed blue-data version. Returns the
        op's result — ``list[dict]`` for the ordinary ops.
        """
        native_query = self.translate_nl_to_native_query(query, schema=schema, llm_fn=llm_fn)
        if not native_query:
            return []
        props = dict(optional_properties or {})
        if read_only:
            props.pop("commit", None)
        return self.execute_query(
            native_query, database=database, collection=collection, optional_properties=props,
        )
