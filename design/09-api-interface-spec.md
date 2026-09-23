# blue-data-web-source — API / Interface Spec

Four external interfaces sit above the core library: a Python library, a REST API, an MCP server, and a CLI. No business logic belongs in any interface layer — all calls delegate to a `WebDataSource` singleton.

---

## 1. Python Library

The canonical interface. All other interfaces delegate to it.

### `WebPipeline`

The main orchestrator. Constructed once per process via `config.py` in each platform service.

```python
class WebPipeline:
    def __init__(
        self,
        chunk_size: int = 1000,
        overlap: int = 150,
        chunker: Chunker | None = None,                   # default: SectionChunker
        embedding_model: EmbeddingModel | None = None,
        vector_source: VectorSource | None = None,
        enricher: Enricher | None = None,                 # default: BasicEnricher
        collection: str = "web",
        timeout: float = 30.0,
        freshness_store: FreshnessStore | None = None,
        max_workers: int = 10,
        classifier: DocumentClassifier | None = None,
        field_extractor: FieldExtractor | None = None,
        record_store: RecordStore | None = None,
        graph_source: GraphSource | None = None,
        graph_database: str = "web",
    ) -> None: ...
```

#### Methods

```python
def ensure_index(self, collection: str | None = None) -> None: ...
    # Create vector index; idempotent

def ingest(self, url: str, collection: str | None = None) -> list[dict]: ...
    # Fetch, extract, chunk, enrich, embed, upsert; returns list[WebChunkRecord.to_dict()]
    # Returns [] when the extractor finds no main content (login wall, nav-only page)

def ingest_many(self, urls: list[str], collection: str | None = None) -> list[dict]: ...
    # Concurrent multi-URL ingest via ThreadPoolExecutor (max_workers threads)
    # URLs that fail to fetch or extract are skipped; their absence is silent

def ingest_feed(
    self,
    url: str,
    since: str | None = None,
    max_entries: int | None = None,
    collection: str | None = None,
) -> list[dict]: ...
    # Parse RSS/Atom feed; ingest each article URL; filter by ISO 8601 since date

def search(
    self,
    query: str,
    k: int = 5,
    collection: str | None = None,
) -> list[dict]: ...
    # Embed query → KNN; returns ranked WebChunkRecord dicts

def find_records(self, filter: dict, coverage: bool = True) -> FindResult: ...
    # Filter page records from RecordStore by extracted field values

def find_then_search(
    self,
    query: str,
    find_filter: dict,
    k: int = 5,
) -> list[dict]: ...
    # Filter pages by fields, then vector search within matched pages only

def graph_neighbors(self, entity: str, k: int = 10) -> list[dict]: ...
    # Find chunks mentioning a named entity via graph traversal

def graph_query(self, node_id: str, depth: int = 1, k: int = 10) -> list[dict]: ...
    # General graph traversal from any node ID
```

### `WebDataSource`

Extends blue-data's `DataSource` ABC. Public entry point for DataRegistry integration.

```python
class WebDataSource(DataSource):
    def execute_query(self, query: dict) -> Any: ...
```

#### Op codes

| Op | Required fields | Optional fields | Returns |
|----|----------------|----------------|---------|
| `WEB.ENSURE_INDEX` | — | `collection` | `None` |
| `WEB.INGEST` | `url` **or** `urls` | `collection` | `list[dict]` (WebChunkRecord dicts) |
| `WEB.CRAWL` | `url` | `depth`, `max_pages`, `same_domain_only`, `collection` | `list[dict]` |
| `WEB.INGEST_FEED` | `url` | `since`, `max_entries`, `collection` | `list[dict]` |
| `WEB.EXTRACT` | `url` **or** `urls`, `schema` | — | `dict` (single URL) or `list[dict]` (multiple URLs) |
| `WEB.SEARCH` | `query` | `k`, `collection` | `list[dict]` |
| `WEB.SEARCH_WEB` | `query` | `k`, `top_k`, `collection` | `list[dict]` |
| `WEB.FIND` | `filter` | `coverage` | `FindResult` |
| `WEB.FIND_THEN_SEARCH` | `query`, `find_filter` | `k` | `list[dict]` |
| `WEB.GRAPH_NEIGHBORS` | `entity` | `k` | `list[dict]` |
| `WEB.GRAPH_QUERY` | `node_id` | `depth`, `k` | `list[dict]` |

An unrecognised op raises `ValueError`.

---

## 2. REST API

Base URL: `http://localhost:8002`. All routes are under `/web/`. No business logic in handlers.

Request and response bodies are JSON. Request models are Pydantic `BaseModel`.

### `GET /web/health`

**Response** `200 OK`
```json
{"status": "ok"}
```

---

### `POST /web/ensure-index`

Create the vector index for a collection (idempotent).

**Request**
```json
{"collection": "web"}
```

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `collection` | `string \| null` | `null` | Defaults to configured `BLUE_WEB_COLLECTION` |

**Response** `200 OK`
```json
{"status": "ok", "collection": "web"}
```

---

### `POST /web/ingest`

