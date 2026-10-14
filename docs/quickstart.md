# Quickstart

Get from a URL to searchable chunks in under five minutes.

---

## 1. Install

Clone the sibling repos alongside this one, then install all three in editable mode:

```bash
pip install -e ../blue-data/lib
pip install -e ../blue-data-doc-source/lib
pip install -e lib/
```

> The installable packages live in each sibling repo's `lib/` subdirectory, so the paths end in `/lib`. Replace `../blue-data` and `../blue-data-doc-source` with the actual paths where you cloned those repos.

---

## 2. Ingest a page (no API key required)

```python
from blue_data_web_source import WebDataSource
from blue_data_doc_source.source.document.embeddings.dummy_embedding import DummyEmbeddingModel
from blue_data.source.vector.memory_vector_source import MemoryVectorSource

source = WebDataSource(
    embedding_model=DummyEmbeddingModel(),
    vector_source=MemoryVectorSource(),
)
source.execute_query({"op": "WEB.ENSURE_INDEX"})
chunks = source.execute_query({"op": "WEB.INGEST", "url": "https://python.org"})

for c in chunks:
    print(c["chunk_id"], c["domain"], c["text"][:80])
```

`DummyEmbeddingModel` produces SHA-256 hash vectors — no OpenAI key needed. `MemoryVectorSource` holds the index in-process; data is lost when the process exits.

---

## 3. Search

```python
results = source.execute_query({"op": "WEB.SEARCH", "query": "download Python", "k": 3})
for r in results:
    print(r["url"], r["text"][:120])
```

> **Note:** with `DummyEmbeddingModel` the ranking is **not semantic** — the hash vectors are deterministic but carry no meaning, so changing the query will not surface topically relevant chunks (you will get results, just not meaningfully ranked). This step only exercises the search mechanics. For real relevance — e.g. a "jobs" query returning the careers section — use real embeddings as shown in section 4.

---

## 4. Add Redis + real embeddings (production)

> Requires Docker, a running Redis Stack instance, and an OpenAI API key. Replace `sk-...` with your actual key.

```bash
docker run -d -p 6379:6379 redis/redis-stack-server:latest
export BLUE_WEB_REDIS_HOST=localhost
export BLUE_WEB_EMBEDDING=openai
export OPENAI_API_KEY=sk-...
```

```python
from blue_data_web_source import WebDataSource

source = WebDataSource.from_env()
source.execute_query({"op": "WEB.ENSURE_INDEX"})
source.execute_query({"op": "WEB.INGEST", "url": "https://python.org"})
results = source.execute_query({"op": "WEB.SEARCH", "query": "download Python", "k": 5})
```

---

## Next steps

- [Guide](guide.md) — recipes for crawling, feeds, live web search, and natural-language queries
- [Features](features.md) — every op with code examples
- [Installation](installation.md) — extras, optional dependencies, environment variables
