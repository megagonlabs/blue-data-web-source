# blue-data-web-source — Coding Style

## Tools

| Tool | Role | Config |
|------|------|--------|
| `ruff` | Linter + formatter (replaces Black, isort, flake8) | `lib/pyproject.toml` — `[tool.ruff]` / `[tool.ruff.lint]` |
| `pyright` (strict) | Static type checker | `lib/pyproject.toml` — `[tool.pyright]` |
| `pytest` | Test runner | `lib/pyproject.toml` — `[tool.pytest.ini_options]` |

All three are declared as `dev` dependencies. CI fails on any lint, type, or test failure.

---

## Python Version and Annotations

- Supported Python: **3.12 and 3.13** (`requires-python = ">=3.12,<3.14"`)
- All files begin with `from __future__ import annotations` to enable PEP 604 union syntax (`X | Y`) and postponed annotation evaluation in ABCs.

---

## Linting and Formatting

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]
```

- `ruff format` is the formatter (Black-compatible, 100-character lines).
- `ruff check --fix` auto-corrects safe lint violations.
- Imports are sorted by `ruff` with `I` rules: stdlib → third-party → local, alphabetical within each group.

---

## Type Annotations

- **All** public functions and methods carry full type annotations (parameters + return type).
- `pyright --strict` must pass with zero errors.
- Use `X | None` (not `Optional[X]`) with `from __future__ import annotations`.
- Use `list[X]` / `dict[K, V]` / `tuple[X, ...]` (not `List`, `Dict`, `Tuple`).
- `Any` is allowed only where the concrete type is genuinely unknown (e.g. JSON-LD payloads, op dict values).

---

## Synchronous Pipeline

The core library is **synchronous**. `WebPipeline`, `WebFetcher`, `WebExtractor`, and all chunkers and enrichers are synchronous. There is no `async`/`await` in the library layer.

Concurrent multi-URL ingestion is achieved with `ThreadPoolExecutor` in `WebPipeline.ingest_many()` — not via `asyncio`. The REST API (`platform/web-api/`) uses FastAPI with async route handlers, but handlers call synchronous pipeline methods directly.

---

## Naming

| Kind | Convention | Example |
|------|-----------|---------|
| Modules | `snake_case` | `web_extractor.py`, `metadata_extractor.py` |
| Classes | `PascalCase` | `WebPipeline`, `WebChunkRecord`, `WebFetcher` |
| Functions / methods | `snake_case` | `ingest_many`, `extract_metadata` |
| Constants | `UPPER_SNAKE_CASE` | `_NAV_FRAGMENT_PATTERNS`, `BLUE_WEB_COLLECTION` |
| Private helpers | `_leading_underscore` | `_strip_nav_fragments`, `_build_source` |
| Type aliases | `PascalCase` | `OpDict = dict[str, Any]` |

Op code strings follow a `NAMESPACE.VERB` pattern in `UPPER_CASE`: `WEB.INGEST`, `WEB.SEARCH`, `WEB.CRAWL`, `WEB.ENSURE_INDEX`.

---

## Abstract Base Classes

- Every extension point exposes an ABC in a `base.py` or inline in its module.
- ABCs use `abc.ABC` + `@abstractmethod`. No shared initialization in ABCs unless genuinely required.
- Concrete implementations live in separate files (`web_fetcher.py`, `web_extractor.py`, etc.).
- ABCs are exported from the package `__init__.py`.

---

## Deferred Imports for Optional Extras

Optional dependencies must **not** be imported at module top-level. They are deferred-imported at call time and fail with an `ImportError` + install hint when absent:

| Package | Deferred in | Extra |
|---------|------------|-------|
| `feedparser` | `FeedParser.parse()` | `[feed]` |
| `openai` | `StructuredFieldExtractor`, `LLMEnricher` | `[openai]` |
| `anthropic` | `NLQueryParser._get_client()` | `[llm]` |
| `tavily` | `TavilySearchProvider.search()` | manual install |

`httpx` and `trafilatura` are hard dependencies — imported at module top-level.

---

## Docstrings

- Single-line docstring for any public method whose name does not make its contract obvious.
- No multi-line docstrings unless a subtlety is non-obvious from the signature.
- Do **not** document what the code does — document **why** or **when** if surprising.

```python
def ingest(self, url: str, collection: str = "web") -> list[dict]:
    """Return [] when the extractor finds no main content (login wall, nav-only page)."""
```

---

## Error Handling

- Unknown ops passed to `execute_query` raise `ValueError`.
- HTTP errors from `WebFetcher` raise `httpx.HTTPStatusError` (non-2xx) or `httpx.RequestError` (network failure). Do not catch these at the library layer — let callers decide how to handle them.
- Do **not** catch and re-raise `Exception` broadly. Let unexpected errors propagate.
- Only catch at system boundaries (op dispatch in `execute_query`, route handlers, CLI command functions) and convert to HTTP status codes or CLI exit codes.
- Pages that fail to fetch or extract during `ingest_many` / `WEB.CRAWL` are skipped silently and their URLs are reported in the response — they do not abort the batch.

---

## File Layout

Each module follows this order:

1. `from __future__ import annotations`
2. stdlib imports
3. third-party imports
4. local imports
5. module-level constants / type aliases
6. class or function definitions

---

## Testing Style

- Test file names: `test_{module_name}.py`.
- Test function names: `test_{what}_{condition}`.
- HTTP is mocked via `pytest-httpx` (`httpx_mock` fixture) — never makes real network calls in unit or integration tests.
- `WebFetcher` is patched with `MagicMock` in pure unit tests; real HTML fixtures in `data/fixtures/` are used for integration tests via `pytest-httpx`.
- Never mock internal collaborators — use real in-process doubles (`MemoryVectorSource`, `DummyEmbeddingModel`).
- New HTML fixtures: commit only synthetic, first-party HTML. Third-party pages are not
  redistributed — register them in `scripts/fetch_fixtures.py` and the README
  "Disclosures" table, git-ignore the saved file, and `pytest.skip` the test when absent.
