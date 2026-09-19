"""MCP server exposing blue-data-web-source as AI-callable tools.

Eight tools: ensure_index, ingest_url, ingest_urls, crawl_site, ingest_feed,
search, search_web, extract.

Not exposed (library-only): WEB.FIND, WEB.FIND_THEN_SEARCH, WEB.GRAPH_NEIGHBORS,
WEB.GRAPH_QUERY. These require RecordStore and GraphSource components that are not
wired by WebDataSource.from_env() and therefore cannot be offered as general-purpose
MCP tools without additional configuration support.

This server exposes tools only; no MCP resources are defined. All operations are
stateful (they write to or read from a vector store) rather than static reads,
so the resource model does not apply.

Error convention: all errors are returned as a payload-level JSON string
``{"error": "<prefix>: <message>"}``. Exceptions are never raised to the MCP
transport layer — callers always receive a parseable JSON string regardless of
outcome, and ``isError: true`` is never set by this server.

Run:
    python platform/web-mcp/src/web_mcp/server.py

Configure via environment variables:

    BLUE_WEB_EMBEDDING          openai | dummy  (default: dummy)
    OPENAI_API_KEY              required when BLUE_WEB_EMBEDDING=openai or BLUE_WEB_ENRICHER=llm
    OPENAI_EMBEDDING_MODEL      default: text-embedding-3-small
    BLUE_WEB_REDIS_HOST         Redis host; omit to run without a vector store
    BLUE_WEB_REDIS_PORT         default: 6379
    BLUE_WEB_COLLECTION         default: web
    BLUE_WEB_CHUNK_SIZE         default: 1000
    BLUE_WEB_OVERLAP            default: 150
    BLUE_WEB_TIMEOUT            HTTP timeout in seconds; default: 30
    BLUE_WEB_MAX_WORKERS        concurrent fetch threads; default: 10
    BLUE_WEB_ENRICHER           none | basic | llm  (default: basic)
    BLUE_WEB_LLM_MODEL          OpenAI model for llm enricher; default: gpt-4o-mini
    TAVILY_API_KEY              required for search_web tool (Tavily backend)
"""
import json
from typing import Annotated, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

mcp = FastMCP("blue-web-source")

# ── Pipeline singleton ────────────────────────────────────────────────────────

_source = None


def _get_source():
    global _source
    if _source is None:
        from blue_data_web_source import WebDataSource
        _source = WebDataSource.from_env(name="web-mcp")
    return _source


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def ensure_index(
    collection: Annotated[str, Field(description="Vector index collection name to create.")] = "web",
) -> str:
    """Create the vector index for a collection.

    Must be called once before the first ingest when using a vector store.
    Safe to call multiple times — skips creation if the index already exists.
    Call this at the start of a session before ingesting any pages.
    """
    try:
        _get_source().execute_query({"op": "WEB.ENSURE_INDEX", "collection": collection})
        return json.dumps({"collection": collection})
    except Exception as exc:
        return json.dumps({"error": f"Ensure index failed: {exc}"})


@mcp.tool()
def ingest_url(
    url: Annotated[str, Field(description="URL of the web page to fetch and ingest.")],
    collection: Annotated[str, Field(description="Vector index collection name.")] = "web",
) -> str:
    """Fetch a web page, extract its main content, chunk it, and store it.

    Strips boilerplate, navigation, and CMS cross-reference fragments.
    Re-ingesting the same URL is safe — URL-hash deduplication means the same
    final URL always produces the same chunk IDs, so upsert overwrites rather
    than duplicates.

    Returns the page title, domain, chunk count, and page_id.
    Returns an error if the page has no extractable content (login walls,
    nav-only pages, HTTP errors).
    """
    try:
        source = _get_source()
        records = source.execute_query({"op": "WEB.INGEST", "url": url, "collection": collection})
        if not records:
            return json.dumps({"error": "No content extracted — page may be a login wall or nav-only"})
        first = records[0]
        return json.dumps({
            "page_id": first.get("page_id"),
            "url": first["url"],
            "domain": first["domain"],
            "title": first.get("title"),
            "date": first.get("date"),
            "chunk_count": len(records),
            "collection": collection,
        })
    except Exception as exc:
        return json.dumps({"error": f"Ingest failed: {exc}"})


