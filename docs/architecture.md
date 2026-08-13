# Architecture

blue-data-web-source is a strict one-way pipeline. Data flows in one direction only — from URL to vector store — with no shared state between layers and no backwards coupling.

```
URL
  │
  ▼
WebFetcher          HTTP GET with redirect following; raises on non-2xx
  │
  ▼
WebExtractor        trafilatura strips boilerplate; strips CMS nav fragments;
                    extracts metadata (title, author, date, language)
                    returns None for pages with no extractable body
  │
  ▼
Chunker             SectionChunker (default) or WindowChunker
                    reused from blue-data-doc-source
  │
  ▼
Enricher            BasicEnricher: regex entity + keyword extraction (optional)
                    reused from blue-data-doc-source
  │
  ▼
EmbeddingModel      embed each chunk text (optional)
                    reused from blue-data-doc-source
  │
  ▼
WebChunkRecord      canonical schema — url, domain, chunk_id, provenance fields
  │
  ▼
VectorSource        upsert to Redis or any blue-data VectorSource (optional)
```

Each layer is independently injected. You can replace any stage without touching the others.

---

## Layers

### WebDataSource

`WebDataSource` extends blue-data's `DataSource` ABC. It is the public entry point for the library.

Responsibilities:
- Accepts `execute_query({"op": "...", ...})` calls
- Dispatches on the `"op"` field to `WEB.INGEST`, `WEB.CRAWL`, `WEB.SEARCH`, `WEB.SEARCH_WEB`, `WEB.ENSURE_INDEX`
- Owns a single `WebPipeline` instance and a single `WebCrawler` instance reused across calls
- Exposes `execute_query_nl(text)` for natural language dispatch via `NLQueryParser` (Claude tool use)

`WebDataSource` adds no logic of its own beyond dispatch and lifecycle management. All ingestion, crawling, search, and index logic lives in `WebPipeline` and `WebCrawler`.

Constructor:

```python
WebDataSource(
    name="web",
    embedding_model=None,       # EmbeddingModel from blue-data-doc-source
    vector_source=None,         # VectorSource from blue-data
    enricher=None,              # Enricher from blue-data-doc-source
    chunk_size=1000,
    overlap=150,
    default_collection="web",
    timeout=30.0,               # HTTP timeout in seconds
    nl_model="claude-opus-4-8", # Claude model for execute_query_nl
    search_provider=None,       # WebSearchProvider for WEB.SEARCH_WEB
    properties=None,
)
```

### WebPipeline

`WebPipeline` orchestrates the ingestion chain. It is the core of the library.

Responsibilities:
- Calls `WebFetcher.fetch(url)` to retrieve the HTML (skipped when `_fetch_result` is passed by the crawler)
- Calls `WebExtractor.extract(html, url)` — returns `[]` when the extractor returns `None`
- Calls `Chunker.chunk_with_titles(text)` on the extracted text
- Calls `Enricher.enrich(text)` on each chunk (if set); attaches entities to the chunk dict externally
- Calls `EmbeddingModel.embed_text(text)` on each chunk (if set)
- Constructs a `WebChunkRecord` per chunk and calls `VectorSource.upsert()` (if both embedding and vector source are set)

Key methods:

| Method | Description |
|---|---|
| `ingest(url, collection, _fetch_result)` | Full pipeline from URL; `_fetch_result` skips fetch (used by crawler) |
| `search(query, k, collection)` | Embed query → KNN via VectorSource |
| `ensure_index(collection)` | Create vector index (no-op if model or vector source absent) |

`ingest` always returns a flat `list[dict]`. Returns `[]` when the extractor yields no content — never stores empty chunks.

### WebFetcher

`WebFetcher` wraps `httpx.Client` with redirect following and a descriptive `User-Agent`.

```
User-Agent: blue-data-web-source/0.1 (compatible; httpx)
```

Key behaviour:
- `follow_redirects=True` — follows HTTP 301/302/307/308 chains
- `final_url` on `FetchResult` is the canonical URL after all redirects — used for chunk ID hashing
- `response.raise_for_status()` — raises `httpx.HTTPStatusError` on non-2xx; propagates to caller
- Default timeout: **30 seconds** — necessary for news sites with slow SSL handshakes

```python
from blue_web_source.source.web.fetcher import WebFetcher

fetcher = WebFetcher(timeout=30.0)
result = fetcher.fetch("https://example.com/article")

print(result.url)          # original URL
print(result.final_url)    # canonical URL after redirects
print(result.status_code)  # 200
print(result.html[:200])   # raw HTML
```

`FetchResult` fields: `url`, `final_url`, `html`, `status_code`, `content_type`.

### WebExtractor

`WebExtractor` wraps trafilatura and is a hard dependency of this package. See [`docs/extraction.md`](extraction.md) for the full reference.

