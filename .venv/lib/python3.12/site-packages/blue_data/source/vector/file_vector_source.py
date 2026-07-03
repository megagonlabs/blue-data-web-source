from __future__ import annotations

import json
import logging
import os

import numpy as np

from blue_data.source.vector.vector_source import VectorSource


class FileVectorSource(VectorSource):
    """File-backed vector storage — persists docs as JSON files per collection."""

    def __init__(self, name: str, directory: str = ".", properties: dict | None = None):
        super().__init__(name, properties=properties or {})
        self.logger = logging.getLogger(self.__class__.__name__)
        self._directory = os.path.abspath(directory)
        os.makedirs(self._directory, exist_ok=True)
        self._indices: dict[str, dict] = self._load_indices()

    # ---------- persistence helpers ----------

    def _indices_path(self) -> str:
        return os.path.join(self._directory, "_indices.json")

    def _collection_path(self, collection: str) -> str:
        return os.path.join(self._directory, collection + ".json")

    def _load_indices(self) -> dict:
        path = self._indices_path()
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return {}

    def _save_indices(self) -> None:
        with open(self._indices_path(), "w") as f:
            json.dump(self._indices, f)

    def _load_collection(self, collection: str) -> dict:
        path = self._collection_path(collection)
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return {}

    def _save_collection(self, collection: str, docs: dict) -> None:
        with open(self._collection_path(collection), "w") as f:
            json.dump(docs, f)

    # ---------- cosine distance ----------

    def _cosine_distance(self, a, b) -> float:
        va = np.asarray(a, dtype=np.float32)
        vb = np.asarray(b, dtype=np.float32)
        norm_a = np.linalg.norm(va)
        norm_b = np.linalg.norm(vb)
        if norm_a == 0.0 or norm_b == 0.0:
            return 1.0
        return float(1.0 - np.dot(va, vb) / (norm_a * norm_b))

    # ---------- primitive operations ----------

    def create_index(self, index_name: str, collection: str, dim: int, distance_metric: str = "COSINE") -> str:
        self._indices[index_name] = {
            "collection": collection,
            "dim": int(dim),
            "metric": distance_metric,
        }
        self._save_indices()
        if not os.path.exists(self._collection_path(collection)):
            self._save_collection(collection, {})
        return "OK"

    def upsert(self, collection: str, doc_id: str, doc: dict, embedding_field: str = "embedding") -> str:
        if embedding_field not in doc:
            raise Exception(f"VECTOR.UPSERT requires doc with '{embedding_field}' field")
        docs = self._load_collection(collection)
        docs[doc_id] = doc
        self._save_collection(collection, docs)
        return "OK"

    def delete(self, collection: str, doc_id: str) -> int:
        docs = self._load_collection(collection)
        docs.pop(doc_id, None)
        self._save_collection(collection, docs)
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
        docs = self._load_collection(coll)

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

    # ---------- DataSource interface ----------

    def fetch_databases(self) -> list[str]:
        return ["default"]

    def fetch_database_collections(self, database: str) -> list[str]:
        return [
            f[:-5] for f in os.listdir(self._directory)
            if f.endswith(".json") and not f.startswith("_")
        ]

    def fetch_database_collection_metadata(self, database: str, collection: str) -> dict:
        return {"doc_count": len(self._load_collection(collection))}

    def fetch_source_stats(self) -> dict:
        collections = self.fetch_database_collections("default")
        total = sum(len(self._load_collection(c)) for c in collections)
        return {"total_collections": len(collections), "total_docs": total}

    def fetch_collection_stats(self, database: str, collection: str, entities=None, relations=None, sample_limit=None) -> dict:
        return {"doc_count": len(self._load_collection(collection))}