@mcp.tool()
def ingest_urls(
    urls: Annotated[list[str], Field(description="List of web page URLs to fetch and ingest.")],
    collection: Annotated[str, Field(description="Vector index collection name.")] = "web",
) -> str:
    """Fetch and ingest multiple web pages in one call.

    Equivalent to calling ingest_url for each URL. Pages that fail to extract
    are skipped silently; their URLs are reported in the 'skipped' list.

    Returns a summary: total chunks ingested and any skipped URLs.
    """
    try:
        source = _get_source()
        records = source.execute_query({"op": "WEB.INGEST", "urls": urls, "collection": collection})
        ingested_urls = {r["url"] for r in records}
        skipped = [u for u in urls if u not in ingested_urls]
        return json.dumps({
            "chunk_count": len(records),
            "ingested": len(ingested_urls),
            "skipped": skipped,
            "collection": collection,
        })
    except Exception as exc:
        return json.dumps({"error": f"Ingest failed: {exc}"})


@mcp.tool()
def crawl_site(
    url: Annotated[str, Field(description="Seed URL for the BFS crawl.")],
    depth: Annotated[int, Field(description="Maximum hop depth from the seed URL.", ge=1)] = 1,
    max_pages: Annotated[int, Field(description="Hard cap on pages ingested regardless of depth.", ge=1)] = 10,
    same_domain_only: Annotated[bool, Field(
        description="Restrict crawl to the seed URL's domain (default True). "
                    "CLI equivalent: absence of the --allow-external flag.",
    )] = True,
    collection: Annotated[str, Field(description="Vector index collection name.")] = "web",
) -> str:
    """BFS-crawl a site starting from a seed URL and ingest all reachable pages.

    Follows links up to `depth` hops from the seed. Stops after `max_pages`
    pages regardless of depth. When `same_domain_only` is True (default), only
    follows links within the seed URL's domain.

    Returns the total chunk count and list of ingested URLs.
    """
    try:
        source = _get_source()
        records = source.execute_query({
            "op": "WEB.CRAWL",
            "url": url,
            "depth": depth,
            "max_pages": max_pages,
            "same_domain_only": same_domain_only,
            "collection": collection,
        })
        ingested_urls = sorted({r["url"] for r in records})
        return json.dumps({
            "chunk_count": len(records),
            "pages_ingested": len(ingested_urls),
            "urls": ingested_urls,
            "collection": collection,
        })
    except Exception as exc:
        return json.dumps({"error": f"Crawl failed: {exc}"})


@mcp.tool()
def search(
    query: Annotated[str, Field(description="Natural-language search query.")],
    collection: Annotated[str, Field(description="Vector index collection name to search.")] = "web",
    k: Annotated[int, Field(description="Maximum number of chunks to return.", ge=1)] = 5,
) -> str:
    """Search ingested web pages by semantic similarity.

    Embeds the query and returns the k most relevant chunks from the vector
    store. Results include the chunk text, source URL, domain, title, and
    section heading. Use this to answer questions from already-ingested pages.

    Requires BLUE_WEB_REDIS_HOST to be set and ensure_index to have been called.
    """
    try:
        source = _get_source()
        results = source.execute_query({"op": "WEB.SEARCH", "query": query, "k": k, "collection": collection})
        hits = []
        for r in results:
            hit: dict = {
                "chunk_id": r.get("chunk_id"),
                "text": r.get("text"),
                "url": r.get("url"),
                "domain": r.get("domain"),
            }
            if r.get("title"):
                hit["title"] = r["title"]
            if r.get("section_title"):
                hit["section_title"] = r["section_title"]
            if r.get("date"):
                hit["date"] = r["date"]
            hits.append(hit)
        return json.dumps({"results": hits, "count": len(hits)})
    except Exception as exc:
        return json.dumps({"error": f"Search failed: {exc}"})


