# Python API Reference

Public classes and their signatures. All are importable from `blue_data_web_source`.

---

## `WebDataSource`

Top-level public entry point. Extends blue-data's `DataSource` ABC.

```python
from blue_data_web_source import WebDataSource
```

### Constructor

```python
WebDataSource(
    name: str = "web",
    embedding_model: EmbeddingModel | None = None,
    vector_source: VectorSource | None = None,
    enricher: Enricher | None = None,
    chunk_size: int = 1000,
    overlap: int = 150,
    default_collection: str = "web",
    timeout: float = 30.0,
    nl_model: str = "claude-opus-4-8",
    search_provider: WebSearchProvider | None = None,
    freshness_store: FreshnessStore | None = None,
    max_workers: int = 10,
    properties: dict | None = None,
    classifier: DocumentClassifier | None = None,
    field_extractor: FieldExtractor | None = None,
    record_store: RecordStore | None = None,
    graph_source: GraphSource | None = None,
    graph_database: str = "web",
)
```

### Factory

```python
@classmethod
def from_env(cls) -> WebDataSource
```

Build a source from `BLUE_WEB_*` environment variables. Used by all platform adapters (REST API, MCP, CLI).

### Methods

```python
def execute_query(self, query: dict, ...) -> Any
```

Dispatch on `query["op"]`. See op codes below.

```python
def execute_query_nl(self, text: str, ...) -> Any
```

Parse a natural-language instruction via `NLQueryParser` (Claude tool use) and execute. Requires `[llm]` extra and `ANTHROPIC_API_KEY`.

### Op codes

| Op | Required fields | Optional fields | Returns |
|----|-----------------|-----------------|---------|
| `WEB.ENSURE_INDEX` | — | `collection` | `None` |
| `WEB.INGEST` | `url` or `urls` | `collection` | `list[dict]` |
| `WEB.CRAWL` | `url` | `depth`(1), `max_pages`(10), `same_domain_only`(True), `collection` | `list[dict]` |
| `WEB.INGEST_FEED` | `url` | `since`, `max_entries`, `collection` | `list[dict]` |
| `WEB.EXTRACT` | (`url` or `urls`) + `schema` | — | `dict` or `list[dict]` |
| `WEB.SEARCH` | `query` | `k`(5), `collection` | `list[dict]` |
| `WEB.SEARCH_WEB` | `query` | `k`(5), `top_k`(5), `collection` | `list[dict]` |
| `WEB.FIND` | — | `filter`, `coverage`(False) | `FindResult` |
| `WEB.FIND_THEN_SEARCH` | `query` | `find_filter`, `k`(5) | `list[dict]` |
| `WEB.GRAPH_NEIGHBORS` | `entity_name` | `entity_type`, `k`(10) | `list[dict]` |
| `WEB.GRAPH_QUERY` | `node_id` | `edge_type`, `direction`(both), `k`(None=all) | `list[dict]` |

---

## `WebPipeline`

Orchestrates the ingestion chain. Owned by `WebDataSource`; can also be used standalone.

```python
from blue_data_web_source.source.web.pipeline import WebPipeline
```

### Constructor

```python
WebPipeline(
    chunk_size: int = 1000,
    overlap: int = 150,
    chunker: Chunker | None = None,
    embedding_model: EmbeddingModel | None = None,
    vector_source: VectorSource | None = None,
    enricher: Enricher | None = None,
    collection: str = "web",
    timeout: float = 30.0,
    freshness_store: FreshnessStore | None = None,
    max_workers: int = 10,
    classifier: DocumentClassifier | None = None,
    field_extractor: FieldExtractor | None = None,
    record_store: RecordStore | None = None,
    graph_source: GraphSource | None = None,
    graph_database: str = "web",
)
```

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `ingest(url, collection)` | `list[dict]` | Full pipeline for one URL; returns `[]` when no content is extractable |
| `ingest_many(urls, collection)` | `list[dict]` | Concurrent ingest via `ThreadPoolExecutor` |
| `ingest_feed(url, since, max_entries, collection)` | `list[dict]` | Parse RSS/Atom feed; ingest each article |
| `search(query, k, collection)` | `list[dict]` | Embed query and run KNN |
| `find_records(filter, coverage)` | `FindResult` | Filter page records by field values |
| `find_then_search(query, find_filter, k)` | `list[dict]` | Field filter + scoped vector search |
| `graph_neighbors(entity_name, entity_type, k)` | `list[dict]` | Chunks mentioning a named entity |
| `graph_query(node_id, edge_type, direction, k)` | `list[dict]` | Graph traversal from any node |
| `ensure_index(collection)` | `None` | Create vector index; idempotent |

