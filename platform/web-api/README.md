# blue-data-web-source — REST API

FastAPI service exposing all web ops over HTTP on port 8002. Eight routes covering ingest, crawl, feed, search, search-web, extract, ensure-index, and health.

## Install

```bash
pip install -e /path/to/blue-data-web-source/lib
pip install -e .
```

## Run

```bash
BLUE_WEB_EMBEDDING=openai OPENAI_API_KEY=sk-... \
BLUE_WEB_REDIS_HOST=localhost \
uvicorn web_api.main:app --port 8002
```

## Docs

- [REST API reference](../../docs/api.md)
- [Installation](../../docs/installation.md)
