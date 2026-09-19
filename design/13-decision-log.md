# blue-data-web-source — Decision Log

Each entry records a significant design decision: what was decided, why, and what consequences follow.

---

## ADR-001 — URL SHA-256 hash as chunk ID prefix

**Date:** 2025-Q1  
**Status:** Accepted

**Decision:** `page_id` is `"web_" + sha256(final_url.encode())[:16]` — the literal prefix `web_` followed by the first 16 hex characters of the URL hash. `chunk_id` follows `{page_id}_chunk_{idx:04d}`. The `final_url` (after redirect resolution) is the canonical identity of a page.

**Rationale:** Content-addressable IDs make re-ingest idempotent. The same URL always produces the same chunk IDs, so vector store upsert overwrites rather than duplicates. No external ID registry is needed. Using `final_url` (not the input URL) ensures that redirect chains always resolve to the same identity.

**Consequences:**
- The same content served at two different URLs produces different chunk IDs — each URL is an independent identity.
- Callers cannot predict chunk IDs from URLs without computing the SHA-256 themselves.
- A URL that redirects to a different final URL will produce different chunk IDs depending on whether the input or final URL is used — `final_url` is always used.

---

## ADR-002 — `trafilatura` as a hard dependency

**Date:** 2025-Q1  
**Status:** Accepted

**Decision:** `trafilatura` is always installed with the package. It is not an optional extra.

**Rationale:** `trafilatura` is the core value of the extraction layer — the boilerplate removal and metadata extraction it provides are what make web content usable for RAG. Without it, the library has no meaningful extraction capability. Making it optional would require a fallback path with dramatically worse output quality, and would force every user to explicitly install it.

**Consequences:**
- `trafilatura` is always present, even in dev installs where only local HTML is processed.
- `trafilatura` depends on `lxml` and `courlan`, which are compiled packages. This increases install size and may require system build tools.

---

## ADR-003 — Reuse chunkers and embeddings from `blue-data-doc-source`

**Date:** 2025-Q1  
**Status:** Accepted

**Decision:** `SectionChunker`, `WindowChunker`, `EmbeddingModel`, `DummyEmbeddingModel`, `OpenAIEmbeddingModel`, `BasicEnricher`, and `LLMEnricher` are imported directly from `blue-data-doc-source`, not reimplemented.

**Rationale:** These components are well-tested, have stable ABCs, and web content does not require different chunking semantics than document content. Reimplementing them would create a maintenance split with no benefit. `blue-data-web-source` extends the stack above the pipeline, not beside it.

**Consequences:**
- `blue-data-doc-source` is a hard dependency. It must be installed before `blue-data-web-source`.
- Changes to chunker or enricher ABCs in `blue-data-doc-source` may require coordinated updates here.

---

## ADR-004 — Nav fragment stripping after trafilatura extraction

**Date:** 2025-Q1  
**Status:** Accepted

**Decision:** `WebExtractor` applies a second pass after trafilatura to strip lines beginning with CMS navigation fragments: `Related Story:`, `Read more:`, `See also:`, `Also read:`, `Advertisement`, `Story continues`.

**Rationale:** `trafilatura` does not remove all inline cross-reference noise from CMS platforms (e.g. WordPress, Arc). These fragments appear at the start of lines and pollute chunk text with irrelevant navigation content, reducing retrieval quality. A simple line-prefix filter removes them cheaply.

**Consequences:**
- Legitimate article text that begins with any of these phrases will be stripped. This is accepted — such cases are rare and the false-negative rate is extremely low.
- New CMS patterns may appear over time. The pattern list in `_NAV_FRAGMENT_PATTERNS` should be extended as new noise patterns are identified.

---

## ADR-005 — Default HTTP timeout of 30 seconds (not 10)

**Date:** 2025-Q1  
**Status:** Accepted

**Decision:** `WebFetcher` defaults to a 30-second HTTP timeout (`BLUE_WEB_TIMEOUT=30`).

**Rationale:** Real news sites and documentation servers often have slow SSL handshakes or high-latency first-byte responses. A 10-second default produces frequent false timeouts on legitimate pages. 30 seconds is consistent with browser behavior and covers the slow-but-legitimate tail of real web servers.

**Consequences:**
- Hung connections can block a worker thread for up to 30 seconds. With `max_workers=10`, up to 10 threads can be blocked simultaneously. Use `BLUE_WEB_TIMEOUT` to tighten this for known-fast internal networks.
- Adversarially slow servers (tarpit attacks) can consume threads. Rate limiting is not built in.

---

## ADR-006 — `ingest_many` uses `ThreadPoolExecutor`, not `asyncio`

**Date:** 2025-Q1  
**Status:** Accepted

**Decision:** Concurrent multi-URL ingest in `WebPipeline.ingest_many()` and `WEB.CRAWL` is implemented with `ThreadPoolExecutor(max_workers=N)`, not `asyncio`.

**Rationale:** The pipeline is synchronous (see ADR-008 in doc-source). Introducing `asyncio` at the fetch layer would require either rewriting the entire pipeline as async or using `asyncio.run_in_executor` with synchronous workers — functionally equivalent to `ThreadPoolExecutor` but more complex. `ThreadPoolExecutor` is simpler, well-understood, and sufficient for the fetch-heavy workload where threads spend most time waiting for I/O.

