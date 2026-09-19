# blue-data-web-source — Repo Organization

## Directory Tree

```
blue-data-web-source/
│
├── .gitignore                           # excludes .venv/, __pycache__/, build/, dist/, .env, *.envrc
├── CLAUDE.md                            # Claude Code instructions for this repo
├── LICENSE                              # BSD 3-Clause with Attribution / NOTICE Requirement
├── README.md                            # project overview, quick start, ops reference
├── presentation.md                      # slide-form overview (internal)
│
├── design/                              # design documents (numbered sequence + design-process.md)
│
├── docs/                                # user-facing documentation
│   ├── architecture.md                  # pipeline layers and component responsibilities
│   ├── crawling.md                      # WEB.CRAWL and WEB.SEARCH_WEB reference
│   ├── extraction.md                    # WebExtractor and MetadataExtractor reference
│   ├── extending.md                     # extension points: WebSearchProvider, custom models
│   ├── features.md                      # feature-by-feature documentation with code examples
│   ├── guide.md                         # quick start and how-to guide
│   ├── installation.md                  # install instructions, extras, external deps
│   └── mcp.md                           # MCP server setup and tool reference
│
├── data/
│   └── fixtures/
│       └── npr_article.html             # saved real article HTML for integration tests
│
├── tests/                               # cross-platform boundary tests
│
├── examples/                            # runnable demos (no API key required for most)
│   ├── demo.py                          # full pipeline demo: ingest → embed → search (offline)
│   ├── demo_web.py                      # end-to-end demo with optional real URLs and --nl-query
│   ├── ingest_url.py                    # minimal live ingest script
│   ├── mock_fetcher.py                  # shared offline helper: fixture HTML + mock WebFetcher
│   ├── README.md
│   ├── scenario-demos/
│   │   ├── extraction_demo.py           # extractor fields, boilerplate stripping, None cases
│   │   └── crawl_vs_single_demo.py      # WEB.INGEST vs WEB.CRAWL comparison
│   ├── feature-demos/
│   │   ├── extractor_demo.py            # WebExtractor fields across 4 page types
│   │   └── content_addressing_demo.py   # URL-hash chunk IDs, idempotent re-ingest
│   ├── integration-demos/
│   │   ├── multi_url_demo.py            # batch ingest with urls list
│   │   ├── crawler_demo.py              # BFS depth/max_pages/same_domain_only experiments
│   │   └── search_web_demo.py           # WEB.SEARCH_WEB via Tavily
│   ├── framework-demos/
│   │   ├── langgraph_rag_demo.py        # two-node RAG graph: retrieve → generate
│   │   └── mcp_demo.py                  # LLM agent calling web-mcp tools
│   └── backends/
│       ├── memory_quickstart.py         # MemoryVectorSource + DummyEmbeddingModel, offline
│       ├── redis_backend_demo.py        # RedisVectorSource as persistent backend
│       └── openai_embeddings_demo.py    # semantic quality: OpenAI vs DummyEmbeddingModel
│
├── lib/                                 # Python library (blue-data-web-source PyPI package)
│   ├── pyproject.toml                   # package metadata, dependencies, extras, tool config
│   ├── src/
│   │   └── blue_data_web_source/
│   │       ├── __init__.py              # re-exports WebDataSource, FileFreshnessStore, MemoryFreshnessStore
│   │       └── source/
│   │           └── web/
│   │               ├── web_source.py    # WebDataSource — entry point, op dispatch
│   │               ├── pipeline.py      # WebPipeline — full ingest chain orchestrator
│   │               ├── fetcher.py       # WebFetcher — httpx HTTP client; FetchResult dataclass
│   │               ├── extractor.py     # WebExtractor — trafilatura wrapper; ExtractResult dataclass
│   │               ├── metadata.py      # MetadataExtractor — JSON-LD/OG/meta parser; StructuredMetadata dataclass
│   │               ├── record.py        # WebChunkRecord — canonical chunk schema; to_dict()
│   │               ├── crawler.py       # WebCrawler — BFS crawl; robots.txt; rate limiting
│   │               ├── feed.py          # FeedParser — RSS/Atom; FeedEntry/FeedParseResult dataclasses
│   │               ├── freshness.py     # FreshnessStore ABC; MemoryFreshnessStore; FileFreshnessStore
│   │               ├── search_provider.py  # WebSearchProvider ABC; TavilySearchProvider
│   │               └── nl.py            # NLQueryParser — Claude tool use → WEB.* op dict
│   └── tests/
│       ├── conftest.py                  # shared fixtures: mock WebFetcher, MemoryVectorSource, DummyEmbeddingModel
│       ├── unit/                        # per-module unit tests; all I/O mocked
│       │   ├── test_fetcher.py
│       │   ├── test_extractor.py
│       │   ├── test_metadata.py
│       │   ├── test_pipeline.py
│       │   ├── test_web_source.py
│       │   ├── test_crawler.py
│       │   ├── test_feed.py
│       │   ├── test_freshness.py
│       │   ├── test_search_web.py
│       │   └── test_nl.py
│       ├── integration/                 # HTTP mocked via pytest-httpx; trafilatura runs for real
│       │   └── test_pipeline_integration.py
│       ├── e2e/                         # full stack through WebDataSource.execute_query; no network
│       │   └── test_e2e.py
│       └── benchmarks/                  # extraction and pipeline throughput benchmarks
│           ├── bench_extraction.py
│           ├── bench_pipeline.py
│           ├── datasets.py
│           ├── runner.py
│           └── benchmarks.md
│
└── platform/                            # external interface services
    ├── web-mcp/                         # MCP server (blue-data-web-mcp)
    │   ├── pyproject.toml
    │   ├── src/
    │   │   └── web_mcp/
    │   │       └── server.py            # fastmcp server; 8 MCP tools; WebDataSource singleton
    │   └── tests/
    │       └── test_server.py
    ├── web-cli/                         # CLI (blue-data-web-cli PyPI package)
    │   ├── pyproject.toml
    │   ├── src/
    │   │   └── blue_data_web_cli/
    │   │       ├── main.py              # Click command groups; all CLI commands
    │   │       └── config.py            # WebDataSource singleton from BLUE_WEB_* env vars
    │   └── tests/
    │       └── test_cli.py
    └── web-api/                         # REST API (blue-data-web-api PyPI package)
        ├── pyproject.toml
        ├── src/
        │   └── web_api/
        │       ├── main.py              # FastAPI app; lifespan; uvicorn entry point (port 8002)
        │       ├── config.py            # WebDataSource singleton from BLUE_WEB_* env vars
        │       └── routers/
        │           └── web.py           # /web/* routes; Pydantic request models
        └── tests/
            ├── conftest.py              # mock_source and TestClient fixtures
            └── test_web.py
```

