# blue-data-web-source — Testing Strategy

## Overview

`blue-data-web-source` uses three test tiers:

- **Unit tests** — run on every push; all I/O boundaries are doubled; no external services
- **Integration tests** — HTTP mocked via `pytest-httpx`; trafilatura runs against real HTML; no external services
- **E2E tests** — full stack through `WebDataSource.execute_query`; `MemoryVectorSource` and `DummyEmbeddingModel`; no external services

Platform adapter tests (REST API, CLI) are a fourth category — they test the translation layer rather than the core pipeline, and live under each platform's own `tests/` directory.

---

## 1. Tooling

| Tool | Purpose |
|------|---------|
| `pytest` | Test runner |
| `pytest-httpx` | Intercept and mock `httpx.Client` HTTP requests in unit and integration tests |
| `pytest-cov` | Coverage measurement (reported, not enforced) |

All tools are declared as `dev` dependencies in `lib/pyproject.toml`.

---

## 2. Test Layout

```
lib/tests/
├── conftest.py                       # shared fixtures: mock WebFetcher, MemoryVectorSource, DummyEmbeddingModel
├── unit/
│   ├── test_fetcher.py               # WebFetcher: redirect following, status errors, User-Agent
│   ├── test_extractor.py             # WebExtractor: extraction, nav-fragment stripping, None cases
│   ├── test_metadata.py              # MetadataExtractor: JSON-LD, OpenGraph, meta tags, canonical
│   ├── test_pipeline.py              # WebPipeline: ingest, ingest_many, search, ensure_index
│   ├── test_web_source.py            # WebDataSource: op dispatch, all 11 ops
│   ├── test_web_find.py              # WEB.FIND, WEB.FIND_THEN_SEARCH, WEB.GRAPH_* ops
│   ├── test_crawler.py               # WebCrawler: BFS, robots.txt, rate limiting, retry
│   ├── test_feed.py                  # FeedParser: RSS, Atom, date filtering, missing fields
│   ├── test_freshness.py             # FreshnessStore: skip unchanged, cross-URL dedup
│   ├── test_search_web.py            # WEB.SEARCH_WEB: provider call, ingest, rank
│   └── test_nl.py                    # NLQueryParser: tool dispatch, op construction
├── integration/
│   └── test_pipeline_integration.py  # fetch → extract → chunk; trafilatura runs for real
├── e2e/
│   └── test_e2e.py                   # full WebDataSource.execute_query across all ops
└── benchmarks/
    ├── bench_extraction.py           # throughput: pages/sec through WebExtractor
    ├── bench_pipeline.py             # throughput: pages/sec through full WebPipeline
    ├── datasets.py                   # benchmark HTML fixtures
    ├── runner.py                     # benchmark harness
    └── benchmarks.md                 # recorded baseline results
```

Platform tests:

```
platform/web-api/tests/
├── conftest.py      # mock_source fixture (MagicMock WebDataSource); TestClient fixture
└── test_web.py      # 38 tests: route dispatch, request validation, error mapping

platform/web-cli/tests/
└── test_cli.py      # 48 tests: Click CliRunner; command → execute_query call args

platform/web-mcp/tests/
└── test_server.py   # MCP tool handlers; patched WebDataSource singleton
```

---

## 3. I/O Test Doubles

### WebFetcher mock

Unit tests replace `WebFetcher` with a `MagicMock` that returns pre-built `FetchResult` instances. This decouples all unit tests from the network and from `httpx`.

```python
# tests/conftest.py
@pytest.fixture
def mock_fetcher():
    fetcher = MagicMock()
    fetcher.fetch.return_value = FetchResult(
        url="https://example.com/article",
        final_url="https://example.com/article",
        html="<html><body><p>Article text.</p></body></html>",
        status_code=200,
        content_type="text/html",
    )
    return fetcher
```

Integration tests use `pytest-httpx` to intercept `httpx.Client.get` at the network boundary, returning real or fixture HTML without making a live connection.

