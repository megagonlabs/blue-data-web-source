from unittest.mock import MagicMock, patch

import pytest

from blue_data_web_source.source.web.fetcher import FetchResult, WebFetcher


def _mock_response(text: str, url: str = "https://example.com", status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.status_code = status
    resp.url = url
    resp.headers = {"content-type": "text/html; charset=utf-8"}
    resp.raise_for_status = MagicMock()
    return resp


def test_fetch_returns_fetch_result():
    mock_resp = _mock_response("<html>hello</html>")
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        fetcher = WebFetcher()
        result = fetcher.fetch("https://example.com")

    assert isinstance(result, FetchResult)
    assert result.url == "https://example.com"
    assert result.html == "<html>hello</html>"
    assert result.status_code == 200
    assert "text/html" in result.content_type


def test_fetch_raises_on_http_error():
    mock_resp = _mock_response("", status=404)
    mock_resp.raise_for_status.side_effect = Exception("404 Not Found")

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        fetcher = WebFetcher()
        with pytest.raises(Exception, match="404"):
            fetcher.fetch("https://example.com/missing")


def test_fetch_sends_user_agent():
    mock_resp = _mock_response("<html>ok</html>")
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        fetcher = WebFetcher()
        fetcher.fetch("https://example.com")

        call_kwargs = mock_client.get.call_args[1]
        assert "User-Agent" in call_kwargs["headers"]
        assert "blue-data-web-source" in call_kwargs["headers"]["User-Agent"]


def test_fetch_custom_headers_merged():
    mock_resp = _mock_response("<html>ok</html>")
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        fetcher = WebFetcher(headers={"X-Custom": "value"})
        fetcher.fetch("https://example.com")

        call_kwargs = mock_client.get.call_args[1]
        assert call_kwargs["headers"]["X-Custom"] == "value"
        assert "User-Agent" in call_kwargs["headers"]


def test_fetch_final_url_reflects_redirect():
    # response.url is the post-redirect URL — final_url must capture it, not the original
    mock_resp = _mock_response("<html>ok</html>", url="https://redirected.example.com")
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        fetcher = WebFetcher()
        result = fetcher.fetch("https://original.example.com")

    assert result.url == "https://original.example.com"
    assert result.final_url == "https://redirected.example.com"


def test_fetch_default_timeout_is_30_seconds():
    mock_resp = _mock_response("<html>ok</html>")
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        fetcher = WebFetcher()  # no explicit timeout — should default to 30s
        fetcher.fetch("https://example.com")

        mock_client_cls.assert_called_once()
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["timeout"] == 30.0
        assert kwargs["follow_redirects"] is True
        # A destination-policy transport is attached so SSRF checks run per hop (#112).
        assert "transport" in kwargs


def test_fetch_configures_timeout():
    mock_resp = _mock_response("<html>ok</html>")
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        fetcher = WebFetcher(timeout=30.0)
        fetcher.fetch("https://example.com")

        mock_client_cls.assert_called_once()
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["timeout"] == 30.0
        assert kwargs["follow_redirects"] is True
        # A destination-policy transport is attached so SSRF checks run per hop (#112).
        assert "transport" in kwargs
