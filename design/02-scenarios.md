# blue-data-web-source — Scenarios

## Scenario 1: Inspecting Extracted Chunks from a URL

A developer wants to see how a news article will be chunked — what text trafilatura extracts, how sections are detected, and what provenance fields each chunk carries — before wiring up embeddings or a vector store.

**Example:**

```python
from blue_data_web_source.source.web.pipeline import WebPipeline

pipeline = WebPipeline(chunk_size=500, overlap=100)
records = pipeline.ingest("https://text.npr.org/nx-s1-5884537")

for r in records:
    print(r["chunk_id"], r.get("section_title"), len(r["text"].split()), "words")
    print("  url:", r["url"], "domain:", r["domain"])
```

No API key, no vector store, no network beyond the single HTTP fetch. Each record carries `url`, `domain`, `fetch_timestamp`, and — when found in page metadata — `title`, `author`, `date`.

---

## Scenario 2: Full Pipeline — Ingest and Search

A developer builds a news monitoring system. Pages are fetched, extracted, chunked, embedded, and upserted to Redis. Search returns semantically ranked chunks with full provenance.

**Example:**

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

source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": "news"})

for url in [
    "https://text.npr.org/nx-s1-5884537",
    "https://text.npr.org/nx-s1-5881234",
]:
    records = source.execute_query({"op": "WEB.INGEST", "url": url, "collection": "news"})
    print(f"{url}: {len(records)} chunks")

results = source.execute_query({
    "op": "WEB.SEARCH",
    "query": "missile test South Pacific",
    "collection": "news",
    "k": 5,
})
for r in results:
    print(r["url"], r.get("section_title"), r["text"][:100])
```

---

## Scenario 3: Batch Ingestion

A developer ingests a list of URLs in a single call. All pages are processed through the full pipeline and their chunks are returned in a flat list.

**Example:**

```python
records = source.execute_query({
    "op": "WEB.INGEST",
    "urls": [
        "https://example.com/article-1",
        "https://example.com/article-2",
        "https://example.com/article-3",
    ],
    "collection": "news",
})
print(f"Total chunks across all pages: {len(records)}")
```

Pages that yield no extractable content (login walls, pure navigation) contribute zero chunks. The list is flat — no per-URL grouping.

---

## Scenario 4: Re-ingestion and Deduplication

A news pipeline re-ingests the same set of URLs on a schedule. Unchanged pages produce the same chunk IDs and are upserted in place — no duplicates accumulate in the vector store.

**Example:**

```python
# First ingest
records = source.execute_query({"op": "WEB.INGEST", "url": url, "collection": "news"})
first_ids = {r["chunk_id"] for r in records}

# Second ingest of same URL
records = source.execute_query({"op": "WEB.INGEST", "url": url, "collection": "news"})
second_ids = {r["chunk_id"] for r in records}

assert first_ids == second_ids  # same URL → same chunk IDs → upsert, not insert
```

Because `chunk_id` is derived from `sha256(final_url)[:16]`, the same URL always produces the same IDs regardless of when ingestion runs.

---

## Scenario 5: Crawling a Documentation Site

A developer needs to ingest all pages of a documentation site for semantic search. Rather than managing a URL list, they use BFS crawl from the root — the crawler follows links up to a configured depth, stays within the same domain, and skips failed pages automatically.

**Example:**

```python
records = source.execute_query({
    "op": "WEB.CRAWL",
    "url": "https://docs.example.com/",
    "depth": 2,           # seed + links from seed + links from those
    "max_pages": 50,      # hard cap regardless of depth
    "same_domain_only": True,  # default — stay within docs.example.com
    "collection": "docs",
})
print(f"Crawled {len(set(r['url'] for r in records))} pages, {len(records)} total chunks")
```

The crawler strips URL fragments before deduplication (`/page#section` and `/page` are treated as the same URL) and reuses each fetched page for ingestion without a second HTTP request.

---

## Scenario 6: Search-Augmented Ingestion

A research assistant needs to answer a question using current web content. Rather than maintaining a pre-built index, it queries the live web, ingests the top results on the fly, and returns the most relevant chunks — all in one call.

**Example:**

```python
from blue_data_web_source import WebDataSource
from blue_data_web_source.source.web.search_provider import TavilySearchProvider

source = WebDataSource(
    name="research",
    embedding_model=OpenAIEmbeddingModel(),
    vector_source=MemoryVectorSource(),
    search_provider=TavilySearchProvider(),  # reads TAVILY_API_KEY from env
)
source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": "research"})

results = source.execute_query({
    "op": "WEB.SEARCH_WEB",
    "query": "2026 greenhouse gas emission reduction targets",
    "k": 5,       # pages to fetch from Tavily
    "top_k": 3,   # chunks to return after semantic ranking
    "collection": "research",
})
for r in results:
    print(r["url"], r["text"][:120])
```

