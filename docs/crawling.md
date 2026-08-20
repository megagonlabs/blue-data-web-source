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
    "max_pages": 50,          # hard cap on total pages; crawl stops when reached
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
| `max_pages` | `10` | Hard cap on total pages fetched regardless of depth. Crawl stops when reached |
| `same_domain_only` | `True` | When `True`, only follows links within the seed URL's domain (`netloc`) |
| `collection` | pipeline default | Collection to ingest into |
| `max_workers` | `10` | Thread pool size for concurrent fetching within each BFS level; set via `BLUE_WEB_MAX_WORKERS` env var or `WebDataSource(max_workers=N)` |

### BFS traversal

The crawler uses a queue-based BFS:

1. Enqueue the seed URL at depth 0
2. Fetch the page; extract all `<a href>` links
3. Normalise each link (resolve relative URLs, strip fragments)
4. If `same_domain_only=True`, discard links whose `netloc` differs from the seed
5. Enqueue unseen links at `depth + 1` if `depth + 1 ≤ max_depth`
6. Ingest the fetched page through `WebPipeline` using the already-fetched HTML (no double fetch)
7. Repeat until queue is empty or `max_pages` is reached

`max_pages` is checked before fetching each page — the crawler stops as soon as the cap is hit, even if there are pages remaining in the queue at the current depth.

### URL normalisation

Before deduplication, each URL is normalised:

- Relative URLs are resolved against the base URL of the page they were found on
- URL fragments are stripped: `/page#section` → `/page`
- Only `http://` and `https://` URLs are followed; `mailto:`, `javascript:`, etc. are silently skipped

### Error handling

Pages that fail to fetch (network error, timeout, 404, 500, etc.) are silently skipped and the crawl continues. The failure is not propagated to the caller. If all pages fail, the result is an empty list.

Pages that extract no content (`WebExtractor` returns `None`) are also skipped silently — they contribute no chunks to the result.

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

Because ingested URLs are stored by URL-hash dedup, re-running the same query does not re-fetch already-ingested pages — their chunk IDs are already in the vector store.

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
