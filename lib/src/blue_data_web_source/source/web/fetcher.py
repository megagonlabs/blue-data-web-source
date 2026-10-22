from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx


_DEFAULT_USER_AGENT = "blue-data-web-source/0.1 (compatible; httpx)"
_log = logging.getLogger(__name__)


@dataclass
class FetchResult:
    url: str
    final_url: str
    html: str
    status_code: int
    content_type: str
    # Raw, undecoded response body. Used where the bytes must be treated strictly
    # as data (e.g. feed parsing, #43) rather than re-decoded or interpreted.
    content: bytes = b""


class WebFetcher:
    def __init__(self, timeout: float = 30.0, headers: dict | None = None):
        self._timeout = timeout
        self._headers = {
            "User-Agent": _DEFAULT_USER_AGENT,
            **(headers or {}),
        }

    def fetch(self, url: str) -> FetchResult:
        _log.debug("fetching %s", url)
        with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
            response = client.get(url, headers=self._headers)
            response.raise_for_status()
            result = FetchResult(
                url=url,
                final_url=str(response.url),
                html=response.text,
                status_code=response.status_code,
                content_type=response.headers.get("content-type", ""),
                content=response.content,
            )
            _log.debug(
                "fetched %s  [%d %s, %d chars]",
                result.final_url,
                result.status_code,
                result.content_type.split(";")[0],
                len(result.html),
            )
            return result
