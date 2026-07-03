from __future__ import annotations

from typing import Callable

from blue_data.registry.search.search_engine import SearchEngine
from blue_data.registry.search.keyword_search import KeywordSearch
from blue_data.registry.search.hybrid_search import HybridSearch
from blue_data.registry.search.indexing import RecordIndexer


class RegistrySearch(SearchEngine):

    def __init__(
        self,
        registry,
        vector_source=None,
        embedding_fn: Callable[[str], list[float]] | None = None,
        index_name: str = "registry_index",
        vector_dim: int = 1536,
        hybrid_config: dict | None = None,
    ):
        self.registry = registry
        self.vector_source = vector_source
        self.embedding_fn = embedding_fn

        self.keyword_engine = KeywordSearch()
        self.hybrid_engine = HybridSearch(
            vector_source=vector_source,
            embedding_fn=embedding_fn,
            index_name=index_name,
            config=hybrid_config,
        )
        self.indexer = RecordIndexer(
            vector_source=vector_source,
            embedding_fn=embedding_fn,
            index_name=index_name,
            vector_dim=vector_dim,
        )

    # ---------- index maintenance ----------

    def index_record(self, record) -> None:
        self.indexer.set_record(record)

    def remove_record_from_index(self, name: str, type_: str, scope: str) -> None:
        self.indexer.delete_record(name, type_, scope)

    # ---------- search API ----------

    def keyword_search(
        self,
        query,
        scope=None,
        record_type=None,
        page=0,
        page_size=10,
    ):
        records = self.registry.list_records()
        return self.keyword_engine.search(
            records=records,
            query=query,
            scope=scope,
            record_type=record_type,
            page=page,
            page_size=page_size,
        )

    def vector_search(
        self,
        query: str,
        type: str | None = None,
        scope: str | None = None,
        page: int = 0,
        page_size: int = 10,
        **kwargs,
    ):
        if self.vector_source is None or self.embedding_fn is None:
            raise NotImplementedError(
                "vector_search requires a vector_source and embedding_fn"
            )
        records = self.registry.list_records()
        query_vector = self.hybrid_engine.vector_searcher.compute_embedding(query)
        if query_vector is None:
            return []

        scored = []
        for r in records:
            if type:
                rtype = r.get("type") if isinstance(r, dict) else getattr(r, "type", None)
                if rtype != type:
                    continue
            if scope:
                rscope = (r.get("scope") if isinstance(r, dict) else getattr(r, "scope", "/")) or "/"
                if not rscope.startswith(scope):
                    continue
            vec = self.hybrid_engine.vector_searcher.score_record(r, query_vector, {})
            scored.append((vec, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        start = page * page_size
        return [r for _, r in scored[start: start + page_size]]

    def hybrid_search(
        self,
        query: str,
        type: str | None = None,
        scope: str | None = None,
        page: int = 0,
        page_size: int = 10,
        **kwargs,
    ):
        records = self.registry.list_records()
        return self.hybrid_engine.search(
            records=records,
            input_query=query,
            type=type,
            scope=scope,
            page=page,
            page_size=page_size,
            **kwargs,
        )

    def search_records_hierarchical(
        self,
        query: str,
        type: str | None = None,
        scope: str | None = None,
        page: int = 0,
        page_size: int = 10,
        **kwargs,
    ):
        records = self.registry.list_records()
        if self.vector_source is not None and self.embedding_fn is not None:
            return self.hybrid_engine.search_hierarchical(
                records=records,
                input_query=query,
                type=type,
                scope=scope,
                page=page,
                page_size=page_size,
                **kwargs,
            )
        return self.keyword_engine.search(
            records=records,
            query=query,
            scope=scope,
            record_type=type,
            page=page,
            page_size=page_size,
        )
