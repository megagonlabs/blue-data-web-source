from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from .fetch_policy import DestinationPolicy


_DEFAULT_USER_AGENT = "blue-data-web-source/0.1 (compatible; httpx)"
_log = logging.getLogger(__name__)


class _PolicyTransport(httpx.HTTPTransport):
    """httpx transport that vets each request's destination before connecting.

    httpx invokes the transport once per request **including every redirect
    hop**, so routing all fetches through it applies the
    :class:`DestinationPolicy` uniformly to the initial URL and each redirect
    (#112). Test suites that stub HTTP (``pytest-httpx``) swap the transport out,
    so the policy only runs on genuine network fetches.
    """

    def __init__(self, policy: DestinationPolicy, **kwargs) -> None:
        super().__init__(**kwargs)
        self._policy = policy

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self._policy.check_url(str(request.url))
        return super().handle_request(request)


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
    def __init__(
        self,
        timeout: float = 30.0,
        headers: dict | None = None,
        policy: Optional[DestinationPolicy] = None,
    ):
        self._timeout = timeout
        self._headers = {
            "User-Agent": _DEFAULT_USER_AGENT,
            **(headers or {}),
        }
        # Block loopback/private/link-local/metadata destinations by default so a
        # caller-chosen URL can't make the server reach internal addresses (#112).
        self._policy = policy if policy is not None else DestinationPolicy()

    def fetch(self, url: str) -> FetchResult:
        _log.debug("fetching %s", url)
        transport = _PolicyTransport(self._policy)
        with httpx.Client(
            timeout=self._timeout, follow_redirects=True, transport=transport
        ) as client:
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
