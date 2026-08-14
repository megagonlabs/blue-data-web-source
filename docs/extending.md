# Extending

## WebSearchProvider

`WebSearchProvider` is the primary extension point unique to web-source. Implement it to add any search API backend for the `WEB.SEARCH_WEB` op.

```python
class WebSearchProvider(ABC):
    def search(self, query: str, k: int = 5) -> list[str]:
        """Return up to k URLs ranked by the provider."""
        ...
```

The contract is minimal: receive a query string, return a list of URLs. `WebDataSource` ingests each URL through the full pipeline and ranks the resulting chunks by vector similarity.

### Example: Brave Search

```python
from blue_data_web_source.source.web.search_provider import WebSearchProvider
import httpx

class BraveSearchProvider(WebSearchProvider):
    def __init__(self, api_key: str) -> None:
        self._key = api_key

    def search(self, query: str, k: int = 5) -> list[str]:
        resp = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": k},
            headers={"Accept": "application/json", "X-Subscription-Token": self._key},
        )
        resp.raise_for_status()
        return [r["url"] for r in resp.json()["web"]["results"][:k]]
```

Wire it in:

```python
from blue_data_web_source import WebDataSource

source = WebDataSource(
    name="research",
    search_provider=BraveSearchProvider(api_key="..."),
    ...
)
```

### Example: SerpAPI

```python
from blue_data_web_source.source.web.search_provider import WebSearchProvider

class SerpAPIProvider(WebSearchProvider):
    def __init__(self, api_key: str) -> None:
        self._key = api_key

    def search(self, query: str, k: int = 5) -> list[str]:
        import httpx
        resp = httpx.get(
            "https://serpapi.com/search",
            params={"q": query, "api_key": self._key, "num": k, "engine": "google"},
        )
        resp.raise_for_status()
        return [r["link"] for r in resp.json().get("organic_results", [])[:k]]
```

---

## Chunker, EmbeddingModel, Enricher

These extension points are provided by `blue-data-doc-source` and shared with web-source by import — no reimplementation here.

- **Chunker ABC** — split text into `(section_title, chunk_text)` pairs
- **EmbeddingModel ABC** — implement `dim` property and `embed_text(text) -> list[float]`
- **Enricher ABC** — implement `enrich(text) -> {"entities": [...], "semantic_hints": {...}}`

See [blue-data-doc-source docs/extending.md](../../blue-data-doc-source/docs/extending.md) for full examples of each.

Pass custom implementations to `WebDataSource` or `WebPipeline` via the `chunker=`, `embedding_model=`, and `enricher=` constructor arguments — the signature is identical to `DocumentDataSource`.

---

## Custom NL parse_fn

Override the built-in Claude tool-use call with any callable that accepts a text string and returns a valid `WEB.*` op dict:

```python
from blue_data_web_source import WebDataSource

def my_llm_parser(text: str) -> dict:
    # call any LLM or rule engine; must return a valid op dict
    ...
    return {"op": "WEB.SEARCH", "query": extracted_query, "k": 5}

source = WebDataSource(
    name="news",
    ...
)

# Monkey-patch the NL parser before first use
from blue_data_web_source.source.web.nl import NLQueryParser
source._nl_parser = NLQueryParser(parse_fn=my_llm_parser)
```

The returned dict is passed directly to `execute_query`, so it must contain a valid `op` field and all required fields for that op.
