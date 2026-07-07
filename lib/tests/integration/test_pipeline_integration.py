"""
Integration tests for WebPipeline.

Uses pytest-httpx to intercept HTTP calls so tests are deterministic and require
no network access. trafilatura runs for real — these tests validate the full
fetch → extract → chunk chain without mocking any layer except HTTP transport.

Run with:
    pytest lib/tests/integration/ -v -m integration
"""
from pathlib import Path

import pytest

from blue_web_source.source.web.pipeline import WebPipeline

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent.parent.parent.parent / "data" / "fixtures"


@pytest.fixture
def npr_article_html() -> str:
    return (_FIXTURES_DIR / "npr_article.html").read_text(encoding="utf-8")


_NPR_URL = "https://text.npr.org/nx-s1-5884537"
_NPR_TITLE = "China test-launches a ballistic missile in the South Pacific and raises regional concerns"


_ARTICLE_HTML = """<!DOCTYPE html>
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
<html>
<head><title>App Shell</title></head>
<body>
  <div id="app" data-reactroot=""></div>
</body>
</html>"""


@pytest.mark.integration
def test_full_pipeline_produces_chunks(httpx_mock):
    httpx_mock.add_response(
        url="https://climate.example.com/report",
        text=_ARTICLE_HTML,
        status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
    )
    pipeline = WebPipeline(chunk_size=60, overlap=10)
    records = pipeline.ingest("https://climate.example.com/report")

    assert len(records) > 0
    assert all(isinstance(r, dict) for r in records)


@pytest.mark.integration
def test_full_pipeline_chunk_fields_populated(httpx_mock):
    httpx_mock.add_response(
        url="https://climate.example.com/report",
        text=_ARTICLE_HTML,
        status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
    )
    pipeline = WebPipeline(chunk_size=60, overlap=10)
    records = pipeline.ingest("https://climate.example.com/report")

    for r in records:
        assert r["url"] == "https://climate.example.com/report"
        assert r["domain"] == "climate.example.com"
        assert r["chunk_index"] >= 0
        assert len(r["text"]) > 0
        assert "fetch_timestamp" in r
        assert "chunk_id" in r


@pytest.mark.integration
def test_full_pipeline_chunk_ids_unique(httpx_mock):
    httpx_mock.add_response(
        url="https://climate.example.com/report",
        text=_ARTICLE_HTML,
        status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
    )
    pipeline = WebPipeline(chunk_size=60, overlap=10)
    records = pipeline.ingest("https://climate.example.com/report")

    chunk_ids = [r["chunk_id"] for r in records]
    assert len(chunk_ids) == len(set(chunk_ids))


@pytest.mark.integration
def test_full_pipeline_title_extracted(httpx_mock):
    httpx_mock.add_response(
        url="https://climate.example.com/report",
        text=_ARTICLE_HTML,
        status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
    )
    pipeline = WebPipeline(chunk_size=500, overlap=50)
    records = pipeline.ingest("https://climate.example.com/report")

    assert len(records) > 0
    assert records[0].get("title") == "Climate Policy Report 2026"


@pytest.mark.integration
def test_full_pipeline_main_content_present(httpx_mock):
    httpx_mock.add_response(
        url="https://climate.example.com/report",
        text=_ARTICLE_HTML,
        status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
    )
    pipeline = WebPipeline(chunk_size=500, overlap=50)
    records = pipeline.ingest("https://climate.example.com/report")

    all_text = " ".join(r["text"] for r in records).lower()
    assert "greenhouse" in all_text or "emissions" in all_text or "climate" in all_text


@pytest.mark.integration
def test_full_pipeline_boilerplate_stripped(httpx_mock):
    httpx_mock.add_response(
        url="https://climate.example.com/report",
        text=_ARTICLE_HTML,
        status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
    )
    pipeline = WebPipeline(chunk_size=500, overlap=50)
    records = pipeline.ingest("https://climate.example.com/report")

    all_text = " ".join(r["text"] for r in records)
    assert "Copyright 2026" not in all_text   # footer stripped
    assert "Home" not in all_text             # nav stripped


