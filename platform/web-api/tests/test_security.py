"""Server-policy tests for the web API — blue_data.server adoption (#75 / XR-08).

Covers the three guarantees the shared helper adds: an API key is required on data
routes by default (401 without it), state-changing routes refuse form-style bodies
(415, CSRF protection), and the service binds to loopback:8002 by default.
"""
import pytest
from fastapi.testclient import TestClient

from blue_data.server import resolve_bind
from web_api.main import app


@pytest.fixture()
def _default_policy(monkeypatch):
    """No key configured and auth NOT disabled — the mandatory-key default."""
    monkeypatch.delenv("BLUE_WEB_API_KEY", raising=False)
    monkeypatch.delenv("BLUE_WEB_API_AUTH_DISABLED", raising=False)


def test_missing_api_key_returns_401(mock_source, _default_policy):
    with TestClient(app) as c:
        r = c.post("/web/ingest", json={"url": "https://example.com"})
    assert r.status_code == 401


def test_health_is_open_without_key(mock_source, _default_policy):
    with TestClient(app) as c:
        r = c.get("/web/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_valid_api_key_is_accepted(mock_source, monkeypatch):
    monkeypatch.setenv("BLUE_WEB_API_KEY", "s3cret")
    monkeypatch.delenv("BLUE_WEB_API_AUTH_DISABLED", raising=False)
    mock_source.execute_query.return_value = []
    with TestClient(app) as c:
        r = c.post(
            "/web/ingest",
            json={"url": "https://example.com"},
            headers={"X-API-Key": "s3cret"},
        )
    assert r.status_code == 200


def test_form_encoded_post_returns_415(mock_source, monkeypatch):
    # Auth disabled so the request clears the key guard and reaches the CSRF guard.
    monkeypatch.setenv("BLUE_WEB_API_AUTH_DISABLED", "true")
    with TestClient(app) as c:
        r = c.post("/web/ingest", data={"url": "https://example.com"})
    assert r.status_code == 415


def test_default_bind_is_loopback_8002(monkeypatch):
    monkeypatch.delenv("BLUE_WEB_API_HOST", raising=False)
    monkeypatch.delenv("BLUE_WEB_API_PORT", raising=False)
    assert resolve_bind("BLUE_WEB_API", default_port=8002) == ("127.0.0.1", 8002)


def test_bind_honours_env_overrides(monkeypatch):
    monkeypatch.setenv("BLUE_WEB_API_HOST", "0.0.0.0")
    monkeypatch.setenv("BLUE_WEB_API_PORT", "9999")
    assert resolve_bind("BLUE_WEB_API", default_port=8002) == ("0.0.0.0", 9999)
