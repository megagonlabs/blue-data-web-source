from __future__ import annotations

from typing import Callable


class RecordIndexer:
    """Keeps the VectorSource index in sync with registry records.

    Builds a text representation of each record and upserts the embedding.
    Without a vector_source or embedding_fn the indexer is a no-op.
    """

    def __init__(
        self,
        vector_source=None,
        embedding_fn: Callable[[str], list[float]] | None = None,
        index_name: str = "registry_index",
        collection: str = "registry_records",
        vector_dim: int = 1536,
    ):
        self.vector_source = vector_source
        self.embedding_fn = embedding_fn
        self.index_name = index_name
        self.collection = collection
        self.vector_dim = vector_dim
        self._index_ensured = False

    # ---------- index lifecycle ----------

    def ensure_index(self) -> None:
        """Create the vector index if it does not exist."""
        if self._index_ensured or self.vector_source is None:
            return
        try:
            self.vector_source.execute_query({
                "op": "VECTOR.CREATE_INDEX",
                "index": self.index_name,
                "collection": self.collection,
                "dim": self.vector_dim,
                "distance_metric": "COSINE",
            })
        except Exception:
            pass
        self._index_ensured = True

    # ---------- record text ----------

    def _record_text(self, record) -> str:
        if isinstance(record, dict):
            parts = [
                record.get("name", ""),
                record.get("type", ""),
                record.get("description", ""),
            ]
            props = record.get("properties", {})
            if isinstance(props, dict):
                meta = props.get("metadata", {})
                if isinstance(meta, dict):
                    parts.append(meta.get("description", ""))
        else:
            parts = [
                getattr(record, "name", ""),
                getattr(record, "type", ""),
                getattr(record, "description", ""),
            ]
        return " ".join(p for p in parts if p).strip()

    def _schema_text(self, record) -> str | None:
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

    # ---------- upsert / delete ----------

    def set_record(self, record) -> None:
        if self.vector_source is None or self.embedding_fn is None:
            return
        self.ensure_index()

        text = self._record_text(record)
        if not text:
            return

        if isinstance(record, dict):
            name = record.get("name", "")
            rtype = record.get("type", "")
            scope = record.get("scope", "/")
            description = record.get("description", "")
        else:
            name = getattr(record, "name", "")
            rtype = getattr(record, "type", "")
            scope = getattr(record, "scope", "/")
            description = getattr(record, "description", "")

        doc_id = f"{scope}/{rtype}/{name}"
        embedding = self.embedding_fn(text)

        doc = {
            "embedding": embedding,
            "name": name,
            "type": rtype,
            "scope": scope,
            "description": description,
        }

        schema_text = self._schema_text(record)
        if schema_text:
            doc["schema_embedding"] = self.embedding_fn(schema_text)

        self.vector_source.execute_query({
            "op": "VECTOR.UPSERT",
            "collection": self.collection,
            "id": doc_id,
            "doc": doc,
            "embedding_field": "embedding",
        })

    def delete_record(self, name: str, type_: str, scope: str) -> None:
        if self.vector_source is None:
            return
        self.ensure_index()
        doc_id = f"{scope}/{type_}/{name}"
        try:
            self.vector_source.execute_query({
                "op": "VECTOR.DELETE",
                "collection": self.collection,
                "id": doc_id,
            })
        except Exception:
            pass
