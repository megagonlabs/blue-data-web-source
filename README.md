# blue-data-web-source

Turn any URL into searchable chunks in a vector store.

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
from blue_web_source.source.web.pipeline import WebPipeline

pipeline = WebPipeline(chunk_size=500, overlap=100)
records = pipeline.ingest("https://en.wikipedia.org/wiki/Python_(programming_language)")

for r in records:
    print(r["chunk_id"], r["domain"], r["text"][:80])
```

## End-to-end pipeline

Full pipeline: fetch → extract → chunk → enrich → embed → upsert to Redis → search.

`blue-data-web-source` depends on both `blue-data` and `blue-data-doc-source`. Pass a `VectorSource` instance (e.g. `RedisVectorSource`) from blue-data as the `vector_source`.

```python
from blue_web_source import WebDataSource
from blue_doc_source.source.document.embeddings import OpenAIEmbeddingModel
from blue_doc_source.source.document.enrichment import BasicEnricher
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

# Ingest a URL
records = source.execute_query({
    "op": "WEB.INGEST",
    "url": "https://example.com/article",
    "collection": "web",
})
print(f"Ingested {len(records)} chunks")

# Search
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

**`WebDataSource`** is the top-level public entry point. It extends blue-data's `DataSource` abstraction and dispatches on `op` codes: `WEB.INGEST`, `WEB.SEARCH`, `WEB.ENSURE_INDEX`. It owns a single `WebPipeline` instance reused across calls.

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

## Current scope

This is a Phase 1 implementation. It covers single-URL ingestion and vector search. The following are **not yet built**:

| Feature | Status |
|---|---|
| Single URL fetch → extract → chunk → embed → search | ✅ Done |
| URL-level deduplication (same URL → same chunk IDs) | ✅ Done |
| Content-level deduplication (same text from different URLs) | Not built |
| Source quality / trust scoring per domain | Not built |
| Sitemap crawling / whole-site ingestion | Not built |
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

A self-contained demo that walks through the full pipeline — no network or external services required. HTTP is intercepted locally and served from fixture files.

```bash
source .venv/bin/activate
python examples/demo.py
```

The demo:
1. Sets up `WebDataSource` with `MemoryVectorSource` and `DummyEmbeddingModel`
2. Creates a vector index
3. Ingests two articles (a synthetic climate report and the saved NPR fixture)
4. Prints a sample chunk from each article
5. Runs two search queries and shows ranked results with metadata

Note: `DummyEmbeddingModel` produces hash-based vectors with no semantic meaning, so search ranking in the demo is not semantically meaningful. Replace it with `OpenAIEmbeddingModel` (or any `EmbeddingModel` implementation) for real similarity search.
