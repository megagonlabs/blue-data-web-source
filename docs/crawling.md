# Crawling and Web Search

Two ops go beyond single-URL ingestion: `WEB.CRAWL` for recursive site traversal, and `WEB.SEARCH_WEB` for live web search followed by automatic ingestion.

---

## WEB.CRAWL

`WEB.CRAWL` takes a seed URL and recursively fetches linked pages using BFS. All reachable pages are ingested through the full pipeline and their chunks are returned as a flat list.

```python
records = source.execute_query({
    "op": "WEB.CRAWL",
    "url": "https://docs.example.com/",
    "depth": 2,               # hops from seed (0 = seed only, 1 = seed + its links, ...)
    "max_pages": 50,          # hard cap on successful fetches; crawl stops when reached
    "same_domain_only": True, # default — stay within docs.example.com
    "collection": "docs",
})

ingested_urls = sorted({r["url"] for r in records})
print(f"Crawled {len(ingested_urls)} pages, {len(records)} total chunks")
```

Returns a flat `list[dict]` — all `WebChunkRecord` dicts from all ingested pages.

### Parameters

| Parameter | Default | Description |
|---|---|---|
| `url` | required | Seed URL — crawl starts here |
| `depth` | `1` | Maximum hops from the seed. `0` = seed page only, `1` = seed + its linked pages, `2` = seed + links + links-of-links |
| `max_pages` | `10` | Max pages to **successfully fetch**, regardless of depth. Every successful fetch counts — including a page skipped as unchanged by the freshness store (which yields no new chunks). Fetches that 404, time out, or otherwise fail do not count toward it |
| `max_attempts` | `max_pages × 10` | Hard cap on fetch attempts, bounding how many dead links the crawl chases while reaching `max_pages` fetches |
| `same_domain_only` | `True` | When `True`, only follows links on the host the seed **resolves to** (after redirects); the original seed host is also kept in scope |
| `collection` | pipeline default | Collection to ingest into |
| `max_workers` | `10` | Thread pool size for concurrent fetching within each BFS level; set via `BLUE_WEB_MAX_WORKERS` env var or `WebDataSource(max_workers=N)` |

### BFS traversal

The crawler uses a queue-based BFS:

1. Enqueue the seed URL at depth 0
2. Fetch the page; extract all `<a href>` links
3. Normalise each link (resolve relative URLs, strip fragments)
4. If `same_domain_only=True`, discard links whose `netloc` is not in scope. Scope is
   the host the seed resolves to after redirects (plus the original seed host), so a
   seed that redirects apex → www or to an alias host still crawls the resolved site
5. Enqueue unseen links at `depth + 1` if `depth + 1 ≤ max_depth`
6. Ingest the fetched page through `WebPipeline` using the already-fetched HTML (no double fetch)
7. Repeat until the queue is empty, `max_pages` successful fetches are reached, or `max_attempts` fetches are made

