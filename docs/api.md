# REST API Reference

The REST API is a thin FastAPI adapter over `WebDataSource`. All business logic lives in the library — the API translates HTTP requests into `execute_query` calls and wraps the results.

**Base URL:** `http://127.0.0.1:8002`  
**Content-Type:** `application/json` (required on state-changing routes — see below)

Start the server:

```bash
pip install -e lib/ -e platform/web-api/
BLUE_WEB_REDIS_HOST=localhost BLUE_WEB_EMBEDDING=openai OPENAI_API_KEY=sk-... \
BLUE_WEB_API_KEY=choose-a-secret \
  blue-data-web-api          # binds 127.0.0.1:8002 by default
```

See [Installation](installation.md) for environment variables and Docker instructions.

## Security & binding

The service uses the shared `blue_data.server` policy. By default it binds
**loopback only** and every data route requires an API key.

| Variable | Default | Effect |
|---|---|---|
| `BLUE_WEB_API_HOST` | `127.0.0.1` | Bind host — loopback only unless overridden |
| `BLUE_WEB_API_PORT` | `8002` | Bind port (family port map: registry 8000, doc 8001, web 8002) |
| `BLUE_WEB_API_KEY` | _(unset)_ | Required on every data route; send as `X-API-Key: <key>` or `Authorization: Bearer <key>`. With no key configured, data routes return **401** |
| `BLUE_WEB_API_AUTH_DISABLED` | _(unset)_ | Set truthy (`1`/`true`/`yes`) to run with no API key (local/dev only) |

- **Authentication** — send the key on every data route (everything except
  `GET /web/health` and the docs routes `/docs`, `/redoc`, `/openapi.json`):

  ```bash
  curl -X POST http://127.0.0.1:8002/web/search \
    -H "X-API-Key: $BLUE_WEB_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"query": "climate targets 2026", "k": 5}'
  ```

  All examples below omit the headers for brevity; send `X-API-Key` (or
  `Authorization: Bearer`) and `Content-Type: application/json` on each.

- **`401 Unauthorized`** — returned by every data route when the API key is
  missing or wrong (or when no key is configured on the server).
- **`415 Unsupported Media Type`** — state-changing routes
  (`POST`/`PUT`/`PATCH`/`DELETE`) require an `application/json` body; form,
  multipart, or text bodies are refused (CSRF protection).

> The default bind is loopback. Set `BLUE_WEB_API_HOST=0.0.0.0` only behind a
> trusted proxy or network boundary, and always with `BLUE_WEB_API_KEY` set.

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

Semantic search over previously ingested pages. Call `POST /web/ensure-index`
and ingest into the collection first (an in-memory store works; Redis is only
needed for persistence across restarts).

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
      "score": 0.08
    }
  ],
  "count": 5
}
```

`score` is a **cosine distance — lower is better** (an exact match is `0.0`); it
is not a similarity. Results are returned nearest-first. Searching an empty or
unindexed collection returns `{"results": [], "count": 0}` with `200`.

---

## `POST /web/search-web`

Live web search via Tavily — fetch, ingest, and rank in one call. No prior ingest required. Requires `TAVILY_API_KEY`.

**Request**
```json
{"query": "2026 greenhouse gas targets", "k": 5, "top_k": 3, "scope": "current_search_results", "collection": "research"}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | string | required | Search query |
| `k` | int | `5` | URLs to fetch from Tavily |
| `top_k` | int | `5` | Chunks to return after semantic ranking |
| `scope` | string | `"current_search_results"` | `"current_search_results"` ranks only the pages this search fetched; `"collection"` ranks over the whole ingested corpus (#35) |
| `collection` | string | env default | Collection to store results |

**Response `200`** — same structure as `/web/search` (`score` is a cosine distance, lower is better).
