"""End-to-end demo: fetch URL → chunk → embed → search.

With no URL argument the demo serves a local fixture (no network needed).
Pass one or more URLs to fetch and ingest real pages.

Usage:
    python examples/demo_web.py                                                    # dummy embeddings, local fixture
    OPENAI_API_KEY=sk-... python examples/demo_web.py --embedding openai --query "what is the missile test about"
    OPENAI_API_KEY=sk-... ANTHROPIC_API_KEY=sk-ant-... python examples/demo_web.py --embedding openai --nl-query "find news about China"
    python examples/demo_web.py https://example.com/article --embedding openai --query "key points"
"""
import argparse
import sys
from pathlib import Path
from unittest.mock import MagicMock

from blue_data.source.vector.memory_vector_source import MemoryVectorSource
from blue_data_doc_source.source.document.embeddings.dummy_embedding import DummyEmbeddingModel
from blue_data_doc_source.source.document.embeddings.openai_embedding import OpenAIEmbeddingModel

from blue_data_web_source import WebDataSource
from blue_data_web_source.source.web.fetcher import FetchResult

_FIXTURES_DIR = Path(__file__).parent.parent / "data" / "fixtures"
_NPR_URL = "https://text.npr.org/nx-s1-5884537"

DEFAULT_QUERY = "what happened with the missile test in the Pacific"

EMBEDDING_DIMS = {"dummy": 8, "openai": 1536}


def _mock_fetcher_for(url: str, html: str):
    """Return a WebFetcher mock that serves a single URL from local HTML."""
    mock = MagicMock()

    def _fetch(u: str) -> FetchResult:
        if u != url:
            raise ValueError(f"No fixture for URL: {u}")
        return FetchResult(url=u, final_url=u, html=html, status_code=200,
                           content_type="text/html; charset=utf-8")

    mock.fetch.side_effect = _fetch
    return mock


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="End-to-end web ingestion + search demo")
    parser.add_argument("urls", nargs="*", default=[], help="URLs to fetch and ingest (default: local NPR fixture)")
    parser.add_argument("--query", default=None, help="Structured search query")
    parser.add_argument("--nl-query", default=None, help="Natural language query (requires ANTHROPIC_API_KEY)")
    parser.add_argument("--embedding", choices=["dummy", "openai"], default="dummy")
    parser.add_argument("--openai-model", default="text-embedding-3-small")
    parser.add_argument("--chunk-size", type=int, default=300)
    parser.add_argument("--overlap", type=int, default=50)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--collection", default="demo_web")
    args = parser.parse_args()

    if args.nl_query and args.embedding != "openai":
        print("--nl-query requires --embedding openai for meaningful results.")
        sys.exit(1)

    if args.embedding == "openai":
        embedding_model = OpenAIEmbeddingModel(model=args.openai_model)
    else:
        embedding_model = DummyEmbeddingModel(dim=EMBEDDING_DIMS["dummy"])

    source = WebDataSource(
        name="demo",
        embedding_model=embedding_model,
        vector_source=MemoryVectorSource(),
        chunk_size=args.chunk_size,
        overlap=args.overlap,
        default_collection=args.collection,
    )

    source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": args.collection})

    use_fixture = not args.urls
    if use_fixture:
        fixture_path = _FIXTURES_DIR / "npr_article.html"
        if not fixture_path.exists():
            print(f"Fixture not found: {fixture_path}")
            print("Pass a URL as an argument to fetch a real page instead.")
            sys.exit(1)
        html = fixture_path.read_text(encoding="utf-8")
        source._pipeline._fetcher = _mock_fetcher_for(_NPR_URL, html)
        urls = [_NPR_URL]
        print(f"(using local fixture: {fixture_path.name})")
    else:
        urls = args.urls

    for url in urls:
        print(f"Ingesting: {url}")
        records = source.execute_query({
            "op": "WEB.INGEST",
            "url": url,
            "collection": args.collection,
        })
        print(f"  Stored {len(records)} chunks\n")

    if args.nl_query:
        print(f"NL query: \"{args.nl_query}\"")
        results = source.execute_query_nl(args.nl_query, collection=args.collection)
    else:
        query = args.query or DEFAULT_QUERY
        print(f"Searching: \"{query}\"")
        results = source.execute_query({
            "op": "WEB.SEARCH",
            "query": query,
            "collection": args.collection,
            "k": args.top_k,
        })

    if not results:
        print("No results returned.")
    else:
        for i, r in enumerate(results, 1):
            section = f"  [{r['section_title']}]" if r.get("section_title") else ""
            score = float(r.get("score", 0))
            snippet = r.get("text", "")[:200].replace("\n", " ")
            print(f"[{i}] {r.get('domain', '')}  score={score:.4f}{section}")
            print(f"    {snippet}...\n")
