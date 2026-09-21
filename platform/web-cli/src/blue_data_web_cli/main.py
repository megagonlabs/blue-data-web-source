"""blue-web — CLI for blue-data-web-source.

Usage:
    blue-web [--json] COMMAND [ARGS]...

Commands:
    ensure-index   Create the vector index for a collection
    ingest         Ingest one or more web pages
    crawl          BFS-crawl a site from a seed URL
    feed           Ingest articles from an RSS or Atom feed
    search         Semantic search over ingested pages
    search-web     Search the live web and ingest results
    extract        Extract structured fields from a web page via JSON Schema

Configure via environment variables:

    BLUE_WEB_EMBEDDING        openai | dummy  (default: dummy)
    OPENAI_API_KEY            required when BLUE_WEB_EMBEDDING=openai
    OPENAI_EMBEDDING_MODEL    default: text-embedding-3-small
    BLUE_WEB_REDIS_HOST       Redis host; omit for no vector store
    BLUE_WEB_REDIS_PORT       default: 6379
    BLUE_WEB_COLLECTION       default collection name (default: web)
    BLUE_WEB_CHUNK_SIZE       default: 1000
    BLUE_WEB_OVERLAP          default: 150
    BLUE_WEB_TIMEOUT          HTTP timeout in seconds; default: 30
    TAVILY_API_KEY            required for search-web
"""
from __future__ import annotations

import json
import os
import sys
from typing import Optional

import click

from blue_data_web_cli.config import get_source


def _trunc(text: str, n: int = 200) -> str:
    return text if len(text) <= n else text[:n] + "…"


def _err(msg: str) -> None:
    click.echo(f"Error: {msg}", err=True)
    sys.exit(1)


def _usage_err(msg: str) -> None:
    click.echo(f"Error: {msg}", err=True)
    sys.exit(2)


def _default_collection() -> str:
    return os.getenv("BLUE_WEB_COLLECTION", "web")


# ── Root group ────────────────────────────────────────────────────────────────

@click.group()
@click.option("--json", "as_json", is_flag=True, default=False, help="Output raw JSON.")
@click.pass_context
def cli(ctx: click.Context, as_json: bool) -> None:
    """blue-web — fetch, extract, chunk, and search web content."""
    ctx.ensure_object(dict)
    ctx.obj["as_json"] = as_json


# ── ensure-index ──────────────────────────────────────────────────────────────

@cli.command("ensure-index")
@click.option("--collection", default=None, help="Collection name (overrides BLUE_WEB_COLLECTION).")
@click.pass_context
def ensure_index(ctx: click.Context, collection: Optional[str]) -> None:
    """Create the vector index for a collection.

    Must be called once before the first ingest when using a vector store.
    Safe to call multiple times — skips creation if the index already exists.
    """
    coll = collection or _default_collection()
    try:
        source = get_source()
        source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": coll})
    except Exception as exc:
        _err(str(exc))

    if ctx.obj["as_json"]:
        click.echo(json.dumps({"collection": coll}))
    else:
        click.echo(f"Index ready: {coll}")


# ── ingest ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("urls", nargs=-1, required=True, metavar="URL [URL]...")
@click.option("--collection", default=None, help="Collection name (overrides BLUE_WEB_COLLECTION).")
@click.pass_context
def ingest(ctx: click.Context, urls: tuple[str, ...], collection: Optional[str]) -> None:
    """Fetch and ingest one or more web pages.

    Extracts main content, strips boilerplate, chunks, enriches, and upserts to
    the configured vector store. Re-ingesting the same URL is safe — URL-hash
    deduplication means the same final URL always produces the same chunk IDs.

    Pages that fail to extract are reported and skipped; the rest continue.
    """
    coll = collection or _default_collection()
    try:
        source = get_source()
    except Exception as exc:
        _err(f"Failed to build source: {exc}")

    per_url: dict[str, list] = {}
    had_failure = False
    for url in urls:
        try:
            records = source.execute_query({"op": "WEB.INGEST", "url": url, "collection": coll})
            per_url[url] = records
            if not records:
                had_failure = True
        except Exception as exc:
            click.echo(f"Error ingesting {url}: {exc}", err=True)
            per_url[url] = []
            had_failure = True

    if ctx.obj["as_json"]:
        output = []
        for url, records in per_url.items():
            if records:
                first = records[0]
                output.append({
                    "page_id": first.get("page_id"),
                    "url": url,
                    "title": first.get("title"),
                    "domain": first.get("domain"),
                    "date": first.get("date"),
                    "chunk_count": len(records),
                    "collection": coll,
                })
            else:
                output.append({"url": url, "error": "no content extracted"})
        click.echo(json.dumps(output[0] if len(output) == 1 else output, indent=2, default=str))
        if had_failure:
            sys.exit(1)
        return

    if len(urls) == 1:
        url = urls[0]
        records = per_url[url]
        if not records:
            _err(f"No content extracted from {url}")
        first = records[0]
        click.echo(f"Ingested {len(records)} chunk{'s' if len(records) != 1 else ''} from {url}")
        if first.get("title"):
            click.echo(f"  title:      {first['title']}")
        click.echo(f"  domain:     {first['domain']}")
        if first.get("date"):
            click.echo(f"  date:       {first['date']}")
        click.echo(f"  collection: {coll}")
    else:
        total_chunks = sum(len(r) for r in per_url.values())
        total_pages = sum(1 for r in per_url.values() if r)
        click.echo(f"Ingested {total_pages} page{'s' if total_pages != 1 else ''}, {total_chunks} chunks")
        for url, records in per_url.items():
            if records:
                click.echo(f"  ✓ {url}  ({len(records)} chunks)")
            else:
                click.echo(f"  ✗ {url}  (no content)")
        click.echo(f"\n  collection: {coll}")

    if had_failure:
        sys.exit(1)


