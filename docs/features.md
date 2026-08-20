# Features

## Web ingestion pipeline

Fetch, extract, chunk, enrich, embed, and upsert in a single `WEB.INGEST` call. Handles redirect following, non-2xx errors, and pages with no extractable content automatically — callers never see partial or empty chunks in the vector store.

```python
from blue_data_web_source import WebDataSource
from blue_data_doc_source.source.document.embeddings import OpenAIEmbeddingModel
from blue_data.source.vector.memory_vector_source import MemoryVectorSource

source = WebDataSource(
    name="news",
    embedding_model=OpenAIEmbeddingModel(),
    vector_source=MemoryVectorSource(),
    default_collection="web",
)

source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": "web"})

records = source.execute_query({
    "op": "WEB.INGEST",
    "url": "https://example.com/article",
    "collection": "web",
})

for r in records:
    print(r["chunk_id"], r["domain"], r["section_title"], r["text"][:80])
```

---

## Boilerplate-aware extraction

Uses [trafilatura](https://trafilatura.readthedocs.io) to identify and extract the main content region of a page. Works well on article and blog-style pages. Pages that yield no extractable body — SPA shells, login walls, JS-redirect pages — return an empty result rather than storing noise.

After trafilatura runs, a post-processing pass strips inline CMS navigation fragments that appear within article text:

| Stripped prefix | Example |
|---|---|
| `Related Story:` | `Related Story: How Coal Plants Are Being Repurposed` |
| `Read more:` | `Read more: The Economics of Green Hydrogen` |
| `See also:` | `See also: Offshore Wind Financing Trends` |
| `Also read:` | `Also read: Battery Costs Hit Record Low` |
| `Advertisement` | `Advertisement` |
| `Story continues` | `Story continues below advertisement` |

```python
from blue_data_web_source.source.web.extractor import WebExtractor

extractor = WebExtractor()
result = extractor.extract(html, url="https://news.example.com/article")

if result is None:
    print("No extractable content — pipeline returns []")
else:
    print(result.title)       # "Energy Policy Update"
    print(result.author)      # "Jane Smith"
    print(result.date)        # "2026-03-15"
    print(result.language)    # "en"
    print(result.text[:200])  # cleaned article body
```

---

## Web-provenance records

Every chunk carries `url`, `domain`, and `fetch_timestamp` on every record. When present in page metadata, `title`, `author`, `date`, and `language` are also extracted and attached. Four structured-metadata fields are additionally extracted from JSON-LD and OpenGraph on every page: `schema_type`, `keywords`, `publisher`, and `canonical_url`.

```python
records = source.execute_query({"op": "WEB.INGEST", "url": url, "collection": "web"})

for r in records:
    print(r["url"])               # "https://news.example.com/article"
    print(r["domain"])            # "news.example.com"
    print(r["fetch_timestamp"])   # "2026-08-12T10:30:00+00:00"
    print(r.get("title"))         # "Energy Policy Update" (if found)
    print(r.get("author"))        # "Jane Smith" (trafilatura or JSON-LD fallback)
    print(r.get("date"))          # "2026-03-15" (trafilatura or JSON-LD fallback)
    print(r.get("section_title")) # "National Commitments" (if detected)
    print(r.get("schema_type"))   # "NewsArticle" (from JSON-LD, if present)
    print(r.get("keywords"))      # ["climate", "energy"] (from JSON-LD or meta, if present)
    print(r.get("publisher"))     # "BBC News" (from JSON-LD or og:site_name, if present)
    print(r.get("canonical_url")) # "https://news.example.com/article" (if present)
```

`WebChunkRecord.to_dict()` omits optional fields when `None` — check key presence rather than value.

`description` from page metadata is attached to **chunk 0 only** — not repeated on subsequent chunks.

---

## URL-hash deduplication

`chunk_id` is derived from the SHA-256 hash of the **final URL after redirects**. Re-ingesting the same URL — including UTM-tracked variants that redirect to the same canonical URL — produces identical chunk IDs and overwrites existing entries in the vector store rather than duplicating them.

```python
# First ingest
records_a = source.execute_query({"op": "WEB.INGEST", "url": "https://example.com/article"})

# Re-ingest same URL — safe, produces identical chunk IDs
records_b = source.execute_query({"op": "WEB.INGEST", "url": "https://example.com/article"})

assert records_a[0]["chunk_id"] == records_b[0]["chunk_id"]  # upsert, not duplicate

# UTM-tracked URL that redirects to the same canonical URL
records_c = source.execute_query({
    "op": "WEB.INGEST",
    "url": "https://example.com/article?utm_source=newsletter",
})
assert records_a[0]["chunk_id"] == records_c[0]["chunk_id"]  # same final_url → same ID
```

`chunk_id` format: `web_{sha256(final_url)[:16]}_chunk_{index:04d}`

---

## Section-aware chunking

Reuses `SectionChunker` from `blue-data-doc-source`. Detects document structure and keeps each chunk within a single section, propagating the heading as `section_title` on every chunk. Falls back to sliding-window chunking when no headers are found.

Recognised header patterns:
- Markdown: `# Title`, `## Subtitle`
- Numbered: `1.`, `1.2`, `1.2.3`
- ALL-CAPS lines (≥ 3 words)
- Underlined: `Title\n======`

```python
from blue_data_web_source import WebDataSource
from blue_data_doc_source.source.document.enrichment import BasicEnricher

source = WebDataSource(
    name="docs",
    chunk_size=800,
    overlap=100,
    enricher=BasicEnricher(),
)

records = source.execute_query({"op": "WEB.INGEST", "url": url})

for r in records:
    section = r.get("section_title", "(no section)")
    print(f"§ {section}  —  {r['text'][:80]}")
```

Both `SectionChunker` and `WindowChunker` operate on **word count**, not character count.

---

## Regex enrichment

`BasicEnricher` (reused from `blue-data-doc-source`) extracts structured metadata from chunk text using regular expressions — no ML dependency, no API key, zero latency.

Extracted entity types:
- **DATE** — `January 2026`, `2026-03-15`, `15/03/26`
- **EMAIL** — `user@example.com`
- **PERCENT** — `12.5%`, `50 percent`
- **MONEY** — `$1,500`, `€200`, `£50`
- **PROPER_NOUN** — capitalised multi-word phrases

```python
from blue_data_doc_source.source.document.enrichment import BasicEnricher

enricher = BasicEnricher()
result = enricher.enrich(
    "EU pledged a 65% reduction by 2030. Contact policy@un.org for details."
)

print(result["entities"])
# [{"text": "65%", "type": "PERCENT"},
#  {"text": "2030", "type": "DATE"},
#  {"text": "policy@un.org", "type": "EMAIL"},
#  {"text": "EU", "type": "PROPER_NOUN"}]

print(result["semantic_hints"])
# {"word_count": 12, "keywords": ["reduction", "pledged", "details"], ...}
```

Pass `enricher=BasicEnricher()` to `WebDataSource` to attach enrichment to every ingested chunk.

---

## Pluggable embeddings

Ships with `OpenAIEmbeddingModel` and `DummyEmbeddingModel` from `blue-data-doc-source`. Any `EmbeddingModel` subclass works — swap without changing the pipeline.

```python
from blue_data_doc_source.source.document.embeddings import OpenAIEmbeddingModel, DummyEmbeddingModel

# Production — calls OpenAI text-embedding-3-small (1536-dim)
model = OpenAIEmbeddingModel(model="text-embedding-3-small")
print(model.dim)  # 1536

# Tests — SHA-256 hash → fixed-dim float vector (not real similarity)
model = DummyEmbeddingModel()
print(model.dim)  # 8
```

Bring your own model by subclassing `EmbeddingModel`:

```python
from blue_data_doc_source.source.document.embeddings import EmbeddingModel

class MyModel(EmbeddingModel):
    @property
    def dim(self) -> int:
        return 768

    def embed_text(self, text: str) -> list[float]:
        return my_inference_server.embed(text)
```

Upsert only fires when both `embedding_model` and `vector_source` are provided.

---

## Blue-data native

Extends `DataSource` and delegates storage and KNN search entirely to Blue's `VectorSource` — no duplicate vector logic. Compatible with `RedisVectorSource`, `MemoryVectorSource`, and any future backend.

```python
from blue_data.source.vector.memory_vector_source import MemoryVectorSource
from blue_data.source.vector.redis.redis_vector_source import RedisVectorSource

# Dev / test — in-process, no external services
source = WebDataSource(
    name="web",
    embedding_model=OpenAIEmbeddingModel(),
    vector_source=MemoryVectorSource(),
)

# Production — persistent Redis Stack
source = WebDataSource(
    name="web",
    embedding_model=OpenAIEmbeddingModel(),
    vector_source=RedisVectorSource(name="redis", host="localhost", port=6379),
)
```

`WebDataSource` is registered as a blue-data `DataSource` — web content sits alongside SQL, document, and graph sources in the same data layer.

---

## Concurrent ingestion

`WEB.INGEST` with a `urls` list and `WEB.CRAWL` both run concurrently using a `ThreadPoolExecutor`. The `max_workers` parameter (default 10, set via `BLUE_WEB_MAX_WORKERS` env var) controls parallelism. The crawler runs BFS level-by-level — all pages at depth N are fetched concurrently before moving to depth N+1, with per-domain politeness locking to avoid hammering a single host.

```python
# Ingest 20 articles concurrently (up to 10 at a time by default)
records = source.execute_query({
    "op": "WEB.INGEST",
    "urls": article_urls,   # list of up to 20 URLs
    "collection": "news",
})
```

Pass `max_workers=N` to `WebDataSource` or `WebPipeline` to tune concurrency.

---

## Structured field extraction

`WEB.EXTRACT` fetches a URL, extracts its text, and uses `StructuredFieldExtractor` (from `blue-data-doc-source`) to pull typed fields defined by a JSON Schema or Pydantic model — no manual parsing required.

```python
schema = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "author":   {"type": "string"},
        "summary":  {"type": "string"},
    },
}

# Single URL → dict
fields = source.execute_query({
    "op": "WEB.EXTRACT",
    "url": "https://news.example.com/article",
    "schema": schema,
})
print(fields["headline"], fields["author"])

# Multiple URLs → list of dicts (each includes the source "url" key)
results = source.execute_query({
    "op": "WEB.EXTRACT",
    "urls": ["https://news.example.com/a1", "https://news.example.com/a2"],
    "schema": schema,
})
```

`schema` can be a JSON Schema dict, a Pydantic model class, or a TypedDict. Requires `blue-data-doc-source` with OpenAI credentials set.

---

## JSON-LD / OpenGraph metadata

`MetadataExtractor` runs on every page ingest (no API key, no ML). It parses JSON-LD `<script>` blocks, `<meta property="og:...">` tags, `<meta name="...">` tags, and `<link rel="canonical">` in a single pass over the raw HTML.

Four fields are added to every chunk record:

| Field | Source priority |
|---|---|
| `schema_type` | JSON-LD `@type` (e.g. `NewsArticle`, `BlogPosting`) |
| `keywords` | JSON-LD `keywords` → `<meta name="keywords">` |
| `publisher` | JSON-LD `publisher.name` → `og:site_name` |
| `canonical_url` | JSON-LD `url` → `<link rel="canonical">` → `og:url` |

`author`, `date`, and `description` are also filled in from structured metadata when trafilatura returns `None` — so pages that trafilatura can extract text from but not metadata (e.g. minimal `<head>` sections) still get enriched records.

---

## Batch ingestion

`WEB.INGEST` accepts a single `url` or a `urls` list — ingests multiple pages in one call and returns a flat list of all chunks across all URLs.

```python
# Single URL
records = source.execute_query({
    "op": "WEB.INGEST",
    "url": "https://example.com/article",
    "collection": "web",
})

# Batch — equivalent to N individual calls, cleaner for known URL sets
records = source.execute_query({
    "op": "WEB.INGEST",
    "urls": [
        "https://example.com/article-1",
        "https://example.com/article-2",
        "https://example.com/article-3",
    ],
    "collection": "web",
})

# Group returned chunks by source URL
from collections import defaultdict
by_url = defaultdict(list)
for r in records:
    by_url[r["url"]].append(r)

for url, chunks in by_url.items():
    print(f"{url}: {len(chunks)} chunks")
```

---

## Crawling

`WEB.CRAWL` takes a seed URL and recursively fetches linked pages via BFS. Configurable `depth` and `max_pages` limit. Stays within the seed domain by default. Strips URL fragments before deduplication, skips failed pages, and continues without interruption.

```python
# Crawl a documentation site up to 2 hops from the seed
records = source.execute_query({
    "op": "WEB.CRAWL",
    "url": "https://docs.example.com/",
    "depth": 2,              # hops from seed (0 = seed only)
    "max_pages": 50,         # hard cap regardless of depth
    "same_domain_only": True, # default — stays within docs.example.com
    "collection": "docs",
})

ingested_urls = sorted({r["url"] for r in records})
print(f"Crawled {len(ingested_urls)} pages, {len(records)} total chunks")
for url in ingested_urls:
    print(f"  {url}")
```

Crawler behaviour:
- BFS — processes pages level by level up to `depth` hops from the seed
- `same_domain_only=True` (default) — only follows links within the seed's domain
- Strips URL fragments before deduplication (`/page#section` → `/page`)
- Skips pages that fail to fetch (404, timeout, connection error) and continues
- Reuses the fetched HTML for ingestion — no double fetch per page

---

## RSS/Atom feed subscription

`WEB.INGEST_FEED` fetches an RSS or Atom feed, parses all entries, and ingests each article URL in one call. Entries are filtered by published date before any HTTP fetch, so stale articles are never re-downloaded.

```python
from datetime import datetime, timezone

# Ingest everything published after a cutoff — skips older entries before fetching
results = source.execute_query({
    "op": "WEB.INGEST_FEED",
    "url": "https://example.com/feed.xml",
    "since": "2026-08-10T00:00:00Z",  # ISO 8601; Z and +00:00 both accepted
    "max_entries": 20,                 # cap after date filtering
    "collection": "news",
})

print(f"Ingested {len(results)} chunks from feed")
```

Feed behaviour:
- Supports both RSS 2.0 (`<pubDate>`) and Atom (`<published>` / `<updated>`)
- `since` filters entries by published date **before** any HTTP fetch — entries without a date always pass through
- `max_entries` caps the number of articles processed after date filtering
- Articles that fail to fetch or yield no extractable content are skipped silently — the pipeline continues
- Requires `pip install 'blue-data-web-source[feed]'` for `feedparser`

---

## Freshness tracking

`FileFreshnessStore` tracks the content hash and last-ingest time for every URL. Skip re-ingesting pages that haven't changed; only download and re-embed pages that have been updated.

```python
from blue_data_web_source.source.web.freshness import FileFreshnessStore

store = FileFreshnessStore(".freshness_cache")

source = WebDataSource(
    name="news",
    embedding_model=OpenAIEmbeddingModel(),
    vector_source=vector_store,
    freshness_store=store,
)

# First run — fetches and embeds all pages
results = source.execute_query({"op": "WEB.INGEST", "url": "https://example.com/article"})

# Second run — page hasn't changed: skip fetch, return []
results = source.execute_query({"op": "WEB.INGEST", "url": "https://example.com/article"})
assert results == []  # no re-embedding, no vector store write

# Compose with WEB.INGEST_FEED for incremental daily updates
results = source.execute_query({
    "op": "WEB.INGEST_FEED",
    "url": "https://example.com/feed.xml",
    "since": "2026-08-17T00:00:00Z",
})
# Only new or changed articles are fetched and embedded
```

Freshness check uses a SHA-256 hash of the raw HTTP response body — byte-level change detection, not timestamp-based. The cache file is a plain JSON file, portable and inspectable.

When a `freshness_store` is set, cross-URL content deduplication is also active: if two different URLs yield identical extracted text, the second ingest is skipped automatically — no re-embedding, no duplicate chunks in the vector store.

---

## Search-augmented ingestion

`WEB.SEARCH_WEB` takes a natural language query, calls a web search API (Tavily by default), fetches and ingests the top-k result pages on the fly, and returns semantically ranked chunks — no pre-ingestion required. The search backend is pluggable via the `WebSearchProvider` ABC.

```python
from blue_data_web_source.source.web.search_provider import TavilySearchProvider

source = WebDataSource(
    name="live-search",
    embedding_model=OpenAIEmbeddingModel(),
    vector_source=MemoryVectorSource(),
    search_provider=TavilySearchProvider(),  # reads TAVILY_API_KEY from env
)
source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": "web"})

results = source.execute_query({
    "op": "WEB.SEARCH_WEB",
    "query": "latest solid-state battery energy density 2026",
    "k": 5,      # URLs to fetch from the search API
    "collection": "web",
})

for r in results:
    print(r["url"], r["score"], r["text"][:120])
```

Plug in any search backend by subclassing `WebSearchProvider`:

```python
from blue_data_web_source.source.web.search_provider import WebSearchProvider

class MySearchProvider(WebSearchProvider):
    def search(self, query: str, k: int) -> list[str]:
        return my_search_api.query(query, top_k=k)
```

---

## Natural language interface

`execute_query_nl` accepts plain-English instructions and dispatches the corresponding `WEB.*` op using Claude tool use (Anthropic). Supports ingest, search, and index operations. Requires `pip install 'blue-data-web-source[nl]'` and `ANTHROPIC_API_KEY`.

```python
source = WebDataSource(
    name="web",
    embedding_model=OpenAIEmbeddingModel(),
    vector_source=vector_store,
    nl_model="claude-opus-4-8",  # default
)

# NLQueryParser maps plain English to WEB.SEARCH, WEB.INGEST, WEB.ENSURE_INDEX
results = source.execute_query_nl("find articles about offshore wind energy")
records = source.execute_query_nl("ingest https://example.com/report")
source.execute_query_nl("ensure the index exists for collection news")
```

The NL interface uses Claude tool use with `tool_choice={"type": "any"}` — a tool call is always returned, never free text. Six tools are exposed: `ingest_url`, `search`, `ensure_index`, `crawl`, `search_web`, `ingest_feed`.

---

## Simple op interface

Six operations — consistent with Blue's query model:

| Op | Required | Optional | Returns |
|---|---|---|---|
| `WEB.ENSURE_INDEX` | — | `collection` | `None` |
| `WEB.INGEST` | `url` or `urls` | `collection` | `list[dict]` |
| `WEB.EXTRACT` | (`url` or `urls`) + `schema` | — | `dict` or `list[dict]` |
| `WEB.CRAWL` | `url` | `depth`(1), `max_pages`(10), `same_domain_only`(True), `collection` | `list[dict]` |
| `WEB.INGEST_FEED` | `url` | `since` (ISO 8601), `max_entries`, `collection` | `list[dict]` |
| `WEB.SEARCH` | `query` | `k`(5), `collection` | `list[dict]` |
| `WEB.SEARCH_WEB` | `query` | `k`(5), `top_k`(=k), `collection` | `list[dict]` |

All ops are dispatched via `WebDataSource.execute_query(op_dict)`. Natural language dispatch is available via `execute_query_nl(text, collection)`.
