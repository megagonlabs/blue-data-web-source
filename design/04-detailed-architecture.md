# blue-data-web-source — Detailed Architecture

## External Interfaces

All four interfaces are thin adapters over the Python library. Each delegates to `WebDataSource.execute_query`.

| Interface | Technology | Primary Use |
|-----------|-----------|-------------|
| Python Library | Native Python API | Embedding web ingestion in Python applications and pipelines |
| MCP Server | fastmcp, stdio | LLM agent tool use (Claude Desktop, Claude Code, etc.) |
| REST API | FastAPI, port 8002 | Non-Python consumers and microservice deployments |
| CLI | Click (`blue-web`) | Development, batch ingestion, and scripting |

---

## Information Flow

### Ingest Path (`WEB.INGEST`)

1. Caller provides a URL (or list of URLs)
2. **WebFetcher** makes an HTTP GET via `httpx.Client` with `follow_redirects=True`; raises on non-2xx; captures `final_url` from `response.url`
3. **WebExtractor** calls `trafilatura.extract(html)` to isolate the main content body; calls `trafilatura.extract_metadata(html)` for title, author, date, description; strips inline CMS navigation fragment lines from the extracted body; returns `None` when no extractable body remains — pipeline returns `[]`
4. **MetadataExtractor** makes a single pass over the raw HTML via `HTMLParser` to collect JSON-LD, OpenGraph tags, `<meta>` tags, and `<link rel="canonical">`; supplements any fields not found by trafilatura
5. **Chunker** splits the extracted body text into `(section_title, text)` pairs (`SectionChunker` default; falls back to window chunking when no headings detected); operates on word count
6. **Enricher** annotates each chunk with entities and semantic hints (`BasicEnricher` default; one regex pass per chunk, no API cost)
7. **EmbeddingModel** converts each chunk text to a fixed-dimension float vector
8. **WebChunkRecord** assembles all fields: `chunk_id` (`web_{sha256(final_url)[:16]}_chunk_{idx:04d}`), `url`, `domain`, `title`, `date`, `section_title`, `page_id`, `chunk_index`, `fetch_timestamp`, `entities`, `semantic_hints`
9. `WebChunkRecord.to_dict()` omits `None` optional fields
10. **VectorSource** upserts each chunk; same chunk_id → upsert overwrites rather than duplicates

For multiple URLs (`ingest_many`), steps 2–10 run concurrently via `ThreadPoolExecutor(max_workers=N)`. Pages that fail to fetch or extract are skipped silently.

---

### Search Path (`WEB.SEARCH`)

1. Caller provides a query string and optional `k`, `collection`
2. **EmbeddingModel** embeds the query text into a float vector
3. **VectorSource.knn** returns the `k` nearest chunk vectors from the named collection
4. Results are returned as a list of `WebChunkRecord` dicts with `chunk_id`, `text`, `url`, `domain`, `title`, `section_title`, `date`

Requires `WEB.ENSURE_INDEX` to have been called and `VectorSource` to be set. Raises `ValueError` when no vector store is configured.

---

### Ensure Index Path (`WEB.ENSURE_INDEX`)

1. Caller provides a collection name
2. **WebPipeline** calls `VectorSource.create_index(collection, dim=embedding_model.dim)`
3. Idempotent — skips creation if the index already exists

Must be called before the first ingest. The pipeline does not auto-create the index.

---

### Crawl Path (`WEB.CRAWL`)

1. Caller provides a seed URL, `depth`, `max_pages`, and `same_domain_only`
2. **WebCrawler** initialises a BFS frontier with the seed URL
3. For each URL in the frontier:
   a. Check `robots.txt` for the domain — skip if disallowed
   b. Run the standard Ingest Path (fetch → extract → chunk → embed → upsert)
   c. Parse all `<a href>` links from the fetched HTML
   d. If `same_domain_only=True`, discard links outside the seed domain
   e. Add unseen, unvisited links to the frontier (up to `depth` hops from seed)
4. Stop when `max_pages` pages have been ingested or the frontier is exhausted
5. Return all chunk dicts from all successfully ingested pages

Failed pages (non-2xx, no extractable content) are counted but skipped — they do not block the crawl.

---

### Feed Ingest Path (`WEB.INGEST_FEED`)

1. Caller provides a feed URL and optional `since` (ISO 8601) and `max_entries`
2. **FeedParser** fetches the feed URL and parses it with `feedparser.parse()`
3. Each entry's `published` / `updated` timestamp is compared against `since`; entries older than `since` are skipped; entries without a date always pass
4. `max_entries` caps the number of entries processed after date filtering
5. For each entry, `entry.link` is passed through the standard Ingest Path
6. Entries that fail to fetch or extract are skipped silently
7. Return all chunk dicts from all successfully ingested articles

---

### Structured Extraction Path (`WEB.EXTRACT`)

1. Caller provides a URL (or list of URLs) and a JSON Schema dict
2. For each URL:
   a. **WebFetcher** fetches the page
   b. **WebExtractor** extracts the main body text (same as Ingest Path steps 2–3)
   c. **StructuredFieldExtractor** (from `blue-data-doc-source`) constructs a prompt from the JSON Schema and the extracted text, calls the OpenAI API, and returns a dict of extracted field values
