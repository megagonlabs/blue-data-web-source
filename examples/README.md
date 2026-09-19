# blue-data-web-source — Examples

Runnable demos organized in five categories. Most self-contained demos work with no API keys.

---

## Quick start (no API key, no Redis)

```bash
# install dependencies (from repo root)
pip install -e /path/to/blue-data
pip install -e /path/to/blue-data-doc-source
pip install -e lib/

# run the minimal quickstart (in-process store, mocked HTTP, no API key)
python3 examples/backends/memory_quickstart.py
```

> Replace `/path/to/blue-data` and `/path/to/blue-data-doc-source` with the paths where you cloned those repos.

---

## Directory layout

```
examples/
  demo.py                      # full pipeline demo: ingest → embed → search (offline, no API key)
  demo_web.py                  # CLI-style demo: optional URL args, --embedding openai, --nl-query
  ingest_url.py                # minimal: ingest one URL and print chunks (no vector store)
  mock_fetcher.py              # shared helper: offline HTML fixtures + build_mock_fetcher()
  README.md                    # this file

  scenario-demos/              # Problem → Solution story demos
  feature-demos/               # Drill-down into one feature at a time
  integration-demos/           # End-to-end multi-component workflows
  framework-demos/             # Integration with LangGraph / MCP (OpenAI required)
  backends/                    # Swappable vector store + embedding backends
```

---

## Root scripts

| Script | What it shows | Deps |
|---|---|---|
| `demo.py` | Full pipeline: ingest → embed → search; works offline via local fixtures | `OPENAI_API_KEY` |
| `demo_web.py` | End-to-end demo with optional real URLs; `--embedding openai`, `--nl-query` | OpenAI optional |
| `ingest_url.py` | Minimal: ingest one URL and print chunks (no vector store required) | live network |

```bash
python3 examples/demo.py
python3 examples/ingest_url.py https://python.org
python3 examples/demo_web.py
OPENAI_API_KEY=sk-... python3 examples/demo_web.py --embedding openai --query "what is this about"
```

---

## Scenario demos — Problem / Solution stories

| Demo | What it shows | Deps |
|---|---|---|
| `scenario-demos/extraction_demo.py` | Nav-fragment stripping, metadata fields, None for nav-only pages | none |
| `scenario-demos/crawl_vs_single_demo.py` | BFS crawl vs single-URL ingest; depth/max_pages effects | none |

```bash
python3 examples/scenario-demos/extraction_demo.py
python3 examples/scenario-demos/crawl_vs_single_demo.py
```

---

## Feature demos — One feature, deep

| Demo | What it shows | Deps |
|---|---|---|
| `feature-demos/extractor_demo.py` | WebExtractor fields; boilerplate stripping; language detection; None for login walls | none |
| `feature-demos/content_addressing_demo.py` | URL-hash chunk IDs; idempotent re-ingest; redirect handling (final_url) | none |

```bash
python3 examples/feature-demos/extractor_demo.py
python3 examples/feature-demos/content_addressing_demo.py
```

---

## Integration demos — End-to-end workflows

| Demo | What it shows | Deps |
|---|---|---|
| `integration-demos/multi_url_demo.py` | Batch ingest with `urls` list vs individual calls; per-page chunk structure | none |
| `integration-demos/crawler_demo.py` | WEB.CRAWL: depth/max_pages/same_domain_only; BFS page ordering | none |
| `integration-demos/search_web_demo.py` | WEB.SEARCH_WEB via Tavily; live fetch + ingest + rank; vs WEB.SEARCH | TAVILY_API_KEY, OpenAI optional |

```bash
python3 examples/integration-demos/multi_url_demo.py
python3 examples/integration-demos/crawler_demo.py

# Requires TAVILY_API_KEY (free tier available at tavily.com):
TAVILY_API_KEY=tvly-... OPENAI_API_KEY=sk-... python3 examples/integration-demos/search_web_demo.py
# Without OpenAI (hash-based embeddings):
TAVILY_API_KEY=tvly-... python3 examples/integration-demos/search_web_demo.py --dummy
```

---

## Framework demos — LangGraph and MCP integration

| Demo | What it shows | Deps |
|---|---|---|
| `framework-demos/langgraph_rag_demo.py` | Two-node RAG graph: [retrieve] → [generate] → END; grounded answers | OpenAI, langgraph |
| `framework-demos/mcp_demo.py` | LLM agent calling web-mcp tools: ensure_index, ingest_url, search | OpenAI, langchain-mcp-adapters, langgraph, MCP server |

```bash
pip install langgraph langchain-openai

# LangGraph RAG (no MCP server needed):
OPENAI_API_KEY=sk-... python3 examples/framework-demos/langgraph_rag_demo.py

# MCP agent — start the server first:
cd platform/web-mcp
BLUE_WEB_EMBEDDING=openai OPENAI_API_KEY=sk-... BLUE_WEB_REDIS_HOST=localhost python platform/web-mcp/src/web_mcp/server.py
# then in another terminal:
OPENAI_API_KEY=sk-... python3 examples/framework-demos/mcp_demo.py
```

---

## Backend demos — Swappable vector stores and embedding models

