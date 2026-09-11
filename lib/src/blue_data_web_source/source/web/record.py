from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WebChunkRecord:
    url: str
    domain: str
    chunk_id: str
    chunk_index: int
    text: str
    title: Optional[str] = None
    author: Optional[str] = None
    date: Optional[str] = None
    description: Optional[str] = None
    language: Optional[str] = None
    collection: Optional[str] = None
    section_title: Optional[str] = None
    fetch_timestamp: str = field(default_factory=_utcnow)
    page_id: Optional[str] = None
    embedding: Optional[List[float]] = None
    entities: List[Dict[str, Any]] = field(default_factory=list)
    semantic_hints: Dict[str, Any] = field(default_factory=dict)
    # Structured metadata from JSON-LD / OpenGraph
    schema_type: Optional[str] = None
    keywords: Optional[List[str]] = None
    publisher: Optional[str] = None
    canonical_url: Optional[str] = None

    def to_dict(self) -> dict:
        d: dict = {
            "url": self.url,
            "domain": self.domain,
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "text": self.text,
            "fetch_timestamp": self.fetch_timestamp,
        }
        if self.page_id is not None:
            d["page_id"] = self.page_id
        for key in (
            "title",
            "author",
            "date",
            "description",
            "language",
            "collection",
            "section_title",
            "schema_type",
            "publisher",
            "canonical_url",
        ):
            val = getattr(self, key)
            if val is not None:
                d[key] = val
        if self.keywords:
            d["keywords"] = self.keywords
        if self.entities:
            d["entities"] = self.entities
        if self.semantic_hints:
            d["semantic_hints"] = self.semantic_hints
        if self.embedding is not None:
            d["embedding"] = self.embedding
        return d
