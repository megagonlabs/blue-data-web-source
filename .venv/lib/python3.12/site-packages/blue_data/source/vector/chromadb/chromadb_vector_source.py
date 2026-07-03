from __future__ import annotations

import logging

import chromadb

from blue_data.source.vector.vector_source import VectorSource


class ChromaDBVectorSource(VectorSource):
    """ChromaDB vector source — persistent or ephemeral via chromadb client."""

    def __init__(self, name: str, path: str | None = None, properties: dict | None = None):
        super().__init__(name, properties=properties or {})
        self.logger = logging.getLogger(self.__class__.__name__)
        self._client = (
            chromadb.PersistentClient(path=path) if path else chromadb.EphemeralClient()
        )
        # index_name -> {"collection": str, "embedding_field": str}
        self._indices: dict[str, dict] = {}

    # ---------- primitive operations ----------

    def create_index(self, index_name: str, collection: str, dim: int, distance_metric: str = "COSINE") -> str:
        metric = distance_metric.lower()
        chroma_metric = {"cosine": "cosine", "l2": "l2", "ip": "ip"}.get(metric, "cosine")
        self._client.get_or_create_collection(
            name=collection,
            metadata={"hnsw:space": chroma_metric},
        )
        self._indices[index_name] = {"collection": collection, "embedding_field": "embedding"}
        return "OK"

    def upsert(self, collection: str, doc_id: str, doc: dict, embedding_field: str = "embedding") -> str:
        if embedding_field not in doc:
            raise Exception(f"VECTOR.UPSERT requires doc with '{embedding_field}' field")
        embedding = doc[embedding_field]
        # ChromaDB metadata values must be str, int, float, or bool
        metadata = {
            k: v for k, v in doc.items()
            if k != embedding_field and isinstance(v, (str, int, float, bool))
        }
        chroma_coll = self._client.get_or_create_collection(collection)
        chroma_coll.upsert(ids=[doc_id], embeddings=[embedding], metadatas=[metadata])
        return "OK"

    def delete(self, collection: str, doc_id: str) -> int:
        chroma_coll = self._client.get_or_create_collection(collection)
        chroma_coll.delete(ids=[doc_id])
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
        chroma_coll = self._client.get_or_create_collection(coll)

        response = chroma_coll.query(
            query_embeddings=[query_vector],
            n_results=k,
            include=["distances", "metadatas"],
        )

        ids = response.get("ids", [[]])[0]
        distances = response.get("distances", [[]])[0]
        metadatas = response.get("metadatas", [[]])[0]

        results = []
        for doc_id, dist, meta in zip(ids, distances, metadatas or []):
            row: dict = {"id": doc_id, score_alias: dist}
            if return_fields:
                for field in return_fields:
                    if field != score_alias and field in (meta or {}):
                        row[field] = meta[field]
            else:
                row.update(meta or {})
            results.append(row)

        return results

    # ---------- DataSource interface ----------

    def fetch_databases(self) -> list[str]:
        return ["default"]

    def fetch_database_collections(self, database: str) -> list[str]:
        return [c.name for c in self._client.list_collections()]

    def fetch_database_collection_metadata(self, database: str, collection: str) -> dict:
        chroma_coll = self._client.get_or_create_collection(collection)
        return {"doc_count": chroma_coll.count()}

    def fetch_source_stats(self) -> dict:
        collections = self.fetch_database_collections("default")
        total = sum(
            self._client.get_or_create_collection(c).count()
            for c in collections
        )
        return {"total_collections": len(collections), "total_docs": total}

    def fetch_collection_stats(self, database: str, collection: str, entities=None, relations=None, sample_limit=None) -> dict:
        chroma_coll = self._client.get_or_create_collection(collection)
        return {"doc_count": chroma_coll.count()}
