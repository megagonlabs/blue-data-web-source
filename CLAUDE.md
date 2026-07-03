# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e /path/to/blue-data
pip install -e /path/to/blue-data-doc-source
pip install -e lib/
pip install pytest pytest-httpx
```

## Tests

```bash
# Unit tests (no external services required)
.venv/bin/pytest lib/tests/unit/ -v

# Single file
.venv/bin/pytest lib/tests/unit/test_pipeline.py -v

# Integration tests (no network — uses pytest-httpx to mock HTTP, trafilatura runs for real)
.venv/bin/pytest lib/tests/integration/ -v -m integration
```

## Running the example

```bash
python examples/ingest_url.py https://en.wikipedia.org/wiki/Python_(programming_language)
python examples/ingest_url.py https://example.com/article --collection news
```

## Architecture

The pipeline is a strict one-way chain of single-responsibility layers:

```
URL
  → fetcher/      — WebFetcher: httpx GET with redirect following; raises on non-2xx
  → extractor/    — WebExtractor: trafilatura strips boilerplate, returns main text + metadata;
                    returns None when no body is extractable (nav-only, login walls, etc.)
  → chunkers/     — SectionChunker (default) detects headers and chunks within sections;
                    WindowChunker is the word-based sliding window fallback;
                    both imported from blue-data-doc-source
  → enrichment/   — Enricher ABC; BasicEnricher does regex entity/keyword extraction (optional);
                    imported from blue-data-doc-source
  → embeddings/   — EmbeddingModel ABC with dim property — imported from blue-data-doc-source
  → record/       — WebChunkRecord dataclass: canonical chunk schema with web provenance fields
  → pipeline/     — WebPipeline orchestrates all layers; entry point for library callers
  → web_source/   — WebDataSource extends blue-data DataSource; top-level public entry point
```

**`WebDataSource`** is the top-level public entry point. It extends blue-data's `DataSource`
abstraction and dispatches on `op` codes: `WEB.INGEST`, `WEB.SEARCH`, `WEB.ENSURE_INDEX`.
It owns a single `WebPipeline` instance reused across calls.

**`WebPipeline`** orchestrates the ingestion chain. `ingest(url)` returns `List[Dict]` and
optionally upserts to a vector store. Returns `[]` when the extractor yields no content.
`search(query)` embeds the query and delegates KNN to the injected `VectorSource`.
`ensure_index(collection)` calls `VectorSource.create_index` with the correct embedding dim.

**`WebFetcher`** wraps `httpx.Client`. It follows redirects and sets a `User-Agent`. The
`final_url` on `FetchResult` (after redirects) is used as the canonical URL for deduplication.

**`WebExtractor`** wraps `trafilatura.extract()` and `trafilatura.extract_metadata()`.
Returns `None` when trafilatura finds no extractable body — the pipeline returns `[]` in
that case rather than storing empty chunks.

**`VectorSource`** is blue-data's abstraction. The pipeline accepts any `VectorSource`
implementation (e.g. `RedisVectorSource`) — no adapter layer needed.

## Key design constraints

- `chunk_id` is derived from `sha256(final_url)[:16]` — re-ingesting the same URL produces
  the same chunk IDs, so vector store upsert overwrites rather than duplicates.
- `WebExtractor.extract()` returns `None` for pages with no extractable main content;
  `WebPipeline.ingest()` returns `[]` in that case — never stores empty chunks.
- Chunkers and embedding models are **reused from `blue-data-doc-source`**, not reimplemented.
  The `EmbeddingModel` ABC requires a `dim` property used by `ensure_index` and validated
  against each embedding's length in `DocumentPipeline` (the upstream check).
- `description` from page metadata is attached to chunk 0 only — not repeated on every chunk.
- `WebChunkRecord.to_dict()` omits `None` optional fields rather than serialising them as null.
- Call `WEB.ENSURE_INDEX` (or `pipeline.ensure_index()`) before the first ingest when using
  a vector store — the pipeline does not auto-create the index.
- `trafilatura` is a hard dependency of this package (not optional). If it is not installed,
  importing `WebExtractor` raises `ImportError` with an install hint.
