# Guide

## Quick start

Fetch a page, extract its text, and inspect the chunks — no API key required:

```python
from blue_data_web_source.source.web.pipeline import WebPipeline

pipeline = WebPipeline(chunk_size=500, overlap=100)
records = pipeline.ingest("https://text.npr.org/nx-s1-5884537")

for r in records:
    print(r["chunk_id"], r.get("section_title"), len(r["text"].split()), "words")
```

---

## Full pipeline

Wire up embeddings and a vector store for production use:

```python
from blue_data_web_source import WebDataSource
from blue_data_doc_source.source.document.embeddings import OpenAIEmbeddingModel
from blue_data_doc_source.source.document.enrichment import BasicEnricher
from blue_data.source.vector.redis.redis_vector_source import RedisVectorSource

source = WebDataSource(
    name="news",
    embedding_model=OpenAIEmbeddingModel(),
    vector_source=RedisVectorSource(name="news", host="localhost", port=6379),
    enricher=BasicEnricher(),
    chunk_size=500,
    overlap=100,
    default_collection="news",
)

# Must be called once before the first ingest
source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": "news"})
```

---

## Ingesting pages

### Single URL

```python
records = source.execute_query({
    "op": "WEB.INGEST",
    "url": "https://text.npr.org/nx-s1-5884537",
    "collection": "news",
})
print(f"{len(records)} chunks ingested")
```

### Multiple URLs

```python
records = source.execute_query({
    "op": "WEB.INGEST",
    "urls": [
        "https://text.npr.org/nx-s1-5884537",
        "https://text.npr.org/nx-s1-5881234",
    ],
    "collection": "news",
})
```

Returns a flat list of chunk dicts across all pages. Pages that yield no extractable body (login walls, pure navigation) contribute zero chunks.

---

## Crawling a site

BFS from a seed URL. By default stays within the same domain, respects `robots.txt`, and enforces a 1-second crawl delay:

```python
records = source.execute_query({
    "op": "WEB.CRAWL",
    "url": "https://docs.example.com/",
    "depth": 2,           # seed + one hop + two hops
    "max_pages": 50,
    "collection": "docs",
})
print(f"{len(set(r['url'] for r in records))} pages, {len(records)} chunks")
```

URL fragments are stripped before deduplication — `https://docs.example.com/page#section` and `https://docs.example.com/page` are treated as one URL.

---

## Searching

```python
results = source.execute_query({
    "op": "WEB.SEARCH",
    "query": "missile test South Pacific",
    "collection": "news",
    "k": 5,
})
for r in results:
    print(r["url"], r.get("section_title"))
    print("  ", r["text"][:120])
```

---

## Search-augmented ingestion

Query the live web, ingest the top results on the fly, and return the most relevant chunks:

```python
from blue_data_web_source.source.web.search_provider import TavilySearchProvider

source = WebDataSource(
    name="research",
    embedding_model=OpenAIEmbeddingModel(),
    vector_source=MemoryVectorSource(),
    search_provider=TavilySearchProvider(),   # reads TAVILY_API_KEY from env
)
source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": "research"})

results = source.execute_query({
    "op": "WEB.SEARCH_WEB",
    "query": "2026 greenhouse gas emission reduction targets",
    "k": 5,       # URLs to fetch from Tavily
    "top_k": 3,   # chunks to return after semantic ranking
    "collection": "research",
})
```

Requires `pip install tavily-python` and `TAVILY_API_KEY`.

---

## Natural language queries

```python
from blue_data_web_source import WebDataSource
from blue_data.source.vector.memory_vector_source import MemoryVectorSource

source = WebDataSource(
    name="news",
    embedding_model=OpenAIEmbeddingModel(),
    vector_source=MemoryVectorSource(),
    nl_model="claude-opus-4-8",
)
source.execute_query({"op": "WEB.ENSURE_INDEX"})

results = source.execute_query_nl("find articles about China's missile tests")
records = source.execute_query_nl("ingest https://text.npr.org/nx-s1-5884537 into the news collection")
pages   = source.execute_query_nl("crawl https://docs.example.com up to depth 2, max 20 pages")
```