**Consequences:**
- Thread count is bounded by `max_workers`. High `max_workers` values may exhaust OS thread limits or overwhelm target servers. The default of 10 is conservative.
- `asyncio`-based fetch is not available. Callers who need true async integration must wrap `ingest_many` in `loop.run_in_executor`.

---

## ADR-007 — `BasicEnricher` as default (not `none`)

**Date:** 2025-Q1  
**Status:** Accepted

**Decision:** `BLUE_WEB_ENRICHER` defaults to `basic`, not `none`. `BasicEnricher` (PROPER_NOUN regex + semantic hints) runs on every chunk at no API cost.

**Rationale:** `BasicEnricher` is zero-cost and adds entity and semantic_hints fields that improve downstream filtering and explainability without slowing ingest. Setting the default to `none` would silently produce impoverished chunk records for users who do not think to configure enrichment. `llm` enrichment is opt-in because it costs one API call per chunk.

**Consequences:**
- `BasicEnricher` runs for all deployments unless explicitly disabled with `BLUE_WEB_ENRICHER=none`. This is intentional.
- The regex-based PROPER_NOUN detector has the same two-word minimum as in `blue-data-doc-source` — single-word entity names are not detected.

---

## ADR-008 — Skipping failed pages during batch operations

**Date:** 2025-Q1  
**Status:** Accepted

**Decision:** During `ingest_many`, `WEB.CRAWL`, and `WEB.INGEST_FEED`, pages that fail to fetch or yield no extractable content are skipped silently. Their absence is visible in the response `urls` list but does not abort the operation or raise an exception.

**Rationale:** Web content is inherently unreliable — login walls, rate limits, transient errors, and nav-only pages are normal. Aborting an entire crawl because one page returns 403 would make the API unusable for large-scale ingestion. Partial success with a visible skip list is the right semantics.

**Consequences:**
- Callers cannot distinguish "page returned 404" from "page had no extractable content" from the skip list alone — both result in the URL being absent from the ingested set.
- Silent skips may mask persistent failures. Callers who need per-URL error details should call `WEB.INGEST` per URL rather than using batch operations.

---

## ADR-009 — `WEB.ENSURE_INDEX` must be called before first ingest

**Date:** 2025-Q1  
**Status:** Accepted

**Decision:** `WebPipeline` does **not** auto-create the vector index on the first ingest. Callers must call `WEB.ENSURE_INDEX` explicitly.

**Rationale:** Auto-creating the index couples the index schema (embedding dimension, extra fields) to the first ingest call in an implicit way. Explicit index creation makes the schema configuration observable and controllable — operators can run `ENSURE_INDEX` during deploy, before any traffic, rather than having it happen at an unpredictable point during the first request. It is also idempotent, so calling it in every deploy script is safe.

**Consequences:**
- Ingesting before calling `ENSURE_INDEX` raises an error from the vector store. This is intentional — the error is immediate and actionable.
- All platform adapter docs and MCP tool descriptions include a note to call `ensure_index` first.

---

## ADR-010 — `description` attached to chunk 0 only

**Date:** 2025-Q1  
**Status:** Accepted

**Decision:** The page `description` field (from `<meta name="description">` or OpenGraph) is attached to `chunk_id` 0 only — not copied to every chunk.

**Rationale:** Repeating the description on every chunk inflates the vector store and makes the description text over-represented in search results. The description is most relevant to the first chunk (which introduces the article) and is accessible via `chunk_id` 0 when needed.

**Consequences:**
- Chunks beyond index 0 do not carry the page description. Callers who want the description for every chunk must fetch chunk 0 and copy the field.

---

## ADR-011 — Platform adapters contain no business logic

**Date:** 2025-Q1  
**Status:** Accepted

**Decision:** All three platform adapters (REST API, MCP server, CLI) delegate entirely to a `WebDataSource` singleton. No web processing logic lives in route handlers, MCP tool handlers, or CLI command functions.

**Rationale:** Business logic in the interface layer is duplicated across interfaces, untested in the library, and hard to evolve. The pipeline is the unit of correctness; the adapters are thin translation layers.

**Consequences:**
- Adapter tests patch the `WebDataSource` singleton and assert only on argument forwarding and output formatting — they do not test pipeline logic.
- Adding a new operation requires: (1) a new op code in `WebDataSource.execute_query`, (2) a new route/tool/command, (3) tests at both levels.

---

## ADR-012 — `NLQueryParser` uses Claude tool-use

**Date:** 2025-Q2  
**Status:** Accepted

**Decision:** `NLQueryParser` converts natural-language instructions to `WEB.*` op dicts using Claude's tool-use API (`tool_choice={"type": "any"}`), not free-form completion parsing.

**Rationale:** Tool-use constrains the model's output to a typed schema and guarantees a tool call is always returned — never free text that requires secondary parsing. Six tools are registered: `ingest_url`, `search`, `ensure_index`, `crawl`, `search_web`, `ingest_feed`. The `any` tool choice means the model must always call one of them.

**Consequences:**
- Requires `anthropic` package (`[llm]` extra) and `ANTHROPIC_API_KEY`.
- Tool definitions must be kept in sync with the op code schema. Adding a new op requires updating both `execute_query` and the tool definition in `NLQueryParser`.
- `tool_choice={"type": "any"}` was chosen over `{"type": "required"}` for compatibility across Claude model versions.