---

## `WebFetcher`

HTTP client. Wraps `httpx.Client` with redirect following and a fixed `User-Agent`.

```python
from blue_data_web_source.source.web.fetcher import WebFetcher, FetchResult
```

```python
WebFetcher(timeout: float = 30.0, headers: dict | None = None)

def fetch(self, url: str) -> FetchResult: ...
# Raises httpx.HTTPStatusError on non-2xx responses
```

`FetchResult` fields: `url`, `final_url`, `html`, `status_code`, `content_type`.

---

## `WebExtractor`

Wraps `trafilatura` to extract clean article text and page metadata.

```python
from blue_data_web_source.source.web.extractor import WebExtractor, ExtractResult
```

```python
WebExtractor()

def extract(self, html: str, url: str) -> ExtractResult | None: ...
# Returns None when no main content is extractable
```

`ExtractResult` fields: `url`, `text`, `title`, `author`, `date`, `description`, `language`.

---

## `WebCrawler`

BFS crawler. Respects `robots.txt`, per-domain rate limiting, and exponential backoff on 5xx errors.

```python
from blue_data_web_source.source.web.crawler import WebCrawler
```

```python
WebCrawler(
    pipeline: WebPipeline,
    fetcher: WebFetcher,
    same_domain_only: bool = True,
    respect_robots: bool = True,
    crawl_delay: float = 1.0,
    max_retries: int = 2,
    max_workers: int = 1,
)

def crawl(self, seed_url: str, depth: int = 1, max_pages: int = 10) -> list[dict]: ...
```

---

## `FreshnessStore`

Tracks content hashes to skip unchanged pages on re-ingest.

```python
from blue_data_web_source.source.web.freshness import (
    FreshnessStore,          # ABC
    MemoryFreshnessStore,    # in-process; lost on restart
    FileFreshnessStore,      # persisted to a JSON file
)
```

```python
FileFreshnessStore(path: str)   # path to the JSON persistence file
```

Pass to `WebDataSource(freshness_store=...)` or `WebPipeline(freshness_store=...)`.

---

## `WebSearchProvider`

ABC for live web search backends used by `WEB.SEARCH_WEB`.

```python
from blue_data_web_source.source.web.search_provider import (
    WebSearchProvider,      # ABC
    TavilySearchProvider,   # built-in implementation
)
```

```python
class WebSearchProvider(ABC):
    def search(self, query: str, k: int = 5) -> list[str]: ...

TavilySearchProvider(api_key: str | None = None)
# api_key defaults to TAVILY_API_KEY env var
```

---

## `NLQueryParser`

Converts natural-language instructions to `WEB.*` op dicts using Claude tool use.

```python
from blue_data_web_source.source.web.nl import NLQueryParser
```

```python
NLQueryParser(
    model: str = "claude-opus-4-8",
    client: anthropic.Anthropic | None = None,
)

def parse(self, text: str) -> dict: ...
```

Requires `pip install 'blue-data-web-source[llm]'` and `ANTHROPIC_API_KEY`.

---

## `WebChunkRecord`

Canonical output schema for a single chunk. Serialised via `.to_dict()`.

```python
from blue_data_web_source.source.web.record import WebChunkRecord
```

| Field | Always present | Description |
|-------|---------------|-------------|
| `chunk_id` | ✓ | `web_{sha256(final_url)[:16]}_chunk_{idx:04d}` |
| `url` | ✓ | Canonical URL after redirects |
| `domain` | ✓ | `netloc` of the canonical URL |
| `chunk_index` | ✓ | 0-based position within the page |
| `text` | ✓ | Chunk text |
| `fetch_timestamp` | ✓ | ISO 8601 UTC ingest time |
| `title` | if present | Page title |
| `author` | if present | Extracted author |
| `date` | if present | Publication date |
| `description` | chunk 0 only | Page description |
| `language` | if present | Language code |
| `section_title` | if present | Heading above the chunk |
| `schema_type` | if present | Schema.org `@type` |
| `keywords` | if present | From JSON-LD or `<meta>` |
| `publisher` | if present | From JSON-LD or `og:site_name` |
| `canonical_url` | if present | Canonical URL from page metadata |
| `collection` | if set | Vector store collection name |
| `page_id` | if set | `"web_" + sha256(final_url)[:16]` |
| `entities` | if enriched | Entity dicts from `BasicEnricher` |
| `semantic_hints` | if enriched | Semantic hint dict from enricher |
| `embedding` | if set | Included by `to_dict()` when an embedding model is configured; callers that do not need the raw vectors may strip this key |
