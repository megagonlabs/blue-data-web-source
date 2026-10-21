"""Adopt blue-data's DataSource conformance kit (#81, cross-repo XR-02).

Subclasses ``blue_data.testing.conformance.DataSourceConformance`` so this source
is verified against the DataSource contract (blue-data design/14): it is a
DataSource, accepts the registry's ``execute_query_nl`` signature, overrides no
sync base method with ``async def``, is reconstructible from ``(name, properties)``
alone, tolerates reserved/shared-optional properties, and returns ``list[dict]``.

The ``data_source`` fixture is built from ``properties`` ONLY (no Python-object
collaborators) — the same path ``DataRegistry.connect_source`` uses to rebuild a
source from its stored record. The ``sample_query`` is a read-only ``WEB.SEARCH``,
so the behavioural checks confirm a properties-only source can actually answer a
query and return ``list[dict]``.

Allowed exceptions to §1 (list[dict]) — documented, not fixed (#81):
``WEB.EXTRACT`` with a single ``url`` returns one ``dict`` and ``WEB.FIND`` with
``coverage=True`` returns a ``FindResult``. These are deliberate ergonomic shapes on
the *direct* API; neither is reachable through the registry, because the NL parser
(``NLQueryParser``) exposes no extract/find tool — so a ``DataRegistry``-hosted
source only ever sees the list[dict] ops exercised here. All registry-reachable ops
(INGEST, SEARCH, CRAWL, SEARCH_WEB, INGEST_FEED, ENSURE_INDEX) return ``list[dict]``.
"""
import pytest

from blue_data.testing.conformance import DataSourceConformance

from blue_data_web_source import WebDataSource


class TestWebSourceConformance(DataSourceConformance):
    @pytest.fixture
    def data_source(self):
        source = WebDataSource(
            "t",
            properties={
                "embedding": "dummy",
                "vector": "memory",
                "collection": "t",
            },
        )
        # A properties-only source must be able to search; make the index exist.
        source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": "t"})
        return source

    @pytest.fixture
    def sample_query(self):
        # Read-only: embeds the query and runs KNN over the (empty) index → [].
        return ({"op": "WEB.SEARCH", "query": "hello world", "collection": "t"}, {})