# ── crawl ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("url")
@click.option("--depth", default=1, show_default=True, metavar="N", help="Crawl depth from seed.")
@click.option("--max-pages", "max_pages", default=10, show_default=True, metavar="N", help="Max pages to ingest.")
@click.option("--allow-external", "allow_external", is_flag=True, default=False,
              help="Follow links outside the seed domain.")
@click.option("--collection", default=None, help="Collection name (overrides BLUE_WEB_COLLECTION).")
@click.pass_context
def crawl(
    ctx: click.Context,
    url: str,
    depth: int,
    max_pages: int,
    allow_external: bool,
    collection: Optional[str],
) -> None:
    """BFS-crawl a site from a seed URL and ingest all reachable pages.

    Follows links up to DEPTH hops from the seed. Stops after MAX_PAGES pages
    regardless of depth. By default stays within the seed domain.
    Pages that fail to fetch or extract are skipped silently.
    """
    coll = collection or _default_collection()
    try:
        source = get_source()
        records = source.execute_query({
            "op": "WEB.CRAWL",
            "url": url,
            "depth": depth,
            "max_pages": max_pages,
            "same_domain_only": not allow_external,
            "collection": coll,
        })
    except Exception as exc:
        _err(str(exc))

    ingested_urls = sorted({r["url"] for r in records})

    if ctx.obj["as_json"]:
        click.echo(json.dumps({
            "chunk_count": len(records),
            "pages_ingested": len(ingested_urls),
            "urls": ingested_urls,
            "collection": coll,
        }, indent=2))
        return

    n = len(ingested_urls)
    click.echo(f"Crawled {n} page{'s' if n != 1 else ''}, {len(records)} chunks  [seed: {url}]")
    for u in ingested_urls:
        click.echo(f"  {u}")
    click.echo(f"\n  collection: {coll}")


# ── feed ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("url")
@click.option("--since", metavar="ISO8601", default=None,
              help="Only ingest entries published at or after this date (e.g. 2026-08-01T00:00:00Z).")
@click.option("--max-entries", "max_entries", metavar="N", default=None, type=int,
              help="Max entries to process after date filtering.")
@click.option("--collection", default=None, help="Collection name (overrides BLUE_WEB_COLLECTION).")
@click.pass_context
def feed(
    ctx: click.Context,
    url: str,
    since: Optional[str],
    max_entries: Optional[int],
    collection: Optional[str],
) -> None:
    """Ingest articles from an RSS or Atom feed.

    Fetches the feed, parses all entries, and ingests each article URL. Use
    --since to skip entries older than a cutoff — useful for daily incremental
    runs. Entries without a publication date are always included.

    Requires: pip install 'blue-data-web-source[feed]'
    """
    coll = collection or _default_collection()
    request: dict = {"op": "WEB.INGEST_FEED", "url": url, "collection": coll}
    if since:
        request["since"] = since
    if max_entries is not None:
        request["max_entries"] = max_entries

    try:
        source = get_source()
        records = source.execute_query(request)
    except Exception as exc:
        _err(str(exc))

    ingested_urls = sorted({r["url"] for r in records})

    if ctx.obj["as_json"]:
        click.echo(json.dumps({
            "chunk_count": len(records),
            "articles_ingested": len(ingested_urls),
            "urls": ingested_urls,
            "collection": coll,
        }, indent=2))
        return

    n = len(ingested_urls)
    click.echo(f"Feed: {url}")
    click.echo(f"  {n} article{'s' if n != 1 else ''} ingested, {len(records)} chunks")
    for u in ingested_urls:
        click.echo(f"  {u}")
    click.echo(f"\n  collection: {coll}")


