from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import List


class WebSearchProvider(ABC):
    """ABC for web search backends that return URLs for a query."""

    @abstractmethod
    def search(self, query: str, k: int = 5) -> List[str]:
        """Return up to *k* URLs relevant to *query*."""


class TavilySearchProvider(WebSearchProvider):
    """Queries the Tavily Search API and returns result URLs.

    Requires the ``tavily-python`` package::

        pip install tavily-python

    The API key is read from the ``TAVILY_API_KEY`` environment variable
    when *api_key* is not supplied.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("TAVILY_API_KEY")

    def search(self, query: str, k: int = 5) -> List[str]:
        try:
            from tavily import TavilyClient
        except ImportError as exc:
            raise ImportError(
                "tavily-python is required for TavilySearchProvider. "
                "Install it with: pip install tavily-python"
            ) from exc
        client = TavilyClient(api_key=self._api_key)
        response = client.search(query, max_results=k)
        return [r["url"] for r in response.get("results", [])]
