# blue-data-web-source

`blue-data-web-source` is a web ingestion and retrieval source for Blue Data Intelligence. It sits upstream of Blue's vector layer, converting live web pages into structured, provenance-aware chunk records that AI agents can search, cite, and reason over.

It handles the full pipeline from URL to queryable vector index: fetching pages over HTTP, stripping boilerplate with trafilatura, detecting section structure, extracting entities and keywords, generating embeddings, and upserting into Blue's vector store. It is built as an acquisition layer on top of `blue-data-doc-source`, reusing the same chunkers, enrichers, and embedding models, and adding HTTP fetch and HTML extraction as the front-end stage. It plugs directly into Blue's `DataSource` abstraction, so web content becomes a first-class member of the same data layer as documents, SQL databases, and graph stores.

Its core capabilities include:

- **Web ingestion pipeline** — fetch → extract → chunk → enrich → embed → upsert in a single `WEB.INGEST` call; handles redirects, errors, and empty pages automatically
- **Boilerplate-aware extraction** — trafilatura identifies the main article body and strips ads, navigation, and inline CMS cross-reference noise
- **Web-provenance records** — every chunk carries `url`, `domain`, `fetch_timestamp`; title, author, date, and language attached when found in page metadata
- **URL-hash deduplication** — `chunk_id` derived from SHA-256 of the final URL after redirects; re-ingesting the same URL is a safe upsert, not a duplicate
- **Section-aware chunking** — reuses `SectionChunker` from `blue-data-doc-source`; preserves `section_title` on every chunk; falls back to sliding-window when no headers found
- **Regex enrichment** — `BasicEnricher` extracts named entities (DATE, EMAIL, MONEY, PERCENT, PROPER_NOUN) and semantic hints with no ML dependency
- **Pluggable embeddings** — `OpenAIEmbeddingModel`, `DummyEmbeddingModel`, or any `EmbeddingModel` subclass
- **Blue-data native** — extends `DataSource`, delegates storage and KNN entirely to Blue's `VectorSource`; works with `MemoryVectorSource`, `RedisVectorSource`, and any future backend
- **Batch ingestion** — `WEB.INGEST` accepts a single `url` or a `urls` list; returns a flat chunk list across all URLs
- **Crawling** — `WEB.CRAWL` BFS from a seed URL; configurable depth and max_pages; same-domain by default; skips failed pages; strips URL fragments before dedup
- **Search-augmented ingestion** — `WEB.SEARCH_WEB` calls a search API (Tavily by default), ingests result pages on the fly, returns semantically ranked chunks; backend pluggable via `WebSearchProvider` ABC
- **Natural language interface** — `execute_query_nl` maps plain-English instructions to `WEB.*` ops using Claude tool use; requires `ANTHROPIC_API_KEY`

