from __future__ import annotations

from dataclasses import dataclass

import httpx


_DEFAULT_USER_AGENT = "blue-data-web-source/0.1 (compatible; httpx)"


@dataclass
class FetchResult:
    url: str
    final_url: str
    html: str
    status_code: int
    content_type: str


class WebFetcher:
    def __init__(self, timeout: float = 30.0, headers: dict | None = None):
        self._timeout = timeout
        self._headers = {
            "User-Agent": _DEFAULT_USER_AGENT,
            **(headers or {}),
        }

    def fetch(self, url: str) -> FetchResult:
        with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
            response = client.get(url, headers=self._headers)
            response.raise_for_status()
            return FetchResult(
                url=url,
                final_url=str(response.url),
                html=response.text,
                status_code=response.status_code,
                content_type=response.headers.get("content-type", ""),
            )
