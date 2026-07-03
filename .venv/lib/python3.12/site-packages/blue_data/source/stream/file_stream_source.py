from __future__ import annotations

import json
import os

from blue_data.source.stream.stream_source import StreamSource


class FileStreamSource(StreamSource):
    """File-backed stream source — appends JSON lines to per-stream files."""

    def __init__(self, name: str, directory: str = ".", properties: dict | None = None):
        super().__init__(name, properties=properties)
        self._directory = os.path.abspath(directory)
        os.makedirs(self._directory, exist_ok=True)
        self._cursors: dict[str, int] = {}

    def _stream_path(self, stream: str) -> str:
        return os.path.join(self._directory, stream + ".jsonl")

    def publish(self, stream: str, message: dict) -> None:
        with open(self._stream_path(stream), "a") as f:
            f.write(json.dumps(message) + "\n")

    def consume(self, stream: str, count: int = 1) -> list[dict]:
        path = self._stream_path(stream)
        if not os.path.exists(path):
            return []
        with open(path) as f:
            lines = f.readlines()
        cursor = self._cursors.get(stream, 0)
        batch = lines[cursor: cursor + count]
        self._cursors[stream] = cursor + len(batch)
        return [json.loads(line) for line in batch]

    def acknowledge(self, stream: str, message_id: str) -> None:
        pass

    def create_stream(self, stream: str, properties: dict | None = None) -> dict:
        path = self._stream_path(stream)
        if not os.path.exists(path):
            open(path, "w").close()
        return {"status": "success"}

    def delete_stream(self, stream: str) -> None:
        path = self._stream_path(stream)
        if os.path.exists(path):
            os.remove(path)
        self._cursors.pop(stream, None)

    # ---------- DataSource interface ----------

    def _stream_names(self) -> list[str]:
        return [f[:-6] for f in os.listdir(self._directory) if f.endswith(".jsonl")]

    def _message_count(self, stream: str) -> int:
        path = self._stream_path(stream)
        if not os.path.exists(path):
            return 0
        with open(path) as f:
            return sum(1 for _ in f)

    def fetch_databases(self) -> list[str]:
        return ["default"]

    def fetch_database_collections(self, database: str) -> list[str]:
        return self._stream_names()

    def fetch_database_collection_metadata(self, database: str, collection: str) -> dict:
        return {"message_count": self._message_count(collection)}

    def create_database_collection(self, database: str, collection: str, properties: dict | None = None) -> dict:
        return self.create_stream(collection, properties)

    def fetch_source_stats(self) -> dict:
        streams = self._stream_names()
        return {
            "total_streams": len(streams),
            "total_messages": sum(self._message_count(s) for s in streams),
        }

    def fetch_collection_stats(self, database: str, collection: str, entities=None, relations=None, sample_limit=None) -> dict:
        return {"message_count": self._message_count(collection)}