→ **[Full feature list](docs/features.md)** · **[Installation](#installation)** · **[Architecture](docs/architecture.md)** · **[Extraction](docs/extraction.md)** · **[Crawling](docs/crawling.md)** · **[MCP server](docs/mcp.md)** · **[Examples](examples/README.md)**

## Repository layout

```
blue-data-web-source/
├── lib/                              # Python library (blue-data-web-source)
│   ├── src/blue_data_web_source/
│   └── tests/
├── platform/
│   └── web-mcp/                      # MCP server for web operations
├── docs/                             # Documentation
├── data/
│   └── fixtures/                     # Saved HTML fixtures for integration tests
└── examples/                         # Runnable examples
```

## Requirements

- Python 3.10+
- [`blue-data`](../blue-data) (local dependency)
- [`blue-data-doc-source`](../blue-data-doc-source) (local dependency — reuses chunkers, embeddings, enrichers)
- Runtime: `httpx`, `trafilatura` — installed automatically via `pip install`
- Optional: `[openai]` — OpenAI embedding models (`OpenAIEmbeddingModel`)
- Optional: `[redis]` — Redis vector storage (`RedisVectorSource`)
- Optional: `[nl]` — natural language query interface via Claude (`execute_query_nl`)

```bash
export OPENAI_API_KEY=sk-...        # for OpenAIEmbeddingModel
export ANTHROPIC_API_KEY=sk-ant-... # for execute_query_nl
export TAVILY_API_KEY=tvly-...      # for WEB.SEARCH_WEB (TavilySearchProvider)
```

## Use case

You have a set of URLs — news articles, blog posts, documentation pages, research reports — and you want to search across their content semantically, or use them as context in an LLM (RAG pipeline). `blue-data-web-source` is the ingestion layer that handles everything between "I have a URL" and "I have searchable chunks in a vector store":

1. **Fetch** the page (follows redirects, configurable timeout)
2. **Extract** the main article body — strips ads, navigation, footers, and inline cross-reference noise via trafilatura
3. **Chunk** the text into overlapping windows (section-aware by default)
4. **Embed** each chunk (OpenAI `text-embedding-3-small` or bring your own)
5. **Upsert** to a vector store (Redis by default, any `VectorSource` implementation works)
6. **Search** with a natural language query — returns the k nearest chunks

Re-ingesting the same URL overwrites rather than duplicates (URL-level deduplication via chunk ID hashing).

**Typical callers:**
- A news monitoring pipeline that ingests articles on a schedule and searches for topics
- A research assistant that collects web pages and lets an LLM query across them
- Any RAG application that needs web content alongside document/PDF sources

`blue-data-web-source` is the web counterpart to `blue-data-doc-source` (PDF/TXT ingestion). It reuses the same chunkers, embedding models, and enrichers — only the fetch and HTML extraction stages are new.

## Installation

`blue-data-web-source` depends on `blue-data` and `blue-data-doc-source`, which are not yet on a public package index. Install them from their local paths first, in order — pip's resolver requires sequential installs here:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e /path/to/blue-data
pip install -e /path/to/blue-data-doc-source
pip install -e lib/
```

Optional extras:

```bash
pip install -e "lib/[openai]"           # OpenAI embeddings
pip install -e "lib/[redis]"            # Redis vector store
pip install -e "lib/[openai,redis,dev]" # everything + dev/test tools
```

## Quick start

Ingest a URL with no embeddings — useful for inspecting extracted chunks before wiring up a vector store:

```python
from blue_data_web_source.source.web.pipeline import WebPipeline

pipeline = WebPipeline(chunk_size=500, overlap=100)
records = pipeline.ingest("https://en.wikipedia.org/wiki/Python_(programming_language)")

for r in records:
    print(r["chunk_id"], r["domain"], r["text"][:80])
```

## End-to-end pipeline

Full pipeline: fetch → extract → chunk → enrich → embed → upsert to Redis → search.

`blue-data-web-source` depends on both `blue-data` and `blue-data-doc-source`. Pass a `VectorSource` instance (e.g. `RedisVectorSource`) from blue-data as the `vector_source`.

```python
from blue_data_web_source import WebDataSource
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
)

# Create the index before first ingest
source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": "web"})

# Ingest a single URL
records = source.execute_query({
    "op": "WEB.INGEST",
    "url": "https://example.com/article",
    "collection": "web",
})
print(f"Ingested {len(records)} chunks")

# Ingest multiple URLs in one call
records = source.execute_query({
    "op": "WEB.INGEST",
    "urls": [
        "https://example.com/article-1",
        "https://example.com/article-2",
    ],
    "collection": "web",
})

# Crawl a site (BFS from seed URL)
records = source.execute_query({
    "op": "WEB.CRAWL",
    "url": "https://docs.example.com/",
    "depth": 2,       # 0 = seed only, 1 = seed + its links, …
    "max_pages": 20,  # hard cap on pages fetched
    "collection": "web",
})

# Search the web on the fly and return semantically ranked chunks
from blue_data_web_source.source.web.search_provider import TavilySearchProvider

source_with_search = WebDataSource(
    name="news",
    embedding_model=OpenAIEmbeddingModel(),
    vector_source=redis_source,
    search_provider=TavilySearchProvider(),  # reads TAVILY_API_KEY from env
)
results = source_with_search.execute_query({
    "op": "WEB.SEARCH_WEB",
    "query": "latest greenhouse gas emission targets 2026",
    "k": 5,      # pages to fetch from the search API
    "top_k": 3,  # chunks to return after semantic ranking
})

# Search existing index
results = source.execute_query({
    "op": "WEB.SEARCH",
    "query": "climate policy",
    "collection": "web",
    "k": 5,
})
for r in results:
    print(r["url"], r["text"][:120])
```

### Without enrichment or a vector source

Embeddings and enrichment are both optional. Omit either to skip that step:

```python
source = WebDataSource(
    embedding_model=OpenAIEmbeddingModel(),  # embeds but no storage
)
records = source.execute_query({"op": "WEB.INGEST", "url": "https://example.com"})
```

Upsert only fires when both `embedding_model` and `vector_source` are provided and the chunk has an embedding.

## CLI example

```bash
# Ingest a URL and print extracted chunks (no embeddings required)
python examples/ingest_url.py https://en.wikipedia.org/wiki/Python_(programming_language)

