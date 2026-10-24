# blue-data-web-source — Software Stack

---

## Language & Runtime

| Concern | Choice |
|---------|--------|
| Language | Python |
| Minimum version | 3.10 |
| Package manager | pip |
| Project config | `pyproject.toml` |

---

## HTTP

| Concern | Choice |
|---------|--------|
| HTTP client | `httpx>=0.27.0` |

`httpx` is the HTTP client used by `WebFetcher`. It is a hard dependency — always installed.

Key properties used:

| Feature | How used |
|---------|---------|
| `httpx.Client` | Synchronous client; opened as a context manager per fetch to ensure connection cleanup |
| `follow_redirects=True` | Follows HTTP 301/302/307/308 chains; `response.url` reflects the final destination |
| `response.raise_for_status()` | Raises `HTTPStatusError` on non-2xx; propagated to caller |
| `timeout=N` | Configurable; default 30 s to accommodate news sites with slow SSL handshakes |

`httpx` was chosen over `requests` for its first-class async support (available for future use) and its `pytest-httpx` companion library, which provides seamless HTTP mocking in tests without monkeypatching.

---

## HTML Extraction

| Concern | Choice |
|---------|--------|
| Boilerplate removal | `trafilatura>=1.9.0` |

`trafilatura` is a hard dependency. It analyses the HTML tree structure and text density to identify the main content region and remove navigation, ads, sidebars, footers, and comments. `WebExtractor` wraps two trafilatura functions:

| Function | Role |
|----------|------|
| `trafilatura.extract(html)` | Returns main article body text or `None` |
| `trafilatura.extract_metadata(html)` | Returns title, author, date, description, language |

After trafilatura runs, `WebExtractor` applies a line-by-line pass to strip inline CMS navigation fragments (`Related Story:`, `Read more:`, `See also:`, `Also read:`, `Advertisement`, `Story continues`).

---

## Structured Metadata

`MetadataExtractor` uses only Python's standard library `html.parser.HTMLParser` — no external dependency. It parses JSON-LD `<script>` blocks, OpenGraph `<meta property="og:...">` tags, standard `<meta name="...">` tags, and `<link rel="canonical">` in a single pass over the raw HTML.

---

## Feed Parsing

| Concern | Choice |
|---------|--------|
| RSS / Atom parsing | `feedparser>=6.0.0` (`[feed]` extra) |

`feedparser` is an optional dependency. It is deferred-imported inside `FeedParser.parse()` so the rest of the library works without it. An `ImportError` with an install hint is raised at call time when it is absent.

---

## Embeddings

| Concern | Choice |
|---------|--------|
| Embedding model interface | `EmbeddingModel` ABC from `blue-data-doc-source` |
| OpenAI embeddings | `openai>=1.0.0` (`[openai]` extra) |

`EmbeddingModel` and its concrete implementations (`OpenAIEmbeddingModel`, `DummyEmbeddingModel`) are imported directly from `blue-data-doc-source` — not reimplemented. The `openai` package is optional; it is only required when `OpenAIEmbeddingModel` is used.

---

## Chunking and Enrichment

`SectionChunker`, `WindowChunker`, `BasicEnricher`, and the `Chunker`/`Enricher` ABCs are all imported from `blue-data-doc-source`. Web-source adds no chunking or enrichment logic of its own.

---

## Structured Field Extraction

`StructuredFieldExtractor` (from `blue-data-doc-source`) is used by `WEB.EXTRACT`. It is deferred-imported at call time and requires `openai>=1.0.0`.

---

## Natural Language Interface

| Concern | Choice |
|---------|--------|
| LLM provider | Anthropic (`anthropic>=0.40.0`, `[llm]` extra) |
| Interface | Claude tool use (`tool_choice={"type": "any"}`) |

`NLQueryParser` calls the Anthropic API to classify free-text instructions into `WEB.*` op dicts. Six tools are registered: `ingest_url`, `search`, `ensure_index`, `crawl`, `search_web`, `ingest_feed`. `tool_choice={"type": "any"}` guarantees a tool call is always returned — never free text.

The `anthropic` package is deferred-imported inside `NLQueryParser._get_client()` so the library works without it when the NL interface is not used.

**Configuration:**

`ANTHROPIC_API_KEY` is read by the `anthropic` SDK automatically. The model is configurable via `WebDataSource(nl_model="claude-opus-4-8")` (default).

---

## Storage

Storage is provided by `blue-data` via the `VectorSource` abstract base class. `WebPipeline` interacts only with the `VectorSource` interface.

### VectorSource

