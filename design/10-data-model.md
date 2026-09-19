# blue-data-web-source — Data Model

## Overview

`blue-data-web-source` processes web content through a strict one-way pipeline. Each stage produces a typed data structure that is passed to the next stage. This document describes every data structure in the pipeline, its fields, and its invariants.

The pipeline stages and their output types:

```
WebFetcher      → FetchResult
WebExtractor    → ExtractResult | None
MetadataExtractor → StructuredMetadata
FeedParser      → FeedParseResult (WEB.INGEST_FEED only)
WebPipeline     → WebChunkRecord (→ dict via to_dict())
FreshnessStore  → FreshnessRecord (internal; not returned to callers)
```

---

## 1. FetchResult

Produced by `WebFetcher.fetch(url)`. Represents a completed HTTP response.

```python
@dataclass
class FetchResult:
    url: str
    final_url: str
    html: str
    status_code: int
    content_type: str
```

| Field | Type | Description |
|-------|------|-------------|
| `url` | str | The URL originally passed to `fetch()` |
| `final_url` | str | The URL after all HTTP redirects have been followed; used as the canonical identity for deduplication and chunk ID computation |
| `html` | str | Raw HTML response body |
| `status_code` | int | HTTP response status code (always 2xx; non-2xx raises before returning) |
| `content_type` | str | Value of the `Content-Type` response header; empty string if absent |

**Invariant:** `WebFetcher.fetch()` either returns a `FetchResult` with a 2xx status code or raises `httpx.HTTPStatusError`. A `FetchResult` with a non-2xx code is never returned.

**Identity:** `final_url` is the canonical URL used everywhere downstream. Two URLs that redirect to the same destination produce the same `final_url` and therefore the same chunk IDs.

---

## 2. ExtractResult

Produced by `WebExtractor.extract(html, url)`. Represents extracted article content and page-level metadata.

```python
@dataclass
class ExtractResult:
    url: str
    text: str
    title: Optional[str] = None
    author: Optional[str] = None
    date: Optional[str] = None
    description: Optional[str] = None
    language: Optional[str] = None
```

| Field | Type | Description |
|-------|------|-------------|
| `url` | str | The URL passed to `extract()` |
| `text` | str | Main article body after boilerplate removal and nav-fragment stripping; always non-empty when present |
| `title` | str \| None | Page title from `<title>`, `og:title`, or `<h1>` |
| `author` | str \| None | Author from `meta[name=author]`, `article:author`, or schema.org byline; semicolon-separated when multiple |
| `date` | str \| None | Publication date from `article:published_time`, `<time>`, JSON-LD, or URL heuristic; ISO 8601 string |
| `description` | str \| None | Page description from `meta[name=description]` or `og:description` |
| `language` | str \| None | Language code from `html[lang]` attribute |

**Invariant:** `WebExtractor.extract()` returns `None` when no extractable body is found (login walls, navigation-only pages, SPA shells, very short pages). It never returns an `ExtractResult` with an empty `text`. `WebPipeline.ingest()` returns `[]` immediately when `None` is received — nothing is stored.

---

## 3. StructuredMetadata

Produced by `MetadataExtractor.extract(html)`. Supplements `ExtractResult` with structured metadata parsed from JSON-LD, OpenGraph, and standard `<meta>` tags.

```python
@dataclass
class StructuredMetadata:
    schema_type: Optional[str] = None
    keywords: Optional[List[str]] = None
    publisher: Optional[str] = None
    canonical_url: Optional[str] = None
    author: Optional[str] = None
    date_published: Optional[str] = None
    description: Optional[str] = None
```

| Field | Type | Source priority |
|-------|------|----------------|
| `schema_type` | str \| None | JSON-LD `@type` (e.g. `NewsArticle`, `BlogPosting`) |
| `keywords` | list[str] \| None | JSON-LD `keywords` → `<meta name="keywords">` |
| `publisher` | str \| None | JSON-LD `publisher.name` → `og:site_name` |
| `canonical_url` | str \| None | JSON-LD `url` → `<link rel="canonical">` → `og:url` |
| `author` | str \| None | JSON-LD `author.name` → `<meta name="author">` |
| `date_published` | str \| None | JSON-LD `datePublished` → `dateCreated` |
| `description` | str \| None | JSON-LD `description` → `og:description` → `<meta name="description">` |

**Invariant:** `MetadataExtractor.extract()` always returns a `StructuredMetadata` instance — never raises. All parsing errors are silently swallowed; unresolvable fields are `None`.

**Pipeline integration:** `WebPipeline` uses `StructuredMetadata` to:
1. Fill `author`, `date`, `description` on `WebChunkRecord` when `ExtractResult` returned `None` for those fields
2. Set `schema_type`, `keywords`, `publisher`, `canonical_url` on every chunk (independent of trafilatura)

---

## 4. FeedEntry and FeedParseResult

Produced by `FeedParser.parse(content, feed_url)` (used by `WEB.INGEST_FEED` only).

```python
@dataclass
class FeedEntry:
    url: str
    title: Optional[str] = None
    published: Optional[str] = None
    summary: Optional[str] = None
    published_parsed: Optional[datetime] = None

@dataclass
class FeedParseResult:
    feed_url: str
    feed_title: Optional[str]
    entries: List[FeedEntry]
```

**`FeedEntry` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `url` | str | Article link (`entry.link` from feedparser) |
| `title` | str \| None | Article headline |
| `published` | str \| None | Raw date string as it appears in the feed |
| `summary` | str \| None | Feed-provided excerpt (not the full article text) |
| `published_parsed` | datetime \| None | UTC-aware `datetime` derived from `published_parsed` or `updated_parsed`; used for `since` filtering |

