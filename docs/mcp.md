# MCP Server

The MCP server exposes blue-data-web-source as six AI-callable tools over the Model Context Protocol. Any MCP-compatible agent — Claude Desktop, Claude Code, or a custom agent using the MCP SDK — can ingest web pages, crawl sites, search content, and query the live web without any custom integration code.

## Tools

| Tool | Description |
|---|---|
| `ensure_index` | Create the vector index before first ingest |
| `ingest_url` | Fetch, extract, chunk, embed, and upsert a single web page |
| `ingest_urls` | Batch ingest multiple URLs in one call |
| `crawl_site` | BFS-crawl a site from a seed URL |
| `search` | Semantic search over ingested pages |
| `search_web` | Live web search via Tavily, ingest results, return ranked chunks |

## Installation

```bash
pip install -e lib/
pip install mcp fastmcp
```

For OpenAI embeddings:

```bash
pip install -e "lib/[openai]"
```

## Configuration

All configuration is via environment variables:

| Variable | Default | Description |
|---|---|---|
| `BLUE_WEB_EMBEDDING` | `dummy` | `openai` or `dummy` |
| `OPENAI_API_KEY` | — | Required when `BLUE_WEB_EMBEDDING=openai` |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | OpenAI model name |
| `BLUE_WEB_REDIS_HOST` | — | Redis host; omit to run without a persistent vector store |
| `BLUE_WEB_REDIS_PORT` | `6379` | Redis port |
| `BLUE_WEB_COLLECTION` | `web` | Default collection name |
| `BLUE_WEB_CHUNK_SIZE` | `1000` | Max words per chunk |
| `BLUE_WEB_OVERLAP` | `150` | Word overlap between chunks |
| `BLUE_WEB_TIMEOUT` | `30` | HTTP timeout in seconds |
| `TAVILY_API_KEY` | — | Required for `search_web` tool (Tavily backend) |

## Running with Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "blue-web-source": {
      "command": "python3",
      "args": ["/path/to/blue-data-web-source/platform/web-mcp/src/server.py"],
      "env": {
        "BLUE_WEB_EMBEDDING": "openai",
        "OPENAI_API_KEY": "sk-...",
        "BLUE_WEB_REDIS_HOST": "localhost",
        "BLUE_WEB_REDIS_PORT": "6379",
        "BLUE_WEB_COLLECTION": "web"
      }
    }
  }
}
```

Restart Claude Desktop after saving. The six tools will appear in the tool picker.

## Running with Claude Code

Add to your project's `.mcp.json` or `~/.claude.json`:

```json
{
  "mcpServers": {
    "blue-web-source": {
      "command": "python3",
      "args": ["platform/web-mcp/src/server.py"],
      "env": {
        "BLUE_WEB_EMBEDDING": "openai",
        "OPENAI_API_KEY": "sk-...",
        "BLUE_WEB_REDIS_HOST": "localhost"
      }
    }
  }
}
```

## Running standalone

For development or debugging, run the server directly:

```bash
# No Redis, dummy embeddings — useful for local testing
BLUE_WEB_EMBEDDING=dummy python3 platform/web-mcp/src/server.py

# Full production setup
BLUE_WEB_EMBEDDING=openai \
OPENAI_API_KEY=sk-... \
BLUE_WEB_REDIS_HOST=localhost \
TAVILY_API_KEY=tvly-... \
python3 platform/web-mcp/src/server.py
```

The server listens on stdio (MCP standard transport). Use the [MCP Inspector](https://github.com/modelcontextprotocol/inspector) to call tools interactively:

```bash
npx @modelcontextprotocol/inspector python3 platform/web-mcp/src/server.py
```

## Redis setup

The vector store is optional — omit `BLUE_WEB_REDIS_HOST` to run without it. Ingest still produces chunk records and returns metadata; they just aren't persisted between sessions. The `search` tool will return `{"error": ...}` when no vector source is configured.

To run Redis locally:

```bash
docker run -d -p 6379:6379 redis/redis-stack-server:latest
```

## Tool reference

### ensure_index

Create the vector index for a collection. Call once before the first ingest. Safe to call multiple times — skips creation if the index already exists.

```
ensure_index(collection="web") → {"status": "ok", "collection": "web"}
```

### ingest_url

Fetch a single web page, extract its main content, chunk it, and store it.

```
ingest_url(url, collection="web")
→ {
    "page_id": "web_cb9904393a53c312",
    "url": "https://example.com/article",
    "domain": "example.com",
    "title": "Article Title",
    "date": "2026-03-15",
    "chunk_count": 8,
    "collection": "web"
  }
```

Returns `{"error": "No content extracted ..."}` when the page is a login wall, nav-only, or yields no extractable body.

### ingest_urls

Batch-ingest multiple pages in one call. Equivalent to calling `ingest_url` for each URL.

```
ingest_urls(urls=["https://...", "https://..."], collection="web")
→ {
    "chunk_count": 24,
    "ingested": 3,
    "skipped": [],
    "collection": "web"
  }
```

`skipped` lists URLs that returned no extractable content.

### crawl_site

BFS-crawl a site from a seed URL and ingest all reachable pages.

```
crawl_site(url, depth=1, max_pages=10, same_domain_only=True, collection="web")
→ {
    "chunk_count": 47,
    "pages_ingested": 12,
    "urls": ["https://docs.example.com/", "https://docs.example.com/guide", ...],
    "collection": "web"
  }
```

### search

Semantic search over ingested pages. Requires `BLUE_WEB_REDIS_HOST` and `ensure_index` to have been called.

```
search(query, collection="web", k=5)
→ {
    "results": [
      {
        "chunk_id": "web_abc123_chunk_0003",
        "text": "...",
        "url": "https://example.com/article",
        "domain": "example.com",
        "title": "Article Title",
        "section_title": "Policy Targets"
      },
      ...
    ],
    "count": 5
  }
```

Optional fields (`title`, `section_title`, `date`) are omitted when not present on the chunk.

### search_web

Live web search via Tavily — fetches fresh pages, ingests them, returns ranked chunks. No prior ingest required. Requires `TAVILY_API_KEY`.

```
search_web(query, k=5, collection="web")
→ {
    "results": [
      {
        "text": "...",
        "url": "https://news.example.com/article",
        "domain": "news.example.com",
        "title": "News Article"
      },
      ...
    ],
    "count": 5
  }
```

## Typical agent session

```
Agent: ensure_index(collection="research")
→ {"status": "ok", "collection": "research"}

Agent: ingest_url(url="https://arxiv.org/abs/2401.00001", collection="research")
→ {"page_id": "web_3a7f...", "domain": "arxiv.org", "chunk_count": 12, ...}

Agent: crawl_site(url="https://docs.example.com/", depth=1, max_pages=20, collection="research")
→ {"chunk_count": 84, "pages_ingested": 18, "urls": [...], ...}

Agent: search(query="transformer attention mechanism", collection="research", k=3)
→ {"results": [{"chunk_id": "...", "text": "...", "url": "...", "section_title": "Method"}, ...], "count": 3}

Agent: search_web(query="latest LLM alignment research 2026", k=5, collection="research")
→ {"results": [...], "count": 5}
```

## Running the tests

```bash
python3 -m pytest platform/web-mcp/tests/ -v
```

Tests stub out the MCP SDK and patch the `WebDataSource` singleton, so no Redis, OpenAI, or network connection is needed.
