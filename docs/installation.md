# Installation

## Components

| Component | Path | Description |
|-----------|------|-------------|
| Library | `lib/` | `blue_data_web_source` importable package |
| MCP server | `platform/web-mcp/` | Expose web ops as MCP tools for Claude Desktop |
| CLI | `platform/web-cli/` | Command-line interface for all web ops |
| REST API | `platform/web-api/` | FastAPI service exposing web ops over HTTP (port 8002) |

---

## Install

blue-data-web-source depends on blue-data and blue-data-doc-source. Install all three in dependency order:

```bash
pip install -e /path/to/blue-data
pip install -e /path/to/blue-data-doc-source
pip install -e /path/to/blue-data-web-source/lib
```

### Optional extras

```bash
# OpenAI embeddings
pip install -e 'lib/[openai]'

# Redis vector store backend
pip install -e 'lib/[redis]'

# Natural-language interface (Claude tool use via Anthropic SDK)
pip install -e 'lib/[nl]'

# All extras
pip install -e 'lib/[openai,redis,nl]'
```

---

## Runtime dependencies

| Dependency | Version | Required by |
|------------|---------|-------------|
| `blue-data` | ≥0.1.0 | Always |
| `blue-data-doc-source` | ≥0.1.0 | Always (chunkers, embeddings, enricher) |
| `httpx` | ≥0.27.0 | Always (HTTP fetch) |
| `trafilatura` | ≥1.9.0 | Always (HTML extraction) |
| `openai` | ≥1.0.0 | `[openai]` extra |
| `redis[hiredis]` | ≥4.5.0 | `[redis]` extra |
| `anthropic` | ≥0.40.0 | `[nl]` extra |

---

## External dependencies

| Dependency | When needed | Notes |
|------------|-------------|-------|
| Redis Stack | `[redis]` extra | Run locally: `docker run -p 6379:6379 redis/redis-stack-server:latest` |
| `OPENAI_API_KEY` | `[openai]` extra | Used by `OpenAIEmbeddingModel` |
| `ANTHROPIC_API_KEY` | `[nl]` extra | Used by `NLQueryParser` (Claude tool use) |
| `TAVILY_API_KEY` | `WEB.SEARCH_WEB` op | Used by `TavilySearchProvider`; requires `pip install tavily-python` |

---

## Running tests

```bash
# Development dependencies
pip install pytest pytest-cov pytest-httpx

# Unit tests — no external services
pytest lib/tests/unit/ -v

# Integration tests — no network (pytest-httpx mocks HTTP; trafilatura runs for real)
pytest lib/tests/integration/ -v -m integration

# End-to-end tests — requires live network + OPENAI_API_KEY + Redis Stack
pytest lib/tests/e2e/ -v -m e2e

# All tests
pytest lib/tests/ -v
```

Integration tests cover two categories:
- **Synthetic tests** — hand-crafted HTML, validate extraction, stripping, deduplication, and HTTP error handling
- **Fixture tests** — load saved HTML from `data/fixtures/` and assert on title, date, domain, content keywords, and absence of nav fragments

When adding a new fixture: fetch the page once with httpx, save the HTML under `data/fixtures/`, commit it, then write fixture-based tests against it using `pytest-httpx`.