Fetch, extract, chunk, embed, and upsert one or more web pages.

**Request**
```json
{"url": "https://example.com/article", "collection": "web"}
```

Provide `url` for a single page or `urls` for a batch (at least one required):

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `url` | `string \| null` | `null` | Single URL |
| `urls` | `list[string] \| null` | `null` | Batch of URLs |
| `collection` | `string \| null` | `null` | Defaults to configured collection |

**Response** `200 OK`
```json
{
    "chunk_count": 12,
    "pages_ingested": 1,
    "urls": ["https://example.com/article"],
    "collection": "web"
}
```

**`422`** when neither `url` nor `urls` is provided.

---

### `POST /web/crawl`

BFS-crawl from a seed URL and ingest all reachable pages.

**Request**
```json
{"url": "https://example.com", "depth": 2, "max_pages": 20, "same_domain_only": true}
```

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `url` | `string` | required | Seed URL |
| `depth` | `integer` | `1` | BFS hop limit |
| `max_pages` | `integer` | `10` | Hard cap on pages ingested |
| `same_domain_only` | `boolean` | `true` | Restrict to seed domain |
| `collection` | `string \| null` | `null` | Defaults to configured collection |

**Response** `200 OK`
```json
{
    "chunk_count": 84,
    "pages_ingested": 7,
    "urls": ["https://example.com/", "https://example.com/about", ...],
    "collection": "web"
}
```

---

### `POST /web/feed`

Fetch an RSS or Atom feed and ingest each article.

**Request**
```json
{"url": "https://example.com/rss.xml", "since": "2026-08-01T00:00:00Z", "max_entries": 50}
```

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `url` | `string` | required | Feed URL |
| `since` | `string \| null` | `null` | ISO 8601 cutoff; entries older than this are skipped |
| `max_entries` | `integer \| null` | `null` | Cap after date filtering |
| `collection` | `string \| null` | `null` | Defaults to configured collection |

**Response** `200 OK`
```json
{
    "chunk_count": 63,
    "articles_ingested": 5,
    "urls": ["https://example.com/article-1", ...],
    "collection": "web"
}
```

---

### `POST /web/extract`

Fetch a URL and extract typed fields using a JSON Schema.

**Request**
```json
{
    "url": "https://example.com/product",
    "schema": {"properties": {"title": {"type": "string"}, "price": {"type": "number"}}}
}
```

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `url` **or** `urls` | `string \| list[string]` | — | Single URL returns a dict; multiple returns a list |
| `schema` | `object` | required | JSON Schema describing fields to extract (request alias: `schema`) |

**Response** `200 OK` (single URL):
```json
{"title": "Acme Widget", "price": 29.99}
```

**Response** `200 OK` (multiple URLs):
```json
[
    {"url": "https://example.com/a", "title": "...", "price": 29.99},
    {"url": "https://example.com/b", "title": "...", "price": 49.00}
]
```

**`400`** on schema parse error. **`422`** when neither `url` nor `urls` is provided.

---

### `POST /web/search`

Semantic search over ingested pages.

**Request**
```json
{"query": "machine learning frameworks", "k": 5, "collection": "web"}
```

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `query` | `string` | required | Natural-language query |
| `k` | `integer` | `5` | Number of results |
| `collection` | `string \| null` | `null` | Defaults to configured collection |

**Response** `200 OK`
```json
{"results": [...], "count": 5}
```

Each result is a WebChunkRecord dict with `chunk_id`, `text`, `url`, `domain`, `title`?, `section_title`?, `date`?.

---

### `POST /web/search-web`

Live web search: query Tavily, ingest top results on the fly, return ranked chunks.

**Request**
```json
{"query": "Python async best practices", "k": 5, "top_k": 5}
```

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `query` | `string` | required | Search query |
| `k` | `integer` | `5` | URLs to fetch from Tavily |
| `top_k` | `integer` | `5` | Chunks to return after ranking |
| `collection` | `string \| null` | `null` | Transient collection for this call |

**Response** `200 OK`
```json
{"results": [...], "count": 5}
```

Requires `TAVILY_API_KEY`.

---

## 3. MCP Server

Entry point: `platform/web-mcp/src/web_mcp/server.py`. Transport: **stdio** (fastmcp). All tools return a JSON-encoded string.

**Tool naming convention:** MCP tool names use domain-appropriate vocabulary aligned with the operation they represent (`ingest_url`, `crawl_site`, `search_web`, `ensure_index`, `extract`) rather than the generic CLI verb list (add/remove/list/create). The rationale: MCP tools are exposed directly to LLMs that need clear, unambiguous names — domain verbs communicate intent more precisely than generic CRUD verbs for an ingestion pipeline.

**Resources:** Tools only — no MCP resources are defined. All operations write to or read from a vector store; none are static reads that map naturally to a resource URI.

### `ensure_index(collection="web")`

Create the vector index. Idempotent.

**Returns** `{"collection": "web"}`

---

### `ingest_url(url, collection="web")`

Fetch and ingest a single web page.

**Returns** `{"page_id": "...", "url": "...", "domain": "...", "title": "...", "date": "...", "chunk_count": 12, "collection": "web"}`

