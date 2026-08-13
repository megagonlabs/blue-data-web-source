"""MCP server exposing blue-data-web-source as AI-callable tools.

Run:
    python platform/web-mcp/src/server.py

Configure via environment variables:

    BLUE_WEB_EMBEDDING          openai | dummy  (default: dummy)
    OPENAI_API_KEY              required when BLUE_WEB_EMBEDDING=openai
    OPENAI_EMBEDDING_MODEL      default: text-embedding-3-small
    BLUE_WEB_REDIS_HOST         Redis host; omit to run without a vector store
    BLUE_WEB_REDIS_PORT         default: 6379
    BLUE_WEB_COLLECTION         default: web
    BLUE_WEB_CHUNK_SIZE         default: 1000
    BLUE_WEB_OVERLAP            default: 150
    BLUE_WEB_TIMEOUT            HTTP timeout in seconds; default: 30
    TAVILY_API_KEY              required for search_web tool (Tavily backend)
"""
import json
import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("blue-web-source")

# ── Pipeline singleton ────────────────────────────────────────────────────────

_source = None


def _build_source():
    from blue_doc_source.source.document.enrichment import BasicEnricher
    from blue_web_source import WebDataSource

    embedding_backend = os.getenv("BLUE_WEB_EMBEDDING", "dummy").lower()
    if embedding_backend == "openai":
        from blue_doc_source.source.document.embeddings.openai_embedding import OpenAIEmbeddingModel
        embedding_model = OpenAIEmbeddingModel(
            model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        )
    else:
        from blue_doc_source.source.document.embeddings.dummy_embedding import DummyEmbeddingModel
        embedding_model = DummyEmbeddingModel()

    vector_source = None
    redis_host = os.getenv("BLUE_WEB_REDIS_HOST")
    if redis_host:
        from blue_data.source.vector.redis.redis_vector_source import RedisVectorSource
        vector_source = RedisVectorSource(
            name="redis",
            host=redis_host,
            port=int(os.getenv("BLUE_WEB_REDIS_PORT", "6379")),
        )

    return WebDataSource(
        name="web-mcp",
        embedding_model=embedding_model,
        vector_source=vector_source,
        enricher=BasicEnricher(),
        chunk_size=int(os.getenv("BLUE_WEB_CHUNK_SIZE", "1000")),
        overlap=int(os.getenv("BLUE_WEB_OVERLAP", "150")),
        default_collection=os.getenv("BLUE_WEB_COLLECTION", "web"),
        timeout=float(os.getenv("BLUE_WEB_TIMEOUT", "30")),
    )


def _get_source():
    global _source
    if _source is None:
        _source = _build_source()
    return _source


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def ensure_index(collection: str = "web") -> str:
    """Create the vector index for a collection.

    Must be called once before the first ingest when using a vector store.
    Safe to call multiple times — skips creation if the index already exists.
    Call this at the start of a session before ingesting any pages.
    """
    try:
        _get_source().execute_query({"op": "WEB.ENSURE_INDEX", "collection": collection})
        return json.dumps({"status": "ok", "collection": collection})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def ingest_url(url: str, collection: str = "web") -> str:
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
            "page_id": first["chunk_id"].rsplit("_chunk_", 1)[0],
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
def ingest_urls(urls: list[str], collection: str = "web") -> str:
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
    url: str,
    depth: int = 1,
    max_pages: int = 10,
    same_domain_only: bool = True,
    collection: str = "web",
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
    query: str,
    collection: str = "web",
    k: int = 5,
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
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return json.dumps({"error": f"Search failed: {exc}"})


@mcp.tool()
def search_web(
    query: str,
    k: int = 5,
    collection: str = "web",
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


if __name__ == "__main__":
    mcp.run()
