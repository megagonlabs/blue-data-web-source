from __future__ import annotations

import json
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import List, Optional


@dataclass
class StructuredMetadata:
    """Structured metadata extracted from JSON-LD and OpenGraph/meta tags.

    Fields that could not be determined are ``None``. ``date_published`` and
    ``author`` are supplementary — the pipeline uses them only when trafilatura
    did not find values.
    """

    schema_type: Optional[str] = None
    keywords: Optional[List[str]] = None
    publisher: Optional[str] = None
    canonical_url: Optional[str] = None
    date_published: Optional[str] = None
    author: Optional[str] = None
    description: Optional[str] = None


class _MetaParser(HTMLParser):
    """Single-pass HTML parser for JSON-LD, OG tags, meta tags, and canonical link."""

    def __init__(self) -> None:
        super().__init__()
        self.og: dict = {}
        self.meta: dict = {}
        self.canonical: Optional[str] = None
        self.jsonld_blocks: List[dict] = []
        self._in_jsonld = False
        self._jsonld_buf: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_dict = dict(attrs)
        if tag == "meta":
            prop = attrs_dict.get("property", "").lower()
            name = attrs_dict.get("name", "").lower()
            content = attrs_dict.get("content", "")
            if prop.startswith("og:"):
                self.og[prop[3:]] = content
            elif name:
                self.meta[name] = content
        elif tag == "link":
            if attrs_dict.get("rel", "").lower() == "canonical":
                self.canonical = attrs_dict.get("href") or None
        elif tag == "script":
            if attrs_dict.get("type", "").lower() == "application/ld+json":
                self._in_jsonld = True
                self._jsonld_buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            raw = "".join(self._jsonld_buf).strip()
            if raw:
                try:
                    data = json.loads(raw)
                    if isinstance(data, list):
                        self.jsonld_blocks.extend(d for d in data if isinstance(d, dict))
                    elif isinstance(data, dict):
                        self.jsonld_blocks.append(data)
                except json.JSONDecodeError:
                    pass
            self._jsonld_buf = []

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._jsonld_buf.append(data)


_ARTICLE_TYPES = frozenset({
    "Article", "NewsArticle", "BlogPosting", "TechArticle",
    "AnalysisNewsArticle", "ReportageNewsArticle", "OpinionNewsArticle",
})


def _pick_jsonld(blocks: List[dict]) -> dict:
    for block in blocks:
        t = block.get("@type", "")
        if isinstance(t, list):
            t = t[0] if t else ""
        if t in _ARTICLE_TYPES:
            return block
    return blocks[0] if blocks else {}


def _coerce_str(val) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, list):
        return _coerce_str(val[0]) if val else None
    s = str(val).strip()
    return s or None


def _parse_keywords(val) -> Optional[List[str]]:
    if not val:
        return None
    if isinstance(val, list):
        kws = [str(k).strip() for k in val if str(k).strip()]
        return kws or None
    if isinstance(val, str):
        kws = [k.strip() for k in val.split(",") if k.strip()]
        return kws or None
    return None


def _parse_author(val) -> Optional[str]:
    if isinstance(val, list):
        return _parse_author(val[0]) if val else None
    if isinstance(val, dict):
        return _coerce_str(val.get("name"))
    return _coerce_str(val)


def _parse_publisher(val) -> Optional[str]:
    if isinstance(val, dict):
        return _coerce_str(val.get("name"))
    return _coerce_str(val)


class MetadataExtractor:
    """Extracts structured metadata from raw HTML without an ML dependency.

    Parses in a single pass:

    - JSON-LD ``<script type="application/ld+json">`` blocks (schema.org)
    - OpenGraph ``<meta property="og:...">`` tags
    - Standard ``<meta name="keywords">`` and ``<meta name="author">``
    - ``<link rel="canonical">``

    When multiple JSON-LD blocks are present, article-type blocks
    (NewsArticle, BlogPosting, etc.) are preferred over generic ones.
    All parsing errors are silently swallowed — the result is always a
    :class:`StructuredMetadata` with ``None`` for unresolvable fields.
    """

    def extract(self, html: str) -> StructuredMetadata:
        parser = _MetaParser()
        try:
            parser.feed(html)
        except Exception:
            pass

        jsonld = _pick_jsonld(parser.jsonld_blocks)

        return StructuredMetadata(
            schema_type=_coerce_str(jsonld.get("@type")),
            keywords=(
                _parse_keywords(jsonld.get("keywords"))
                or _parse_keywords(parser.meta.get("keywords"))
            ),
            publisher=(
                _parse_publisher(jsonld.get("publisher"))
                or _coerce_str(parser.og.get("site_name"))
            ),
            canonical_url=(
                _coerce_str(jsonld.get("url"))
                or parser.canonical
                or _coerce_str(parser.og.get("url"))
            ),
            date_published=_coerce_str(
                jsonld.get("datePublished") or jsonld.get("dateCreated")
            ),
            author=(
                _parse_author(jsonld.get("author"))
                or _coerce_str(parser.meta.get("author"))
            ),
            description=(
                _coerce_str(jsonld.get("description"))
                or _coerce_str(parser.og.get("description"))
                or _coerce_str(parser.meta.get("description"))
            ),
        )