@mcp.tool()
def search_web(
    query: Annotated[str, Field(description="Search query sent to the live web search API (Tavily).")],
    collection: Annotated[str, Field(description="Vector index collection name where results are stored.")] = "web",
    k: Annotated[int, Field(description="Number of URLs to fetch from the search API; each is chunked and ranked.", ge=1)] = 5,
) -> str:
    """Search the live web, ingest the top results, and return ranked chunks.

    Uses Tavily to find the k most relevant URLs for the query, ingests each
    page on the fly, then returns semantically ranked chunks. Results are fresh
    (fetched at call time) rather than from a pre-built index.

    Requires TAVILY_API_KEY and BLUE_WEB_REDIS_HOST environment variables.
    """
    try:
        source = _get_source()
        results = source.execute_query({
            "op": "WEB.SEARCH_WEB",
            "query": query,
            "k": k,
            "collection": collection,
        })
        hits = []
        for r in results:
            hit: dict = {
                "text": r.get("text"),
                "url": r.get("url"),
                "domain": r.get("domain"),
            }
            if r.get("title"):
                hit["title"] = r["title"]
            if r.get("section_title"):
                hit["section_title"] = r["section_title"]
            hits.append(hit)
        return json.dumps({"results": hits, "count": len(hits)})
    except Exception as exc:
        return json.dumps({"error": f"Search web failed: {exc}"})


@mcp.tool()
def extract(
    url: Annotated[str, Field(description="URL of the web page to fetch and extract fields from.")],
    schema: Annotated[str, Field(
        description='JSON-encoded JSON Schema describing the fields to extract. '
                    'Example: \'{"properties": {"title": {"type": "string"}, "price": {"type": "number"}}}\'',
    )],
) -> str:
    """Extract structured fields from a web page using a caller-supplied JSON Schema.

    Fetches the URL, extracts the main body text, then calls an LLM to fill in
    the fields described by the JSON Schema. Returns a JSON object whose keys
    match the schema properties.

    Requires OPENAI_API_KEY and ``pip install 'blue-data-web-source[openai]'``.
    """
    try:
        import json as _json
        schema_dict = _json.loads(schema)
        result = _get_source().execute_query({
            "op": "WEB.EXTRACT",
            "url": url,
            "schema": schema_dict,
        })
        return json.dumps({"url": url, "result": result})
    except Exception as exc:
        return json.dumps({"error": f"Extract failed: {exc}"})


@mcp.tool()
def ingest_feed(
    url: Annotated[str, Field(description="URL of the RSS or Atom feed.")],
    since: Annotated[Optional[str], Field(
        description="ISO 8601 cutoff date (e.g. '2026-08-01T00:00:00Z'). "
                    "Only entries published at or after this date are ingested. "
                    "Entries without a publication date are always included.",
    )] = None,
    max_entries: Annotated[Optional[int], Field(
        description="Maximum number of feed entries to process after date filtering.",
        ge=1,
    )] = None,
    collection: Annotated[str, Field(description="Vector index collection name.")] = "web",
) -> str:
    """Fetch an RSS or Atom feed and ingest all articles.

    Parses the feed, fetches each article URL, extracts its main content,
    and stores it as searchable chunks. Articles that fail to load are skipped.

    Use ``since`` to only ingest entries published at or after a cutoff date —
    useful when running on a schedule to pick up only new content.

    When a freshness store is configured, articles whose content has not changed
    since the last call are also skipped without re-embedding.

    Returns the total chunk count and list of ingested article URLs.

    Requires: pip install 'blue-data-web-source[feed]'
    """
    try:
        source = _get_source()
        request: dict = {"op": "WEB.INGEST_FEED", "url": url, "collection": collection}
        if since is not None:
            request["since"] = since
        if max_entries is not None:
            request["max_entries"] = max_entries
        records = source.execute_query(request)
        ingested_urls = sorted({r["url"] for r in records})
        return json.dumps({
            "chunk_count": len(records),
            "articles_ingested": len(ingested_urls),
            "urls": ingested_urls,
            "collection": collection,
        })
    except Exception as exc:
        return json.dumps({"error": f"Feed ingest failed: {exc}"})


def main():
    mcp.run()


if __name__ == "__main__":
    main()
