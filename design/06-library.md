# blue-data-web-source — Library

---

## WebPipeline

Orchestrates the full web ingestion chain: fetch → extract → strip nav fragments → chunk → enrich → embed → store. All optional components (`embedding_model`, `vector_source`, `enricher`) can be omitted; upsert fires only when both `embedding_model` and `vector_source` are set.

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
)
```

- `chunker` — defaults to `SectionChunker(chunk_size, overlap)` when omitted; `section_title` is preserved on every chunk
- `timeout` — HTTP request timeout in seconds; 30 s default to accommodate real sites with slow SSL handshakes

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `ingest(url, collection, _fetch_result)` | `list[dict]` | Fetch, extract, chunk, enrich, embed, and upsert one URL; returns `[]` when extraction yields no body |
| `ingest_many(urls, collection)` | `list[dict]` | Concurrent ingest via `ThreadPoolExecutor`; result order not guaranteed; failed URLs silently skipped |
| `ingest_feed(url, since, max_entries, collection)` | `list[dict]` | Parse RSS/Atom feed; filter by `since` date; ingest each article URL |
| `search(query, k=5, collection=None)` | `list[dict]` | Embed query and run KNN; raises if `embedding_model` or `vector_source` is not set |
| `find_records(filter, coverage=True)` | `FindResult` | Filter page records from `RecordStore` by field values |
| `find_then_search(query, find_filter, k=5)` | `list[dict]` | Filter pages by fields, then vector search within matched pages |
| `graph_neighbors(entity, k=10)` | `list[dict]` | Find chunks mentioning a named entity via graph traversal |
| `graph_query(node_id, depth=1, k=10)` | `list[dict]` | General graph traversal from any node ID |
| `ensure_index(collection=None)` | `None` | Create vector index for the collection; idempotent |

### Page identity and deduplication

`page_id` is `"web_" + sha256(final_url)[:16]`, where `final_url` is the URL after all redirects. Chunk IDs follow `{page_id}_chunk_{index:04d}`. The same URL always produces the same IDs — re-ingestion is an upsert, never a duplicate. `description` from page metadata is attached to chunk 0 only.

---

## WebDataSource

Top-level public entry point. Extends blue-data's `DataSource` ABC and wraps a `WebPipeline` plus a `WebCrawler`. Dispatches on the `"op"` field of the request dict.

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

### Ops

| Op | Required fields | Optional fields | Returns |
|----|-----------------|-----------------|---------|
| `WEB.INGEST` | `url` OR `urls` | `collection` | `list[dict]` — one dict per chunk across all ingested pages |
| `WEB.EXTRACT` | (`url` OR `urls`) + `schema` | — | `dict` (single URL) or `list[dict]` (multiple URLs) — extracted fields |
| `WEB.CRAWL` | `url` | `depth` (default 1), `max_pages` (default 10), `same_domain_only` (default True), `collection` | `list[dict]` — flat chunk list from all crawled pages |
| `WEB.SEARCH_WEB` | `query` | `k` (search result URLs, default 5), `top_k` (returned chunks, default k=5), `collection` | `list[dict]` — semantically ranked chunks |
| `WEB.SEARCH` | `query` | `k` (default 5), `collection` | `list[dict]` — ranked chunks from local index |
| `WEB.INGEST_FEED` | `url` | `since` (ISO 8601), `max_entries`, `collection` | `list[dict]` — chunks from all ingested feed articles |
| `WEB.ENSURE_INDEX` | — | `collection` | `None` |
| `WEB.FIND` | `filter` | `coverage` (default True) | `FindResult` — page records matching the filter |
| `WEB.FIND_THEN_SEARCH` | `query`, `find_filter` | `k` (default 5) | `list[dict]` — vector search scoped to matched pages |
| `WEB.GRAPH_NEIGHBORS` | `entity` | `k` (default 10) | `list[dict]` — chunks mentioning the entity |
| `WEB.GRAPH_QUERY` | `node_id` | `depth` (default 1), `k` (default 10) | `list[dict]` — graph traversal from the given node |

### Methods

| Method | Description |
|--------|-------------|
| `execute_query(query, database=None, collection=None, optional_properties=None)` | Dispatch on `query["op"]` |
| `execute_query_nl(text, ...)` | Parse a plain-English instruction via `NLQueryParser` (Claude tool use) and execute; requires `[nl]` extra and `ANTHROPIC_API_KEY` |

---

## WebFetcher

HTTP client layer. Wraps `httpx.Client` with redirect following and a fixed `User-Agent`.

### Constructor

```python
WebFetcher(
    timeout: float = 30.0,
    headers: dict | None = None,
)
```

- Default `User-Agent` is set to a well-behaved crawler string; supply `headers` to add or override
- Raises `httpx.HTTPStatusError` on non-2xx responses

### FetchResult

```python
@dataclass
class FetchResult:
    url: str          # original requested URL
    final_url: str    # URL after all redirects — canonical identity for deduplication
    html: str         # raw HTML response body
    status_code: int
    content_type: str
```

`final_url` is always used for `page_id` computation — so `http://example.com/page` and its redirect target produce the same chunk IDs.

---

## WebExtractor

Wraps `trafilatura.extract()` and `trafilatura.extract_metadata()` to produce clean article text and page-level metadata.

```python
class WebExtractor:
    def extract(self, html: str, url: str) -> ExtractResult | None: ...
```

