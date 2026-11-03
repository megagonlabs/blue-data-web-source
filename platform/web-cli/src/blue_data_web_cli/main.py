"""blue-web — CLI for blue-data-web-source.

Usage:
    blue-web [--profile NAME] [--format FORMAT] COMMAND [ARGS]...

Commands:
    page import    Fetch and import one or more web pages
    page crawl     BFS-crawl a site from a seed URL
    page search    Semantic search over imported pages
    page extract   Extract structured fields from a web page via JSON Schema
    feed import    Import articles from an RSS or Atom feed
    index ensure   Create the vector index for a collection
    live search    Search the live web and import results
    profile        Manage named configuration profiles

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
    BLUE_WEB_FRESHNESS        file | memory | none  (default: file)
    BLUE_WEB_FRESHNESS_PATH   file store path; default: ~/.blue-web/freshness.json
    TAVILY_API_KEY            required for live search
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Optional

import click

from blue_data_web_cli.config import get_source
from blue_data_web_cli.profiles import (
    apply_profile,
    active_profile_name,
    list_profiles,
    get_profile,
    upsert_profile,
    remove_profile,
    set_default,
    default_profile_name,
    ENV_MAP,
)


def _trunc(text: str, n: int = 200) -> str:
    return text if len(text) <= n else text[:n] + "…"


# ── Logging ───────────────────────────────────────────────────────────────────

_LOG_LIMIT_DEFAULT = 200


class _TruncatingFormatter(logging.Formatter):
    def __init__(self, limit: int) -> None:
        super().__init__("%(levelname)s %(name)s: %(message)s")
        self._limit = limit

    def format(self, record: logging.LogRecord) -> str:
        s = super().format(record)
        return s if len(s) <= self._limit else s[:self._limit] + "…"


def _configure_logging(log: bool, log_limit: int) -> None:
    """Enable DEBUG-level logging for blue_data_web_source to stderr."""
    if not log:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_TruncatingFormatter(log_limit))
    logger = logging.getLogger("blue_data_web_source")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)


def _log_options(fn):
    """Decorator that adds --log and --log-limit options to a command."""
    fn = click.option(
        "--log-limit", "log_limit", default=_LOG_LIMIT_DEFAULT, show_default=True, metavar="N",
        help="Truncate each log line to N characters.",
    )(fn)
    fn = click.option(
        "--log", "-l", "log", is_flag=True, default=False,
        help="Log per-step diagnostic info to stderr.",
    )(fn)
    return fn


def _err(msg: str, fmt: str = "table") -> None:
    if fmt in ("json", "nl"):
        click.echo(json.dumps({"error": msg}), err=True)
    else:
        click.echo(f"Error: {msg}", err=True)
    sys.exit(1)


def _usage_err(msg: str, fmt: str = "table") -> None:
    if fmt in ("json", "nl"):
        click.echo(json.dumps({"error": msg}), err=True)
    else:
        click.echo(f"Error: {msg}", err=True)
    sys.exit(2)


def _emit(data: object, fmt: str) -> None:
    if fmt == "json":
        click.echo(json.dumps(data, indent=2, default=str))
    elif fmt == "nl":
        if isinstance(data, list):
            for item in data:
                click.echo(json.dumps(item, default=str))
        else:
            click.echo(json.dumps(data, default=str))


def _default_collection() -> str:
    return os.getenv("BLUE_WEB_COLLECTION", "web")


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


# ── Root group ────────────────────────────────────────────────────────────────

@click.group()
@click.version_option(package_name="blue-data-web-cli")
@click.option("--format", "fmt",
              type=click.Choice(["table", "json", "nl"], case_sensitive=False),
              default=None, envvar="BLUE_DATA_CLI_FORMAT",
              help="Output format: table (default), json, or nl (newline-delimited JSON).")
@click.option("--profile", default=None, envvar="BLUE_DATA_PROFILE", metavar="NAME",
              help="Named configuration profile to use.")
@click.pass_context
def cli(ctx: click.Context, fmt: Optional[str], profile: Optional[str]) -> None:
    """blue-web — fetch, extract, chunk, and search web content."""
    ctx.ensure_object(dict)
    ctx.obj["fmt"] = fmt or "table"
    ctx.obj["profile"] = profile
    apply_profile(profile or active_profile_name())


# ── page ──────────────────────────────────────────────────────────────────────

@cli.group()
def page() -> None:
    """Import, crawl, search, and extract web pages."""


@page.command("import")
@click.argument("urls", nargs=-1, required=True, metavar="URL [URL]...")
@click.option("--collection", default=None, show_default="web", help="Collection name (overrides BLUE_WEB_COLLECTION).")
@_log_options
@click.pass_context
def page_import(ctx: click.Context, urls: tuple[str, ...], collection: Optional[str], log: bool, log_limit: int) -> None:
    """Fetch and import one or more web pages.

    Extracts main content, strips boilerplate, chunks, enriches, and upserts to
    the configured vector store. Re-importing the same URL is safe — URL-hash
    deduplication means the same final URL always produces the same chunk IDs.

    Pages that fail to extract are reported and skipped; the rest continue.
    """
    _configure_logging(log, log_limit)
    fmt = ctx.obj["fmt"]
    coll = collection or _default_collection()
    try:
        source = get_source()
    except Exception as exc:
        _err(f"Failed to build source: {exc}", fmt)

    # Per-URL outcome dicts carry a status (ingested / unchanged / duplicate /
    # no_content / error). unchanged and duplicate are routine successes — a
    # re-import of a tracked page must not be reported as an error (#99).
    per_url: dict[str, dict] = {}
    had_failure = False
    for url in urls:
        try:
            result = source.ingest_result(url, collection=coll)
        except Exception as exc:
            result = {"status": "error", "url": url, "chunk_count": 0,
                      "records": [], "error": str(exc)}
        per_url[url] = result
        if result["status"] in ("no_content", "error"):
            had_failure = True

    if fmt in ("json", "nl"):
        output = []
        for url, result in per_url.items():
            status = result["status"]
            if status == "ingested":
                first = result["records"][0]
                output.append({
                    "status": status,
                    "page_id": result.get("page_id"),
                    "url": url,
                    "title": first.get("title"),
                    "domain": first.get("domain"),
                    "date": first.get("date"),
                    "chunk_count": result["chunk_count"],
                    "collection": coll,
                })
            elif status in ("unchanged", "duplicate"):
                entry = {"status": status, "url": url, "collection": coll}
                if result.get("duplicate_of"):
                    entry["duplicate_of"] = result["duplicate_of"]
                output.append(entry)
            else:
                output.append({"status": status, "url": url,
                               "error": result.get("error", "no content extracted")})
        _emit(output[0] if len(output) == 1 else output, fmt)
        if had_failure:
            sys.exit(1)
        return

    if len(urls) == 1:
        url = urls[0]
        result = per_url[url]
        status = result["status"]
        if status == "no_content":
            _err(f"No content extracted from {url}")
        elif status == "error":
            _err(f"Error importing {url}: {result.get('error', 'unknown error')}")
        elif status == "unchanged":
            click.echo(f"Unchanged: {url} (already up to date)")
            click.echo(f"  collection: {coll}")
        elif status == "duplicate":
            suffix = f" (same content as {result['duplicate_of']})" if result.get("duplicate_of") else ""
            click.echo(f"Duplicate: {url} — content already ingested{suffix}")
            click.echo(f"  collection: {coll}")
        else:
            records = result["records"]
            first = records[0]
            click.echo(f"Imported {len(records)} chunk{'s' if len(records) != 1 else ''} from {url}")
            if first.get("title"):
                click.echo(f"  title:      {first['title']}")
            click.echo(f"  domain:     {first['domain']}")
            if first.get("date"):
                click.echo(f"  date:       {first['date']}")
            click.echo(f"  collection: {coll}")
    else:
        total_chunks = sum(r["chunk_count"] for r in per_url.values())
        total_pages = sum(1 for r in per_url.values() if r["status"] == "ingested")
        click.echo(f"Imported {total_pages} page{'s' if total_pages != 1 else ''}, {total_chunks} chunks")
        for url, result in per_url.items():
            status = result["status"]
            if status == "ingested":
                click.echo(f"  ✓ {url}  ({result['chunk_count']} chunks)")
            elif status == "unchanged":
                click.echo(f"  ≡ {url}  (unchanged)")
            elif status == "duplicate":
                click.echo(f"  ≡ {url}  (duplicate)")
            elif status == "error":
                click.echo(f"  ✗ {url}  ({result.get('error', 'error')})")
            else:
                click.echo(f"  ✗ {url}  (no content)")
        click.echo(f"\n  collection: {coll}")

    if had_failure:
        sys.exit(1)


@page.command("crawl")
@click.argument("url")
@click.option("--depth", default=1, show_default=True, metavar="N", help="Crawl depth from seed.")
@click.option("--max-pages", "max_pages", default=10, show_default=True, metavar="N", help="Max pages to import.")
@click.option("--allow-external", "allow_external", is_flag=True, default=False,
              help="Follow links outside the seed domain.")
@click.option("--collection", default=None, show_default="web", help="Collection name (overrides BLUE_WEB_COLLECTION).")
@_log_options
@click.pass_context
def page_crawl(
    ctx: click.Context,
    url: str,
    depth: int,
    max_pages: int,
    allow_external: bool,
    collection: Optional[str],
    log: bool,
    log_limit: int,
) -> None:
    """BFS-crawl a site from a seed URL and import all reachable pages.

    Follows links up to DEPTH hops from the seed. Stops after MAX_PAGES pages
    regardless of depth. By default stays within the seed domain.
    Pages that fail to fetch or extract are skipped silently.
    """
    _configure_logging(log, log_limit)
    fmt = ctx.obj["fmt"]
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
        _err(str(exc), fmt)

    ingested_urls = sorted({r["url"] for r in records})

    if fmt in ("json", "nl"):
        _emit({
            "chunk_count": len(records),
            "pages_imported": len(ingested_urls),
            "urls": ingested_urls,
            "collection": coll,
        }, fmt)
        return

    n = len(ingested_urls)
    click.echo(f"Crawled {n} page{'s' if n != 1 else ''}, {len(records)} chunks  [seed: {url}]")
    for u in ingested_urls:
        click.echo(f"  {u}")
    click.echo(f"\n  collection: {coll}")


@page.command("search")
@click.argument("query")
@click.option("--limit", "-k", default=5, show_default=True, metavar="N", help="Maximum number of chunks to return.")
@click.option("--collection", default=None, show_default="web", help="Collection name (overrides BLUE_WEB_COLLECTION).")
@click.pass_context
def page_search(ctx: click.Context, query: str, limit: int, collection: Optional[str]) -> None:
    """Search imported pages by semantic similarity.

    Embeds the query and returns the k most relevant chunks from the vector
    store. Requires BLUE_WEB_REDIS_HOST and an indexed collection.
    """
    fmt = ctx.obj["fmt"]
    coll = collection or _default_collection()
    try:
        source = get_source()
        results = source.execute_query({"op": "WEB.SEARCH", "query": query, "k": limit, "collection": coll})
    except Exception as exc:
        _err(str(exc), fmt)

    if fmt == "nl":
        _emit(results, fmt)
        return
    if fmt == "json":
        _emit({"results": results, "count": len(results)}, fmt)
        return
    _print_results(results, query)


@page.command("extract")
@click.argument("url")
@click.option("--schema", "schema_str", default=None, metavar="JSON",
              help="JSON Schema as a JSON string.")
@click.option("--schema-file", "schema_file", default=None,
              type=click.Path(exists=True), metavar="FILE",
              help="Path to a JSON Schema file.")
@_log_options
@click.pass_context
def page_extract(
    ctx: click.Context,
    url: str,
    schema_str: Optional[str],
    schema_file: Optional[str],
    log: bool,
    log_limit: int,
) -> None:
    """Extract structured fields from a web page via a JSON Schema.

    Fetches the URL, extracts main body text, and calls an LLM to fill in the
    fields described by the schema. Pass the schema inline as a JSON string or
    via a file.

    Examples:

    \b
        blue-web page extract https://example.com/product \\
            --schema '{"properties": {"title": {"type": "string"}, "price": {"type": "number"}}}'
        blue-web page extract https://example.com/product --schema-file product_schema.json

    Requires OPENAI_API_KEY.
    """
    _configure_logging(log, log_limit)
    fmt = ctx.obj["fmt"]
    if not schema_str and not schema_file:
        _usage_err("Provide --schema JSON or --schema-file FILE", fmt)

    try:
        if schema_file:
            with open(schema_file) as fh:
                schema = json.load(fh)
        else:
            schema = json.loads(schema_str)
    except Exception as exc:
        _usage_err(f"Invalid schema: {exc}", fmt)

    try:
        source = get_source()
        result = source.execute_query({"op": "WEB.EXTRACT", "url": url, "schema": schema})
    except Exception as exc:
        _err(str(exc), fmt)

    if fmt in ("json", "nl"):
        _emit(result, fmt)
        return

    if not result:
        click.echo("No fields extracted (page may have no extractable content).")
        return

    click.echo(f"Extracted from {url}\n")
    for key, val in result.items():
        click.echo(f"  {key}: {val}")


# ── feed ──────────────────────────────────────────────────────────────────────

@cli.group()
def feed() -> None:
    """Import articles from RSS and Atom feeds."""


@feed.command("import")
@click.argument("url")
@click.option("--since", metavar="ISO8601", default=None,
              help="Only import entries published at or after this date (e.g. 2026-08-01T00:00:00Z).")
@click.option("--max-entries", "max_entries", metavar="N", default=None, type=int,
              help="Max entries to process after date filtering.")
@click.option("--collection", default=None, show_default="web", help="Collection name (overrides BLUE_WEB_COLLECTION).")
@_log_options
@click.pass_context
def feed_import(
    ctx: click.Context,
    url: str,
    since: Optional[str],
    max_entries: Optional[int],
    collection: Optional[str],
    log: bool,
    log_limit: int,
) -> None:
    """Import articles from an RSS or Atom feed.

    Fetches the feed, parses all entries, and imports each article URL. Use
    --since to skip entries older than a cutoff — useful for daily incremental
    runs. Entries without a publication date are always included.

    Requires: pip install 'blue-data-web-source[feed]'
    """
    _configure_logging(log, log_limit)
    fmt = ctx.obj["fmt"]
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
        _err(str(exc), fmt)

    imported_urls = sorted({r["url"] for r in records})

    if fmt in ("json", "nl"):
        _emit({
            "chunk_count": len(records),
            "articles_imported": len(imported_urls),
            "urls": imported_urls,
            "collection": coll,
        }, fmt)
        return

    n = len(imported_urls)
    click.echo(f"Feed: {url}")
    click.echo(f"  {n} article{'s' if n != 1 else ''} imported, {len(records)} chunks")
    for u in imported_urls:
        click.echo(f"  {u}")
    click.echo(f"\n  collection: {coll}")


# ── index ─────────────────────────────────────────────────────────────────────

@cli.group()
def index() -> None:
    """Manage vector indexes."""


@index.command("ensure")
@click.option("--collection", default=None, show_default="web", help="Collection name (overrides BLUE_WEB_COLLECTION).")
@click.pass_context
def index_ensure(ctx: click.Context, collection: Optional[str]) -> None:
    """Create the vector index for a collection.

    Must be called once before the first import when using a vector store.
    Safe to call multiple times — skips creation if the index already exists.
    """
    fmt = ctx.obj["fmt"]
    coll = collection or _default_collection()
    try:
        source = get_source()
        source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": coll})
    except Exception as exc:
        _err(str(exc), fmt)

    if fmt in ("json", "nl"):
        _emit({"collection": coll}, fmt)
    else:
        click.echo(f"Index ready: {coll}")


# ── live ──────────────────────────────────────────────────────────────────────

@cli.group()
def live() -> None:
    """Search the live web."""


@live.command("search")
@click.argument("query")
@click.option("--urls", default=5, show_default=True, metavar="N", help="Number of URLs to fetch from the live search API.")
@click.option("--scope", type=click.Choice(["current_search_results", "collection"]), default="current_search_results",
              show_default=True,
              help="Rank only this search's result pages (default) or the whole collection.")
@click.option("--collection", default=None, show_default="web", help="Collection name (overrides BLUE_WEB_COLLECTION).")
@_log_options
@click.pass_context
def live_search(ctx: click.Context, query: str, urls: int, scope: str, collection: Optional[str], log: bool, log_limit: int) -> None:
    """Search the live web, import the top results, and return ranked chunks.

    Calls Tavily to find the k most relevant URLs, fetches and imports each
    on the fly, then returns semantically ranked chunks. Results are always fresh
    rather than from a pre-built index. By default only the pages this search
    returned are ranked (so an older imported page is never surfaced); use
    --scope collection to rank over everything already imported.

    Requires TAVILY_API_KEY and BLUE_WEB_REDIS_HOST.
    """
    _configure_logging(log, log_limit)
    fmt = ctx.obj["fmt"]
    coll = collection or _default_collection()
    try:
        source = get_source()
        results = source.execute_query({"op": "WEB.SEARCH_WEB", "query": query, "k": urls, "collection": coll, "scope": scope})
    except Exception as exc:
        _err(str(exc), fmt)

    if fmt == "nl":
        _emit(results, fmt)
        return
    if fmt == "json":
        _emit({"results": results, "count": len(results)}, fmt)
        return
    _print_results(results, query)


# ── profile ───────────────────────────────────────────────────────────────────

@cli.group()
def profile() -> None:
    """Manage named configuration profiles.

    Profiles store non-secret settings so you can switch between environments
    (e.g. dev, staging, prod) without re-exporting variables. API keys must
    stay in environment variables — they are never written to a profile.

    Configuration is stored in ~/.blue-web/profiles.json.

    Precedence: shell env var > active profile > built-in default.
    """


def _profile_options(fn):
    fn = click.option("--embedding-model", "embedding_model", default=None, metavar="MODEL",
                      help="OpenAI embedding model name (OPENAI_EMBEDDING_MODEL).")(fn)
    fn = click.option("--llm-model",       "llm_model",       default=None, metavar="MODEL",
                      help="OpenAI model for llm enricher (BLUE_WEB_LLM_MODEL).")(fn)
    fn = click.option("--enricher",                           default=None,
                      type=click.Choice(["none", "basic", "llm"]),
                      help="Enricher backend (BLUE_WEB_ENRICHER).")(fn)
    fn = click.option("--timeout",                            default=None, metavar="SECS", type=float,
                      help="HTTP timeout in seconds (BLUE_WEB_TIMEOUT).")(fn)
    fn = click.option("--overlap",                            default=None, metavar="N", type=int,
                      help="Chunk overlap in words (BLUE_WEB_OVERLAP).")(fn)
    fn = click.option("--chunk-size",      "chunk_size",      default=None, metavar="N", type=int,
                      help="Chunk word-count target (BLUE_WEB_CHUNK_SIZE).")(fn)
    fn = click.option("--embedding",                          default=None,
                      type=click.Choice(["openai", "dummy"]),
                      help="Embedding backend (BLUE_WEB_EMBEDDING).")(fn)
    fn = click.option("--collection",                         default=None, metavar="NAME",
                      help="Default collection name (BLUE_WEB_COLLECTION).")(fn)
    fn = click.option("--redis-port",      "redis_port",      default=None, metavar="PORT", type=int,
                      help="Redis port (BLUE_WEB_REDIS_PORT).")(fn)
    fn = click.option("--redis-host",      "redis_host",      default=None, metavar="HOST",
                      help="Redis host (BLUE_WEB_REDIS_HOST).")(fn)
    return fn


def _collect_settings(
    redis_host, redis_port, collection, embedding,
    chunk_size, overlap, timeout, enricher, llm_model, embedding_model,
) -> dict:
    settings: dict = {}
    if redis_host      is not None: settings["redis_host"]      = redis_host
    if redis_port      is not None: settings["redis_port"]      = redis_port
    if collection      is not None: settings["collection"]      = collection
    if embedding       is not None: settings["embedding"]       = embedding
    if chunk_size      is not None: settings["chunk_size"]      = chunk_size
    if overlap         is not None: settings["overlap"]         = overlap
    if timeout         is not None: settings["timeout"]         = timeout
    if enricher        is not None: settings["enricher"]        = enricher
    if llm_model       is not None: settings["llm_model"]       = llm_model
    if embedding_model is not None: settings["embedding_model"] = embedding_model
    return settings


@profile.command("add")
@click.argument("name")
@_profile_options
@click.pass_context
def profile_add(
    ctx: click.Context, name: str,
    redis_host, redis_port, collection, embedding,
    chunk_size, overlap, timeout, enricher, llm_model, embedding_model,
) -> None:
    """Create a new named profile.

    Fails if a profile with that name already exists — use
    'blue-web profile config NAME' to update an existing one.

    \b
    Example:
        blue-web profile add prod \\
            --redis-host redis.prod.internal \\
            --collection prod-web \\
            --embedding openai
    """
    fmt = ctx.obj["fmt"]
    if get_profile(name) is not None:
        _err(f"Profile '{name}' already exists. Use 'profile config {name}' to update it.", fmt)

    settings = _collect_settings(
        redis_host, redis_port, collection, embedding,
        chunk_size, overlap, timeout, enricher, llm_model, embedding_model,
    )
    if not settings:
        _usage_err("Provide at least one setting (e.g. --redis-host HOST --collection NAME).", fmt)

    upsert_profile(name, settings)
    if fmt in ("json", "nl"):
        _emit({"profile": name, "settings": get_profile(name)}, fmt)
    else:
        click.echo(f"Profile '{name}' created.")


@profile.command("remove")
@click.argument("name")
@click.pass_context
def profile_remove(ctx: click.Context, name: str) -> None:
    """Remove a named profile."""
    fmt = ctx.obj["fmt"]
    if not remove_profile(name):
        _err(f"Profile '{name}' does not exist.", fmt)
    if fmt in ("json", "nl"):
        _emit({"removed": name}, fmt)
    else:
        click.echo(f"Profile '{name}' removed.")


@profile.command("config")
@click.argument("name")
@_profile_options
@click.pass_context
def profile_config(
    ctx: click.Context, name: str,
    redis_host, redis_port, collection, embedding,
    chunk_size, overlap, timeout, enricher, llm_model, embedding_model,
) -> None:
    """Update settings in an existing profile.

    Merges the supplied options into the profile; omitted options are unchanged.
    Use 'blue-web profile add NAME' to create a new profile first.

    \b
    Example:
        blue-web profile config prod --redis-host redis2.prod.internal
    """
    fmt = ctx.obj["fmt"]
    if get_profile(name) is None:
        _err(f"Profile '{name}' does not exist. Use 'profile add {name}' to create it.", fmt)

    settings = _collect_settings(
        redis_host, redis_port, collection, embedding,
        chunk_size, overlap, timeout, enricher, llm_model, embedding_model,
    )
    if not settings:
        _usage_err("Provide at least one setting to update (e.g. --redis-host HOST).", fmt)

    upsert_profile(name, settings)
    if fmt in ("json", "nl"):
        _emit({"profile": name, "settings": get_profile(name)}, fmt)
    else:
        click.echo(f"Profile '{name}' updated.")


@profile.command("list")
@click.pass_context
def profile_list(ctx: click.Context) -> None:
    """List all profiles."""
    fmt = ctx.obj["fmt"]
    names = list_profiles()
    default = default_profile_name()
    active = active_profile_name()

    if fmt in ("json", "nl"):
        _emit({"profiles": names, "default": default, "active": active}, fmt)
        return

    if not names:
        click.echo("No profiles defined.  Use 'blue-web profile add NAME' to create one.")
        return

    for n in names:
        marker = " (default)" if n == default else ""
        click.echo(f"  {n}{marker}")


@profile.command("inspect")
@click.argument("name", required=False, default=None)
@click.pass_context
def profile_inspect(ctx: click.Context, name: Optional[str]) -> None:
    """Show all settings stored in a profile.

    NAME defaults to the currently active profile when omitted.
    """
    fmt = ctx.obj["fmt"]
    resolved = name or active_profile_name()
    if not resolved:
        _usage_err("No profile specified and no active profile is set.", fmt)

    data = get_profile(resolved)
    if data is None:
        _err(f"Profile '{resolved}' does not exist.", fmt)

    if fmt in ("json", "nl"):
        _emit({"profile": resolved, "settings": data}, fmt)
        return

    click.echo(f"Profile: {resolved}")
    if not data:
        click.echo("  (empty)")
        return
    for field, value in sorted(data.items()):
        env_var = ENV_MAP.get(field, field)
        click.echo(f"  {field}: {value}  ({env_var})")


@profile.command("select")
@click.argument("name")
@click.pass_context
def profile_select(ctx: click.Context, name: str) -> None:
    """Set the default profile used when --profile is not specified."""
    fmt = ctx.obj["fmt"]
    if get_profile(name) is None:
        _err(f"Profile '{name}' does not exist.", fmt)
    set_default(name)
    if fmt in ("json", "nl"):
        _emit({"default": name}, fmt)
    else:
        click.echo(f"Default profile set to '{name}'.")


if __name__ == "__main__":
    cli()
