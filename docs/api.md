# REST API Reference

The REST API is a thin FastAPI adapter over `WebDataSource`. All business logic lives in the library — the API translates HTTP requests into `execute_query` calls and wraps the results.

**Base URL:** `http://localhost:8002`  
**Content-Type:** `application/json`

Start the server:

```bash
pip install -e lib/ -e platform/web-api/
BLUE_WEB_REDIS_HOST=localhost BLUE_WEB_EMBEDDING=openai OPENAI_API_KEY=sk-... \
  uvicorn web_api.main:app --host 0.0.0.0 --port 8002
```

See [Installation](installation.md) for environment variables and Docker instructions.

**Library-only operations (no REST route):** `WEB.FIND`, `WEB.FIND_THEN_SEARCH`, `WEB.GRAPH_NEIGHBORS`, `WEB.GRAPH_QUERY`. These require `RecordStore` and `GraphSource` components not wired by `WebDataSource.from_env()`. Use these ops directly through the Python library.

---

## `GET /web/health`

Health check. Returns `200` when the server is running regardless of backend state.

**Response**
```json
{"status": "ok"}
```

---

## `POST /web/ensure-index`

Create the vector index for a collection. Idempotent — safe to call on every deploy. Must be called before the first ingest when using a vector store.

**Request**
```json
{"collection": "news"}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `collection` | string | No | Index name; defaults to `BLUE_WEB_COLLECTION` env var or `"web"` |

**Response `200`**
```json
{"status": "ok", "collection": "news"}
```

---

## `POST /web/ingest`

Fetch, extract, chunk, embed, and upsert one or more web pages. Re-ingesting an unchanged URL is safe — URL-hash deduplication means the same URL always produces the same chunk IDs (upsert, not insert).

**Request**
```json
{"url": "https://example.com/article", "collection": "news"}
```
```json
{"urls": ["https://example.com/a", "https://example.com/b"], "collection": "news"}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string | One of `url`/`urls` | Single URL to ingest |
| `urls` | string[] | One of `url`/`urls` | Batch of URLs to ingest concurrently |
| `collection` | string | No | Target collection |

**Response `200`**
```json
{
  "chunk_count": 12,
  "pages_ingested": 1,
  "urls": ["https://example.com/article"],
  "collection": "news"
}
```

**Error `422`** — neither `url` nor `urls` provided.

---

## `POST /web/crawl`

BFS-crawl a site from a seed URL and ingest all reachable pages. Respects `robots.txt` and enforces per-domain rate limiting.

**Request**
```json
{
  "url": "https://docs.example.com/",
  "depth": 2,
  "max_pages": 50,
  "same_domain_only": true,
  "collection": "docs"
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | required | Seed URL |
| `depth` | int | `1` | Hops from seed to follow |
| `max_pages` | int | `10` | Hard cap on pages successfully fetched (unchanged pages skipped by the freshness store still count; failed fetches don't) |
| `same_domain_only` | bool | `true` | Restrict crawl to seed domain |
| `collection` | string | env default | Target collection |

**Response `200`**
```json
{
  "chunk_count": 47,
  "pages_ingested": 8,
  "urls": ["https://docs.example.com/", "https://docs.example.com/guide"],
  "collection": "docs"
}
```

---

## `POST /web/feed`

Fetch an RSS or Atom feed and ingest each article URL. Use `since` for incremental daily runs.

**Request**
```json
{
  "url": "https://feeds.npr.org/1001/rss.xml",
  "since": "2026-09-01T00:00:00Z",
  "max_entries": 20,
  "collection": "news"
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | required | Feed URL (RSS or Atom) |
| `since` | string | none | ISO 8601 cutoff; skip entries published before this |
| `max_entries` | int | none | Cap entries after date filtering |
| `collection` | string | env default | Target collection |

Requires `pip install 'blue-data-web-source[feed]'` on the server.

**Response `200`**
```json
{
  "chunk_count": 34,
  "articles_ingested": 5,
  "urls": ["https://text.npr.org/..."],
  "collection": "news"
}
```

---

## `POST /web/extract`

Fetch a URL and extract typed fields using a caller-supplied JSON Schema. Stateless — does not chunk or store. Requires `OPENAI_API_KEY` on the server.

**Request — single URL**
```json
{
  "url": "https://example.com/product",
  "schema": {
    "properties": {
      "title": {"type": "string"},
      "price": {"type": "number"},
      "summary": {"type": "string"}
    }
  }
}
```

**Request — multiple URLs**
```json
{
  "urls": ["https://example.com/a", "https://example.com/b"],
  "schema": {"properties": {"title": {"type": "string"}}}
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string | One of `url`/`urls` | Single page |
| `urls` | string[] | One of `url`/`urls` | Multiple pages |
| `schema` | object | Yes | JSON Schema describing the fields to extract |

**Response `200` — single URL:** extracted field dict  
**Response `200` — multiple URLs:** list of dicts, each with a `url` key  
**Error `422`** — neither `url` nor `urls` provided.  
**Error `400`** — extraction failed (e.g. no extractable content).

---

## `POST /web/search`

Semantic search over ingested pages. Requires Redis and an indexed collection.

**Request**
```json
{"query": "climate targets 2026", "k": 5, "collection": "news"}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | string | required | Natural-language search query |
| `k` | int | `5` | Number of results |
| `collection` | string | env default | Collection to search |

**Response `200`**
```json
{
  "results": [
    {
      "chunk_id": "web_a3f1..._chunk_0000",
      "url": "https://example.com/article",
      "domain": "example.com",
      "title": "Climate targets ...",
      "text": "...",
      "score": 0.94
    }
  ],
  "count": 5
}
```

**Error `400`** — no vector store configured.

---

## `POST /web/search-web`

Live web search via Tavily — fetch, ingest, and rank in one call. No prior ingest required. Requires `TAVILY_API_KEY`.

**Request**
```json
{"query": "2026 greenhouse gas targets", "k": 5, "top_k": 3, "collection": "research"}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | string | required | Search query |
| `k` | int | `5` | URLs to fetch from Tavily |
| `top_k` | int | `5` | Chunks to return after semantic ranking |
| `collection` | string | env default | Collection to store results |

**Response `200`** — same structure as `/web/search`.

**Error `400`** — no vector store configured.
