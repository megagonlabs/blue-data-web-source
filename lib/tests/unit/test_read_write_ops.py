"""WebDataSource declares every op as a read or a write (#106).

blue-data's registry refuses an op called without ``optional_properties={"commit":
True}`` when the source lists it in ``_WRITE_OPS``, and (deny-by-default,
blue-data#237) when the source does not list it in ``_READ_OPS``. These tests pin
the classification and check that the registry refuses each write before it
touches the vector store or fetches a URL.
"""
import inspect
import re

import pytest

from blue_data.registry.data_registry import DataRegistry

from blue_data_web_source import WebDataSource

WRITE_OPS = {"WEB.INGEST", "WEB.INGEST_FEED", "WEB.CRAWL", "WEB.SEARCH_WEB", "WEB.ENSURE_INDEX"}


def _served_ops():
    """Every ``WEB.*`` op ``execute_query`` dispatches on."""
    src = inspect.getsource(WebDataSource.execute_query)
    return set(re.findall(r'op == "(WEB\.[A-Z_]+)"', src))


def test_every_served_op_is_declared_exactly_once():
    served = _served_ops()
    assert served, "found no dispatched ops; update the parser in this test"
    assert WebDataSource._READ_OPS.isdisjoint(WebDataSource._WRITE_OPS)
    assert WebDataSource._READ_OPS | WebDataSource._WRITE_OPS == served


def test_write_ops_are_the_ones_that_store_or_index():
    assert WebDataSource._WRITE_OPS == WRITE_OPS


@pytest.fixture
def registry_and_source():
    src = WebDataSource(
        "web",
        properties={"embedding": "dummy", "vector": "memory", "freshness": "none", "collection": "demo"},
    )
    reg = DataRegistry()
    reg.register_source(src)
    return reg, src


@pytest.mark.parametrize("op", sorted(WRITE_OPS))
def test_registry_refuses_write_without_commit(registry_and_source, op, monkeypatch):
    reg, src = registry_and_source
    called = []
    for handler in ("_ingest", "_ingest_feed", "_crawl", "_search_web", "_ensure_index"):
        monkeypatch.setattr(src, handler, lambda *a, **k: called.append(op))
    with pytest.raises(ValueError, match="commit"):
        reg.execute_query("web", {"op": op, "url": "http://placeholder.invalid/page", "query": "q"})
    assert called == []


def test_registry_allows_ensure_index_with_commit(registry_and_source):
    reg, _ = registry_and_source
    result = reg.execute_query("web", {"op": "WEB.ENSURE_INDEX"}, optional_properties={"commit": True})
    assert result == [{"status": "ok", "collection": "demo"}]


def test_registry_allows_search_without_commit(registry_and_source):
    reg, src = registry_and_source
    src.execute_query({"op": "WEB.ENSURE_INDEX"})
    assert reg.execute_query("web", {"op": "WEB.SEARCH", "query": "hello"}) == []
