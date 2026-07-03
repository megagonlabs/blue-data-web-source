from __future__ import annotations

import json
import os
from typing import Any

from blue_data.source.json.json_source import JSONSource
from blue_data.core.utils.json_utils import json_query_get, json_query_set, json_query_delete


class FileJSONSource(JSONSource):
    """File-backed JSON storage backend."""

    def __init__(self, path: str):
        self.path = path
        if not os.path.exists(self.path):
            self._write({})

    def _read(self) -> dict:
        with open(self.path, "r") as f:
            return json.load(f)

    def _write(self, data: dict) -> None:
        with open(self.path, "w") as f:
            json.dump(data, f, indent=2)

    def get(self, path: str | None = None) -> Any:
        data = self._read()
        if path in (None, "", "$"):
            return data
        return json_query_get(data, path)

    def set(self, path: str | None, value: Any) -> None:
        data = self._read()
        if path in (None, "", "$"):
            data = value
        else:
            json_query_set(data, path, value)
        self._write(data)

    def delete(self, path: str | None = None) -> None:
        data = self._read()
        if path in (None, "", "$"):
            data = {}
        else:
            json_query_delete(data, path)
        self._write(data)

    # ---------- DataSource interface ----------

    def fetch_databases(self) -> list[str]:
        return ["default"]

    def fetch_database_collections(self, database: str) -> list[str]:
        data = self._read()
        return list(data.keys()) if isinstance(data, dict) else []

    def fetch_source_stats(self) -> dict:
        data = self._read()
        return {"total_keys": len(data) if isinstance(data, dict) else 0}
