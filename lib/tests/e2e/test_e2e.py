"""
End-to-end scenario tests for blue-data-web-source.

Exercises the full stack through WebDataSource.execute_query:
  WEB.ENSURE_INDEX → WEB.INGEST → WEB.SEARCH

Uses:
  - pytest-httpx  to intercept HTTP (no network required)
  - MemoryVectorSource  (in-memory KNN, no Redis)
  - DummyEmbeddingModel (deterministic hash-based vectors, no API key)
  - trafilatura runs for real

Run with:
    pytest lib/tests/e2e/ -v -m e2e
"""
from pathlib import Path

import pytest

from blue_data.source.vector.memory_vector_source import MemoryVectorSource
from blue_doc_source.source.document.embeddings.dummy_embedding import DummyEmbeddingModel

from blue_web_source import WebDataSource

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent.parent.parent.parent / "data" / "fixtures"

_NPR_URL = "https://text.npr.org/nx-s1-5884537"
_CLIMATE_URL = "https://climate.example.com/report"

_CLIMATE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Climate Policy Report 2026</title>
  <meta name="author" content="Research Team">
  <meta name="description" content="An overview of global climate policy developments in 2026.">
</head>
<body>
  <nav><ul><li>Home</li><li>Reports</li><li>Contact</li></ul></nav>
  <article>
    <h1>Climate Policy Report 2026</h1>
    <p>Global greenhouse gas emissions continued to rise in 2025 despite international
    agreements. The Paris Accord signatories convened in March to discuss revised targets
    and implementation timelines. Scientists warned that without immediate action, warming
    could exceed 1.5 degrees Celsius by 2035 according to the latest IPCC assessment.</p>

    <h2>National Commitments</h2>
    <p>Several major economies announced updated nationally determined contributions this
    year. The European Union pledged a 65 percent reduction by 2030 compared to 1990
    levels. The United States committed to net-zero emissions by 2050 with interim targets
    for 2030 and 2035. China indicated that peak emissions would occur no later than 2028.</p>

    <h2>Technology Outlook</h2>
    <p>Renewable energy capacity additions reached a record high globally in 2025. Solar
    photovoltaic technology costs dropped another twelve percent year-on-year. Battery
    storage deployment doubled compared to the prior year. Carbon capture projects received
    substantial new investment commitments across G7 nations and emerging economies alike.</p>
  </article>
  <footer>Copyright 2026 Climate Research Institute. All rights reserved.</footer>
