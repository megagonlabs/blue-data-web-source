# blue-data-web-source — Overview

## Objective

Web pages are noisy, structurally heterogeneous, and built for browsers — not for AI agents. Getting usable content out of a URL requires navigating redirects, stripping ads and navigation, discarding pages with no extractable body, and cleaning up CMS cross-reference fragments that CMSes inject into article text. Without a pipeline purpose-built for web content, agents either receive raw HTML soup or miss relevant passages buried in boilerplate.

blue-data-web-source is the web acquisition layer for Blue Data Intelligence. It sits between live URLs and Blue's vector store, converting web pages into structured, provenance-aware chunk records that AI agents can search, cite, and reason over. It is a peer to `blue-data-doc-source` — both extend blue-data's `DataSource` abstraction — and reuses doc-source's chunkers, enrichers, and embedding models, adding HTTP fetch and HTML extraction as the front-end stage unique to web content.

## What blue-data-web-source Does

- **Fetch** pages via HTTP with redirect following, configurable timeout, and a well-behaved `User-Agent`; raise on non-2xx responses
- **Extract** the main article body using trafilatura — strips navigation, ads, footers, and comments; returns `None` for pages with no extractable body (login walls, pure navigation, redirect pages)
- **Strip** inline CMS navigation fragments — lines beginning with `Related Story:`, `Read more:`, `See also:`, `Also read:`, `Advertisement`, or `Story continues` — removed after extraction before text reaches the chunker
- **Chunk** extracted text with `SectionChunker` (default) or any `Chunker` subclass imported from `blue-data-doc-source`; preserves `section_title` on every chunk
- **Enrich** each chunk with `BasicEnricher` or any custom `Enricher` from `blue-data-doc-source` (optional)
- **Embed** chunks with `OpenAIEmbeddingModel`, `DummyEmbeddingModel`, or any `EmbeddingModel` subclass from `blue-data-doc-source` (optional)
- **Store** embedded chunks in any blue-data `VectorSource` backend via URL-hash-based upsert — re-ingesting the same URL overwrites rather than duplicates
- **Search** the vector store with a natural-language query: embed the query, run KNN, return ranked chunk records with full web provenance
- **Crawl** sites using BFS from a seed URL — configurable depth and page cap, same-domain by default, fragment deduplication, skips failed pages
- **Search the live web** via `WebSearchProvider` — query a search API for URLs, ingest the results through the full pipeline, return semantically ranked chunks
- **Extract structured fields** from any URL using `WEB.EXTRACT` with a JSON Schema or Pydantic model — typed data without manual HTML parsing
- **Deduplicate across URLs** — when a `FreshnessStore` is set, identical text fetched from two different URLs is detected via content hash; the second ingest is skipped silently
- **Enrich with structured metadata** — `MetadataExtractor` parses JSON-LD, OpenGraph, and standard `<meta>` tags in one pass; adds `schema_type`, `keywords`, `publisher`, and `canonical_url` to every chunk; fills in `author`/`date`/`description` when trafilatura returns `None`
- **Fetch concurrently** — `ingest_many` and BFS crawl both use a `ThreadPoolExecutor`; per-domain politeness locks prevent hammering a single host
- **Expose** all operations via blue-data's `DataSource` interface (`WEB.INGEST`, `WEB.EXTRACT`, `WEB.CRAWL`, `WEB.SEARCH_WEB`, `WEB.SEARCH`, `WEB.INGEST_FEED`, `WEB.ENSURE_INDEX`) and a natural-language query interface backed by Claude tool use

## Design Philosophy

**Peer to doc-source, not a subclass** — `WebDataSource` extends blue-data's `DataSource` directly, the same as `DocumentDataSource`. Web-source reuses chunkers, embedders, and enrichers from doc-source via import, but the acquisition stage (HTTP fetch + HTML extraction) is unique to web content and does not fit the file-parsing abstraction. The two sources are siblings in the blue-data ecosystem.

**Extraction returns all or nothing** — `WebExtractor.extract()` either returns a result with non-empty body text, or returns `None`. There is no partial extraction. A `None` result causes `WebPipeline.ingest()` to return an empty list — no empty or near-empty chunks are ever stored.

**URL-hash identity** — `chunk_id` is derived from `sha256(final_url)[:16]`, where `final_url` is the URL after all redirects. The same URL always produces the same chunk IDs. Re-ingestion is a safe upsert at the vector store level, never a duplicate.

**Search backends are pluggable** — `WebSearchProvider` is an ABC with a single `search(query, k) -> list[str]` method. `TavilySearchProvider` is the built-in implementation; any search API can be wired in by subclassing.

**VectorSource directly, no adapter** — the pipeline calls `VectorSource.upsert` and `VectorSource.knn` directly. Any `VectorSource` from blue-data works without an adapter layer. Adding a new vector backend to blue-data makes it immediately available here.