| Demo | What it shows | Deps |
|---|---|---|
| `backends/memory_quickstart.py` | Minimal quickstart: MemoryVectorSource + DummyEmbeddingModel, fully offline | none |
| `backends/redis_backend_demo.py` | RedisVectorSource as persistent backend; re-ingest idempotency | Redis Stack |
| `backends/openai_embeddings_demo.py` | Semantic quality comparison: OpenAI vs DummyEmbeddingModel | OpenAI |

```bash
# In-memory (no deps):
python3 examples/backends/memory_quickstart.py

# Redis (requires Redis Stack):
docker run -p 6379:6379 redis/redis-stack-server:latest
python3 examples/backends/redis_backend_demo.py

# OpenAI embeddings:
OPENAI_API_KEY=sk-... python3 examples/backends/openai_embeddings_demo.py
```

---

## Op-code reference

Source of truth: [docs/api-reference.md](../docs/api-reference.md).

All operations are dispatched via `WebDataSource.execute_query(op_dict)`.

| Op | Required fields | Optional | Returns |
|---|---|---|---|
| `WEB.ENSURE_INDEX` | — | `collection` | `None` |
| `WEB.INGEST` | `url` or `urls` (list) | `collection` | `list[dict]` (WebChunkRecord) |
| `WEB.EXTRACT` | (`url` or `urls`) + `schema` | — | `dict` or `list[dict]` |
| `WEB.CRAWL` | `url` | `collection`, `depth`(1), `max_pages`(10), `same_domain_only`(True) | `list[dict]` |
| `WEB.INGEST_FEED` | `url` | `since`, `max_entries`, `collection` | `list[dict]` |
| `WEB.SEARCH` | `query` | `collection`, `k`(5) | `list[dict]` |
| `WEB.SEARCH_WEB` | `query` | `collection`, `k`(5) | `list[dict]` |

`execute_query_nl(text, collection)` — Claude tool use; maps natural language to the above ops.
Requires `pip install 'blue-data-web-source[llm]'` and `ANTHROPIC_API_KEY`.

---

## WebChunkRecord field reference

Source of truth: [docs/api-reference.md](../docs/api-reference.md).

| Field | Type | Present | Description |
|---|---|---|---|
| `chunk_id` | str | always | `web_{sha256(final_url)[:16]}_chunk_{idx:04d}` |
| `chunk_index` | int | always | 0-based index within the page |
| `url` | str | always | Canonical URL (post-redirect) |
| `domain` | str | always | Parsed `netloc` from `url` |
| `text` | str | always | Chunk text, boilerplate stripped |
| `fetch_timestamp` | str | always | UTC ISO-8601 at ingest time |
| `title` | str\|None | if extracted | Page title from trafilatura |
| `author` | str\|None | if extracted | Trafilatura or JSON-LD/meta fallback |
| `date` | str\|None | if extracted | Trafilatura or JSON-LD fallback |
| `description` | str\|None | chunk 0 only | Trafilatura or OG fallback — NOT on later chunks |
| `language` | str\|None | if detected | html[lang] attribute |
| `section_title` | str\|None | if detected | Heading above the chunk |
| `schema_type` | str\|None | if present | Schema.org `@type` from JSON-LD (e.g. `NewsArticle`) |
| `keywords` | list[str]\|None | if present | From JSON-LD or `<meta name="keywords">` |
| `publisher` | str\|None | if present | From JSON-LD `publisher.name` or `og:site_name` |
| `canonical_url` | str\|None | if present | From JSON-LD, `<link rel="canonical">`, or `og:url` |
| `collection` | str\|None | if set | Collection name |
| `embedding` | list[float]\|None | if set | Included by `to_dict()` when an embedding model is configured |
| `entities` | list[dict] | if enriched | Added by WebPipeline when BasicEnricher used |

`WebChunkRecord.to_dict()` omits fields with `None` values — check key presence, not value.

---

## mock_fetcher.py — Shared offline helper

All offline demos import from `examples/mock_fetcher.py`:

```python
from mock_fetcher import (
    NPR_URL, CLIMATE_URL, CLIMATE_HTML,
    POLICY_URL, POLICY_HTML,
    load_npr_html,           # loads data/fixtures/npr_article.html
    build_mock_fetcher,      # returns a MagicMock WebFetcher
)

source._pipeline._fetcher = build_mock_fetcher({NPR_URL: npr_html, CLIMATE_URL: CLIMATE_HTML})
```

The mock fetcher raises `ValueError` for unregistered URLs — fast failure when a
demo accidentally hits a URL not in the fixture map.

---

## Dependency matrix

| Category | numpy | trafilatura | redis-py | openai | tavily-python | langgraph | mcp |
|---|---|---|---|---|---|---|---|
| scenario-demos | ✓ | ✓ | — | — | — | — | — |
| feature-demos | ✓ | ✓ | — | — | — | — | — |
| integration-demos/multi_url | ✓ | ✓ | — | — | — | — | — |
| integration-demos/crawler | ✓ | ✓ | — | — | — | — | — |
| integration-demos/search_web | ✓ | ✓ | ✓ | optional | ✓ | — | — |
| framework-demos/langgraph | ✓ | ✓ | — | ✓ | — | ✓ | — |
| framework-demos/mcp | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ |
| backends/memory | ✓ | ✓ | — | — | — | — | — |
| backends/redis | ✓ | ✓ | ✓ | — | — | — | — |
| backends/openai | ✓ | ✓ | — | ✓ | — | — | — |
