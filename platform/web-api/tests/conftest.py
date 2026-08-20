"""Shared fixtures for web-api tests."""
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from web_api.main import app
from web_api import config as _config_module


def _make_source(default_collection: str = "web"):
    source = MagicMock()
    source._pipeline._collection = default_collection
    return source


@pytest.fixture()
def mock_source():
    source = _make_source()
    _config_module._source = source
    yield source
    _config_module._source = None


@pytest.fixture()
def client(mock_source):
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
