"""blue-data-web-source REST API.

Run:
    # install
    pip install -e ../../lib && pip install -e .

    # start (auto-reload for dev)
    uvicorn web_api.main:app --reload --port 8002

    # or via the installed script
    blue-data-web-api

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
    BLUE_WEB_MAX_WORKERS        concurrent fetch threads; default: 10
    BLUE_WEB_ENRICHER           none | basic | llm  (default: basic)
    BLUE_WEB_LLM_MODEL          OpenAI model for llm enricher; default: gpt-4o-mini
    BLUE_WEB_FRESHNESS          file | memory | none  (default: file)
    BLUE_WEB_FRESHNESS_PATH     file store path; default: ~/.blue-web/freshness.json
    TAVILY_API_KEY              required for /web/search-web (Tavily backend)

Server policy (via blue_data.server, cross-repo XR-08):

    BLUE_WEB_API_HOST           bind host; default 127.0.0.1 (loopback only)
    BLUE_WEB_API_PORT           bind port; default 8002 (family port map)
    BLUE_WEB_API_KEY            API key required on every data route (sent as
                                X-API-Key or Authorization: Bearer). Without it,
                                data routes return 401.
    BLUE_WEB_API_AUTH_DISABLED  set truthy (1/true/yes) to run with no API key

State-changing routes (POST/PUT/PATCH/DELETE) require an application/json body;
form, multipart, or text bodies are refused with 415 (CSRF protection). The
health and docs paths (/web/health, /docs, /redoc, /openapi.json) stay open.
"""
from contextlib import asynccontextmanager

import uvicorn
from blue_data.server import DEFAULT_OPEN_PATHS, resolve_bind, security_dependencies
from fastapi import FastAPI

from web_api.routers import web


@asynccontextmanager
async def lifespan(app: FastAPI):
    from web_api.config import get_source
    get_source()
    yield


# /web/health is the service health probe; the default open paths cover the
# unprefixed docs routes. Everything else requires an API key and a JSON body.
_OPEN_PATHS = DEFAULT_OPEN_PATHS | {"/web/health"}

app = FastAPI(
    title="blue-data-web-source API",
    description="REST API for ingesting, crawling, extracting, and searching web content.",
    version="1.0.0",
    lifespan=lifespan,
    dependencies=security_dependencies("BLUE_WEB_API", open_paths=_OPEN_PATHS),
)

app.include_router(web.router)


def run():
    # Loopback by default; host/port from BLUE_WEB_API_HOST / BLUE_WEB_API_PORT.
    host, port = resolve_bind("BLUE_WEB_API", default_port=8002)
    uvicorn.run("web_api.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    run()
