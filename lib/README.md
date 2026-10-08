# blue-data-web-source — Library

Core Python library. Provides `WebDataSource`, `WebPipeline`, and all supporting components for fetching, extracting, chunking, embedding, and searching web pages.

## Install

```bash
pip install -e /path/to/blue-data
pip install -e /path/to/blue-data-doc-source
pip install -e .
```

> Replace `/path/to/blue-data` and `/path/to/blue-data-doc-source` with the paths where you cloned those repos.

Optional extras: `[openai]`, `[redis]`, `[llm]`, `[feed]`, `[all]`.

## Testing

```bash
# Unit tests (no external services or network required)
.venv/bin/pytest lib/tests/unit/ -v

# Integration tests (HTTP mocked via pytest-httpx, trafilatura runs for real)
.venv/bin/pytest lib/tests/integration/ -v -m integration

# End-to-end scenario tests (no network, no external services)
.venv/bin/pytest lib/tests/e2e/ -v -m e2e

# All tests
.venv/bin/pytest lib/tests/ -v
```

| Layer | Location | What it tests |
|---|---|---|
| Unit | `lib/tests/unit/` | Each component in isolation — fetcher, extractor, pipeline, web source, feed parser; all I/O mocked |
| Integration | `lib/tests/integration/` | Full fetch → extract → chunk chain; only HTTP is mocked, trafilatura runs for real |
| E2E | `lib/tests/e2e/` | Full stack through `WebDataSource.execute_query` — all ops in realistic scenarios; uses `MemoryVectorSource` and `DummyEmbeddingModel` |
| Repo | `lib/tests/test_example_output.py`, `lib/tests/test_platform_boundaries.py` | Example scripts match their `.expected` output; platform services stay thin adapters over `lib/` |

Integration tests use two kinds of fixtures:

- **Synthetic HTML** — hand-crafted article HTML covering known extraction behaviour
- **Saved real HTML** — `data/fixtures/npr_article.html` is a real news article committed to the repo; tests assert on exact title, date, domain, and absence of nav fragments

## Docs

- [Quickstart](../docs/quickstart.md)
- [Installation](../docs/installation.md)
- [Python API Reference](../docs/api-reference.md)
- [Full docs index](../docs/README.md)