@pytest.mark.integration
def test_full_pipeline_reingest_same_url_produces_same_chunk_ids(httpx_mock):
    # Deduplication: same URL → same chunk_ids → upsert overwrites, no duplicates
    httpx_mock.add_response(
        url="https://climate.example.com/report",
        text=_ARTICLE_HTML,
        status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
    )
    httpx_mock.add_response(
        url="https://climate.example.com/report",
        text=_ARTICLE_HTML,
        status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
    )
    pipeline = WebPipeline(chunk_size=500, overlap=50)
    first = pipeline.ingest("https://climate.example.com/report")
    second = pipeline.ingest("https://climate.example.com/report")

    assert [r["chunk_id"] for r in first] == [r["chunk_id"] for r in second]


@pytest.mark.integration
def test_full_pipeline_boilerplate_only_returns_empty(httpx_mock):
    httpx_mock.add_response(
        url="https://example.com/nav",
        text=_BOILERPLATE_HTML,
        status_code=200,
        headers={"content-type": "text/html"},
    )
    pipeline = WebPipeline()
    records = pipeline.ingest("https://example.com/nav")
    assert records == []


@pytest.mark.integration
def test_full_pipeline_http_error_propagates(httpx_mock):
    httpx_mock.add_response(
        url="https://example.com/missing",
        status_code=404,
    )
    pipeline = WebPipeline()
    with pytest.raises(Exception):
        pipeline.ingest("https://example.com/missing")


# ---------------------------------------------------------------------------
# Fixture-based tests: real saved HTML, deterministic, no network required
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_npr_fixture_produces_chunks(httpx_mock, npr_article_html):
    httpx_mock.add_response(
        url=_NPR_URL, text=npr_article_html, status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
    )
    records = WebPipeline().ingest(_NPR_URL)
    assert len(records) > 0
    assert all(isinstance(r, dict) for r in records)


@pytest.mark.integration
def test_npr_fixture_title_extracted(httpx_mock, npr_article_html):
    httpx_mock.add_response(
        url=_NPR_URL, text=npr_article_html, status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
    )
    records = WebPipeline().ingest(_NPR_URL)
    assert records[0].get("title") == _NPR_TITLE


@pytest.mark.integration
def test_npr_fixture_date_extracted(httpx_mock, npr_article_html):
    httpx_mock.add_response(
        url=_NPR_URL, text=npr_article_html, status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
    )
    records = WebPipeline().ingest(_NPR_URL)
    assert records[0].get("date") == "2026-07-07"


@pytest.mark.integration
def test_npr_fixture_domain_correct(httpx_mock, npr_article_html):
    httpx_mock.add_response(
        url=_NPR_URL, text=npr_article_html, status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
    )
    records = WebPipeline().ingest(_NPR_URL)
    assert all(r["domain"] == "text.npr.org" for r in records)


@pytest.mark.integration
def test_npr_fixture_article_content_present(httpx_mock, npr_article_html):
    httpx_mock.add_response(
        url=_NPR_URL, text=npr_article_html, status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
    )
    records = WebPipeline().ingest(_NPR_URL)
    all_text = " ".join(r["text"] for r in records).lower()
    assert "ballistic missile" in all_text
    assert "south pacific" in all_text


@pytest.mark.integration
def test_npr_fixture_no_nav_fragments_in_chunks(httpx_mock, npr_article_html):
    """Inline cross-reference lines must be stripped from chunk text."""
    httpx_mock.add_response(
        url=_NPR_URL, text=npr_article_html, status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
    )
    records = WebPipeline().ingest(_NPR_URL)
    all_text = " ".join(r["text"] for r in records)
    assert "Related Story" not in all_text


@pytest.mark.integration
def test_npr_fixture_chunk_ids_stable_on_reingest(httpx_mock, npr_article_html):
    """URL-level deduplication: re-ingesting the same URL must produce identical chunk IDs."""
    for _ in range(2):
        httpx_mock.add_response(
            url=_NPR_URL, text=npr_article_html, status_code=200,
            headers={"content-type": "text/html; charset=utf-8"},
        )
    pipeline = WebPipeline()
    first = pipeline.ingest(_NPR_URL)
    second = pipeline.ingest(_NPR_URL)
    assert [r["chunk_id"] for r in first] == [r["chunk_id"] for r in second]


@pytest.mark.integration
def test_npr_fixture_description_on_first_chunk_only(httpx_mock, npr_article_html):
    httpx_mock.add_response(
        url=_NPR_URL, text=npr_article_html, status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
    )
    pipeline = WebPipeline(chunk_size=60, overlap=10)
    records = pipeline.ingest(_NPR_URL)
    # Description may be absent (empty from metadata) but must never appear on chunk > 0
    for r in records[1:]:
        assert "description" not in r or r["description"] is None