# Ingest into a named collection
python examples/ingest_url.py https://example.com/article --collection news
```

## Architecture

```
URL
  → fetcher/      — WebFetcher: httpx GET with redirect following and configurable timeout
  → extractor/    — WebExtractor: trafilatura strips boilerplate, extracts main text + metadata
  → chunkers/     — SectionChunker (default) or WindowChunker — reused from blue-data-doc-source
  → enrichment/   — Enricher ABC; BasicEnricher does regex entity/keyword extraction (optional)
  → embeddings/   — EmbeddingModel ABC — reused from blue-data-doc-source (optional)
  → record/       — WebChunkRecord: canonical chunk schema with web provenance fields
  → pipeline/     — WebPipeline orchestrates all layers; entry point for library callers
  → web_source/   — WebDataSource extends blue-data DataSource; public entry point
```

**`WebDataSource`** is the top-level public entry point. It extends blue-data's `DataSource` abstraction and dispatches on `op` codes: `WEB.INGEST`, `WEB.CRAWL`, `WEB.SEARCH_WEB`, `WEB.SEARCH`, `WEB.ENSURE_INDEX`. It owns a single `WebPipeline` instance reused across calls.

**`WebPipeline`** orchestrates the ingestion chain. `ingest(url)` fetches, extracts, chunks, embeds, and optionally upserts; it returns `List[Dict]`. `search(query)` embeds the query and delegates KNN to the injected `VectorSource`. `ensure_index(collection)` calls `VectorSource.create_index` with the correct embedding dim.

**`WebFetcher`** wraps `httpx.Client` with redirect following and a configurable `User-Agent`. Raises `httpx.HTTPStatusError` on non-2xx responses.

**`WebExtractor`** wraps `trafilatura`. It returns `None` when the page yields no extractable main content (e.g. pure navigation or login walls), causing `WebPipeline.ingest` to return an empty list.

**`VectorSource`** is blue-data's abstraction. The pipeline accepts any `VectorSource` implementation — no adapter layer needed.

### WebChunkRecord schema

Every ingested chunk becomes a `WebChunkRecord`, serialised via `.to_dict()`. Optional fields are omitted when `None`.

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

### Chunking

Chunkers are imported directly from `blue-data-doc-source`. `SectionChunker` is the default — it detects markdown-style headers and chunks within sections, falling back to `WindowChunker` when none are found. Both operate on **word count**, not character count.

### Extraction

`trafilatura` is used for both content extraction and metadata parsing. It removes navigation, ads, footers, and comments, keeping only the main article body. Pages with no extractable body return `None` from `WebExtractor.extract()` — the pipeline returns `[]` in this case rather than storing empty chunks.

After trafilatura runs, `WebExtractor` applies a post-processing pass that strips inline navigation fragments that CMSes inject into article text — lines beginning with `Related Story:`, `Read more:`, `See also:`, `Also read:`, `Advertisement`, or `Story continues`. These are removed before text reaches the chunker.

### Embeddings

| Class | Use case |
|---|---|
| `OpenAIEmbeddingModel` | Production; calls `text-embedding-3-small` by default |
| `DummyEmbeddingModel` | Tests; SHA-256 hash → fixed-dim float vector (not real similarity) |

Both are imported from `blue-data-doc-source`.

### Deduplication

`chunk_id` is derived from a SHA-256 hash of the final URL. Re-ingesting the same URL produces the same chunk IDs, so vector store upsert overwrites rather than duplicates.

## Ops reference

| Op | Required fields | Optional fields | Returns |
|---|---|---|---|
| `WEB.ENSURE_INDEX` | — | `collection` | `None` |
| `WEB.INGEST` | `url` or `urls` | `collection` | `List[Dict]` — all chunks |
| `WEB.CRAWL` | `url` | `depth` (1), `max_pages` (10), `same_domain_only` (True), `collection` | `List[Dict]` — all chunks |
| `WEB.SEARCH_WEB` | `query` | `k` (5 URLs to fetch), `top_k` (5 chunks to return), `collection` | `List[Dict]` — ranked chunks |
| `WEB.SEARCH` | `query` | `k` (5), `collection` | `List[Dict]` — ranked chunks |

### WEB.CRAWL behaviour

- BFS traversal — processes pages level by level up to `depth` hops from the seed
- `same_domain_only=True` (default) — only follows links within the seed URL's domain
- Skips pages that fail to fetch (404, timeout, etc.) and continues
- Deduplicates URLs including fragment stripping (`/page#section` → `/page`)
- Reuses the fetched HTML for ingestion — no double fetch per page

### WEB.SEARCH_WEB behaviour

- Queries the configured `WebSearchProvider` (default: `TavilySearchProvider`) for top-`k` URLs
- Ingests each URL through the full pipeline (fetch → extract → chunk → embed → upsert)
- Runs semantic search over the freshly ingested content and returns `top_k` ranked chunks
- Re-running the same query does not re-fetch already-ingested URLs (URL-hash deduplication)
- Swap the search backend by passing any `WebSearchProvider` subclass to `WebDataSource`