**`FeedParseResult` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `feed_url` | str | URL the feed was fetched from |
| `feed_title` | str \| None | Feed-level title (e.g. `"NPR News"`) |
| `entries` | list[FeedEntry] | Parsed entries; entries without a `link` field are silently skipped |

**Invariant:** Entries without a `link` are excluded before returning. `published_parsed` is `None` when neither `published_parsed` nor `updated_parsed` is present on the feedparser entry — these entries always pass the `since` filter in `WebPipeline`.

---

## 5. WebChunkRecord

The canonical output unit of `WebPipeline`. One record per chunk per page. Serialised via `to_dict()`, which omits `None` optional fields — downstream consumers must check key presence, not value.

```python
@dataclass
class WebChunkRecord:
    # Required — always present
    url: str
    domain: str
    chunk_id: str
    chunk_index: int
    text: str
    fetch_timestamp: str
    # Optional — omitted from to_dict() when None or empty
    title: Optional[str] = None
    author: Optional[str] = None
    date: Optional[str] = None
    description: Optional[str] = None
    language: Optional[str] = None
    collection: Optional[str] = None
    section_title: Optional[str] = None
    schema_type: Optional[str] = None
    keywords: Optional[List[str]] = None
    publisher: Optional[str] = None
    canonical_url: Optional[str] = None
    page_id: Optional[str] = None
    embedding: Optional[List[float]] = None
    entities: List[Dict[str, Any]] = field(default_factory=list)
    semantic_hints: Dict[str, Any] = field(default_factory=dict)
```

**Required fields (always present in `to_dict()` output):**

| Field | Type | Description |
|-------|------|-------------|
| `url` | str | `FetchResult.final_url` — canonical URL after all redirects |
| `domain` | str | `urlparse(url).netloc` |
| `chunk_id` | str | `web_{sha256(url.encode())[:16]}_chunk_{index:04d}` |
| `chunk_index` | int | 0-based position within the page |
| `text` | str | Chunk text |
| `fetch_timestamp` | str | ISO 8601 UTC timestamp set at `WebChunkRecord` construction |

**Optional fields (omitted when `None`):**

| Field | Type | Source |
|-------|------|--------|
| `title` | str | `ExtractResult.title` |
| `author` | str | `ExtractResult.author` or `StructuredMetadata.author` fallback |
| `date` | str | `ExtractResult.date` or `StructuredMetadata.date_published` fallback |
| `description` | str | `ExtractResult.description` or `StructuredMetadata.description` fallback; **chunk 0 only** |
| `language` | str | `ExtractResult.language` |
| `collection` | str | Collection name passed to `ingest()` |
| `section_title` | str | Section heading from `SectionChunker`; absent when no structure detected |
| `schema_type` | str | `StructuredMetadata.schema_type` |
| `keywords` | list[str] | `StructuredMetadata.keywords` |
| `publisher` | str | `StructuredMetadata.publisher` |
| `canonical_url` | str | `StructuredMetadata.canonical_url` |
| `embedding` | list[float] | Embedding vector; present only when `embedding_model` is set; omitted by `to_dict()` |
| `page_id` | str | `"web_" + sha256(final_url)[:16]`; set on every chunk by `WebPipeline` |
| `entities` | list[dict] | Entity dicts from `BasicEnricher`; omitted from `to_dict()` when empty |
| `semantic_hints` | dict | Semantic hint dict from enricher; omitted from `to_dict()` when empty |

**Invariants:**

- `chunk_id` is derived deterministically from `final_url` — the same URL always produces the same chunk IDs. Re-ingestion is an upsert at the vector store level, never a duplicate.
- `description` is set on chunk 0 only. `WebPipeline` passes `description` only when `idx == 0`.
- `to_dict()` omits fields with `None` values. Callers must use `record.get("field")`, not `record["field"]`, for optional fields.
- `entities` and `semantic_hints` are proper `WebChunkRecord` fields populated at construction time by `WebPipeline`. They are omitted from `to_dict()` output only when empty (empty list / empty dict).

---

## 6. FreshnessRecord

Stored internally by `FreshnessStore`. Not returned to callers. Keyed by `page_id = "web_" + sha256(final_url.encode())[:16]`.

| Field | Type | Description |
|-------|------|-------------|
| `page_id` | str | `"web_" + sha256(final_url)[:16]` |
| `url` | str | The `final_url` this record covers |
| `content_hash` | str | SHA-256 hash of the extracted text body; used to detect content changes |
| `chunk_count` | int | Number of chunks produced on the last ingest |
| `last_ingested_at` | str | ISO 8601 UTC timestamp of the last ingest where content changed |
| `last_checked_at` | str | ISO 8601 UTC timestamp of the last fetch, even if content was unchanged |

**Invariants:**

- `last_checked_at` is updated on every ingest attempt.
- `last_ingested_at` is updated only when `content_hash` changes.
- A reverse index from `content_hash` to `page_id` enables cross-URL deduplication: if two different URLs produce the same extracted text, the second ingest is skipped.

---

## Data Flow Summary

```
URL
  → WebFetcher.fetch()           → FetchResult
  → WebExtractor.extract()       → ExtractResult | None
  → MetadataExtractor.extract()  → StructuredMetadata
  → Chunker.chunk_with_titles()  → List[(section_title, text)]
  → EmbeddingModel.embed_text()  → List[float]
  → WebChunkRecord(...)          → dict via to_dict()
  → VectorSource.upsert()        (side effect, no return value)
  → FreshnessStore.set()         (side effect, no return value)
```

`WebPipeline.ingest()` returns the list of chunk dicts. `WebDataSource.execute_query()` returns that list directly to the caller.
