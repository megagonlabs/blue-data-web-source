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
    TAVILY_API_KEY              required for /web/search-web (Tavily backend)
"""
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from web_api.routers import web


@asynccontextmanager
async def lifespan(app: FastAPI):
    from web_api.config import get_source
    get_source()
    yield


app = FastAPI(
    title="blue-data-web-source API",
    description="REST API for ingesting, crawling, extracting, and searching web content.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(web.router)


def run():
    uvicorn.run("web_api.main:app", host="0.0.0.0", port=8002, reload=False)


if __name__ == "__main__":
    run()