## Current scope

| Feature | Status |
|---|---|
| Single URL ingestion (`WEB.INGEST url`) | ✅ Done |
| Batch URL ingestion (`WEB.INGEST urls:[...]`) | ✅ Done |
| BFS site crawling (`WEB.CRAWL`) | ✅ Done |
| Web search → ingest → rank (`WEB.SEARCH_WEB`) | ✅ Done |
| Natural language interface (`execute_query_nl`) | ✅ Done |
| URL-level deduplication (same URL → same chunk IDs) | ✅ Done |
| Content-level deduplication (same text from different URLs) | Not built |
| Source quality / trust scoring per domain | Not built |
| Freshness tracking / re-ingest scheduling | Not built |
| Entity resolution across sources | Not built |

## Running tests

```bash
# Unit tests (no external services or network required)
.venv/bin/pytest lib/tests/unit/ -v

# Single file
.venv/bin/pytest lib/tests/unit/test_pipeline.py -v

# Integration tests (no network — HTTP mocked via pytest-httpx, trafilatura runs for real)
.venv/bin/pytest lib/tests/integration/ -v -m integration

# End-to-end scenario tests (no network, no external services)
.venv/bin/pytest lib/tests/e2e/ -v -m e2e

# All tests
.venv/bin/pytest lib/tests/ -v
```

### Test structure

| Layer | Location | What it tests |
|---|---|---|
| Unit | `lib/tests/unit/` | Each component in isolation — fetcher, extractor, pipeline, web source; all I/O mocked |
| Integration | `lib/tests/integration/` | Full fetch → extract → chunk chain; only HTTP is mocked (via `pytest-httpx`), trafilatura runs for real |
| E2E | `lib/tests/e2e/` | Full stack through `WebDataSource.execute_query` — all three ops in realistic scenarios; uses `MemoryVectorSource` and `DummyEmbeddingModel` |

Integration tests use two kinds of fixtures:

- **Synthetic HTML** — hand-crafted article HTML covering known extraction behaviour (title, author, boilerplate stripping, section chunking)
- **Saved real HTML** — `data/fixtures/npr_article.html` is a real news article fetched once and committed to the repo; tests assert on exact title, date, domain, article keywords, and absence of nav fragments

The saved fixture makes integration tests offline and deterministic — no network calls, no flakiness from site changes.

### E2E scenarios

E2E tests cover six scenario groups using `MemoryVectorSource` (in-process KNN, no Redis) and `DummyEmbeddingModel` (deterministic hash-based vectors, no API key):

| Scenario | What is verified |
|---|---|
| Full ingest → search cycle | Results returned, expected fields present, correct domain, `k` limit respected |
| Re-ingest deduplication | Same chunk IDs on second ingest; vector store does not grow |
| Multi-article ingest | Total chunk count, both domains present in search results |
| Collection isolation | Ingesting into collection A does not surface in searches against collection B |
| Edge cases | Boilerplate-only page yields `[]`; HTTP 404 propagates; `WEB.ENSURE_INDEX` is idempotent; search before any ingest returns `[]` |
| NPR fixture (real HTML) | Full cycle on a saved real article; title in results; no nav fragments in returned text |

## Demo

### Showcase demo (`examples/demo.py`)

A formatted walkthrough of the full pipeline — no network or external services required. HTTP is intercepted and served from local fixture files.

```bash
python examples/demo.py
```

Walks through: setup → create index → ingest two articles → sample chunks → semantic search → natural language queries (requires `ANTHROPIC_API_KEY`).

### CLI demo (`examples/demo_web.py`)

A minimal CLI that fetches real URLs and runs search or NL queries. Uses the local NPR fixture when no URL is supplied.

```bash
# Dummy embeddings, local fixture (no API keys needed)
python examples/demo_web.py --query "what is the missile test about"

# Real semantic search with OpenAI embeddings
OPENAI_API_KEY=sk-... python examples/demo_web.py \
    --embedding openai \
    --query "what are greenhouse gas targets"

# Natural language query (Claude parses intent, OpenAI embeds)
OPENAI_API_KEY=sk-... ANTHROPIC_API_KEY=sk-ant-... python examples/demo_web.py \
    --embedding openai \
    --nl-query "find news about China's missile test"

# Pass your own URL
OPENAI_API_KEY=sk-... python examples/demo_web.py \
    https://text.npr.org/nx-s1-5884537 \
    --embedding openai \
    --query "South Pacific missile launch"
```
