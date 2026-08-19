# blue-data-web-source

`blue-data-web-source` is a web ingestion and retrieval source for Blue Data Intelligence. It turns URLs, RSS feeds, and crawled sites into searchable vector chunks — with freshness tracking, polite crawling, and live-search-then-ingest built in — and registers web content as a first-class member of Blue's DataRegistry alongside databases, documents, and graph stores.

→ **[Installation](#installation)** · **[Ops reference](#ops-reference)** · **[Architecture](#architecture)** · **[Testing](#running-tests)**

---

## Why blue-data-web-source

Web loaders exist in LangChain, LlamaIndex, Crawl4AI, and as managed services (Firecrawl, Jina). Here is an honest account of where this library earns its place.

### Freshness-aware ingestion

Most web loaders are stateless: they re-fetch and re-embed every page on every call. `blue-data-web-source` tracks a SHA-256 hash of each page's extracted text. When `WEB.INGEST` or `WEB.INGEST_FEED` is called again on the same URL, unchanged pages return in milliseconds without touching the embedding model. Only new or changed content goes through the pipeline.

```python
from blue_data_web_source import WebDataSource, FileFreshnessStore

source = WebDataSource(
    ...,
    freshness_store=FileFreshnessStore("freshness.json"),
)

# First call: fetches, extracts, embeds, stores
source.execute_query({"op": "WEB.INGEST", "url": "https://example.com/article"})

# Second call: content unchanged → returns [] in milliseconds, no re-embedding
source.execute_query({"op": "WEB.INGEST", "url": "https://example.com/article"})
```

`last_checked_at` updates on every call; `last_ingested_at` updates only when content changes. The freshness store persists across restarts (`FileFreshnessStore`) or stays in-process (`MemoryFreshnessStore`).

### RSS/Atom feed subscription with incremental updates

`WEB.INGEST_FEED` parses any RSS or Atom feed and ingests each article. Combined with freshness tracking, calling the same feed URL daily only re-embeds new or changed articles — the rest are skipped.

```python
# Day 1: ingests 20 articles, embeds all 20
source.execute_query({
    "op": "WEB.INGEST_FEED",
    "url": "https://example.com/rss",
    "max_items": 20,
})

# Day 2: 18 unchanged, 2 new → only 2 articles re-embedded
source.execute_query({
    "op": "WEB.INGEST_FEED",
    "url": "https://example.com/rss",
    "max_items": 20,
})
```

LangChain's `RSSFeedLoader` and LlamaIndex's `RssReader` have no freshness state — they re-load and re-embed every entry on every call.

### DataRegistry integration

`WebDataSource` extends Blue's `DataSource` abstraction. Web content participates in the same `DataRegistry` as SQL tables, document collections, and knowledge graphs. An agent using the registry can query across all of them with one call — no per-source glue code needed.

Web ingestion alone does not require this. If you just want to fetch URLs, the pipeline layer (`WebPipeline`) is sufficient. The DataRegistry integration is the reason to use `WebDataSource`.

### Search-then-ingest in one op

`WEB.SEARCH_WEB` calls a search API (Tavily by default), ingests the top-k result pages on the fly, and returns semantically ranked chunks — all in one call. Most tools give you either a search API or an ingestion library; wiring them together is left to the caller.

```python
results = source.execute_query({
    "op": "WEB.SEARCH_WEB",
    "query": "latest greenhouse gas reduction targets 2026",
    "k": 5,
})
```

### Polite crawling by default

`WEB.CRAWL` checks `robots.txt` before fetching each page and enforces per-domain rate limiting with exponential backoff on retries. LangChain's `RecursiveUrlLoader` does neither. This matters when running agents against real sites on a schedule.

### What it does not claim to be

- **Not the best HTML extractor**: Firecrawl (managed) and Crawl4AI produce cleaner output in some cases. This library uses `trafilatura`, which is solid open-source but not the market leader on extraction quality.
- **Not the only RSS ingestion tool**: LangChain and LlamaIndex both have RSS loaders. The differentiator here is freshness composition, not RSS support alone.
- **Not the only web crawler**: Many libraries crawl. The differentiator here is robots.txt + rate limiting + DataRegistry integration combined.

---

## Core capabilities

- **WEB.INGEST** — fetch → extract → chunk → enrich → embed → upsert; single URL or batch `urls` list
- **WEB.INGEST_FEED** — parse RSS/Atom feed, ingest each article; `max_items` cap; freshness-aware
- **WEB.CRAWL** — BFS from seed URL; configurable depth, max_pages, same-domain filter; robots.txt + rate limiting
- **WEB.SEARCH_WEB** — Tavily search → auto-ingest → semantic rank in one op; pluggable search backend
- **WEB.SEARCH** — KNN over ingested chunks with configurable k and collection
- **WEB.ENSURE_INDEX** — create the vector index for a collection
- **Freshness tracking** — content-hash skip; `MemoryFreshnessStore` or `FileFreshnessStore` (atomic JSON)
- **Natural language interface** — `execute_query_nl` maps plain English to `WEB.*` ops via Claude tool use
- **MCP server** — all ops exposed as MCP tools for agent use

---

## Repository layout

```
blue-data-web-source/
├── lib/                              # Python library (blue-data-web-source)
│   ├── src/blue_data_web_source/
│   └── tests/
├── platform/
│   └── web-mcp/                      # MCP server for web operations
├── docs/
├── data/
│   └── fixtures/                     # Saved HTML fixtures for integration tests
└── examples/
```

---

## Installation

`blue-data-web-source` depends on `blue-data` and `blue-data-doc-source`. Install them from their local paths first:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e /path/to/blue-data
pip install -e /path/to/blue-data-doc-source
pip install -e lib/
```

Optional extras:

```bash
pip install -e "lib/[openai]"               # OpenAI embedding models
pip install -e "lib/[redis]"                # Redis vector store
pip install -e "lib/[feed]"                 # RSS/Atom feed ingestion (feedparser)
pip install -e "lib/[nl]"                   # Natural language interface (Claude)
pip install -e "lib/[openai,redis,feed,nl,dev]"  # everything + dev/test tools
```

Environment variables:

```bash
export OPENAI_API_KEY=sk-...        # for OpenAIEmbeddingModel
export ANTHROPIC_API_KEY=sk-ant-... # for execute_query_nl
export TAVILY_API_KEY=tvly-...      # for WEB.SEARCH_WEB
```

---

## Quick start

Ingest a URL — no embeddings required to inspect extracted chunks:

```python
from blue_data_web_source.source.web.pipeline import WebPipeline

pipeline = WebPipeline(chunk_size=500, overlap=100)
records = pipeline.ingest("https://en.wikipedia.org/wiki/Python_(programming_language)")

for r in records:
    print(r["chunk_id"], r["domain"], r["text"][:80])
```

---

## End-to-end pipeline

```python
from blue_data_web_source import WebDataSource, FileFreshnessStore
from blue_data_doc_source.source.document.embeddings import OpenAIEmbeddingModel
from blue_data_doc_source.source.document.enrichment import BasicEnricher
from blue_data.source.vector.redis.redis_vector_source import RedisVectorSource

redis_source = RedisVectorSource(name="my_redis", host="localhost", port=6379)

source = WebDataSource(
    name="news",
    embedding_model=OpenAIEmbeddingModel(model="text-embedding-3-small"),
    vector_source=redis_source,
    enricher=BasicEnricher(),
    chunk_size=1000,
    overlap=150,
    default_collection="web",
    freshness_store=FileFreshnessStore("freshness.json"),
)

# Create the index before first ingest
source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": "web"})

# Ingest a single URL (re-ingest is safe and cheap when content is unchanged)
records = source.execute_query({
    "op": "WEB.INGEST",
    "url": "https://example.com/article",
    "collection": "web",
})
print(f"Ingested {len(records)} chunks")

# Ingest an RSS feed — only new/changed articles are re-embedded
source.execute_query({
    "op": "WEB.INGEST_FEED",
    "url": "https://example.com/rss",
    "max_items": 20,
    "collection": "web",
})

# Crawl a site (BFS, robots.txt + rate limiting)
source.execute_query({
    "op": "WEB.CRAWL",
    "url": "https://docs.example.com/",
    "depth": 2,
    "max_pages": 20,
    "collection": "web",
})

# Search the live web and return semantically ranked chunks in one call
from blue_data_web_source.source.web.search_provider import TavilySearchProvider

source_with_search = WebDataSource(
    name="news",
    embedding_model=OpenAIEmbeddingModel(),
    vector_source=redis_source,
    search_provider=TavilySearchProvider(),
)
results = source_with_search.execute_query({
    "op": "WEB.SEARCH_WEB",
    "query": "latest greenhouse gas emission targets 2026",
    "k": 5,
    "top_k": 3,
})

# Search already-ingested content
results = source.execute_query({
    "op": "WEB.SEARCH",
    "query": "climate policy",
    "collection": "web",
    "k": 5,
})
for r in results:
    print(r["url"], r["text"][:120])
```

---

## Ops reference

| Op | Required fields | Optional fields | Returns |
|---|---|---|---|
| `WEB.ENSURE_INDEX` | — | `collection` | `None` |
| `WEB.INGEST` | `url` or `urls` | `collection` | `List[Dict]` — all chunks |
| `WEB.INGEST_FEED` | `url` | `max_items`, `collection` | `List[Dict]` — all chunks |
| `WEB.CRAWL` | `url` | `depth` (1), `max_pages` (10), `same_domain_only` (True), `collection` | `List[Dict]` — all chunks |
| `WEB.SEARCH_WEB` | `query` | `k` (5 URLs), `top_k` (5 chunks), `collection` | `List[Dict]` — ranked chunks |
| `WEB.SEARCH` | `query` | `k` (5), `collection` | `List[Dict]` — ranked chunks |

### WEB.INGEST_FEED behaviour

- Fetches the feed URL with the same `WebFetcher` used for pages
- Parses RSS 0.9x/1.0/2.0 and Atom with `feedparser`
- Calls `WEB.INGEST` logic on each article URL in order
- Articles that fail to fetch or yield no extractable content are silently skipped
- `max_items` caps the number of entries processed (default: all)
- Composes with freshness tracking: unchanged articles return `[]` without re-embedding
- Requires `pip install 'blue-data-web-source[feed]'`

### WEB.CRAWL behaviour

- BFS traversal — processes pages level by level up to `depth` hops from the seed
- `same_domain_only=True` (default) — only follows links within the seed URL's domain
- Checks `robots.txt` before fetching each page; skips disallowed paths
- Per-domain rate limiting with configurable `crawl_delay` (default: 1 s)
- Exponential backoff retry on transient failures
- Deduplicates URLs including fragment stripping (`/page#section` → `/page`)
- Reuses fetched HTML for ingestion — no double fetch per page

### WEB.SEARCH_WEB behaviour

- Queries the configured `WebSearchProvider` (default: `TavilySearchProvider`) for top-`k` URLs
- Ingests each URL through the full pipeline (fetch → extract → chunk → embed → upsert)
- Runs semantic search over the freshly ingested content and returns `top_k` ranked chunks
- URL-hash deduplication means re-running the same query does not re-fetch already-ingested pages
- Swap the search backend by passing any `WebSearchProvider` subclass to `WebDataSource`

---

## Architecture

```
URL / Feed URL
  → fetcher/      — WebFetcher: httpx GET with redirect following and configurable timeout
  → feed/         — FeedParser: feedparser RSS/Atom → FeedEntry list (WEB.INGEST_FEED only)
  → extractor/    — WebExtractor: trafilatura strips boilerplate, extracts main text + metadata
  → chunkers/     — SectionChunker (default) or WindowChunker — reused from blue-data-doc-source
  → enrichment/   — Enricher ABC; BasicEnricher does regex entity/keyword extraction (optional)
  → embeddings/   — EmbeddingModel ABC — reused from blue-data-doc-source (optional)
  → record/       — WebChunkRecord: canonical chunk schema with web provenance fields
  → pipeline/     — WebPipeline orchestrates all layers; entry point for library callers
  → web_source/   — WebDataSource extends blue-data DataSource; public entry point
```

**`WebDataSource`** is the top-level public entry point. It extends Blue's `DataSource` abstraction and dispatches on `op` codes. It owns a single `WebPipeline` instance reused across calls.

**`WebPipeline`** orchestrates the ingestion chain. `ingest(url)` returns `List[Dict]` and optionally upserts to a vector store. `ingest_feed(url)` calls `ingest()` per article with optional `max_items`. Returns `[]` when the extractor yields no content, or when freshness tracking determines content is unchanged.

**`WebFetcher`** wraps `httpx.Client` with redirect following and a custom `User-Agent`. The `final_url` after redirects is the canonical URL for deduplication.

**`WebExtractor`** wraps `trafilatura`. Returns `None` for pages with no extractable body (nav-only, login walls). After extraction it strips inline CMS navigation fragments (`Related Story:`, `Read more:`, `See also:`, `Also read:`, `Advertisement`, `Story continues`).

**`FreshnesStore`** — `MemoryFreshnessStore` (in-process) or `FileFreshnessStore` (atomic JSON file). Stores `{page_id, content_hash, url, chunk_count, last_ingested_at, last_checked_at}` keyed by `page_id = "web_" + sha256(final_url)[:16]`.

**`VectorSource`** is Blue's abstraction. The pipeline accepts any `VectorSource` implementation — no adapter layer needed.

### WebChunkRecord schema

Every ingested chunk is serialised via `WebChunkRecord.to_dict()`. Optional fields are omitted when `None`.

| Field | Type | Notes |
|---|---|---|
| `url` | str | Final URL after redirects |
| `domain` | str | `urlparse(url).netloc` |
| `chunk_id` | str | `web_{sha256(url)[:16]}_chunk_{index:04d}` |
| `chunk_index` | int | Zero-based position within the page |
| `text` | str | Chunk text |
| `fetch_timestamp` | str | ISO 8601 UTC timestamp of the fetch |
| `title` | str \| omitted | Page title from trafilatura metadata |
| `author` | str \| omitted | Author from page metadata |
| `date` | str \| omitted | Publication date from page metadata |
| `description` | str \| omitted | Meta description (first chunk only) |
| `language` | str \| omitted | Detected language code |
| `collection` | str \| omitted | Collection name used for this ingest |
| `section_title` | str \| omitted | Section heading from SectionChunker |
| `embedding` | list[float] \| omitted | Present only when an embedding model is set |

---

## Running tests

```bash
# Unit tests (no external services or network required)
.venv/bin/pytest lib/tests/unit/ -v

# Integration tests (HTTP mocked via pytest-httpx, trafilatura runs for real)
.venv/bin/pytest lib/tests/integration/ -v -m integration

# End-to-end scenario tests (no network, no external services)
.venv/bin/pytest lib/tests/e2e/ -v -m e2e

# All tests
.venv/bin/pytest lib/tests/ -v
```

### Test structure

| Layer | Location | What it tests |
|---|---|---|
| Unit | `lib/tests/unit/` | Each component in isolation — fetcher, extractor, pipeline, web source, feed parser; all I/O mocked |
| Integration | `lib/tests/integration/` | Full fetch → extract → chunk chain; only HTTP is mocked, trafilatura runs for real |
| E2E | `lib/tests/e2e/` | Full stack through `WebDataSource.execute_query` — all ops in realistic scenarios; uses `MemoryVectorSource` and `DummyEmbeddingModel` |

Integration tests use two kinds of fixtures:

- **Synthetic HTML** — hand-crafted article HTML covering known extraction behaviour
- **Saved real HTML** — `data/fixtures/npr_article.html` is a real news article committed to the repo; tests assert on exact title, date, domain, and absence of nav fragments

---

## Feature status

| Feature | Status |
|---|---|
| Single URL ingestion (`WEB.INGEST url`) | ✅ |
| Batch URL ingestion (`WEB.INGEST urls:[...]`) | ✅ |
| RSS/Atom feed ingestion (`WEB.INGEST_FEED`) | ✅ |
| BFS site crawling with robots.txt (`WEB.CRAWL`) | ✅ |
| Web search → ingest → rank (`WEB.SEARCH_WEB`) | ✅ |
| Freshness tracking — skip re-embedding unchanged pages | ✅ |
| Natural language interface (`execute_query_nl`) | ✅ |
| URL-level deduplication (same URL → same chunk IDs) | ✅ |
| Content-level deduplication (same text, different URLs) | Not built |
| Source quality / trust scoring per domain | Not built |
| Entity resolution across sources | Not built |
