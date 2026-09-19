# blue-data-web-source — Deployment & Ops

## Packages

`blue-data-web-source` ships as three separate installable artifacts:

| Artifact | PyPI name | Entry point |
|----------|-----------|-------------|
| Core library | `blue-data-web-source` | Python import |
| REST API | `blue-data-web-api` | `uvicorn web_api.main:app` |
| CLI | `blue-data-web-cli` | `blue-web` |

The MCP server (`platform/web-mcp/`) is not published separately — it is run directly via `python platform/web-mcp/src/web_mcp/server.py` or invoked by an MCP host over stdio.

---

## External Dependencies

| Service | Required by | Notes |
|---------|------------|-------|
| Redis Stack | REST API, MCP, CLI (production) | `redis/redis-stack-server:latest`; requires both RediSearch and ReJSON modules; port 6379 |
| OpenAI API | REST API, MCP, CLI when `BLUE_WEB_EMBEDDING=openai`; also `WEB.EXTRACT` and `llm` enricher | `text-embedding-3-small` for embeddings; `gpt-4o-mini` for LLM enrichment and structured extraction |
| Tavily API | `WEB.SEARCH_WEB` | Requires `TAVILY_API_KEY` and `pip install tavily-python` (not a declared extra) |
| Anthropic API | `NLQueryParser` (`[llm]` extra) | `ANTHROPIC_API_KEY` read by the `anthropic` SDK; `claude-opus-4-8` default model |

Without `BLUE_WEB_REDIS_HOST`, the pipeline falls back to `MemoryVectorSource` — all data is lost on restart. Suitable for local development only.

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BLUE_WEB_EMBEDDING` | No | `dummy` | Embedding model: `openai` or `dummy` (SHA-256 hash; for dev/test only) |
| `OPENAI_API_KEY` | When `BLUE_WEB_EMBEDDING=openai` or using `llm` enricher or `WEB.EXTRACT` | — | OpenAI API key |
| `OPENAI_EMBEDDING_MODEL` | No | `text-embedding-3-small` | Embedding model name; ignored when `BLUE_WEB_EMBEDDING=dummy` |
| `BLUE_WEB_REDIS_HOST` | No | — | Redis host; omit to use `MemoryVectorSource` |
| `BLUE_WEB_REDIS_PORT` | No | `6379` | Redis port |
| `BLUE_WEB_COLLECTION` | No | `web` | Default vector index collection name |
| `BLUE_WEB_CHUNK_SIZE` | No | `1000` | Word-count target per chunk |
| `BLUE_WEB_OVERLAP` | No | `150` | Word-count overlap between consecutive window chunks |
| `BLUE_WEB_TIMEOUT` | No | `30` | HTTP request timeout in seconds |
| `BLUE_WEB_MAX_WORKERS` | No | `10` | Concurrent fetch threads for batch ingest and crawl |
| `BLUE_WEB_ENRICHER` | No | `basic` | Enricher: `none`, `basic`, or `llm` |
| `BLUE_WEB_LLM_MODEL` | No | `gpt-4o-mini` | OpenAI model for `llm` enricher; ignored when enricher is not `llm` |
| `TAVILY_API_KEY` | When using `WEB.SEARCH_WEB` | — | Tavily search API key |
| `ANTHROPIC_API_KEY` | When using `NLQueryParser` | — | Anthropic API key; read automatically by the `anthropic` SDK |

All variables are read once at startup during singleton construction. Changing them requires a process restart.

---

## Running the REST API

### Development

```bash
cd platform/web-api
pip install -e ".[dev]"
BLUE_WEB_REDIS_HOST=localhost BLUE_WEB_EMBEDDING=openai OPENAI_API_KEY=sk-... \
  uvicorn web_api.main:app --reload --host 0.0.0.0 --port 8002
```

### Docker

```dockerfile
FROM python:3.10-slim
WORKDIR /app
COPY . .
RUN pip install -e "lib/[openai]" && pip install -e "platform/web-api/"
EXPOSE 8002
CMD ["uvicorn", "web_api.main:app", "--host", "0.0.0.0", "--port", "8002"]
```

```bash
docker run -p 8002:8002 \
  -e BLUE_WEB_REDIS_HOST=redis \
  -e BLUE_WEB_EMBEDDING=openai \
  -e OPENAI_API_KEY=sk-... \
  blue-data-web-api
```

### Redis Stack

```bash
docker run -d -p 6379:6379 redis/redis-stack-server:latest
```

---

## Running the MCP Server

The MCP server communicates over **stdio** — it is launched by the MCP host rather than run as a daemon.

```bash
# Direct invocation (for testing)
BLUE_WEB_REDIS_HOST=localhost BLUE_WEB_EMBEDDING=openai OPENAI_API_KEY=sk-... \
  python platform/web-mcp/src/web_mcp/server.py
```

MCP host config entry (e.g. `claude_desktop_config.json`):

```json
{
    "mcpServers": {
        "blue-data-web": {
            "command": "python",
            "args": ["/path/to/blue-data-web-source/platform/web-mcp/src/web_mcp/server.py"],
            "env": {
                "BLUE_WEB_REDIS_HOST": "localhost",
                "BLUE_WEB_EMBEDDING": "openai",
                "OPENAI_API_KEY": "sk-...",
                "TAVILY_API_KEY": "tvly-..."
            }
        }
    }
}
```

---

## Running the CLI

```bash
pip install -e "lib/" -e "platform/web-cli/"

export BLUE_WEB_REDIS_HOST=localhost
export BLUE_WEB_EMBEDDING=openai
export OPENAI_API_KEY=sk-...

blue-web ensure-index
blue-web ingest https://example.com/article
blue-web search "machine learning frameworks" -k 5
blue-web crawl https://docs.example.com --depth 2 --max-pages 30
```

---

## Port Assignments

| Service | Port |
|---------|------|
| blue-data REST API | 8000 |
| blue-data-doc-source REST API | 8001 |
| **blue-data-web-source REST API** | **8002** |
| Redis Stack | 6379 |

---

## Index Lifecycle

1. Call `WEB.ENSURE_INDEX` (or `blue-web ensure-index`) before the first ingest. The REST API does **not** auto-create the index on startup — index schema must be managed explicitly.
2. `ENSURE_INDEX` is idempotent — safe to call on every deploy.
3. Changing `BLUE_WEB_CHUNK_SIZE` or the embedding model dimension after initial indexing requires dropping and recreating the index. Re-ingest all pages after a schema change.

---

## Observability

No OTEL or Prometheus instrumentation is wired in V1. Observability relies on:

- **Uvicorn access logs** — request/response logging from the ASGI server.
- **Startup log** — the REST API logs the resolved pipeline configuration (embedding model, Redis host, collection, timeout, max_workers, enricher) at startup.
- **Error propagation** — unhandled exceptions return `500`; schema errors return `400`.

Pages that fail during batch ingest or crawl are skipped and reported in the response body rather than aborting the operation — callers can detect skipped URLs from the response `urls` list.

Structured logging and metrics are planned for a future release.

---

## Feed Ingestion Extra

`WEB.INGEST_FEED` requires `feedparser`:

```bash
pip install 'blue-data-web-source[feed]'
```

When `feedparser` is absent, calling `WEB.INGEST_FEED` raises `ImportError` with an install hint. The rest of the pipeline works without this extra.