---

## Layout Rationale

### `lib/` — the Python library

All core business logic lives under `lib/src/`. This is the only artifact published to PyPI as `blue-data-web-source`. The `src/` layout prevents the package from being accidentally importable from the project root during development.

### Module-to-architecture mapping

| Module | Responsibility |
|--------|---------------|
| `web_source.py` | `WebDataSource` — public entry point; dispatches on `op` field |
| `pipeline.py` | `WebPipeline` — orchestrates fetch → extract → chunk → enrich → embed → upsert |
| `fetcher.py` | `WebFetcher` — HTTP GET with redirect following; `FetchResult` dataclass |
| `extractor.py` | `WebExtractor` — trafilatura wrapper; nav-fragment stripping; `ExtractResult` dataclass |
| `metadata.py` | `MetadataExtractor` — single-pass JSON-LD/OG/meta parser; `StructuredMetadata` dataclass |
| `record.py` | `WebChunkRecord` — canonical output schema; `to_dict()` omits None fields |
| `crawler.py` | `WebCrawler` — BFS traversal; robots.txt; per-domain rate limiting; retry backoff |
| `feed.py` | `FeedParser` — feedparser wrapper; `FeedEntry`/`FeedParseResult` dataclasses |
| `freshness.py` | `FreshnessStore` ABC; `MemoryFreshnessStore`; `FileFreshnessStore` |
| `search_provider.py` | `WebSearchProvider` ABC; `TavilySearchProvider` |
| `nl.py` | `NLQueryParser` — Claude tool use; maps natural language to `WEB.*` op dicts |

### `platform/` — external interface services

Each external interface is an independent deployable unit under `platform/`. They contain no business logic — each is a thin adapter over `WebDataSource`. Separating them from `lib` keeps the library free of server and CLI dependencies and allows each service to be versioned and deployed independently.

| Service | Package | Status | Entry point |
|---------|---------|--------|------------|
| `web-mcp/` | — | Implemented | `python3 platform/web-mcp/src/web_mcp/server.py` |
| `web-cli/` | `blue-data-web-cli` | Implemented | `blue-web` CLI command |
| `web-api/` | `blue-data-web-api` | Implemented | `blue-data-web-api` or `uvicorn web_api.main:app --port 8002` |

All three read the same `BLUE_WEB_*` environment variables and build a `WebDataSource` singleton via their respective `config.py`.

### `tests/`

Library tests live under `lib/tests/`. Each platform service has its own `tests/` directory for adapter-layer tests.

```
lib/tests/
├── conftest.py      # shared fixtures: mock WebFetcher, MemoryVectorSource, DummyEmbeddingModel
├── unit/            # per-module tests; all network I/O mocked
├── integration/     # HTTP mocked via pytest-httpx; trafilatura runs for real against real HTML
├── e2e/             # full stack through WebDataSource.execute_query; MemoryVectorSource backend
└── benchmarks/      # throughput benchmarks for extraction and pipeline
```

Each platform's `tests/` directory defines its own fixtures independently — platform tests exercise a different entry point (HTTP routes, Click commands) rather than calling `WebDataSource` directly.