3. Single URL → returns a dict; multiple URLs → returns a list of dicts each with a `url` key

Requires `OPENAI_API_KEY`. Does not chunk, embed, or upsert — extraction is stateless.

---

### Live Web Search Path (`WEB.SEARCH_WEB`)

1. Caller provides a query string, `k` (URLs to fetch), and `top_k` (chunks to return)
2. **TavilySearchProvider** calls the Tavily API with the query and returns `k` URLs
3. Each URL is passed through the standard Ingest Path (fetch → extract → chunk → embed → upsert) — results are stored in the configured collection
4. **VectorSource.knn** searches the collection for the query embedding and returns `top_k` ranked chunks

Requires `TAVILY_API_KEY`. Requires a configured `VectorSource` — unlike `WEB.EXTRACT`, results are stored in the vector index.

---

## Component Contracts

### `WebFetcher`

```python
class WebFetcher:
    def fetch(self, url: str) -> FetchResult: ...
    # FetchResult: html: str, final_url: str, status_code: int
```

`final_url` is `response.url` after redirect resolution. Always used as the canonical URL for `chunk_id` derivation and deduplication.

---

### `WebExtractor`

```python
class WebExtractor:
    def extract(self, html: str, url: str) -> ExtractResult | None: ...
    # ExtractResult: text: str, title: str | None, author: str | None,
    #                date: str | None, description: str | None, language: str | None
```

Returns `None` when the page has no extractable main content. The pipeline returns `[]` in that case — never stores empty chunks.

---

### `MetadataExtractor`

```python
class MetadataExtractor:
    def extract(self, html: str) -> StructuredMetadata: ...
    # StructuredMetadata: schema_type, keywords, publisher, canonical_url
```

Supplements fields missing from `ExtractResult`. Makes one pass over raw HTML via stdlib `HTMLParser` — no external dependency.

---

### `WebChunkRecord`

```python
@dataclass
class WebChunkRecord:
    # Required — always present in to_dict()
    chunk_id:        str        # web_{sha256(final_url)[:16]}_chunk_{index:04d}
    url:             str        # final_url
    domain:          str        # netloc of final_url
    text:            str
    chunk_index:     int        # 0-based index within the page
    fetch_timestamp: str        # UTC ISO-8601

    # Optional — omitted from to_dict() when None or empty
    title:           str | None = None
    author:          str | None = None
    date:            str | None = None
    description:     str | None = None   # attached to chunk 0 only
    language:        str | None = None
    section_title:   str | None = None
    schema_type:     str | None = None
    keywords:        list[str] | None = None
    publisher:       str | None = None
    canonical_url:   str | None = None
    collection:      str | None = None
    page_id:         str | None = None   # "web_" + sha256(final_url)[:16]
    embedding:       list[float] | None = None  # never in to_dict(); passed separately to VectorSource
    entities:        list[dict] = field(default_factory=list)   # from BasicEnricher; omitted when empty
    semantic_hints:  dict = field(default_factory=dict)         # omitted when empty
```

`chunk_id` determinism: the same `final_url` always produces the same `chunk_id`s. Re-ingesting an unchanged URL is an upsert — no duplicates.

---

## Dependency Graph

```
blue-data-web-source
    ├── httpx                    (WebFetcher)
    ├── trafilatura              (WebExtractor)
    ├── blue-data-doc-source
    │   ├── SectionChunker       (chunking)
    │   ├── WindowChunker        (chunking fallback)
    │   ├── BasicEnricher        (enrichment)
    │   ├── LLMEnricher          (enrichment, opt-in)
    │   ├── OpenAIEmbeddingModel (embeddings, [openai] extra)
    │   ├── DummyEmbeddingModel  (dev/test)
    │   └── StructuredFieldExtractor (WEB.EXTRACT)
    └── blue-data
        ├── DataSource ABC       (WebDataSource extends this)
        ├── MemoryVectorSource   (dev/test)
        └── RedisVectorSource    (production, [redis] extra)

Optional extras:
    feedparser      [feed]    — RSS/Atom feed parsing
    openai          [openai]  — OpenAI embeddings + StructuredFieldExtractor
    redis           [redis]   — RedisVectorSource
    anthropic       [llm]     — NLQueryParser (Claude tool use)
    tavily-python   (manual)  — TavilySearchProvider for WEB.SEARCH_WEB
    mcp             [mcp]     — fastmcp runtime for MCP server
```

---

## Reuse Boundary

`blue-data-web-source` deliberately reuses all chunking, enrichment, and embedding logic from `blue-data-doc-source`. The web-specific layer adds only:

- `WebFetcher` — HTTP fetch with redirect resolution
- `WebExtractor` — trafilatura wrapper + CMS fragment stripping
- `MetadataExtractor` — structured metadata from HTML tags
- `WebChunkRecord` — web-specific provenance fields (url, domain, title, date, etc.)
- `WebPipeline` — wiring + concurrent ingest
- `WebCrawler` — BFS + robots.txt
- `FeedParser` — RSS/Atom feed ingestion
- `TavilySearchProvider` — live web search backend
- `NLQueryParser` — Claude tool-use interface
