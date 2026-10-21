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
BLUE_WEB_API_KEY=choose-a-secret \
blue-data-web-api           # binds 127.0.0.1:8002 by default
```

## Security & binding

The service uses the shared `blue_data.server` policy (cross-repo XR-08):

| Variable | Default | Effect |
|---|---|---|
| `BLUE_WEB_API_HOST` | `127.0.0.1` | Bind host — loopback only unless overridden |
| `BLUE_WEB_API_PORT` | `8002` | Bind port (family port map: registry 8000, doc 8001, web 8002) |
| `BLUE_WEB_API_KEY` | _(unset)_ | Required on every data route; send as `X-API-Key: <key>` or `Authorization: Bearer <key>`. Without a key configured, data routes return **401** |
| `BLUE_WEB_API_AUTH_DISABLED` | _(unset)_ | Set truthy (`1`/`true`/`yes`) to run with no API key (local/dev) |

State-changing routes (`POST`/`PUT`/`PATCH`/`DELETE`) require an `application/json`
body; form, multipart, or text bodies are refused with **415** (CSRF protection).
`/web/health` and the docs routes (`/docs`, `/redoc`, `/openapi.json`) stay open.

Note: the default bind is loopback. Set `BLUE_WEB_API_HOST=0.0.0.0` only behind a
trusted proxy or network boundary, and always with `BLUE_WEB_API_KEY` set.

## Testing

```bash
.venv/bin/pytest platform/web-api/tests/ -v
```

Tests use FastAPI's `TestClient` with a stubbed `WebDataSource`, so no network or Redis is required. The functional route tests run with `BLUE_WEB_API_AUTH_DISABLED=true`; `test_security.py` covers the API-key (401), CSRF (415), and loopback-bind defaults.

## Docs

- [REST API reference](../../docs/api.md)
- [Installation](../../docs/installation.md)