| Method | Role |
|--------|------|
| `upsert(collection, doc_id, doc)` | Called on every embedded chunk |
| `knn(index_name, query_vector, k)` | Called on `WEB.SEARCH` |
| `create_index(index_name, collection, dim)` | Called by `WEB.ENSURE_INDEX` |

### Backends

| Backend | Class | Persistence | Typical use |
|---------|-------|-------------|-------------|
| Memory | `MemoryVectorSource` | None (in-process) | Development, testing, demos |
| Redis | `RedisVectorSource` | Yes | Production; requires Redis Stack |

`redis[hiredis]>=4.5.0` is an optional dependency (`[redis]` extra). All backends are provided by `blue-data`.

---

## Web Search

| Concern | Choice |
|---------|--------|
| Default search backend | `TavilySearchProvider` (requires `pip install tavily-python` and `TAVILY_API_KEY`) |
| Extension point | `WebSearchProvider` ABC — implement `search(query, k) -> list[str]` |

`tavily-python` is not a declared extra — it must be installed separately. The import is deferred inside `TavilySearchProvider.search()`.

---

## External Interfaces

### MCP Server

| Concern | Choice |
|---------|--------|
| Library | `mcp` / `fastmcp` |

Eight MCP tools: `ensure_index`, `ingest_url`, `ingest_urls`, `crawl_site`, `search`, `search_web`, `extract`, `ingest_feed`. Tool handlers delegate directly to a `WebDataSource` singleton.

### REST API

| Concern | Choice |
|---------|--------|
| Framework | FastAPI (`fastapi>=0.110.0`) |
| ASGI server | Uvicorn (`uvicorn[standard]>=0.29.0`) |
| Port | 8002 |

Eight routes under `/web/` (one `GET /web/health` + seven POST handlers for the core ops). Request/response models are Pydantic `BaseModel` subclasses. No business logic in the route handlers — all calls delegate to `WebDataSource.execute_query`. `WEB.FIND`, `WEB.FIND_THEN_SEARCH`, `WEB.GRAPH_NEIGHBORS`, and `WEB.GRAPH_QUERY` are library-only — no REST API routes.

### CLI

| Concern | Choice |
|---------|--------|
| Argument parsing | Click |
| Output | plain text (default) or JSON (`--json` flag) |

Seven Click commands: `ensure-index`, `ingest`, `crawl`, `feed`, `search`, `search-web`, `extract`. All delegate to `WebDataSource.execute_query` via the shared `config.py` singleton.

---

## Testing

| Concern | Choice |
|---------|--------|
| Test runner | `pytest` |
| HTTP mocking | `pytest-httpx` |
| Coverage | `pytest-cov` |

All tools are declared as `dev` dependencies in `lib/pyproject.toml`.

Unit tests mock `WebFetcher` via `MagicMock` and use `MemoryVectorSource` + `DummyEmbeddingModel` — no external services required. Integration tests mock HTTP via `pytest-httpx` but run trafilatura against real HTML. Platform adapter tests use FastAPI's `TestClient` (REST API) and `click.testing.CliRunner` (CLI).

---

## Dependency Summary

| Package | Version | Extra | Role |
|---------|---------|-------|------|
| `blue-data` | ≥1.0.0 | always | `DataSource` ABC, `VectorSource` ABC and backends |
| `blue-data-doc-source` | ≥1.0.0 | always | Chunkers, embedding models, enrichers, `StructuredFieldExtractor` |
| `httpx` | ≥0.27.0 | always | HTTP fetch |
| `trafilatura` | ≥1.9.0 | always | HTML boilerplate removal and metadata extraction |
| `openai` | ≥1.0.0 | `[openai]` | `OpenAIEmbeddingModel`; `StructuredFieldExtractor` |
| `redis[hiredis]` | ≥4.5.0 | `[redis]` | `RedisVectorSource` backend |
| `anthropic` | ≥0.40.0 | `[llm]` | `NLQueryParser` (Claude tool use) |
| `feedparser` | ≥6.0.0 | `[feed]` | RSS / Atom feed parsing |
| `fastapi` | ≥0.110.0 | `web-api` | REST API framework |
| `uvicorn[standard]` | ≥0.29.0 | `web-api` | ASGI server |
| `click` | latest | `web-cli` | CLI argument parsing and command routing |
| `tavily-python` | latest | manual | `TavilySearchProvider` (WEB.SEARCH_WEB) |
| `pytest` | latest | `dev` | Test runner |
| `pytest-httpx` | latest | `dev` | HTTP mocking in unit and integration tests |
| `pytest-cov` | latest | `dev` | Coverage measurement |
