# CLI Reference

`blue-web` is the command-line interface for blue-data-web-source. Install it with:

```bash
pip install -e lib/ -e platform/web-cli/
```

Configure via environment variables (see [Installation](installation.md) for the full list).

**Library-only operations (no CLI command):** `WEB.FIND`, `WEB.FIND_THEN_SEARCH`, `WEB.GRAPH_NEIGHBORS`, `WEB.GRAPH_QUERY`. These require `RecordStore` and `GraphSource` components not wired by `WebDataSource.from_env()`. Use these ops directly through the Python library.

---

## Global flag

```
blue-web [--json] COMMAND [ARGS]...
```

| Flag | Description |
|------|-------------|
| `--json` | Output raw JSON instead of human-readable text |

---

## Commands

### `ensure-index`

Create the vector index for a collection. Must be called once before the first ingest. Idempotent — safe to call on every deploy.

```bash
blue-web ensure-index
blue-web ensure-index --collection news
```

| Option | Default | Description |
|--------|---------|-------------|
| `--collection` | `$BLUE_WEB_COLLECTION` or `web` | Collection to index |

---

### `ingest`

Fetch and ingest one or more web pages. Re-ingesting an unchanged URL is safe — URL-hash deduplication means re-ingest is an upsert, never a duplicate.

```bash
blue-web ingest https://example.com/article
blue-web ingest https://example.com/a https://example.com/b --collection news
blue-web --json ingest https://example.com/article
```

| Argument / Option | Description |
|-------------------|-------------|
| `URL [URL]...` | One or more URLs to ingest |
| `--collection` | Collection name (overrides `BLUE_WEB_COLLECTION`) |

---

### `crawl`

BFS-crawl a site from a seed URL and ingest all reachable pages. Respects `robots.txt` and stays within the seed domain by default.

```bash
blue-web crawl https://docs.example.com
blue-web crawl https://docs.example.com --depth 2 --max-pages 50
blue-web crawl https://example.com --allow-external
```

| Option | Default | Description |
|--------|---------|-------------|
| `--depth N` | `1` | Crawl depth from the seed URL |
| `--max-pages N` | `10` | Hard cap on pages ingested |
| `--allow-external` | off | Follow links outside the seed domain (MCP equivalent: `same_domain_only=False`) |
| `--collection` | `$BLUE_WEB_COLLECTION` or `web` | Collection name |

---

### `feed`

Fetch an RSS or Atom feed and ingest each article. Useful for incremental daily runs with `--since`.

```bash
blue-web feed https://feeds.npr.org/1001/rss.xml
blue-web feed https://feeds.npr.org/1001/rss.xml --since 2026-09-01T00:00:00Z
blue-web feed https://feeds.npr.org/1001/rss.xml --max-entries 20 --collection news
```

Requires `pip install 'blue-data-web-source[feed]'`.

| Option | Default | Description |
|--------|---------|-------------|
| `--since ISO8601` | none | Skip entries published before this date |
| `--max-entries N` | none | Cap entries processed after date filtering |
| `--collection` | `$BLUE_WEB_COLLECTION` or `web` | Collection name |

---

### `search`

Semantic search over ingested pages. Requires `BLUE_WEB_REDIS_HOST` and an indexed collection.

```bash
blue-web search "climate targets 2026"
blue-web search "climate targets 2026" -k 10 --collection news
blue-web --json search "climate targets 2026"
```

| Argument / Option | Default | Description |
|-------------------|---------|-------------|
| `QUERY` | — | Natural-language search query |
| `-k N` | `5` | Number of results to return |
| `--collection` | `$BLUE_WEB_COLLECTION` or `web` | Collection to search |

---

### `search-web`

Query the live web via Tavily, ingest the top results on the fly, and return semantically ranked chunks. No prior ingest required.

```bash
blue-web search-web "2026 greenhouse gas targets"
blue-web search-web "2026 greenhouse gas targets" -k 8
```

Requires `TAVILY_API_KEY` and `BLUE_WEB_REDIS_HOST`.

| Argument / Option | Default | Description |
|-------------------|---------|-------------|
| `QUERY` | — | Search query sent to Tavily |
| `-k N` | `5` | Number of URLs to fetch from Tavily |
| `--collection` | `$BLUE_WEB_COLLECTION` or `web` | Collection name |

---

### `extract`

Fetch a URL and extract typed fields using a JSON Schema. Does not chunk or store — extraction is stateless.

```bash
blue-web extract https://example.com/product \
    --schema '{"properties": {"title": {"type": "string"}, "price": {"type": "number"}}}'

blue-web extract https://example.com/product --schema-file product_schema.json
```

Requires `OPENAI_API_KEY`.

| Option | Description |
|--------|-------------|
| `--schema JSON` | JSON Schema as an inline JSON string |
| `--schema-file FILE` | Path to a JSON Schema file |

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BLUE_WEB_EMBEDDING` | `dummy` | `openai` or `dummy` |
| `OPENAI_API_KEY` | — | Required when `BLUE_WEB_EMBEDDING=openai` |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | OpenAI model name |
| `BLUE_WEB_REDIS_HOST` | — | Redis host; omit to use in-memory store |
| `BLUE_WEB_REDIS_PORT` | `6379` | Redis port |
| `BLUE_WEB_COLLECTION` | `web` | Default collection name |
| `BLUE_WEB_CHUNK_SIZE` | `1000` | Word-count target per chunk |
| `BLUE_WEB_OVERLAP` | `150` | Word-count overlap between chunks |
| `BLUE_WEB_TIMEOUT` | `30` | HTTP timeout in seconds |
| `BLUE_WEB_MAX_WORKERS` | `10` | Thread pool size for concurrent fetch |
| `BLUE_WEB_ENRICHER` | `basic` | Enricher: `none`, `basic`, or `llm` |
| `BLUE_WEB_LLM_MODEL` | `gpt-4o-mini` | OpenAI model used when `BLUE_WEB_ENRICHER=llm` |
| `TAVILY_API_KEY` | — | Required for `search-web` |
