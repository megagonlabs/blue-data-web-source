"""
Ingest one or more URLs into a local (in-memory / no vector store) pipeline
and print the resulting chunks.

Usage:
    python examples/ingest_url.py https://example.com
    python examples/ingest_url.py https://example.com --collection news
"""

import argparse
import json
import sys

from blue_web_source import WebDataSource


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a URL with WebDataSource")
    parser.add_argument("url", help="URL to ingest")
    parser.add_argument("--collection", default="web", help="Collection name (default: web)")
    args = parser.parse_args()

    source = WebDataSource(
        name="demo",
        default_collection=args.collection,
    )

    print(f"Ingesting {args.url} ...", file=sys.stderr)
    records = source.execute_query(
        {"op": "WEB.INGEST", "url": args.url, "collection": args.collection}
    )

    if not records:
        print("No content extracted.", file=sys.stderr)
        sys.exit(1)

    print(f"Extracted {len(records)} chunk(s).\n", file=sys.stderr)
    for record in records:
        print(json.dumps({k: v for k, v in record.items() if k != "embedding"}, indent=2))
        print()


if __name__ == "__main__":
    main()