Returns `None` when trafilatura finds no extractable body (login walls, pure navigation, redirect pages). After extraction, strips CMS navigation fragments — lines beginning with `Related Story:`, `Read more:`, `See also:`, `Also read:`, `Advertisement`, or `Story continues` — then collapses runs of 3+ blank lines. If stripping leaves an empty body, also returns `None`.

### ExtractResult

```python
@dataclass
class ExtractResult:
    url: str
    text: str
    title: str | None = None
    author: str | None = None
    date: str | None = None
    description: str | None = None
    language: str | None = None
```

---

## WebCrawler

BFS crawler that discovers and ingests pages reachable from a seed URL.

### Constructor

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
```

- `same_domain_only` — only follows links whose `netloc` matches the seed URL's domain
- `respect_robots` — parses `robots.txt` per domain (cached for the crawl duration); falls back to allow-all when unreachable; honours `Crawl-delay` when it exceeds `crawl_delay`
- `crawl_delay` — minimum seconds between requests to the same domain
- `max_retries` — exponential backoff (1s, 2s, …) on 5xx and transport errors; failed pages are skipped

### Behaviour

URL deduplication strips fragments before queuing — `https://example.com/page#section` and `https://example.com/page` are the same URL. The seed page is always ingested regardless of robots.txt (consistent with standard crawler conventions). Each fetched page is passed directly to `WebPipeline.ingest()` — no second HTTP request.

### Key method

```python
def crawl(self, seed_url: str, depth: int = 1, max_pages: int = 10) -> list[dict]: ...
```

Returns a flat list of chunk records from all successfully ingested pages.

---

## WebChunkRecord

Canonical chunk schema for web content. Constructed by `WebPipeline` and serialised via `.to_dict()`. Optional fields are omitted from the dict when `None`.

| Field | Type | Notes |
|-------|------|-------|
| `chunk_id` | str | `{page_id}_chunk_{index:04d}` |
| `url` | str | Canonical URL after redirects (`final_url`) |
| `domain` | str | `netloc` of the canonical URL |
| `chunk_index` | int | 0-based position within the page |
| `text` | str | Chunk text |
| `fetch_timestamp` | str | ISO 8601 UTC timestamp of the HTTP fetch |
| `title` | str \| omitted | Page `<title>` or og:title from metadata |
| `author` | str \| omitted | Trafilatura or JSON-LD/meta fallback |
| `date` | str \| omitted | Trafilatura or JSON-LD `datePublished` fallback |
| `description` | str \| omitted | Page description (chunk 0 only); trafilatura or OG fallback |
| `language` | str \| omitted | Language code from trafilatura |
| `collection` | str \| omitted | Vector store collection name |
| `section_title` | str \| omitted | Section heading from `SectionChunker`; omitted when no structure detected |
| `schema_type` | str \| omitted | Schema.org `@type` from JSON-LD (e.g. `NewsArticle`) |
| `keywords` | list[str] \| omitted | From JSON-LD `keywords` or `<meta name="keywords">` |
| `publisher` | str \| omitted | From JSON-LD `publisher.name` or `og:site_name` |
| `canonical_url` | str \| omitted | From JSON-LD `url`, `<link rel="canonical">`, or `og:url` |
| `page_id` | str \| omitted | `"web_" + sha256(final_url)[:16]`; set by `WebPipeline` on every chunk |
| `entities` | list[dict] \| omitted | Entity dicts from `BasicEnricher`; omitted when empty |
| `semantic_hints` | dict \| omitted | Semantic hint dict from enricher; omitted when empty |
| `embedding` | list[float] \| omitted | Present only when an embedding model is set |

---

## WebSearchProvider (ABC)

Pluggable interface for querying external search APIs. Implement this to add any search backend.

```python
class WebSearchProvider(ABC):
    def search(self, query: str, k: int = 5) -> list[str]: ...
    # Returns a list of URLs (up to k) ranked by the provider
```

Used by `WEB.SEARCH_WEB`: the provider returns URLs, `WebDataSource` ingests each one through the full pipeline, then ranks the resulting chunks by vector similarity to return `top_k` results.

### TavilySearchProvider

Built-in implementation backed by the Tavily Search API.

```python
TavilySearchProvider(api_key: str | None = None)
```

- `api_key` — reads `TAVILY_API_KEY` from environment when omitted
- Requires `pip install tavily-python` (included in the `[nl]` extra)

### Custom provider example

```python
from blue_data_web_source.source.web.search_provider import WebSearchProvider

class BraveSearchProvider(WebSearchProvider):
    def __init__(self, api_key: str) -> None:
        self._key = api_key

    def search(self, query: str, k: int = 5) -> list[str]:
        import httpx
        resp = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": k},
            headers={"Accept": "application/json", "X-Subscription-Token": self._key},
        )
        resp.raise_for_status()
        return [r["url"] for r in resp.json()["web"]["results"][:k]]
```

---

## NLQueryParser

Translates plain-English instructions into `WEB.*` op dicts using Claude tool use, then hands off to `execute_query`. Used by `WebDataSource.execute_query_nl`.

### Constructor

```python
NLQueryParser(
    model: str = "claude-opus-4-8",
    client: anthropic.Anthropic | None = None,
)
```

When `client` is provided it is used as the Anthropic SDK client instead of constructing a new one. Useful for injecting a pre-configured client in tests or shared-client scenarios.

Requires `pip install 'blue-data-web-source[nl]'` and `ANTHROPIC_API_KEY`.
