from __future__ import annotations

from abc import ABC, abstractmethod

from blue_data.source.source import DataSource


class VectorSource(DataSource, ABC):
    """Base class for vector data sources."""

    # ---------- primitive operations ----------

    @abstractmethod
    def create_index(self, index_name: str, collection: str, dim: int, distance_metric: str = "COSINE") -> str:
        raise NotImplementedError

    @abstractmethod
    def upsert(self, collection: str, doc_id: str, doc: dict, embedding_field: str = "embedding") -> str:
        raise NotImplementedError

    @abstractmethod
    def delete(self, collection: str, doc_id: str) -> int:
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    # ---------- query ----------

    def execute_query(self, query, database=None, collection=None, optional_properties=None):
        """Execute a compound vector operation.

        query must be a dict with:
            op – one of:
                "VECTOR.CREATE_INDEX"  index, collection, dim, distance_metric (opt)
                "VECTOR.UPSERT"        collection, id, doc, embedding_field (opt)
                "VECTOR.DELETE"        collection, id
                "VECTOR.KNN"           index, vector/embedding, k (opt), field (opt),
                                       score_alias (opt), return_fields (opt), filter (opt)
        """
        if not isinstance(query, dict):
            raise ValueError(
                "VectorSource.execute_query expects query as a dict with 'op' and operation-specific fields"
            )

        op = (query.get("op") or "").upper()

        if op == "VECTOR.CREATE_INDEX":
            return self.create_index(
                index_name=query["index"],
                collection=query["collection"],
                dim=int(query["dim"]),
                distance_metric=query.get("distance_metric", "COSINE"),
            )

        if op == "VECTOR.UPSERT":
            return self.upsert(
                collection=query["collection"],
                doc_id=query["id"],
                doc=query["doc"],
                embedding_field=query.get("embedding_field", "embedding"),
            )

        if op == "VECTOR.DELETE":
            return self.delete(
                collection=query["collection"],
                doc_id=query["id"],
            )

        if op == "VECTOR.KNN":
            query_vector = (
                query.get("vector")
                or query.get("embedding")
                or query.get("query_vector")
                or query.get("query_embedding")
            )
            if query_vector is None:
                raise ValueError(
                    "VECTOR.KNN requires a query vector "
                    "(key: vector, embedding, query_vector, or query_embedding)"
                )
            return self.knn(
                index_name=query["index"],
                query_vector=query_vector,
                k=int(query.get("k", 5)),
                embedding_field=query.get("field") or query.get("vector_field", "embedding"),
                score_alias=query.get("score_alias") or query.get("score_field", "score"),
                return_fields=list(query.get("return_fields") or []),
                filter_expr=query.get("filter", "*"),
            )

        raise ValueError(
            f"Unsupported VectorSource operation: {op!r}. "
            "Supported: VECTOR.CREATE_INDEX, VECTOR.UPSERT, VECTOR.DELETE, VECTOR.KNN"
        )
