"""
Integration tests for WebPipeline.

Uses pytest-httpx to intercept HTTP calls so tests are deterministic and require
no network access. trafilatura runs for real — these tests validate the full
fetch → extract → chunk chain without mocking any layer except HTTP transport.

Run with:
    pytest lib/tests/integration/ -v -m integration
"""
import pytest

from blue_web_source.source.web.pipeline import WebPipeline


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
