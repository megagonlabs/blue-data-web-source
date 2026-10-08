# blue-data-web-source

Ingest web pages, RSS feeds, and live search results into a queryable vector index.

Web pages are live data — they update on a schedule, arrive through feeds, and belong in an agent's data layer alongside SQL tables, documents, and graph stores. `blue-data-web-source` handles the full path from URL to queryable vector index with built-in freshness tracking, and plugs into blue-data's `DataRegistry` so web content becomes a first-class member of the same intelligence layer as the rest of your data.

→ **[Full documentation](docs/README.md)** · **[Quick start](docs/quickstart.md)** · **[Installation](docs/installation.md)** · **[Examples](examples/README.md)** · **[CLI](docs/cli.md)** · **[MCP server](docs/mcp.md)**

---

## What it does

- **Ingest** web pages, RSS/Atom feeds, and URL batches — fetch, extract, chunk, enrich, embed, and upsert in one call.
- **Crawl** a site from a seed URL with a polite BFS crawler (robots.txt, rate limiting, retry backoff).
- **Search** your ingested corpus by semantic similarity, or search the live web and ingest the results in one op.
- **Extract** typed fields from any page using a caller-supplied JSON Schema.
- **Skip** unchanged pages on every run through built-in content-hash freshness tracking — only new or changed content is re-embedded.
- **Register** web content in blue-data's `DataRegistry` alongside SQL, document, and graph sources, with a consistent chunk schema.

Full feature list: [docs/features.md](docs/features.md). Runnable demos: [examples/](examples/).

---

## Repository layout

```
blue-data-web-source/
├── lib/                    # Core Python library (blue-data-web-source)
│   ├── src/blue_data_web_source/
│   └── tests/              # unit, integration, e2e, and repo-level tests
├── platform/
│   ├── web-cli/            # `blue-web` command-line interface
│   ├── web-mcp/            # MCP server (Claude Desktop / Claude Code)
│   └── web-api/            # FastAPI REST service (port 8002)
├── docs/                   # Documentation
├── examples/               # Runnable example scripts with captured output
├── design/                 # Design notes and decision log
└── data/
    └── fixtures/           # Saved HTML fixtures for integration tests
```

---

## Requirements

- Python 3.10+
- Sibling repos cloned beside this one: [`blue-data`](../blue-data) and [`blue-data-doc-source`](../blue-data-doc-source)
- API keys, as needed for the features you use:

```bash
export OPENAI_API_KEY=sk-...        # embeddings and WEB.EXTRACT
export ANTHROPIC_API_KEY=sk-ant-... # natural-language queries (execute_query_nl)
export TAVILY_API_KEY=tvly-...      # live web search (WEB.SEARCH_WEB)
```

Optional extras (`[openai]`, `[redis]`, `[llm]`, `[feed]`, `[all]`) are documented in [docs/installation.md](docs/installation.md).

---

## Quick start

```bash
# Clone blue-data and blue-data-doc-source beside this repo first, then:
python -m venv .venv && source .venv/bin/activate
bash lib/scripts/install.sh          # installs blue-data, blue-data-doc-source, and lib/

# Ingest a page and inspect the extracted chunks — no API key required:
python - <<'PY'
from blue_data_web_source.source.web.pipeline import WebPipeline

for r in WebPipeline(chunk_size=500, overlap=100).ingest("https://text.npr.org/nx-s1-5884537"):
    print(r["chunk_id"], r["domain"], r["text"][:80])
PY
```

For the full end-to-end walkthrough — embeddings, a vector store, feeds, crawling, and search — see [docs/quickstart.md](docs/quickstart.md) and the [guide](docs/guide.md).

---

## Packages

| Package | Description |
|---|---|
| [`lib/`](lib/README.md) | Core library `blue-data-web-source` — `WebDataSource`, `WebPipeline`, and all ingestion components. Install: `bash lib/scripts/install.sh` |
| [`platform/web-cli/`](platform/web-cli/README.md) | `blue-web` command-line interface over the core ops. Install: `pip install -e platform/web-cli` |
| [`platform/web-mcp/`](platform/web-mcp/README.md) | MCP server exposing web ops as AI-callable tools. Install: `pip install -e platform/web-mcp` |
| [`platform/web-api/`](platform/web-api/README.md) | FastAPI REST service on port 8002. Install: `pip install -e platform/web-api` |