Returns `{"error": "..."}` when the page yields no extractable content.

---

### `ingest_urls(urls, collection="web")`

Fetch and ingest multiple pages. Pages that fail are skipped.

**Returns** `{"chunk_count": 42, "ingested": 4, "skipped": ["https://..."], "collection": "web"}`

---

### `crawl_site(url, depth=1, max_pages=10, same_domain_only=True, collection="web")`

BFS-crawl from seed URL and ingest all reachable pages.

**Returns** `{"chunk_count": 84, "pages_ingested": 7, "urls": [...], "collection": "web"}`

---

### `search(query, collection="web", k=5)`

Semantic search over ingested pages.

**Returns** `{"results": [...], "count": 5}`

Each result includes `chunk_id`, `text`, `url`, `domain`, and optional `title`, `section_title`, `date`.

---

### `search_web(query, collection="web", k=5)`

Live Tavily search → ingest → rank. Requires `TAVILY_API_KEY`.

**Returns** `{"results": [...], "count": 5}`

---

### `extract(url, schema)`

Extract typed fields from a page using a JSON Schema string.

`schema` must be a JSON-encoded JSON Schema object: `'{"properties": {"title": {"type": "string"}}}'`

**Returns** `{"url": "https://...", "result": {"title": "...", ...}}` — the caller-specified fields wrapped under `"result"`, plus the source `"url"`.

---

### `ingest_feed(url, since=None, max_entries=None, collection="web")`

Parse RSS/Atom feed and ingest articles. Use `since` (ISO 8601) for incremental runs.

**Returns** `{"chunk_count": 63, "articles_ingested": 5, "urls": [...], "collection": "web"}`

Requires `pip install 'blue-data-web-source[feed]'`.

---

## 4. CLI

Package: `blue-data-web-cli`. Entry point: `blue-web`.

Global option: `--json` — print output as JSON instead of plain text.

```
blue-web [--json] <command> [options]
```

### `ensure-index`

```
blue-web ensure-index [--collection COLLECTION]
```

Prints `"Index ready: <collection>"` on success.

### `ingest`

```
blue-web ingest URL [URL ...] [--collection COLLECTION]
```

Ingests one or more URLs. For a single URL, prints title, domain, date, chunk count, collection. For multiple URLs, prints a summary line then per-URL results with `✓` / `✗` markers.

### `crawl`

```
blue-web crawl URL [--depth N] [--max-pages N] [--allow-external] [--collection COLLECTION]
```

BFS-crawl from seed. `--allow-external` lifts the same-domain restriction. Prints crawled page count, chunk count, and each ingested URL.

### `feed`

```
blue-web feed URL [--since ISO8601] [--max-entries N] [--collection COLLECTION]
```

Ingest articles from an RSS or Atom feed. `--since` filters by publication date.

### `search`

```
blue-web search QUERY [--limit N] [-k N] [--collection COLLECTION]
```

Prints numbered results with domain, title, text preview, and URL.

### `search-web`

```
blue-web search-web QUERY [--urls N] [--collection COLLECTION]
```

Live Tavily search → ingest → rank. Requires `TAVILY_API_KEY`.

### `extract`

```
blue-web extract URL [--schema JSON | --schema-file FILE]
```

Prints extracted fields as `key: value` pairs. With `--json`, prints the full result dict.

---

## Environment Variables

All platforms (API, MCP, CLI) read the same env vars.

| Variable | Default | Notes |
|----------|---------|-------|
| `BLUE_WEB_EMBEDDING` | `dummy` | `openai` or `dummy` |
| `OPENAI_API_KEY` | — | Required when `BLUE_WEB_EMBEDDING=openai`; also used by `LLMEnricher` and `WEB.EXTRACT` |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Ignored when `BLUE_WEB_EMBEDDING=dummy` |
| `BLUE_WEB_REDIS_HOST` | — | Omit to use `MemoryVectorSource` (data lost on restart) |
| `BLUE_WEB_REDIS_PORT` | `6379` | Ignored when `BLUE_WEB_REDIS_HOST` is unset |
| `BLUE_WEB_COLLECTION` | `web` | Default collection name |
| `BLUE_WEB_CHUNK_SIZE` | `1000` | Word-count target per chunk |
| `BLUE_WEB_OVERLAP` | `150` | Word-count overlap for window chunking |
| `BLUE_WEB_TIMEOUT` | `30` | HTTP request timeout in seconds |
| `BLUE_WEB_MAX_WORKERS` | `10` | Concurrent fetch threads for `ingest_many` / `WEB.CRAWL` |
| `BLUE_WEB_ENRICHER` | `basic` | `none`, `basic`, or `llm` |
| `BLUE_WEB_LLM_MODEL` | `gpt-4o-mini` | OpenAI model for `llm` enricher; ignored otherwise |
| `TAVILY_API_KEY` | — | Required for `WEB.SEARCH_WEB` |
| `ANTHROPIC_API_KEY` | — | Required for `NLQueryParser` (`[llm]` extra) |