Re-running the same query does not re-fetch already-ingested URLs — URL-hash deduplication prevents double storage.

---

## Scenario 7: Natural Language Interface

An application lets users interact with the web ingestion system in plain English. The NL layer uses Claude tool use to parse the instruction into the right `WEB.*` op and parameters.

**Example:**

```python
source = WebDataSource(
    name="news",
    embedding_model=OpenAIEmbeddingModel(),
    vector_source=MemoryVectorSource(),
    nl_model="claude-opus-4-8",   # default
)
source.execute_query({"op": "WEB.ENSURE_INDEX"})

# Claude figures out the op and parameters from natural language
results = source.execute_query_nl("find articles about China's missile tests")
records = source.execute_query_nl("ingest https://text.npr.org/nx-s1-5884537 into news collection")
pages   = source.execute_query_nl("crawl https://docs.example.com up to depth 2")
```

Requires `pip install 'blue-data-web-source[nl]'` and `ANTHROPIC_API_KEY`.

---

## Scenario 8: MCP Access from Claude Desktop

A team exposes the web ingestion pipeline as an MCP server. Claude Desktop can ingest URLs, crawl sites, and search content without any Python code.

**Example Claude Desktop config:**

```json
{
  "mcpServers": {
    "web-source": {
      "command": "python",
      "args": ["/path/to/blue-data-web-source/platform/web-mcp/src/web_mcp/server.py"],
      "env": {
        "OPENAI_API_KEY": "sk-...",
        "ANTHROPIC_API_KEY": "sk-ant-...",
        "BLUE_WEB_REDIS_HOST": "localhost",
        "BLUE_WEB_REDIS_PORT": "6379"
      }
    }
  }
}
```

Claude can then:
- Ingest a URL: `"ingest https://example.com/article into the news collection"`
- Crawl a site: `"crawl https://docs.example.com up to depth 2, max 20 pages"`
- Search: `"find everything about payment obligations in the news collection"`
- Search the live web: `"search the web for 2026 climate targets and return the most relevant passages"`

---

## Scenario 9: Structured Field Extraction

A developer wants to extract specific typed fields from a list of news articles — headline, author, and a one-sentence summary — without writing a custom HTML parser.

**Example:**

```python
schema = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "author":   {"type": "string"},
        "summary":  {"type": "string"},
    },
}

# Extract from a single URL — returns a dict
fields = source.execute_query({
    "op": "WEB.EXTRACT",
    "url": "https://text.npr.org/nx-s1-5884537",
    "schema": schema,
})
print(fields["headline"], fields.get("author"))

# Extract from multiple URLs — returns a list of dicts, each with a "url" key
results = source.execute_query({
    "op": "WEB.EXTRACT",
    "urls": [
        "https://text.npr.org/nx-s1-5884537",
        "https://text.npr.org/nx-s1-5881234",
    ],
    "schema": schema,
})
for r in results:
    print(r["url"], r.get("headline"))
```

Requires `OPENAI_API_KEY`. The `schema` field accepts a JSON Schema dict, a Pydantic model class, or a TypedDict.

---

## Scenario 10: Concurrent Ingestion of a Large URL Set

A news monitoring pipeline needs to ingest 50 articles as quickly as possible. Serial ingestion at ~2 seconds per page takes 100 seconds; concurrent ingestion with 10 workers completes in roughly 10–15 seconds.

**Example:**

```python
from blue_data_web_source import WebDataSource
from blue_data_doc_source.source.document.embeddings import OpenAIEmbeddingModel

source = WebDataSource(
    name="news",
    embedding_model=OpenAIEmbeddingModel(),
    vector_source=redis_source,
    max_workers=10,             # up to 10 concurrent HTTP fetches
    default_collection="news",
)
source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": "news"})

# All 50 URLs ingested concurrently — returns flat chunk list
records = source.execute_query({
    "op": "WEB.INGEST",
    "urls": article_urls,       # list of 50 URLs
    "collection": "news",
})
print(f"Ingested {len(set(r['url'] for r in records))} pages, {len(records)} total chunks")
```

`max_workers` is also respected by `WEB.CRAWL` — each BFS level is fetched concurrently. Per-domain locks prevent more than one simultaneous request to the same host.
