# blue-data-web-source

Web pages are live data — they update on a schedule, appear through feeds, and belong alongside SQL tables, documents, and graph stores in an agent's data layer. `blue-data-web-source` handles the full pipeline from URL to queryable vector index, with freshness tracking that makes incremental updates cheap and feed subscription that makes scheduled monitoring automatic. It plugs into blue-data's `DataRegistry` so web content becomes a first-class member of the same intelligence layer as the rest of your data.

→ **[Installation](#installation)** · **[Ops reference](#ops-reference)** · **[Architecture](#architecture)** · **[Testing](#running-tests)**

---

## Why blue-data-web-source

The genuine differentiator is not any single feature — it is the combination: content-addressed freshness tracking, feed subscription with date-based filtering, a live-search-then-ingest op, and DataRegistry integration. Together these make web content an updatable, queryable part of an agent's data layer rather than a point-in-time scrape that goes stale immediately.

**Failure 1 — Re-embedding 500 pages when 480 haven't changed**

A news monitoring agent ingests 500 pages per scheduled run. Most days the majority of those pages — evergreen articles, documentation, policy pages — haven't changed since the last run. Without freshness tracking, every run re-fetches and re-embeds all 500. At standard embedding API pricing, a corpus that generates 250K tokens of chunks costs real money per run in unchanged content alone.

`blue-data-web-source` tracks a SHA-256 hash of each page's extracted text. Re-ingesting an unchanged URL returns `[]` in milliseconds — no HTTP re-fetch, no embedding call, no upsert. Only new or changed pages touch the embedding model.

```python
from blue_data_web_source import WebDataSource, FileFreshnessStore

source = WebDataSource(
    embedding_model=OpenAIEmbeddingModel(),
    vector_source=redis_source,
    freshness_store=FileFreshnessStore("freshness.json"),  # persists across restarts
)

# Run 1: all 20 articles fetched, extracted, and embedded
source.execute_query({"op": "WEB.INGEST", "urls": article_urls, "collection": "news"})

# Run 2 (next day): content unchanged → 0 embedding calls, [] per unchanged URL
source.execute_query({"op": "WEB.INGEST", "urls": article_urls, "collection": "news"})
```

`last_checked_at` updates on every run; `last_ingested_at` updates only when content changes. LangChain's web loaders and LlamaIndex readers are stateless — they re-process everything every call.

**Failure 2 — Monitoring a blog means crawling the whole site or maintaining a URL list**

An agent that monitors a publication for new posts has two options without feed support: crawl the entire site on a schedule (slow, impolite, picks up non-article pages) or maintain a list of known URLs by hand (misses new posts, requires external tooling). Neither scales to a corpus of ten sources monitored daily.

RSS and Atom feeds give you article URLs, titles, and publication dates without crawling. `WEB.INGEST_FEED` fetches the feed, filters by `since` date so only new entries are considered, and ingests each article through the same pipeline as `WEB.INGEST`. Combined with freshness tracking, re-running on subsequent days only re-embeds articles whose content actually changed.

```python
# Day 1: all entries on or after the cutoff are new — fetched and embedded
source.execute_query({
    "op": "WEB.INGEST_FEED",
    "url": "https://feeds.npr.org/1001/rss.xml",
    "since": "2026-08-01T00:00:00Z",  # only entries published at or after this date
    "max_entries": 20,
    "collection": "news",
})

# Day 2: advance the cutoff; freshness store skips articles that haven't changed
source.execute_query({
    "op": "WEB.INGEST_FEED",
    "url": "https://feeds.npr.org/1001/rss.xml",
    "since": "2026-08-17T00:00:00Z",
    "max_entries": 20,
    "collection": "news",
})
```

Entries without a publication date are always included — they pass the `since` filter regardless of the cutoff. LangChain's `RSSFeedLoader` and LlamaIndex's `RssReader` re-load all entries on every call with no date filtering.

**Failure 3 — Live web search gives you URLs; you still have to fetch, extract, embed, and rank them**

An agent needs current information on a topic. It calls a search API, gets 10 URLs, then must fetch each page, strip boilerplate, chunk the text, embed each chunk, and rank by relevance to the original query. That is 50+ lines of integration code before a single result comes back — and it needs timeout handling, failed-fetch recovery, and deduplication on top.

`WEB.SEARCH_WEB` does this in one call: search API → fetch and extract each result → chunk and embed → semantic rank → return chunks.

```python
results = source.execute_query({
    "op": "WEB.SEARCH_WEB",
    "query": "GDPR enforcement actions Q3 2026",
    "k": 5,      # pages to fetch from the search API
    "top_k": 3,  # chunks to return after semantic ranking
    "collection": "web",
})
for r in results:
    print(r["url"], r["text"][:120])
```

Re-running the same query does not re-fetch already-ingested pages. URL-hash deduplication means the same final URL always maps to the same chunk IDs — re-running is a safe upsert.

**The integration story: web content alongside your other data**

A research agent answers questions by combining recent web pages, internal documents, and structured database records. With standalone web loaders, web content lives in a separate search index — the agent queries it separately, merges results by hand, and deals with different chunk schemas across sources.

`WebDataSource` extends blue-data's `DataSource` ABC and registers in the same `DataRegistry` as SQL sources, document collections, and knowledge graphs. Web content becomes a first-class member of the same data layer — same registration model, consistent chunk schema, no per-source glue code.

```python
from blue_data import DataRegistry

registry = DataRegistry()
registry.register(sql_source)   # CRM: company and vendor records
registry.register(doc_source)   # internal contracts and policies
registry.register(web_source)   # live web pages and monitored feeds
```

**What it does not do**

No JavaScript rendering. Sites that require a headless browser — React/Next.js apps, login-walled content, dynamically loaded article bodies — return empty from `WebExtractor`. Use Firecrawl or Crawl4AI when JS execution is required.

Not built for high-volume crawling. The BFS crawler is polite (robots.txt, rate limiting, retry backoff) and appropriate for tens to hundreds of pages. For thousands of pages or production-scale scraping, use Firecrawl or Apify.

Not the only RSS loader or web crawler. LangChain and LlamaIndex both have RSS loaders and web crawlers. The differentiator here is freshness composition and DataRegistry integration, not RSS or crawling alone.

---

## Core capabilities

- **WEB.INGEST** — fetch → extract → chunk → enrich → embed → upsert; single URL or batch `urls` list
- **WEB.INGEST_FEED** — parse RSS/Atom feed, filter by `since` date, ingest each article; `max_entries` cap; freshness-aware
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

## Requirements

- Python 3.10+
- [`blue-data`](../blue-data) — core registry and DataSource abstractions
- [`blue-data-doc-source`](../blue-data-doc-source) — chunkers, embedding models, and enrichers (reused by this package)
- Runtime: `httpx`, `trafilatura` — installed automatically
- Optional: `[openai]` — `OpenAIEmbeddingModel`
- Optional: `[redis]` — `RedisVectorSource`
- Optional: `[feed]` — RSS/Atom feed ingestion (`feedparser`)
- Optional: `[nl]` — natural language interface via Claude (`execute_query_nl`)

```bash
export OPENAI_API_KEY=sk-...        # for OpenAIEmbeddingModel
export ANTHROPIC_API_KEY=sk-ant-... # for execute_query_nl
export TAVILY_API_KEY=tvly-...      # for WEB.SEARCH_WEB
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
    "max_entries": 20,
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
| `WEB.INGEST_FEED` | `url` | `since`, `max_entries`, `collection` | `List[Dict]` — all chunks |
| `WEB.CRAWL` | `url` | `depth` (1), `max_pages` (10), `same_domain_only` (True), `collection` | `List[Dict]` — all chunks |
| `WEB.SEARCH_WEB` | `query` | `k` (5 URLs), `top_k` (5 chunks), `collection` | `List[Dict]` — ranked chunks |
| `WEB.SEARCH` | `query` | `k` (5), `collection` | `List[Dict]` — ranked chunks |

### WEB.INGEST_FEED behaviour

- Fetches the feed URL with the same `WebFetcher` used for pages
- Parses RSS 0.9x/1.0/2.0 and Atom with `feedparser`
- Calls `WEB.INGEST` logic on each article URL in order
- Articles that fail to fetch or yield no extractable content are silently skipped
- `since` (ISO 8601) filters entries by publication date before fetching; entries without a date are always included
- `max_entries` caps the number of entries processed after date filtering (default: all)
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

**`WebPipeline`** orchestrates the ingestion chain. `ingest(url)` returns `List[Dict]` and optionally upserts to a vector store. `ingest_feed(url)` filters entries by `since` date, caps with `max_entries`, and calls `ingest()` per article. Returns `[]` when the extractor yields no content, or when freshness tracking determines content is unchanged.

**`WebFetcher`** wraps `httpx.Client` with redirect following and a custom `User-Agent`. The `final_url` after redirects is the canonical URL for deduplication.

**`WebExtractor`** wraps `trafilatura`. Returns `None` for pages with no extractable body (nav-only, login walls). After extraction it strips inline CMS navigation fragments (`Related Story:`, `Read more:`, `See also:`, `Also read:`, `Advertisement`, `Story continues`).

**`FreshnessStore`** — `MemoryFreshnessStore` (in-process) or `FileFreshnessStore` (atomic JSON file). Stores `{page_id, content_hash, url, chunk_count, last_ingested_at, last_checked_at}` keyed by `page_id = "web_" + sha256(final_url)[:16]`.

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
