from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from blue_data.source.source import DataSource


class JSONSource(DataSource, ABC):
    """Abstract JSON storage interface."""

    def __init__(self, name: str = "", properties: dict | None = None):
        super().__init__(name, properties=properties)

    @abstractmethod
    def get(self, path: str | None = None) -> Any:
        pass

    @abstractmethod
    def set(self, path: str | None, value: Any) -> None:
        pass

    @abstractmethod
    def delete(self, path: str | None = None) -> None:
        pass

    def execute_query(
        self,
        query,
        database: str | None = None,
        collection: str | None = None,
        optional_properties: dict | None = None,
    ) -> Any:
        """Execute a compound JSON operation.

        query must be a dict with:
            op    – "GET", "SET", or "DEL" (also accepts "JSON.GET", "JSON.SET", "JSON.DEL")
            path  – dot-notation or JSONPath string; None / omitted means the root
            value – required for SET operations
        """
        if not isinstance(query, dict):
            raise ValueError(
                "JSONSource.execute_query expects query as a dict "
                "with 'op', 'path', and optional 'value'"
            )

        op = (query.get("op") or "").upper()
        path = query.get("path")

        if op in ("GET", "JSON.GET"):
            return self.get(path)

        if op in ("SET", "JSON.SET"):
            value = query.get("value")
            self.set(path, value)
            return None

        if op in ("DEL", "DELETE", "JSON.DEL"):
            self.delete(path)
            return None

        raise ValueError(
            f"Unsupported JSONSource operation: {op!r}. Supported: GET, SET, DEL"
        )
