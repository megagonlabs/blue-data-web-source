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

blue-data-web-source depends on blue-data and blue-data-doc-source. The
installable package in each sibling repo lives in its `lib/` subdirectory (the
repo root is not a Python project), so the paths end in `/lib`. Install all
three in dependency order — the siblings first, so `lib`'s requirements are
already satisfied:

```bash
pip install -e ../blue-data/lib
pip install -e ../blue-data-doc-source/lib
pip install -e lib
```

> Run these from the `blue-data-web-source` repo root, with `blue-data` and
> `blue-data-doc-source` cloned beside it. Replace `../blue-data` and
> `../blue-data-doc-source` with the actual paths if your layout differs.

### Platform adapters

Install individual platform components on top of the library:

```bash
# CLI (blue-web command)
pip install -e platform/web-cli/

# REST API (uvicorn web_api.main:app)
pip install -e platform/web-api/

# MCP server
pip install -e platform/web-mcp/
```

Each platform adapter also has a convenience script that installs the library and all sibling deps in one step:

```bash
bash lib/scripts/install.sh                      # library only
bash platform/web-cli/scripts/install.sh         # CLI + library
bash platform/web-api/scripts/install.sh         # REST API + library
bash platform/web-mcp/scripts/install.sh         # MCP server + library
```

The scripts resolve `../blue-data/lib` and `../blue-data-doc-source/lib` automatically from the directory layout and install them before the library.

### Optional extras

```bash
# OpenAI embeddings
pip install -e 'lib/[openai]'

# Redis vector store backend
pip install -e 'lib/[redis]'

# Natural-language interface (Claude tool use via Anthropic SDK)
pip install -e 'lib/[llm]'

# RSS/Atom feed support (WEB.INGEST_FEED)
pip install -e 'lib/[feed]'

# All extras at once
pip install -e 'lib/[all]'
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
| `anthropic` | ≥0.40.0 | `[llm]` extra |
| `feedparser` | ≥6.0.0 | `[feed]` extra |

---

## External dependencies

| Dependency | When needed | Notes |
|------------|-------------|-------|
| Redis Stack | `[redis]` extra | Run locally: `docker run -p 6379:6379 redis/redis-stack-server:latest` |
| `OPENAI_API_KEY` | `[openai]` extra | Used by `OpenAIEmbeddingModel` |
| `ANTHROPIC_API_KEY` | `[llm]` extra | Used by `NLQueryParser` (Claude tool use) |
| `TAVILY_API_KEY` | `WEB.SEARCH_WEB` op | Used by `TavilySearchProvider`; requires `pip install -e 'platform/web-mcp/[tavily]'` or `pip install tavily-python` |

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
- **Fixture tests** — load saved HTML from `data/fixtures/` and assert on title, date, domain, content keywords, and absence of nav fragments. Third-party fixtures are not committed to the repo; fetch them first with `python scripts/fetch_fixtures.py` (tests skip themselves when a fixture is absent).

When adding a new fixture: commit only synthetic, first-party HTML. For third-party pages, register the URL in `scripts/fetch_fixtures.py` and the README "Disclosures" table, git-ignore the saved file, and `pytest.skip` the test when it is absent — do not commit the HTML.
