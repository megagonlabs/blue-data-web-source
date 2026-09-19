# blue-data-web-source — MCP Server

Exposes all web ops as MCP tools for use with Claude Desktop and Claude Code. Provides eight tools: `ensure_index`, `ingest_url`, `ingest_urls`, `crawl_site`, `ingest_feed`, `search`, `search_web`, `extract`.

## Install

```bash
pip install -e /path/to/blue-data-web-source/lib
pip install -e .
```

## Run

```bash
BLUE_WEB_EMBEDDING=openai OPENAI_API_KEY=sk-... \
BLUE_WEB_REDIS_HOST=localhost \
python src/web_mcp/server.py
```

## Docs

- [MCP server reference](../../docs/mcp.md)
- [Installation](../../docs/installation.md)