Key behaviour:
- Calls `trafilatura.extract()` on raw HTML — returns `None` when no body is extractable
- Calls `trafilatura.extract_metadata()` for title, author, date, description, language
- Applies a post-processing pass to strip inline CMS nav fragments
- Returns `ExtractResult` with `text`, `title`, `author`, `date`, `description`, `language`, `url` — or `None`

### Chunker

Chunkers are imported directly from `blue-data-doc-source`. The same `SectionChunker` and `WindowChunker` used for document ingestion are reused here unchanged. See blue-data-doc-source's `docs/chunkers.md` for the full reference.

`SectionChunker` is the default. It operates on **word count**, not character count.

Pass a custom chunker via `WebPipeline(chunker=...)` or `WebDataSource(..., chunk_size=..., overlap=...)`.

### Enricher

`BasicEnricher` is imported from `blue-data-doc-source`. It is optional — omit to skip enrichment.

When set, enrichment results are attached to the chunk dict **externally** by `WebPipeline` after `WebChunkRecord.to_dict()` is called — `entities` is not a `WebChunkRecord` field. This keeps the record schema clean while allowing enrichment output to travel with each chunk.

### EmbeddingModel

`EmbeddingModel` is imported from `blue-data-doc-source`. Upsert fires only when both `embedding_model` and `vector_source` are set.

### WebChunkRecord

`WebChunkRecord` is the canonical unit of output from the pipeline.

```python
@dataclass
class WebChunkRecord:
    url: str                              # final URL after redirects
    domain: str                           # urlparse(url).netloc
    chunk_id: str                         # "web_{sha256(url)[:16]}_chunk_{index:04d}"
    chunk_index: int                      # 0-based position within the page
    text: str
    fetch_timestamp: str                  # auto-set to UTC ISO-8601 at construction
    title: Optional[str] = None
    author: Optional[str] = None
    date: Optional[str] = None
    description: Optional[str] = None    # set on chunk 0 only
    language: Optional[str] = None
    collection: Optional[str] = None
    section_title: Optional[str] = None
    embedding: Optional[List[float]] = None
```

`to_dict()` serialises the record and **omits** `None` optional fields — downstream consumers must check key presence, not value.

`chunk_id` format: `web_{sha256(final_url.encode())[:16]}_chunk_{index:04d}` — note 4-digit padding (vs 6-digit in doc-source).

`description` is attached to **chunk 0 only** — not repeated on subsequent chunks.

### WebCrawler

`WebCrawler` implements BFS traversal from a seed URL. See [`docs/crawling.md`](crawling.md) for the full reference.

Key behaviour:
- BFS — processes pages level by level up to `depth` hops from the seed
- Reuses `WebPipeline` for each page — passes `_fetch_result` to avoid double-fetching
- `same_domain_only=True` (default) — skips links outside the seed's domain
- Strips URL fragments before dedup (`/page#section` → `/page`)
- Silently skips pages that fail to fetch; continues the crawl

### VectorSource

`VectorSource` is a blue-data abstraction. `WebPipeline` accepts any implementation that provides:

- `upsert(collection, doc_id, doc)` — called on every embedded chunk
- `knn(index_name, query_vector, k)` — called on search
- `create_index(index_name, collection, dim)` — called by `ensure_index`

No adapter layer is needed. Pass any `VectorSource` instance directly as `vector_source=`.

---

## Deduplication

`chunk_id` is derived from the SHA-256 hash of `final_url` — the canonical URL after all redirects. Re-ingesting the same URL produces identical chunk IDs and overwrites existing entries in the vector store rather than duplicating them.

Unlike `blue-data-doc-source` (which hashes file bytes), web-source hashes the URL — identical content fetched from different URLs produces different chunk IDs.

---

## Reuse from blue-data-doc-source

These components are imported directly — not reimplemented:

| Component | Import |
|---|---|
| `SectionChunker`, `WindowChunker` | `blue_doc_source.source.document.chunkers` |
| `BasicEnricher` | `blue_doc_source.source.document.enrichment` |
| `OpenAIEmbeddingModel`, `DummyEmbeddingModel` | `blue_doc_source.source.document.embeddings` |
| `EmbeddingModel` ABC | `blue_doc_source.source.document.embeddings` |

This means chunking behaviour, enrichment output, and embedding models are identical across doc-source and web-source — chunks from both sources are comparable and can coexist in the same vector collection.

---

## Extension points

| To change... | Inject via... |
|---|---|
| Chunking strategy | `WebDataSource(chunk_size=..., overlap=...)` or `WebPipeline(chunker=...)` |
| Enrichment logic | `WebDataSource(enricher=MyEnricher())` |
| Embedding model | `WebDataSource(embedding_model=MyModel())` |
| Vector store backend | `WebDataSource(vector_source=MyVectorSource())` |
| Web search backend | `WebDataSource(search_provider=MySearchProvider())` |
| HTTP timeout | `WebDataSource(timeout=60.0)` |
| NL query model | `WebDataSource(nl_model="claude-opus-4-8")` |
