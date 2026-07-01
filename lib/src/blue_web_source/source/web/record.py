from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WebChunkRecord:
    url: str
    domain: str
    chunk_id: str
    chunk_index: int
    text: str
    title: str | None = None
    author: str | None = None
    date: str | None = None
    description: str | None = None
    language: str | None = None
    collection: str | None = None
    section_title: str | None = None
    fetch_timestamp: str = field(default_factory=_utcnow)
    embedding: list[float] | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "url": self.url,
            "domain": self.domain,
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "text": self.text,
            "fetch_timestamp": self.fetch_timestamp,
        }
        for key in (
            "title",
            "author",
            "date",
            "description",
            "language",
            "collection",
            "section_title",
        ):
            val = getattr(self, key)
            if val is not None:
                d[key] = val
        if self.embedding is not None:
            d["embedding"] = self.embedding
        return d