Requires `pip install 'blue-data-web-source[nl]'` and `ANTHROPIC_API_KEY`.

---

## Structured field extraction

`WEB.EXTRACT` fetches a URL, strips boilerplate, and pulls typed fields defined by a JSON Schema or Pydantic model using `StructuredFieldExtractor` from `blue-data-doc-source`.

```python
schema = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "author":   {"type": "string"},
        "summary":  {"type": "string"},
    },
}

# Single URL → dict of extracted fields
fields = source.execute_query({
    "op": "WEB.EXTRACT",
    "url": "https://news.example.com/article",
    "schema": schema,
})
print(fields["headline"], fields.get("author"))

# Multiple URLs → list of dicts (each includes the source "url" key)
results = source.execute_query({
    "op": "WEB.EXTRACT",
    "urls": ["https://news.example.com/a1", "https://news.example.com/a2"],
    "schema": schema,
})
```

Requires `OPENAI_API_KEY` — `StructuredFieldExtractor` calls the OpenAI chat API.

---

## Deduplication

Chunk IDs are derived from `sha256(final_url)[:16]` — the URL after all redirects. The same URL always produces the same chunk IDs regardless of when ingestion runs.

```python
# Ingesting the same URL twice is safe — the second call is an upsert
source.execute_query({"op": "WEB.INGEST", "url": url, "collection": "news"})
source.execute_query({"op": "WEB.INGEST", "url": url, "collection": "news"})
# No duplicates; vector store contains one copy
```

`WEB.CRAWL` deduplicates discovered URLs within the crawl session using the same URL-normalisation (fragment stripping).

---

## DataRegistry integration

Register `WebDataSource` with blue-data's `DataRegistry` so it participates in cross-source search and enrichment:

```python
from blue_data import DataRegistry

registry = DataRegistry()
registry.register(source)

# Stats and metadata enrichment
await registry.sync()

# Cross-source search
results = registry.search("climate change targets")
```

---

## Chunking configuration

The default chunker is `SectionChunker` from `blue-data-doc-source`. It detects markdown, numbered, ALL-CAPS, and underlined section headers and chunks within sections, falling back to window chunking when no structure is found.

To use a different chunker:

```python
from blue_data_doc_source.source.document.chunkers import WindowChunker

source = WebDataSource(
    name="news",
    chunker=WindowChunker(chunk_size=300, overlap=50),
    ...
)
```

---

## Embeddings

The default is no embedding model (chunks are returned without vectors). Pass any `EmbeddingModel` from `blue-data-doc-source`:

```python
from blue_data_doc_source.source.document.embeddings import OpenAIEmbeddingModel, DummyEmbeddingModel

# Production
model = OpenAIEmbeddingModel()                          # text-embedding-3-small, dim=1536
model = OpenAIEmbeddingModel(model="text-embedding-3-large")  # dim=3072

# Tests / no API key
model = DummyEmbeddingModel(dim=8)                      # deterministic, not real similarity
```

---

## Chunk record schema

Each returned chunk is a dict with:

| Key | Always present | Notes |
|-----|---------------|-------|
| `chunk_id` | Yes | `{page_id}_chunk_{index:04d}` |
| `url` | Yes | Canonical URL (after redirects) |
| `domain` | Yes | `netloc` of the canonical URL |
| `chunk_index` | Yes | 0-based position within the page |
| `text` | Yes | Chunk text |
| `fetch_timestamp` | Yes | ISO 8601 UTC |
| `title` | When found | Page title from metadata |
| `author` | When found | Trafilatura or JSON-LD/meta fallback |
| `date` | When found | Trafilatura or JSON-LD fallback |
| `description` | Chunk 0 only | Trafilatura or OpenGraph fallback |
| `section_title` | When detected | Section heading from `SectionChunker` |
| `schema_type` | When present | Schema.org `@type` from JSON-LD (e.g. `NewsArticle`) |
| `keywords` | When present | From JSON-LD or `<meta name="keywords">` |
| `publisher` | When present | From JSON-LD `publisher.name` or `og:site_name` |
| `canonical_url` | When present | From JSON-LD, `<link rel="canonical">`, or `og:url` |
| `embedding` | When model set | Float vector |