# ── extract ───────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("url")
@click.option("--schema", "schema_str", default=None, metavar="JSON",
              help="JSON Schema as a JSON string.")
@click.option("--schema-file", "schema_file", default=None,
              type=click.Path(exists=True), metavar="FILE",
              help="Path to a JSON Schema file.")
@click.pass_context
def extract(
    ctx: click.Context,
    url: str,
    schema_str: Optional[str],
    schema_file: Optional[str],
) -> None:
    """Extract structured fields from a web page via a JSON Schema.

    Fetches the URL, extracts main body text, and calls an LLM to fill in the
    fields described by the schema. Pass the schema inline as a JSON string or
    via a file.

    Examples:

    \b
        blue-web extract https://example.com/product \\
            --schema '{"properties": {"title": {"type": "string"}, "price": {"type": "number"}}}'
        blue-web extract https://example.com/product --schema-file product_schema.json

    Requires OPENAI_API_KEY.
    """
    if not schema_str and not schema_file:
        _usage_err("Provide --schema JSON or --schema-file FILE")

    try:
        if schema_file:
            with open(schema_file) as fh:
                schema = json.load(fh)
        else:
            schema = json.loads(schema_str)
    except Exception as exc:
        _usage_err(f"Invalid schema: {exc}")

    try:
        source = get_source()
        result = source.execute_query({"op": "WEB.EXTRACT", "url": url, "schema": schema})
    except Exception as exc:
        _err(str(exc))

    if ctx.obj["as_json"]:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    if not result:
        click.echo("No fields extracted (page may have no extractable content).")
        return

    click.echo(f"Extracted from {url}\n")
    for key, val in result.items():
        click.echo(f"  {key}: {val}")


# ── Shared result printer ─────────────────────────────────────────────────────

def _print_results(results: list[dict], query: str) -> None:
    if not results:
        click.echo("No results.")
        return
    n = len(results)
    click.echo(f"{n} result{'s' if n != 1 else ''} for \"{query}\"\n")
    for i, r in enumerate(results, 1):
        heading = r.get("domain", "")
        if r.get("title"):
            heading += f" — \"{r['title']}\""
        click.echo(f"{i}. {heading}")
        if r.get("section_title"):
            click.echo(f"   § {r['section_title']}")
        click.echo(f"   {_trunc(r.get('text', ''))}")
        click.echo(f"   {r.get('url', '')}")
        click.echo()


# ── search ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("query")
@click.option("-k", default=5, show_default=True, metavar="N", help="Number of results.")
@click.option("--collection", default=None, help="Collection name (overrides BLUE_WEB_COLLECTION).")
@click.pass_context
def search(ctx: click.Context, query: str, k: int, collection: Optional[str]) -> None:
    """Search ingested pages by semantic similarity.

    Embeds the query and returns the k most relevant chunks from the vector
    store. Requires BLUE_WEB_REDIS_HOST and an indexed collection.
    """
    coll = collection or _default_collection()
    try:
        source = get_source()
        results = source.execute_query({"op": "WEB.SEARCH", "query": query, "k": k, "collection": coll})
    except Exception as exc:
        _err(str(exc))

    if ctx.obj["as_json"]:
        click.echo(json.dumps({"results": results, "count": len(results)}, indent=2, default=str))
        return
    _print_results(results, query)


# ── search-web ────────────────────────────────────────────────────────────────

@cli.command("search-web")
@click.argument("query")
@click.option("-k", default=5, show_default=True, metavar="N", help="Number of URLs to fetch from the search API.")
@click.option("--collection", default=None, help="Collection name (overrides BLUE_WEB_COLLECTION).")
@click.pass_context
def search_web(ctx: click.Context, query: str, k: int, collection: Optional[str]) -> None:
    """Search the live web, ingest the top results, and return ranked chunks.

    Calls Tavily to find the k most relevant URLs, fetches and ingests each
    on the fly, then returns semantically ranked chunks. Results are always fresh
    rather than from a pre-built index.

    Requires TAVILY_API_KEY and BLUE_WEB_REDIS_HOST.
    """
    coll = collection or _default_collection()
    try:
        source = get_source()
        results = source.execute_query({"op": "WEB.SEARCH_WEB", "query": query, "k": k, "collection": coll})
    except Exception as exc:
        _err(str(exc))

    if ctx.obj["as_json"]:
        click.echo(json.dumps({"results": results, "count": len(results)}, indent=2, default=str))
        return
    _print_results(results, query)


if __name__ == "__main__":
    cli()
