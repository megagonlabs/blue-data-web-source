"""WebDataSource singleton — built once from environment variables, reused across requests.

Environment variables:

    BLUE_WEB_EMBEDDING        openai | dummy  (default: dummy)
    OPENAI_API_KEY            required when BLUE_WEB_EMBEDDING=openai
    OPENAI_EMBEDDING_MODEL    default: text-embedding-3-small
    BLUE_WEB_REDIS_HOST       Redis host; omit to run without a vector store
    BLUE_WEB_REDIS_PORT       default: 6379
    BLUE_WEB_COLLECTION       default collection name (default: web)
    BLUE_WEB_CHUNK_SIZE       default: 1000
    BLUE_WEB_OVERLAP          default: 150
    BLUE_WEB_TIMEOUT          HTTP timeout in seconds; default: 30
    BLUE_WEB_MAX_WORKERS      concurrent fetch threads; default: 10
    BLUE_WEB_ENRICHER         none | basic | llm  (default: basic)
    BLUE_WEB_LLM_MODEL        OpenAI model for llm enricher; default: gpt-4o-mini
"""
from __future__ import annotations

import os

_source = None


def get_source():
    global _source
    if _source is None:
        _source = _build_source()
    return _source


def reset_source() -> None:
    global _source
    _source = None


def _build_source():
    from blue_data_web_source import WebDataSource

    embedding_backend = os.getenv("BLUE_WEB_EMBEDDING", "dummy").lower()
    if embedding_backend == "openai":
        from blue_data_doc_source.source.document.embeddings.openai_embedding import OpenAIEmbeddingModel
        embedding_model = OpenAIEmbeddingModel(
            model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        )
    else:
        from blue_data_doc_source.source.document.embeddings.dummy_embedding import DummyEmbeddingModel
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

    enricher_backend = os.getenv("BLUE_WEB_ENRICHER", "basic").lower()
    if enricher_backend == "none":
        enricher = None
    elif enricher_backend == "llm":
        from blue_data_doc_source.source.document.enrichment import BasicEnricher, LLMEnricher
        llm_model = os.getenv("BLUE_WEB_LLM_MODEL", "gpt-4o-mini")
        def _llm_fn(prompt: str) -> str:
            from openai import OpenAI
            return OpenAI().chat.completions.create(
                model=llm_model,
                messages=[{"role": "user", "content": prompt}],
            ).choices[0].message.content or ""
        enricher = LLMEnricher(llm_fn=_llm_fn, fallback=BasicEnricher())
    else:
        from blue_data_doc_source.source.document.enrichment import BasicEnricher
        enricher = BasicEnricher()

    return WebDataSource(
        name="web-api",
        embedding_model=embedding_model,
        vector_source=vector_source,
        enricher=enricher,
        chunk_size=int(os.getenv("BLUE_WEB_CHUNK_SIZE", "1000")),
        overlap=int(os.getenv("BLUE_WEB_OVERLAP", "150")),
        default_collection=os.getenv("BLUE_WEB_COLLECTION", "web"),
        timeout=float(os.getenv("BLUE_WEB_TIMEOUT", "30")),
        max_workers=int(os.getenv("BLUE_WEB_MAX_WORKERS", "10")),
    )
