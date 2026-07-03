from __future__ import annotations

from typing import Callable

from blue_data.core.utils.similarity_utils import compute_vector_score


class VectorSearch:
    """Handles embedding computation and KNN search via an injected VectorSource.

    embedding_fn and vector_source are optional — without them the class degrades
    gracefully so the pipeline can run in keyword-only mode.
    """

    def __init__(
        self,
        vector_source=None,
        embedding_fn: Callable[[str], list[float]] | None = None,
        index_name: str = "registry_index",
        vector_dim: int = 1536,
    ):
        self.vector_source = vector_source
        self.embedding_fn = embedding_fn
        self.index_name = index_name
        self.vector_dim = vector_dim

    # ---------- embedding ----------

    def compute_embedding(self, text: str) -> list[float] | None:
        if self.embedding_fn is None:
            return None
        return self.embedding_fn(text)

    # ---------- similarity ----------

    def compute_similarity(self, query_vector, doc_vector, normalize: bool = True) -> float:
        return compute_vector_score(query_vector, doc_vector, normalize_score=normalize)

    # ---------- index-backed search ----------

    def knn_search(
        self,
        query_vector: list[float],
        k: int = 10,
        filter_expr: str = "*",
        return_fields: list[str] | None = None,
    ) -> list[dict]:
        if self.vector_source is None:
            raise NotImplementedError("No VectorSource configured for vector search")

        return self.vector_source.execute_query({
            "op": "VECTOR.KNN",
            "index": self.index_name,
            "vector": query_vector,
            "k": k,
            "filter": filter_expr,
            "return_fields": return_fields or ["name", "type", "scope", "description"],
        })

    def score_record(self, record: dict, query_vector, params: dict) -> float:
        """Return cosine similarity of query_vector to the record's stored embedding.

        Falls back to 0.0 when the VectorSource is unavailable or the doc is not indexed.
        """
        if query_vector is None or self.vector_source is None:
            return 0.0

        doc_key = (
            f"{record.get('scope', '/')}/{record.get('type', '')}/{record.get('name', '')}"
        )

        try:
            results = self.vector_source.execute_query({
                "op": "VECTOR.KNN",
                "index": self.index_name,
                "vector": query_vector,
                "k": 1,
                "filter": f"@id:{{{doc_key}}}",
                "return_fields": ["embedding", "schema_embedding"],
            })
        except Exception:
            return 0.0

        if not results:
            return 0.0

        doc = results[0]
        score = self.compute_similarity(query_vector, doc.get("embedding"))

        if params.get("enable_schema") and record.get("type") == "collection":
            schema_vec = doc.get("schema_embedding")
            if schema_vec is not None:
                score = max(score, self.compute_similarity(query_vector, schema_vec))

        return score