### VectorSource

Unit and E2E tests use `MemoryVectorSource` from `blue-data`. It requires no setup, holds state in-process, and is discarded after each test. The same code path that runs against Redis in production runs against `MemoryVectorSource` in tests.

### EmbeddingModel

`DummyEmbeddingModel` from `blue-data-doc-source` produces deterministic fixed-dimension vectors from a SHA-256 hash of the input text. It requires no API key and runs in microseconds. Embedding similarity scores in tests using `DummyEmbeddingModel` are not meaningful — tests assert on structure and call behaviour, not on ranking quality.

---

## 4. Unit Tests

Unit tests cover each module in isolation. All I/O is mocked. Tests assert on:

- Return value structure and field presence
- Call arguments passed to dependencies
- Error conditions and edge cases (empty body, missing fields, non-2xx status)

**Example — pipeline skips storage when extractor returns None:**

```python
def test_ingest_returns_empty_when_no_content(mock_fetcher, mock_vector_source):
    mock_fetcher.fetch.return_value = FetchResult(
        url="https://example.com/nav",
        final_url="https://example.com/nav",
        html="<html><nav>Navigation only</nav></html>",
        status_code=200,
        content_type="text/html",
    )
    pipeline = WebPipeline(
        embedding_model=DummyEmbeddingModel(),
        vector_source=mock_vector_source,
    )
    pipeline._fetcher = mock_fetcher
    result = pipeline.ingest("https://example.com/nav")
    assert result == []
    mock_vector_source.upsert.assert_not_called()
```

**Example — chunk ID is deterministic:**

```python
def test_chunk_id_is_deterministic(pipeline, mock_fetcher):
    records_a = pipeline.ingest("https://example.com/article")
    records_b = pipeline.ingest("https://example.com/article")
    assert records_a[0]["chunk_id"] == records_b[0]["chunk_id"]
```

---

## 5. Integration Tests

Integration tests exercise the full fetch → extract → chunk chain. HTTP is mocked via `pytest-httpx` — no live network connection is made. `trafilatura` runs for real against the HTML returned by the mock, so the tests verify actual extraction behaviour rather than a stub.

Two fixture categories:

**Synthetic HTML** — hand-crafted articles covering specific extraction scenarios:
- Title, author, date present vs absent
- Nav-fragment lines in article body
- Pages that trafilatura returns `None` for

**Saved real HTML** — `data/fixtures/npr_article.html` is a real news article. It is
third-party content, so it is **not** committed to the repo (see the README
"Disclosures" section): it is git-ignored and downloaded on demand with
`python scripts/fetch_fixtures.py`. Integration tests assert on the exact title, date,
domain, content keywords, and absence of nav fragments from the known fixture, and
`pytest.skip` themselves when the fixture has not been fetched.

```python
@pytest.mark.integration
def test_npr_article_extraction(httpx_mock):
    npr_html = Path("data/fixtures/npr_article.html").read_text()
    httpx_mock.add_response(url=NPR_URL, text=npr_html)

    pipeline = WebPipeline()
    records = pipeline.ingest(NPR_URL)

    assert len(records) > 0
    assert records[0]["domain"] == "text.npr.org"
    assert "China" in records[0]["title"]
    assert not any("Read more:" in r["text"] for r in records)
```

When adding a new fixture: if the page is third-party content, add it to the
`_FIXTURES` table in `scripts/fetch_fixtures.py` and the README "Disclosures > Datasets"
table, git-ignore the saved file, and make the test `pytest.skip` when it is absent —
do **not** commit the HTML. Only synthetic, first-party HTML should be committed under
`data/fixtures/`.

---

## 6. E2E Tests

E2E tests exercise the full stack through `WebDataSource.execute_query`. All eleven ops are covered. `WebFetcher` is replaced with a mock; all other components (`WebExtractor`, `MetadataExtractor`, `SectionChunker`, `DummyEmbeddingModel`, `MemoryVectorSource`) run for real.

