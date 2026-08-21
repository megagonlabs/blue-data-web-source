# blue-data-web-source — High Level Architecture

## Diagram

```
                    External Interfaces

┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ Python Lib  │  │ MCP Server  │  │  REST API   │  │     CLI     │
└──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
       └────────────────┴────────────────┴────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          WebDataSource                              │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                        WebPipeline                          │   │
│  │                                                             │   │
│  │  ┌──────────┐  ┌────────────┐  ┌────────────────────────┐  │   │
│  │  │ Fetcher  │→ │ Extractor  │→ │  Metadata Extractor    │  │   │
│  │  └──────────┘  └────────────┘  └────────────────────────┘  │   │
│  │                                           │                 │   │
│  │                                           ▼                 │   │
│  │                              ┌────────────────────────┐     │   │
│  │                              │       Chunker          │     │   │
│  │                              │ (from doc-source)      │     │   │
│  │                              └────────────────────────┘     │   │
│  │                                           │                 │   │
│  │                                           ▼                 │   │
│  │                              ┌────────────────────────┐     │   │
│  │                              │       Enricher         │     │   │
│  │                              │ (from doc-source)      │     │   │
│  │                              └────────────────────────┘     │   │
│  │                                           │                 │   │
│  │                                           ▼                 │   │
│  │                              ┌────────────────────────┐     │   │
│  │                              │   Embedding Model      │     │   │
│  │                              │ (from doc-source)      │     │   │
│  │                              └────────────────────────┘     │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────────────────┐  ┌──────────────────┐  ┌─────────────────┐  │
│  │   Web Crawler    │  │   Feed Parser    │  │  NL Interface   │  │
│  └──────────────────┘  └──────────────────┘  └─────────────────┘  │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │              Structured Field Extractor                     │   │
│  │                  (from doc-source)                          │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────┬───────────────────────┘
                                              │
                       ┌──────────────────────▼──────────────────────┐
                       │  ┌────────────────┐  ┌────────────────────┐  │
                       │  │ MemoryVector   │  │  RedisVectorSource │  │
                       │  │    Source      │  │  (blue-data)       │  │
                       │  └────────────────┘  └────────────────────┘  │
                       └─────────────────────────────────────────────┘
```

## Components

### External Interfaces

Four interface adapters sit in front of `WebDataSource` and delegate to it. They share no business logic — each is a thin translation layer.

**Python Library** — Native Python API (`WebDataSource`, `WebPipeline`). Used to embed web ingestion directly in Python applications and agentic pipelines.

**MCP Server** — Exposes `ensure_index`, `ingest_url`, `ingest_urls`, `crawl_site`, `search`, `search_web`, `extract`, and `ingest_feed` as MCP tools, enabling LLM agents to ingest and search web content within an agentic context.

**REST API** — HTTP/JSON interface (FastAPI, port 8002). Eight routes under `/web/` mirror the eight op codes. No business logic in handlers — all calls delegate to `WebDataSource`.

**CLI** — Click command group (`blue-web`). Seven commands: `ensure-index`, `ingest`, `crawl`, `feed`, `search`, `search-web`, `extract`.

---

### WebDataSource

The top-level public entry point. Extends blue-data's `DataSource` ABC. Owns a single `WebPipeline` instance and dispatches on seven op codes: `WEB.ENSURE_INDEX`, `WEB.INGEST`, `WEB.CRAWL`, `WEB.INGEST_FEED`, `WEB.EXTRACT`, `WEB.SEARCH`, `WEB.SEARCH_WEB`.

---

### WebPipeline

Orchestrates the ingestion chain for a single URL. Owns all components and wires them together. Key responsibilities:

- `ingest(url)` — runs the full fetch → extract → metadata → chunk → enrich → embed → upsert chain
- `ingest_many(urls)` — concurrent batch ingest via `ThreadPoolExecutor`
- `ensure_index(collection)` — delegates to `VectorSource.create_index` with the correct embedding dimension
- `search(query, k)` — embeds the query and delegates KNN to `VectorSource`

---

### Fetcher

`WebFetcher` wraps `httpx.Client`. Follows redirects and captures the `final_url` after redirect resolution. The `final_url` is the canonical identity used for deduplication — two input URLs that redirect to the same destination produce the same chunk IDs.

---

### Extractor

`WebExtractor` wraps `trafilatura.extract()` and `trafilatura.extract_metadata()`. Strips HTML boilerplate (navigation, ads, sidebars, footers) and returns the main body text plus title, author, date, and description. Applies a second pass to remove inline CMS navigation fragments (`Related Story:`, `Read more:`, `See also:`, etc.). Returns `None` when no extractable body remains — the pipeline returns `[]` in that case.

---

### Metadata Extractor

`MetadataExtractor` uses Python's stdlib `html.parser.HTMLParser` — no external dependency. Makes a single pass over the raw HTML to extract JSON-LD structured data, OpenGraph tags, standard `<meta>` tags, and `<link rel="canonical">`. Supplements author, date, description, schema_type, keywords, publisher, and canonical_url when trafilatura does not find them.

---

### Chunker

`SectionChunker` (default) and `WindowChunker` are imported directly from `blue-data-doc-source` — not reimplemented. Both operate on word count. `SectionChunker` detects Markdown/heading markers and falls back to window chunking when no structure is detected.

---

### Enricher

`BasicEnricher` (default), `LLMEnricher`, and the `Enricher` ABC are imported from `blue-data-doc-source`. `BasicEnricher` adds PROPER_NOUN entity detection and semantic hints (has_numeric, has_date, is_short) with no API cost. `LLMEnricher` is opt-in (`BLUE_WEB_ENRICHER=llm`) — one GPT-4o-mini call per chunk.

---

### Embedding Model

`OpenAIEmbeddingModel` and `DummyEmbeddingModel` are imported from `blue-data-doc-source`. The `EmbeddingModel` ABC requires a `dim` property used to configure the vector index at `WEB.ENSURE_INDEX` time.

---

### Web Crawler

`WebCrawler` implements BFS from a seed URL. Respects `robots.txt` and enforces per-domain rate limiting. Stops when `max_pages` is reached or when the BFS frontier is exhausted. When `same_domain_only=True` (the default), links outside the seed domain are not followed.

---

### Feed Parser

`FeedParser` wraps `feedparser` (the `[feed]` extra). Parses RSS and Atom feeds, returns a list of `FeedEntry` objects (url, title, published, summary). Each entry URL is then passed through the standard ingest pipeline. Supports `since` date filtering to skip entries older than a cutoff.

---

### Structured Field Extractor

`StructuredFieldExtractor` is imported from `blue-data-doc-source`. Used by `WEB.EXTRACT` — fetches a URL, extracts the body text, then calls an LLM to fill in the fields described by a caller-supplied JSON Schema. Requires `OPENAI_API_KEY`.

---

### NL Interface

`NLQueryParser` converts free-text instructions to `WEB.*` op dicts using Claude's tool-use API. Six tools are registered: `ingest_url`, `search`, `ensure_index`, `crawl`, `search_web`, `ingest_feed`. Requires the `[llm]` extra (`anthropic` package) and `ANTHROPIC_API_KEY`.

---

### Vector Store

Provided by `blue-data` via the `VectorSource` ABC. `WebPipeline` interacts only with this interface — no adapter layer.

| Backend | Class | Notes |
|---------|-------|-------|
| Memory | `MemoryVectorSource` | In-process numpy cosine; for dev/test |
| Redis | `RedisVectorSource` | Production; requires Redis Stack |