Every successful fetch counts toward `max_pages` — including a page the freshness
store skips as unchanged (it yields no new chunks but was still fetched), so
re-crawling an unchanged site stays bounded to ~`max_pages` instead of climbing
each run (#100). Fetches that 404, time out, or otherwise fail do **not** count, so
a handful of dead links ahead of live ones no longer exhausts the budget;
`max_attempts` bounds the total fetches so an all-dead-links page can't loop forever.
The seed URL is always crawled regardless of robots.txt (see [features](features.md)).

### URL normalisation

Before deduplication, each URL is normalised:

- Relative URLs are resolved against the base URL of the page they were found on
- URL fragments are stripped: `/page#section` → `/page`
- Only `http://` and `https://` URLs are followed; `mailto:`, `javascript:`, etc. are silently skipped

### Error handling

Pages that fail to fetch (network error, timeout, 404, 500, etc.) are silently skipped and the crawl continues. The failure is not propagated to the caller. If all pages fail, the result is an empty list.

Pages that extract no content (`WebExtractor` returns `None`) are also skipped silently — they contribute no chunks to the result.

### Fetch destination policy (SSRF guard)

Every server-side fetch — `WEB.INGEST`, `WEB.CRAWL`, `WEB.INGEST_FEED`,
`WEB.SEARCH_WEB` and `WEB.EXTRACT`, and **each redirect hop** — is checked
against a destination policy *after DNS resolution*. By default the server
**refuses** hosts that resolve to loopback, private-network, link-local, or
cloud-metadata (`169.254.169.254`) addresses, so a caller-chosen URL cannot make
the host reach internal services (#112). A refused fetch raises
`FetchBlockedError`; in a crawl the link is simply skipped, and `WEB.EXTRACT`
additionally caps a single call to 50 URLs.

Deployments that legitimately need to fetch internal hosts opt in:

| Env var | Effect |
|---|---|
| `BLUE_WEB_FETCH_ALLOW_PRIVATE=1` | Disable the guard entirely (trusted/offline networks) |
| `BLUE_WEB_FETCH_ALLOWLIST=host1,10.0.0.0/8,…` | Admit specific hostnames / IP networks only |

In-process callers can pass `fetch_allow_private=` / `fetch_allowlist=` to
`WebDataSource` or `WebPipeline` instead.

### same_domain_only vs cross-domain

```python
# Stay within docs.example.com only
records = source.execute_query({
    "op": "WEB.CRAWL",
    "url": "https://docs.example.com/",
    "same_domain_only": True,   # default
})

# Follow links to any domain (use carefully — can grow very large)
records = source.execute_query({
    "op": "WEB.CRAWL",
    "url": "https://example.com/",
    "same_domain_only": False,
    "max_pages": 20,            # always set a cap when crawling cross-domain
})
```

### Choosing depth and max_pages

| Use case | Suggested depth | Suggested max_pages |
|---|---|---|
| Single documentation page with subpages | 1 | 20–50 |
| Full documentation site | 2–3 | 50–200 |
| Blog index + all posts | 1 | 50–100 |
| Known URL list | Use `WEB.INGEST urls:[...]` instead | — |

`depth` and `max_pages` interact: `depth` controls how far the crawler travels, `max_pages` is the hard cap that overrides depth when the site is larger than expected. Always set `max_pages` to avoid unbounded crawls on large sites.

---

## WEB.SEARCH_WEB

`WEB.SEARCH_WEB` takes a natural language query, calls a web search API to find relevant URLs, fetches and ingests the top results on the fly, and returns semantically ranked chunks. No prior ingestion step is required.

```python
from blue_data_web_source.source.web.search_provider import TavilySearchProvider

source = WebDataSource(
    name="live-search",
    embedding_model=OpenAIEmbeddingModel(),
    vector_source=MemoryVectorSource(),
    search_provider=TavilySearchProvider(),  # reads TAVILY_API_KEY from env
)
source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": "web"})

results = source.execute_query({
    "op": "WEB.SEARCH_WEB",
    "query": "solid-state battery energy density 2026",
    "k": 5,           # number of URLs to fetch from the search API
    "collection": "web",
})

for r in results:
    print(r["url"], r.get("score"), r["text"][:120])
```

### How it works

1. Calls `WebSearchProvider.search(query, k)` → list of URLs
2. Ingests each URL through the full pipeline (fetch → extract → chunk → embed → upsert)
3. Runs `WEB.SEARCH` over the freshly ingested content
4. Returns semantically ranked chunks

Re-running the same query **re-fetches and re-extracts** each result — the pipeline always fetches a page before it can compare anything, so there is no fetch short-circuit. What URL-hash dedup guarantees is stable *storage*: a page's chunk IDs are derived from its final URL, so re-ingesting overwrites the existing chunks instead of duplicating them. Re-embedding still happens on every run unless a `freshness_store` is configured — that skips re-chunking and re-embedding pages whose extracted text is unchanged (the page is still fetched). See [Features → Freshness tracking](features.md).

### Parameters

| Parameter | Default | Description |
|---|---|---|
| `query` | required | Natural language query passed to the search API |
| `k` | `5` | Number of URLs to retrieve from the search API |
| `collection` | pipeline default | Collection to ingest into and search against |

### TavilySearchProvider

`TavilySearchProvider` is the default search backend. It reads `TAVILY_API_KEY` from the environment.

```bash
pip install tavily-python
export TAVILY_API_KEY=tvly-...
```

```python
from blue_data_web_source.source.web.search_provider import TavilySearchProvider

provider = TavilySearchProvider()
urls = provider.search("climate policy EU 2026", k=5)
# ["https://...", "https://...", ...]
```

Tavily returns URLs pre-ranked by relevance. The ingestion pipeline processes them in order.

### WebSearchProvider ABC

Plug in any search backend by subclassing `WebSearchProvider`:

```python
from blue_data_web_source.source.web.search_provider import WebSearchProvider

class MySearchProvider(WebSearchProvider):
    def search(self, query: str, k: int) -> list[str]:
        """Return up to k URLs relevant to the query."""
        return my_search_api.query(query, top_k=k)

source = WebDataSource(
    name="web",
    embedding_model=...,
    vector_source=...,
    search_provider=MySearchProvider(),
)
```

The ABC requires only `search(query, k) -> list[str]`. The returned list can contain fewer than `k` URLs — the pipeline ingests whatever is returned.

### WEB.SEARCH_WEB vs WEB.SEARCH

| | `WEB.SEARCH_WEB` | `WEB.SEARCH` |
|---|---|---|
| Input | Natural language query | Natural language query |
| Source | Fetches live pages via search API | Queries pre-built vector index |
| Requires prior ingest | No | Yes |
| Latency | Slow (HTTP + embed per page) | Fast (vector lookup only) |
| Content freshness | Always current | As fresh as last ingest |
| Best for | Exploratory queries, recent events | Controlled content, low latency |

Use `WEB.SEARCH_WEB` when you need current information or don't have a curated URL list. Use `WEB.SEARCH` once you have ingested a known set of pages and want fast repeated retrieval.

---

## WebCrawler internals

`WebCrawler` is instantiated by `WebDataSource` and reuses the same `WebPipeline` instance as direct `WEB.INGEST` calls.

```python
from blue_data_web_source.source.web.crawler import WebCrawler

crawler = WebCrawler(pipeline=pipeline)
records = crawler.crawl(
    seed_url="https://docs.example.com/",
    depth=2,
    max_pages=50,
    collection="docs",
)
```

The crawler passes the already-fetched `FetchResult` into `pipeline.ingest()` via the `_fetch_result` parameter — each page is fetched exactly once, even though the pipeline would otherwise re-fetch it.

Link extraction uses `_LinkExtractor`, a minimal `HTMLParser` subclass that collects `href` attributes from `<a>` tags. It does not execute JavaScript or follow `<meta http-equiv="refresh">` redirects.