E2E tests verify:
- Op dispatch routes to the correct pipeline method
- Round-trip: ingest a URL, search, get that URL's chunk back
- Freshness: re-ingest unchanged URL returns `[]`
- Crawl: seed + discovered links are all ingested
- Feed: `since` filtering excludes old entries

```python
@pytest.mark.e2e
def test_ingest_then_search_round_trip(source, mock_fetcher):
    source._pipeline._fetcher = mock_fetcher
    source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": "test"})
    source.execute_query({"op": "WEB.INGEST", "url": NPR_URL, "collection": "test"})
    results = source.execute_query({"op": "WEB.SEARCH", "query": "missile test", "collection": "test", "k": 3})
    assert len(results) > 0
    assert any(NPR_URL in r["url"] for r in results)
```

---

## 7. Platform Adapter Tests

Each platform adapter is tested independently. These are unit tests — they use a `MagicMock` `WebDataSource` rather than the real one, so they exercise the translation layer (request parsing, response serialisation, error mapping) in isolation.

### REST API (`platform/web-api/tests/`)

Uses FastAPI's `TestClient`. The `WebDataSource` singleton is replaced with a `MagicMock` via `conftest.py`. Tests assert on:
- HTTP status codes (200, 422, 400)
- Request fields passed to `execute_query`
- Response JSON structure

```python
def test_ingest_passes_url_to_execute_query(client, mock_source):
    mock_source.execute_query.return_value = [_CHUNK]
    client.post("/web/ingest", json={"url": "https://example.com/article"})
    call_arg = mock_source.execute_query.call_args[0][0]
    assert call_arg["op"] == "WEB.INGEST"
    assert call_arg["url"] == "https://example.com/article"
```

### CLI (`platform/web-cli/tests/`)

Uses Click's `CliRunner`. The `WebDataSource` singleton is patched via `unittest.mock.patch`. Tests assert on:
- Exit codes
- `execute_query` call arguments
- Stdout output format (plain text and `--json`)

---

## 8. NL Interface Tests

`NLQueryParser` tests mock `anthropic.Anthropic` via `unittest.mock.patch` to avoid live API calls. Tests assert that specific natural language inputs produce the expected `WEB.*` op dict via the tool dispatch logic.

```python
def test_ingest_url_instruction(mock_anthropic):
    mock_anthropic.return_value.messages.create.return_value = _tool_response(
        "ingest_url", {"url": "https://example.com/article"}
    )
    parser = NLQueryParser()
    op = parser.parse("ingest https://example.com/article")
    assert op == {"op": "WEB.INGEST", "url": "https://example.com/article"}
```

---

## 9. Coverage

Coverage is measured via `pytest-cov`. No minimum threshold is enforced.

```toml
# lib/pyproject.toml
[tool.pytest.ini_options]
testpaths = ["lib/tests/unit"]
markers = [
    "integration: end-to-end tests requiring external services (Redis, network)",
]
```

---

## 10. Running Tests

```bash
# Unit tests only (default; no external services)
.venv/bin/pytest lib/tests/unit/ -v

# Integration tests (no network; trafilatura runs for real)
.venv/bin/pytest lib/tests/integration/ -v -m integration

# E2E tests (no external services)
.venv/bin/pytest lib/tests/e2e/ -v -m e2e

# All lib tests
.venv/bin/pytest lib/tests/ -v

# REST API adapter tests
.venv/bin/pytest platform/web-api/tests/ -v

# CLI adapter tests
.venv/bin/pytest platform/web-cli/tests/ -v

# All tests across lib and platforms (run separately to avoid conftest conflicts)
.venv/bin/pytest lib/tests/ -v
.venv/bin/pytest platform/web-api/tests/ -v
.venv/bin/pytest platform/web-cli/tests/ -v
```
