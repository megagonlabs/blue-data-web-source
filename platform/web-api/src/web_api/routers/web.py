from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from web_api.config import get_source

router = APIRouter(prefix="/web", tags=["web"])


# ── Request models ────────────────────────────────────────────────────────────

class EnsureIndexRequest(BaseModel):
    collection: Optional[str] = None


class IngestRequest(BaseModel):
    url: Optional[str] = None
    urls: Optional[List[str]] = None
    collection: Optional[str] = None


class CrawlRequest(BaseModel):
    url: str
    depth: int = 1
    max_pages: int = 10
    same_domain_only: bool = True
    collection: Optional[str] = None


class FeedRequest(BaseModel):
    url: str
    since: Optional[str] = None
    max_entries: Optional[int] = None
    collection: Optional[str] = None


class ExtractRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    url: Optional[str] = None
    urls: Optional[List[str]] = None
    json_schema: Dict[str, Any] = Field(alias="schema")


class SearchRequest(BaseModel):
    query: str
    k: int = 5
    collection: Optional[str] = None


class SearchWebRequest(BaseModel):
    query: str
    k: int = 5
    top_k: int = 5
    collection: Optional[str] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/ensure-index")
def ensure_index(request: EnsureIndexRequest):
    """Create the vector index for a collection. Idempotent — safe to call multiple times."""
    source = get_source()
    source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": request.collection})
    return {"status": "ok", "collection": request.collection or source._pipeline._collection}


@router.post("/ingest")
def ingest(request: IngestRequest) -> Dict[str, Any]:
    """Fetch, extract, chunk, embed, and upsert one or more web pages.

    Pass ``url`` for a single page or ``urls`` for a batch. Re-ingesting an
    unchanged URL returns an empty chunk list without re-embedding.
    """
    if not request.url and not request.urls:
        raise HTTPException(status_code=422, detail="Provide 'url' or 'urls'")

    source = get_source()
    op: Dict[str, Any] = {"op": "WEB.INGEST"}
    if request.url:
        op["url"] = request.url
    else:
        op["urls"] = request.urls
    if request.collection:
        op["collection"] = request.collection

    chunks = source.execute_query(op)

    urls_ingested = sorted({c["url"] for c in chunks})
    return {
        "chunk_count": len(chunks),
        "pages_ingested": len(urls_ingested),
        "urls": urls_ingested,
        "collection": request.collection or source._pipeline._collection,
    }


@router.post("/crawl")
def crawl(request: CrawlRequest) -> Dict[str, Any]:
    """BFS-crawl a site from a seed URL and ingest all reachable pages.

    Respects robots.txt and enforces per-domain rate limiting. Returns a
    summary with chunk count and the list of ingested URLs.
    """
    source = get_source()
    op: Dict[str, Any] = {
        "op": "WEB.CRAWL",
        "url": request.url,
        "depth": request.depth,
        "max_pages": request.max_pages,
        "same_domain_only": request.same_domain_only,
    }
    if request.collection:
        op["collection"] = request.collection

    chunks = source.execute_query(op)
    urls_ingested = sorted({c["url"] for c in chunks})
    return {
        "chunk_count": len(chunks),
        "pages_ingested": len(urls_ingested),
        "urls": urls_ingested,
        "collection": request.collection or source._pipeline._collection,
    }


@router.post("/feed")
def feed(request: FeedRequest) -> Dict[str, Any]:
    """Fetch an RSS or Atom feed and ingest each article.

    ``since`` (ISO 8601) filters entries by publication date before fetching.
    Entries without a date always pass the filter. ``max_entries`` caps the
    number of articles processed after date filtering.
    """
    source = get_source()
    op: Dict[str, Any] = {"op": "WEB.INGEST_FEED", "url": request.url}
    if request.since:
        op["since"] = request.since
    if request.max_entries is not None:
        op["max_entries"] = request.max_entries
    if request.collection:
        op["collection"] = request.collection

    chunks = source.execute_query(op)
    urls_ingested = sorted({c["url"] for c in chunks})
    return {
        "chunk_count": len(chunks),
        "articles_ingested": len(urls_ingested),
        "urls": urls_ingested,
        "collection": request.collection or source._pipeline._collection,
    }


@router.post("/extract")
def extract(request: ExtractRequest) -> Any:
    """Fetch a URL and extract typed fields using a JSON Schema.

    Pass ``url`` for a single page (returns a dict) or ``urls`` for multiple
    pages (returns a list of dicts, each with a ``url`` key). Requires
    OPENAI_API_KEY — uses StructuredFieldExtractor from blue-data-doc-source.
    """
    if not request.url and not request.urls:
        raise HTTPException(status_code=422, detail="Provide 'url' or 'urls'")

    source = get_source()
    op: Dict[str, Any] = {"op": "WEB.EXTRACT", "schema": request.json_schema}
    if request.url:
        op["url"] = request.url
    else:
        op["urls"] = request.urls

    try:
        return source.execute_query(op)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/search")
def search(request: SearchRequest) -> Dict[str, Any]:
    """Semantic search over ingested pages.

    Requires BLUE_WEB_REDIS_HOST and ``/web/ensure-index`` to have been called.
    Returns ranked chunk dicts with full web provenance.
    """
    source = get_source()
    try:
        results = source.execute_query({
            "op": "WEB.SEARCH",
            "query": request.query,
            "k": request.k,
            "collection": request.collection,
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"results": results, "count": len(results)}


@router.post("/search-web")
def search_web(request: SearchWebRequest) -> Dict[str, Any]:
    """Live web search via Tavily — fetch, ingest, and rank in one call.

    Queries the search API for ``k`` URLs, ingests each through the full
    pipeline, and returns ``top_k`` semantically ranked chunks. Requires
    TAVILY_API_KEY. No prior ingest required.
    """
    source = get_source()
    try:
        results = source.execute_query({
            "op": "WEB.SEARCH_WEB",
            "query": request.query,
            "k": request.k,
            "top_k": request.top_k,
            "collection": request.collection,
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"results": results, "count": len(results)}
