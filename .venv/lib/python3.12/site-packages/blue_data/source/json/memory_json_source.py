from __future__ import annotations

from typing import Any

from blue_data.source.json.json_source import JSONSource
from blue_data.core.utils.json_utils import json_query_get, json_query_set, json_query_delete


class MemoryJSONSource(JSONSource):
    """In-memory JSON storage backend for local/dev/test use."""

    def __init__(self):
        self.data: dict[str, Any] = {}

    def get(self, path: str | None = None) -> Any:
        if path in (None, "", "$"):
            return self.data
        return json_query_get(self.data, path)

    def set(self, path: str | None, value: Any) -> None:
        if path in (None, "", "$"):
            self.data = value
        else:
            json_query_set(self.data, path, value)

    def delete(self, path: str | None = None) -> None:
        if path in (None, "", "$"):
            self.data = {}
        else:
            json_query_delete(self.data, path)

    # ---------- DataSource interface ----------

    def fetch_databases(self) -> list[str]:
        return ["default"]

    def fetch_database_collections(self, database: str) -> list[str]:
        return list(self.data.keys()) if isinstance(self.data, dict) else []

    def fetch_source_stats(self) -> dict:
        return {"total_keys": len(self.data) if isinstance(self.data, dict) else 0}
