# blue-data-web-source

Web page ingestion, extraction, and retrieval pipeline for Blue Data Intelligence. Fetches URLs, strips boilerplate via trafilatura, chunks the main content, optionally enriches with entity extraction, embeds via OpenAI, and upserts to a Redis vector store.

Built as an acquisition and normalisation layer on top of `blue-data-doc-source` — it reuses the same chunkers, embedding models, and enrichers, adding HTTP fetch and HTML extraction as a front-end stage.

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

### Embeddings

| Class | Use case |
|---|---|
| `OpenAIEmbeddingModel` | Production; calls `text-embedding-3-small` by default |
| `DummyEmbeddingModel` | Tests; SHA-256 hash → fixed-dim float vector (not real similarity) |

Both are imported from `blue-data-doc-source`.

### Deduplication

`chunk_id` is derived from a SHA-256 hash of the final URL. Re-ingesting the same URL produces the same chunk IDs, so vector store upsert overwrites rather than duplicates.

## Running tests

```bash
# Unit tests (no external services or network required)
.venv/bin/pytest lib/tests/unit/ -v

# Single file
.venv/bin/pytest lib/tests/unit/test_pipeline.py -v

# Integration tests (no network — HTTP mocked via pytest-httpx, trafilatura runs for real)
.venv/bin/pytest lib/tests/integration/ -v -m integration

# All tests
.venv/bin/pytest lib/tests/ -v
```
