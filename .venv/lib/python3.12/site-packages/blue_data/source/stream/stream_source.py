from __future__ import annotations

from abc import ABC, abstractmethod

from blue_data.source.source import DataSource


class StreamSource(DataSource, ABC):
    """Base class for stream data sources."""

    @abstractmethod
    def publish(self, stream: str, message: dict) -> None:
        raise NotImplementedError

    @abstractmethod
    def consume(self, stream: str, count: int = 1) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def acknowledge(self, stream: str, message_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def create_stream(self, stream: str, properties: dict | None = None) -> dict:
        raise NotImplementedError

    @abstractmethod
    def delete_stream(self, stream: str) -> None:
        raise NotImplementedError

    # ---------- query ----------

    def execute_query(self, query, database=None, collection=None, optional_properties=None):
        """Execute a compound stream operation.

        query must be a dict with:
            op – one of:
                "STREAM.PUBLISH"     stream, message
                "STREAM.CONSUME"     stream, count (opt, default 1)
                "STREAM.ACKNOWLEDGE" stream, message_id
                "STREAM.CREATE"      stream, properties (opt)
                "STREAM.DELETE"      stream
        """
        if not isinstance(query, dict):
            raise ValueError(
                "StreamSource.execute_query expects query as a dict with 'op' and operation-specific fields"
            )

        op = (query.get("op") or "").upper()

        if op == "STREAM.PUBLISH":
            self.publish(stream=query["stream"], message=query["message"])
            return None

        if op == "STREAM.CONSUME":
            return self.consume(stream=query["stream"], count=query.get("count", 1))

        if op == "STREAM.ACKNOWLEDGE":
            self.acknowledge(stream=query["stream"], message_id=query["message_id"])
            return None

        if op == "STREAM.CREATE":
            return self.create_stream(stream=query["stream"], properties=query.get("properties"))

        if op == "STREAM.DELETE":
            self.delete_stream(stream=query["stream"])
            return None

        raise ValueError(
            f"Unsupported StreamSource operation: {op!r}. "
            "Supported: STREAM.PUBLISH, STREAM.CONSUME, STREAM.ACKNOWLEDGE, STREAM.CREATE, STREAM.DELETE"
        )
