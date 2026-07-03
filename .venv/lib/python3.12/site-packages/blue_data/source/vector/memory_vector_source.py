from __future__ import annotations

import logging
import re

import numpy as np

from blue_data.source.vector.vector_source import VectorSource


class MemoryVectorSource(VectorSource):
    """In-memory vector storage for dev/test — mirrors RedisVectorSource's execute_query interface.

    Stores docs in a plain dict and computes KNN via cosine distance with numpy.
    No Redis required.
    """

    def __init__(self, name: str = "memory_vector", properties: dict | None = None):
        super().__init__(name, properties=properties or {})
        self.logger = logging.getLogger(self.__class__.__name__)
        # index_name -> {"collection": str, "dim": int, "metric": str}
        self._indices: dict[str, dict] = {}
        # collection -> {doc_id: doc_dict}
        self._store: dict[str, dict[str, dict]] = {}

    # ---------- DataSource interface (stubs — not relevant for registry search) ----------

    def fetch_source_stats(self) -> dict:
        return {"total_docs": sum(len(c) for c in self._store.values())}

    def fetch_databases(self) -> list[str]:
        return list(self._store.keys())

    def fetch_database_collections(self, database: str) -> list[str]:
        return list(self._store.get(database, {}).keys())

    def fetch_collection_stats(self, database, collection, entities=None, relations=None, sample_limit=None) -> dict:
        return {"num_docs": len(self._store.get(collection, {}))}

    # ---------- cosine distance ----------

    def _cosine_distance(self, a, b) -> float:
        va = np.asarray(a, dtype=np.float32)
        vb = np.asarray(b, dtype=np.float32)
        norm_a = np.linalg.norm(va)
        norm_b = np.linalg.norm(vb)
        if norm_a == 0.0 or norm_b == 0.0:
            return 1.0
        return float(1.0 - np.dot(va, vb) / (norm_a * norm_b))

    # ---------- filter parsing ----------

    def _apply_filter(self, docs: dict[str, dict], filter_expr: str) -> dict[str, dict]:
        """Supports '*' (all) and '@id:{doc_id}' (exact ID match)."""
        if not filter_expr or filter_expr.strip() == "*":
            return docs

        match = re.match(r"@id:\{(.+)\}", filter_expr.strip())
        if match:
            doc_id = match.group(1)
            return {k: v for k, v in docs.items() if k == doc_id}

        self.logger.warning(f"MemoryVectorSource: unsupported filter '{filter_expr}' — returning all docs")
        return docs

    # ---------- primitive operations ----------

    def create_index(self, index_name: str, collection: str, dim: int, distance_metric: str = "COSINE") -> str:
        self._indices[index_name] = {
            "collection": collection,
            "dim": int(dim),
            "metric": distance_metric,
        }
        self._store.setdefault(collection, {})
        return "OK"

    def upsert(self, collection: str, doc_id: str, doc: dict, embedding_field: str = "embedding") -> str:
        if embedding_field not in doc:
            raise Exception(f"VECTOR.UPSERT requires doc with '{embedding_field}' field")
        self._store.setdefault(collection, {})[doc_id] = doc
        return "OK"

    def delete(self, collection: str, doc_id: str) -> int:
        self._store.get(collection, {}).pop(doc_id, None)
        return 1

    def knn(
        self,
        index_name: str,
        query_vector: list,
        k: int = 5,
        embedding_field: str = "embedding",
        score_alias: str = "score",
        return_fields: list[str] | None = None,
        filter_expr: str = "*",
    ) -> list[dict]:
        if index_name not in self._indices:
            return []

        return_fields = return_fields or []
        coll = self._indices[index_name]["collection"]
        docs = self._store.get(coll, {})
        docs = self._apply_filter(docs, filter_expr)

        scored = []
        for doc_id, doc in docs.items():
            emb = doc.get(embedding_field)
            if emb is None:
                continue
            dist = self._cosine_distance(query_vector, emb)
            scored.append((dist, doc_id, doc))

        scored.sort(key=lambda x: x[0])

        results = []
        for dist, doc_id, doc in scored[:k]:
            row: dict = {"id": doc_id, score_alias: dist}
            if return_fields:
                for field in return_fields:
                    if field != score_alias and field in doc:
                        row[field] = doc[field]
            else:
                for field, val in doc.items():
                    if field != embedding_field:
                        row[field] = val
            results.append(row)

        return results
