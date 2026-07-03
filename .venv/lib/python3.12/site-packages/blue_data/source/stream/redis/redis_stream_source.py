from __future__ import annotations

import copy
import logging

import redis

from blue_data.source.stream.stream_source import StreamSource


class RedisStreamSource(StreamSource):
    """Redis Streams-backed source using XADD / XREAD."""

    def __init__(self, name: str, connection=None, properties: dict | None = None):
        super().__init__(name, properties=properties)
        self.logger = logging.getLogger(self.__class__.__name__)

        if connection is not None:
            self.connection = connection
        else:
            self._initialize_connection_properties()
            self.connection = self._connect()

        self._cursors: dict[str, str] = {}

    # ---------- connection ----------

    def _initialize_connection_properties(self):
        conn = self.properties.get("connection", {})
        conn.setdefault("host", "localhost")
        conn.setdefault("port", 6379)
        conn.setdefault("db", 0)
        self.properties["connection"] = conn

    def _connect(self):
        conn = self.properties.get("connection", {})
        config = copy.deepcopy(conn)
        config.pop("protocol", None)
        return redis.Redis(
            host=config.get("host", "localhost"),
            port=config.get("port", 6379),
            db=config.get("db", 0),
            password=config.get("password"),
            decode_responses=True,
        )

    # ---------- stream ops ----------

    def publish(self, stream: str, message: dict) -> None:
        self.connection.xadd(stream, message)

    def consume(self, stream: str, count: int = 1) -> list[dict]:
        last_id = self._cursors.get(stream, "0")
        results = self.connection.xread({stream: last_id}, count=count)
        if not results:
            return []
        messages = []
        for _stream, entries in results:
            for message_id, data in entries:
                messages.append({"_id": message_id, **data})
                last_id = message_id
        self._cursors[stream] = last_id
        return messages

    def acknowledge(self, stream: str, message_id: str) -> None:
        pass

    def create_stream(self, stream: str, properties: dict | None = None) -> dict:
        return {"status": "success"}

    def delete_stream(self, stream: str) -> None:
        self.connection.delete(stream)
        self._cursors.pop(stream, None)

    # ---------- DataSource interface ----------

    def fetch_databases(self) -> list[str]:
        return ["default"]

    def fetch_database_collections(self, database: str) -> list[str]:
        cursor = 0
        streams = []
        while True:
            cursor, keys = self.connection.scan(cursor=cursor, count=500, _type="stream")
            streams.extend(keys)
            if cursor == 0:
                break
        return streams

    def fetch_database_collection_metadata(self, database: str, collection: str) -> dict:
        return {"message_count": self.connection.xlen(collection)}

    def create_database_collection(self, database: str, collection: str, properties: dict | None = None) -> dict:
        return self.create_stream(collection, properties)

    def fetch_source_stats(self) -> dict:
        streams = self.fetch_database_collections("default")
        total = sum(self.connection.xlen(s) for s in streams)
        return {"total_streams": len(streams), "total_messages": total}

    def fetch_collection_stats(self, database: str, collection: str, entities=None, relations=None, sample_limit=None) -> dict:
        return {"message_count": self.connection.xlen(collection)}