</body>
</html>"""

_BOILERPLATE_HTML = """<!DOCTYPE html>
<html><head><title>App Shell</title></head>
<body><div id="app" data-reactroot=""></div></body>
</html>"""


@pytest.fixture
def npr_html() -> str:
    return (_FIXTURES_DIR / "npr_article.html").read_text(encoding="utf-8")


def _make_source(collection: str = "web", dim: int = 8) -> WebDataSource:
    return WebDataSource(
        name="test",
        embedding_model=DummyEmbeddingModel(dim=dim),
        vector_source=MemoryVectorSource(),
        default_collection=collection,
    )


# ---------------------------------------------------------------------------
# Scenario 1: Full ingest → search cycle
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_e2e_ensure_index_then_ingest_then_search(httpx_mock):
    httpx_mock.add_response(
        url=_CLIMATE_URL, text=_CLIMATE_HTML, status_code=200,
        headers={"content-type": "text/html"},
    )
    source = _make_source()

    source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": "web"})
    records = source.execute_query({"op": "WEB.INGEST", "url": _CLIMATE_URL})
    results = source.execute_query({"op": "WEB.SEARCH", "query": "greenhouse gas emissions", "k": 3})

    assert len(records) > 0
    assert len(results) > 0


@pytest.mark.e2e
def test_e2e_search_results_contain_expected_fields(httpx_mock):
    httpx_mock.add_response(
        url=_CLIMATE_URL, text=_CLIMATE_HTML, status_code=200,
        headers={"content-type": "text/html"},
    )
    source = _make_source()
    source.execute_query({"op": "WEB.ENSURE_INDEX"})
    source.execute_query({"op": "WEB.INGEST", "url": _CLIMATE_URL})
    results = source.execute_query({"op": "WEB.SEARCH", "query": "climate policy", "k": 3})

    assert len(results) > 0
    for r in results:
        assert "url" in r
        assert "text" in r
        assert "chunk_id" in r
        assert "score" in r


@pytest.mark.e2e
def test_e2e_search_results_have_correct_domain(httpx_mock):
    httpx_mock.add_response(
        url=_CLIMATE_URL, text=_CLIMATE_HTML, status_code=200,
        headers={"content-type": "text/html"},
    )
    source = _make_source()
    source.execute_query({"op": "WEB.ENSURE_INDEX"})
    source.execute_query({"op": "WEB.INGEST", "url": _CLIMATE_URL})
    results = source.execute_query({"op": "WEB.SEARCH", "query": "renewables", "k": 5})

    assert all(r["domain"] == "climate.example.com" for r in results)


@pytest.mark.e2e
def test_e2e_search_respects_k_limit(httpx_mock):
    httpx_mock.add_response(
        url=_CLIMATE_URL, text=_CLIMATE_HTML, status_code=200,
        headers={"content-type": "text/html"},
    )
    # Small chunk_size forces multiple chunks so we can test k capping
    source = WebDataSource(
        name="test",
        embedding_model=DummyEmbeddingModel(dim=8),
        vector_source=MemoryVectorSource(),
        chunk_size=80,
        overlap=10,
        default_collection="web",
    )
    source.execute_query({"op": "WEB.ENSURE_INDEX"})
    records = source.execute_query({"op": "WEB.INGEST", "url": _CLIMATE_URL})
    results = source.execute_query({"op": "WEB.SEARCH", "query": "policy", "k": 2})

    assert len(results) <= 2
    assert len(records) > 2  # confirms we have more chunks than k


# ---------------------------------------------------------------------------
# Scenario 2: Re-ingest deduplication
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_e2e_reingest_produces_same_chunk_ids(httpx_mock):
    for _ in range(2):
        httpx_mock.add_response(
            url=_CLIMATE_URL, text=_CLIMATE_HTML, status_code=200,
            headers={"content-type": "text/html"},
        )
    source = _make_source()
    source.execute_query({"op": "WEB.ENSURE_INDEX"})

    first = source.execute_query({"op": "WEB.INGEST", "url": _CLIMATE_URL})
    second = source.execute_query({"op": "WEB.INGEST", "url": _CLIMATE_URL})

    assert [r["chunk_id"] for r in first] == [r["chunk_id"] for r in second]


@pytest.mark.e2e
def test_e2e_reingest_does_not_grow_vector_store(httpx_mock):
    for _ in range(2):
        httpx_mock.add_response(
            url=_CLIMATE_URL, text=_CLIMATE_HTML, status_code=200,
            headers={"content-type": "text/html"},
        )
    source = _make_source()
    source.execute_query({"op": "WEB.ENSURE_INDEX"})

    source.execute_query({"op": "WEB.INGEST", "url": _CLIMATE_URL})
    after_first = source.execute_query({"op": "WEB.SEARCH", "query": "emissions", "k": 100})

    source.execute_query({"op": "WEB.INGEST", "url": _CLIMATE_URL})
    after_second = source.execute_query({"op": "WEB.SEARCH", "query": "emissions", "k": 100})

    assert len(after_first) == len(after_second)


# ---------------------------------------------------------------------------
# Scenario 3: Multi-article ingest and search
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_e2e_multi_article_ingest_total_chunks(httpx_mock, npr_html):
    httpx_mock.add_response(
        url=_CLIMATE_URL, text=_CLIMATE_HTML, status_code=200,
        headers={"content-type": "text/html"},
    )
    httpx_mock.add_response(
        url=_NPR_URL, text=npr_html, status_code=200,
        headers={"content-type": "text/html"},
    )
    source = _make_source()
    source.execute_query({"op": "WEB.ENSURE_INDEX"})

    climate_chunks = source.execute_query({"op": "WEB.INGEST", "url": _CLIMATE_URL})
    npr_chunks = source.execute_query({"op": "WEB.INGEST", "url": _NPR_URL})

    assert len(climate_chunks) > 0
    assert len(npr_chunks) > 0

    results = source.execute_query({"op": "WEB.SEARCH", "query": "policy", "k": 100})
    assert len(results) == len(climate_chunks) + len(npr_chunks)


@pytest.mark.e2e
def test_e2e_multi_article_ingest_domains_present(httpx_mock, npr_html):
    httpx_mock.add_response(
        url=_CLIMATE_URL, text=_CLIMATE_HTML, status_code=200,
        headers={"content-type": "text/html"},
    )
    httpx_mock.add_response(
        url=_NPR_URL, text=npr_html, status_code=200,
        headers={"content-type": "text/html"},
    )
    source = _make_source()
    source.execute_query({"op": "WEB.ENSURE_INDEX"})
    source.execute_query({"op": "WEB.INGEST", "url": _CLIMATE_URL})
    source.execute_query({"op": "WEB.INGEST", "url": _NPR_URL})

    results = source.execute_query({"op": "WEB.SEARCH", "query": "developments", "k": 100})
    domains = {r["domain"] for r in results}

    assert "climate.example.com" in domains
    assert "text.npr.org" in domains


# ---------------------------------------------------------------------------
# Scenario 4: Collection isolation
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_e2e_collection_isolation_ingest_and_search(httpx_mock):
    httpx_mock.add_response(
        url=_CLIMATE_URL, text=_CLIMATE_HTML, status_code=200,
        headers={"content-type": "text/html"},
    )
    source = _make_source()
    source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": "science"})
    source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": "news"})

    source.execute_query({"op": "WEB.INGEST", "url": _CLIMATE_URL, "collection": "science"})

    science_results = source.execute_query(
        {"op": "WEB.SEARCH", "query": "emissions", "k": 5, "collection": "science"}
    )
    news_results = source.execute_query(
        {"op": "WEB.SEARCH", "query": "emissions", "k": 5, "collection": "news"}
    )

    assert len(science_results) > 0
    assert len(news_results) == 0


# ---------------------------------------------------------------------------
# Scenario 5: Edge cases
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_e2e_boilerplate_page_ingests_no_chunks(httpx_mock):
    httpx_mock.add_response(
        url="https://example.com/shell", text=_BOILERPLATE_HTML, status_code=200,
        headers={"content-type": "text/html"},
    )
    source = _make_source()
    source.execute_query({"op": "WEB.ENSURE_INDEX"})
    records = source.execute_query({"op": "WEB.INGEST", "url": "https://example.com/shell"})
    results = source.execute_query({"op": "WEB.SEARCH", "query": "anything", "k": 5})

    assert records == []
    assert results == []


@pytest.mark.e2e
def test_e2e_http_error_propagates(httpx_mock):
    httpx_mock.add_response(url="https://example.com/gone", status_code=404)
    source = _make_source()
    source.execute_query({"op": "WEB.ENSURE_INDEX"})
    with pytest.raises(Exception):
        source.execute_query({"op": "WEB.INGEST", "url": "https://example.com/gone"})


@pytest.mark.e2e
def test_e2e_ensure_index_is_idempotent(httpx_mock):
    httpx_mock.add_response(
        url=_CLIMATE_URL, text=_CLIMATE_HTML, status_code=200,
        headers={"content-type": "text/html"},
    )
    source = _make_source()
    source.execute_query({"op": "WEB.ENSURE_INDEX"})
    source.execute_query({"op": "WEB.ENSURE_INDEX"})  # second call must not raise
    records = source.execute_query({"op": "WEB.INGEST", "url": _CLIMATE_URL})
    assert len(records) > 0


@pytest.mark.e2e
def test_e2e_search_before_any_ingest_returns_empty(httpx_mock):
    source = _make_source()
    source.execute_query({"op": "WEB.ENSURE_INDEX"})
    results = source.execute_query({"op": "WEB.SEARCH", "query": "anything", "k": 5})
    assert results == []


# ---------------------------------------------------------------------------
# Scenario 6: NPR fixture — full E2E with real HTML
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_e2e_npr_fixture_full_cycle(httpx_mock, npr_html):
    httpx_mock.add_response(
        url=_NPR_URL, text=npr_html, status_code=200,
        headers={"content-type": "text/html"},
    )
    source = _make_source(collection="news")
    source.execute_query({"op": "WEB.ENSURE_INDEX", "collection": "news"})
    records = source.execute_query({"op": "WEB.INGEST", "url": _NPR_URL, "collection": "news"})
    results = source.execute_query({"op": "WEB.SEARCH", "query": "missile", "k": 3, "collection": "news"})

    assert len(records) > 0
    assert len(results) > 0
    assert all(r["domain"] == "text.npr.org" for r in results)


@pytest.mark.e2e
def test_e2e_npr_fixture_title_in_search_results(httpx_mock, npr_html):
    httpx_mock.add_response(
        url=_NPR_URL, text=npr_html, status_code=200,
        headers={"content-type": "text/html"},
    )
    source = _make_source()
    source.execute_query({"op": "WEB.ENSURE_INDEX"})
    source.execute_query({"op": "WEB.INGEST", "url": _NPR_URL})
    results = source.execute_query({"op": "WEB.SEARCH", "query": "pacific", "k": 3})

    assert len(results) > 0
    assert all("title" in r for r in results)
    assert "ballistic missile" in results[0]["title"].lower()


@pytest.mark.e2e
def test_e2e_npr_no_nav_fragments_in_search_results(httpx_mock, npr_html):
    httpx_mock.add_response(
        url=_NPR_URL, text=npr_html, status_code=200,
        headers={"content-type": "text/html"},
    )
    source = _make_source()
    source.execute_query({"op": "WEB.ENSURE_INDEX"})
    source.execute_query({"op": "WEB.INGEST", "url": _NPR_URL})
    results = source.execute_query({"op": "WEB.SEARCH", "query": "news", "k": 10})

    all_text = " ".join(r.get("text", "") for r in results)
    assert "Related Story" not in all_text
