from __future__ import annotations

from typing import Callable

from blue_data.registry.search.vector_search import VectorSearch
from blue_data.registry.search.indexing import RecordIndexer
from blue_data.registry.search.ranking import (
    combine_scores,
    normalize_weights,
    aggregate_value_scores,
    find_best_score_among_nodes,
)
from blue_data.core.utils.similarity_utils import compute_bm25_score, normalize_bm25_scores

DEFAULT_PARAMS: dict = {
    "bm25_weight": 0.3,
    "vector_weight": 0.7,
    "value_weight": 0.15,
    "bm25_normalization": "minmax",
    "enable_schema": True,
    "bm25_threshold": 0.0,
    "vector_threshold": 0.5,
    "value_threshold": 0.3,
    "combined_threshold": 0.36,
    "hierarchical_enabled": True,
    "hierarchical_database_types": ["database", "collection", "entity", "attribute"],
    "hierarchical_collection_types": ["collection", "entity", "attribute"],
    "search_limit": 1000,
}


class HybridSearch:
    """Three-component search: BM25 + vector similarity + value-relevance.

    Works without a vector_source / embedding_fn — degrades to keyword-only.
    """

    def __init__(
        self,
        vector_source=None,
        embedding_fn: Callable[[str], list[float]] | None = None,
        index_name: str = "registry_index",
        config: dict | None = None,
    ):
        self.vector_searcher = VectorSearch(
            vector_source=vector_source,
            embedding_fn=embedding_fn,
            index_name=index_name,
        )
        self.indexer = RecordIndexer(
            vector_source=vector_source,
            embedding_fn=embedding_fn,
            index_name=index_name,
        )
        self.config = {**DEFAULT_PARAMS, **(config or {})}

    # ---------- params ----------

    def prepare_parameters(
        self,
        input_query: str,
        type: str | None = None,
        scope: str | None = None,
        **overrides,
    ) -> dict:
        params = {**self.config, **overrides}
        params["query"] = input_query
        params["type"] = type
        params["scope"] = scope
        bw, vw, vaw = normalize_weights(
            params["bm25_weight"],
            params["vector_weight"],
            params["value_weight"],
        )
        params["bm25_weight"] = bw
        params["vector_weight"] = vw
        params["value_weight"] = vaw
        return params

    # ---------- filtering ----------

    def _filter_records(self, records: list, type: str | None, scope: str | None) -> list:
        out = []
        for r in records:
            if type:
                rtype = r.get("type") if isinstance(r, dict) else getattr(r, "type", None)
                if rtype != type:
                    continue
            if scope:
                rscope = r.get("scope") if isinstance(r, dict) else getattr(r, "scope", None)
                rscope = rscope or "/"
                if not rscope.startswith(scope):
                    continue
            out.append(r)
        return out

    # ---------- text for a record ----------

    @staticmethod
    def _record_doc_text(record) -> str:
        if isinstance(record, dict):
            parts = [record.get("name", ""), record.get("description", "")]
            meta = (record.get("properties") or {}).get("metadata") or {}
            parts.append(meta.get("description", "") if isinstance(meta, dict) else "")
        else:
            parts = [
                getattr(record, "name", ""),
                getattr(record, "description", ""),
            ]
        return " ".join(p for p in parts if p)

    @staticmethod
    def _schema_text(record) -> str | None:
        props = (
            record.get("properties", {})
            if isinstance(record, dict)
            else getattr(record, "properties", {})
        ) or {}
        schema = props.get("schema") if isinstance(props, dict) else None
        if not schema:
            return None
        if isinstance(schema, dict):
            parts = []
            for ent in schema.get("entities", {}).values():
                parts.append(ent.get("name", ""))
                for attr in ent.get("attributes", {}).values():
                    parts.append(attr.get("name", ""))
            return " ".join(p for p in parts if p) or None
        if isinstance(schema, str):
            return schema
        return None

    # ---------- core search ----------

    def search(
        self,
        records: list,
        input_query: str,
        type: str | None = None,
        scope: str | None = None,
        page: int = 0,
        page_size: int = 10,
        **overrides,
    ) -> list[dict]:
        params = self.prepare_parameters(input_query, type=type, scope=scope, **overrides)
        candidates = self._filter_records(records, type, scope)[: params["search_limit"]]

        query_vector = self.vector_searcher.compute_embedding(input_query)

        # --- BM25 pass ---
        raw_bm25: list[float] = []
        schema_enabled = params["enable_schema"]
        for r in candidates:
            doc_text = self._record_doc_text(r)
            schema_text = self._schema_text(r) if schema_enabled else None
            raw_bm25.append(compute_bm25_score(input_query, doc_text, schema_text))

        norm_bm25 = normalize_bm25_scores(raw_bm25, method=params["bm25_normalization"])

        # --- score + threshold ---
        scored: list[tuple[float, dict]] = []
        for idx, r in enumerate(candidates):
            nbm = norm_bm25[idx]
            vec = self.vector_searcher.score_record(r, query_vector, params)
            value_norm = min(1.0, max(
                getattr(r, "value_relevance_score", 0.0) if not isinstance(r, dict)
                else 0.0,
                getattr(r, "aggregated_value_relevance", 0.0) if not isinstance(r, dict)
                else 0.0,
            ))

            value_ok = value_norm >= params["value_threshold"]
            if not value_ok and (
                nbm < params["bm25_threshold"]
                or vec < params["vector_threshold"]
            ):
                continue

            combined = combine_scores(
                params["bm25_weight"], nbm,
                params["vector_weight"], vec,
                params["value_weight"], value_norm,
            )
            if combined < params["combined_threshold"]:
                continue

            result = r if isinstance(r, dict) else vars(r)
            scored.append((combined, {**result, "_score": combined}))

        scored.sort(key=lambda x: x[0], reverse=True)

        start = page * page_size
        return [item for _, item in scored[start: start + page_size]]

    # ---------- hierarchical search ----------

    def search_hierarchical(
        self,
        records: list,
        input_query: str,
        type: str | None = None,
        scope: str | None = None,
        page: int = 0,
        page_size: int = 10,
        **overrides,
    ) -> list[dict]:
        """Score all records then propagate child scores to target-type parents."""
        params = self.prepare_parameters(input_query, type=type, scope=scope, **overrides)
        if not params["hierarchical_enabled"]:
            return self.search(records, input_query, type, scope, page, page_size, **overrides)

        query_vector = self.vector_searcher.compute_embedding(input_query)
        all_candidates = records[: params["search_limit"]]

        # BM25 over everything
        raw_bm25 = []
        for r in all_candidates:
            doc_text = self._record_doc_text(r)
            schema_text = self._schema_text(r) if params["enable_schema"] else None
            raw_bm25.append(compute_bm25_score(input_query, doc_text, schema_text))
        norm_bm25 = normalize_bm25_scores(raw_bm25, method=params["bm25_normalization"])

        # Build hierarchy: node_id -> {record, children, parent}
        hierarchy: dict[str, dict] = {}
        for idx, r in enumerate(all_candidates):
            if isinstance(r, dict):
                name, rtype, rscope = r.get("name", ""), r.get("type", ""), r.get("scope", "/")
            else:
                name, rtype, rscope = (
                    getattr(r, "name", ""),
                    getattr(r, "type", ""),
                    getattr(r, "scope", "/"),
                )
            node_id = f"{rscope}/{rtype}/{name}"
            vec = self.vector_searcher.score_record(r, query_vector, params)

            # Attach scores as attributes on a copy
            node_record = dict(r) if isinstance(r, dict) else dict(vars(r))
            node_record["normalized_bm25_score"] = norm_bm25[idx]
            node_record["vector_score"] = vec

            hierarchy[node_id] = {
                "record": type("_R", (), node_record)(),
                "children": set(),
                "parent": rscope,
            }

        # Link children to parents
        for node_id, node in hierarchy.items():
            parent_id = node["parent"]
            if parent_id and parent_id in hierarchy:
                hierarchy[parent_id]["children"].add(node_id)

        # Determine target types
        hierarchical_types: list[str] = (
            params["hierarchical_database_types"]
            if type in ("source", "database", None)
            else params["hierarchical_collection_types"]
        )

        # Collect target nodes and score via children propagation
        scored: list[tuple[float, dict]] = []
        for node_id, node in hierarchy.items():
            record = node["record"]
            rtype = getattr(record, "type", "")
            if type and rtype != type:
                continue

            descendant_ids: set[str] = set()
            self._collect_descendants(node_id, hierarchy, descendant_ids, hierarchical_types)

            value_agg = aggregate_value_scores(
                descendant_ids, hierarchy,
                mode="topk", top_k=3, decay=0.9,
            )
            best_score, best_child, best_values, best_value_score = find_best_score_among_nodes(
                descendant_ids | {node_id}, hierarchy, params,
            )

            if best_child is None:
                continue

            combined = 1.0 - best_score
            result = {
                k: v for k, v in vars(best_child).items()
                if not k.startswith("_")
            }
            result["_score"] = combined
            result["_most_relevant_values"] = best_values
            scored.append((combined, result))

        scored.sort(key=lambda x: x[0], reverse=True)
        start = page * page_size
        return [item for _, item in scored[start: start + page_size]]

    def _collect_descendants(
        self,
        node_id: str,
        hierarchy: dict,
        result: set,
        allowed_types: list[str],
    ) -> None:
        for child_id in hierarchy.get(node_id, {}).get("children", set()):
            child_record = hierarchy[child_id]["record"]
            if getattr(child_record, "type", "") in allowed_types:
                result.add(child_id)
            self._collect_descendants(child_id, hierarchy, result, allowed_types)
