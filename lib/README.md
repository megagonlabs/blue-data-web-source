# blue-data-web-source — Library

Core Python library. Provides `WebDataSource`, `WebPipeline`, and all supporting components for fetching, extracting, chunking, embedding, and searching web pages.

## Install

```bash
# from the blue-data-web-source repo root, siblings cloned beside it:
pip install -e ../blue-data/lib
pip install -e ../blue-data-doc-source/lib
pip install -e lib
```

> The sibling packages live in each repo's `lib/` subdirectory, so the paths end in `/lib`. Install the siblings first so `lib`'s requirements are already satisfied. Replace `../blue-data` and `../blue-data-doc-source` with the actual paths if your layout differs.

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
- **Saved real HTML** — `data/fixtures/npr_article.html` is a real news article. As third-party content it is not committed to the repo (see the README "Disclosures"); fetch it with `python scripts/fetch_fixtures.py`. Tests assert on exact title, date, domain, and absence of nav fragments, and skip when the fixture is absent

## Docs

- [Quickstart](../docs/quickstart.md)
- [Installation](../docs/installation.md)
- [Python API Reference](../docs/api-reference.md)
- [Full docs index](../docs/README.md)
