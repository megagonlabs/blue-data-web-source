import pytest

from blue_data_web_source.source.web.extractor import ExtractResult, WebExtractor
from blue_data_web_source.source.web.fetcher import FetchResult
from blue_data_web_source.source.web.pipeline import WebPipeline

_SECTIONED_HTML = (
    "<html><head><title>Guide</title></head><body><article>"
    "<h1>Guide</h1>"
    "<h2>Installation</h2><p>" + "Install the package with pip and configure it. " * 20 + "</p>"
    "<h2>Usage</h2><p>" + "Call the function with arguments to run it. " * 20 + "</p>"
    "</article></body></html>"
)

_SAMPLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta name="author" content="Jane Doe">
  <meta name="description" content="An article about testing.">
  <title>Test Article</title>
</head>
<body>
  <nav>Site navigation</nav>
  <article>
    <h1>Test Article</h1>
    <p>This is the first paragraph with meaningful content about a topic.</p>
    <p>This is the second paragraph with more details about the subject matter.</p>
  </article>
  <footer>Footer content</footer>
</body>
</html>"""

_BOILERPLATE_ONLY_HTML = """<!DOCTYPE html>
<html>
<head><title>App Shell</title></head>
<body>
  <div id="app" data-reactroot=""></div>
</body>
</html>"""


def test_extract_returns_extract_result():
    extractor = WebExtractor()
    result = extractor.extract(_SAMPLE_HTML, "https://example.com/article")
    assert result is not None
    assert isinstance(result, ExtractResult)
    assert result.url == "https://example.com/article"
    assert len(result.text) > 0


def test_extract_captures_main_content():
    extractor = WebExtractor()
    result = extractor.extract(_SAMPLE_HTML, "https://example.com/article")
    assert result is not None
    assert "meaningful content" in result.text or "first paragraph" in result.text


def test_extract_returns_none_for_boilerplate_only():
    extractor = WebExtractor()
    result = extractor.extract(_BOILERPLATE_ONLY_HTML, "https://example.com/nav")
    assert result is None


def test_extract_returns_none_for_empty_html():
    extractor = WebExtractor()
    result = extractor.extract("", "https://example.com")
    assert result is None


def test_extract_title_from_metadata():
    extractor = WebExtractor()
    result = extractor.extract(_SAMPLE_HTML, "https://example.com/article")
    assert result is not None
    assert result.title is not None
    assert "Test Article" in result.title


def test_extract_author_from_metadata():
    extractor = WebExtractor()
    result = extractor.extract(_SAMPLE_HTML, "https://example.com/article")
    assert result is not None
    assert result.author == "Jane Doe"


def test_extract_description_from_metadata():
    extractor = WebExtractor()
    result = extractor.extract(_SAMPLE_HTML, "https://example.com/article")
    assert result is not None
    assert result.description is not None
    assert "testing" in result.description.lower()


def test_extract_url_preserved():
    extractor = WebExtractor()
    result = extractor.extract(_SAMPLE_HTML, "https://example.com/article")
    assert result is not None
    assert result.url == "https://example.com/article"


def test_extract_text_excludes_nav_and_footer():
    extractor = WebExtractor()
    result = extractor.extract(_SAMPLE_HTML, "https://example.com/article")
    assert result is not None
    assert "Site navigation" not in result.text
    assert "Footer content" not in result.text


# --- Nav fragment stripping ---

_ARTICLE_WITH_NAV_FRAGMENTS = """<!DOCTYPE html>
<html lang="en">
<head><title>News Article</title></head>
<body>
  <article>
    <p>Breaking news: something important happened today in the region.</p>
    <p>Related Story: NPR</p>
    <p>Officials confirmed the development on Monday morning.</p>
    <p>Read more: Reuters</p>
    <p>See also: BBC News</p>
    <p>The investigation remains ongoing according to sources.</p>
  </article>
</body>
</html>"""


def test_extract_strips_related_story_lines():
    extractor = WebExtractor()
    result = extractor.extract(_ARTICLE_WITH_NAV_FRAGMENTS, "https://news.example.com/article")
    assert result is not None
    assert "Related Story" not in result.text


def test_extract_strips_read_more_lines():
    extractor = WebExtractor()
    result = extractor.extract(_ARTICLE_WITH_NAV_FRAGMENTS, "https://news.example.com/article")
    assert result is not None
    assert "Read more" not in result.text


def test_extract_strips_see_also_lines():
    extractor = WebExtractor()
    result = extractor.extract(_ARTICLE_WITH_NAV_FRAGMENTS, "https://news.example.com/article")
    assert result is not None
    assert "See also" not in result.text


def test_extract_preserves_article_body_after_stripping():
    extractor = WebExtractor()
    result = extractor.extract(_ARTICLE_WITH_NAV_FRAGMENTS, "https://news.example.com/article")
    assert result is not None
    assert "important happened" in result.text or "Breaking news" in result.text
    assert "investigation remains" in result.text or "ongoing" in result.text


# --- Markdown headings → section_title (#97) ---

def test_extract_preserves_h2_headings_as_markdown():
    # The extractor must emit markdown so <h2> survives as "## Heading" — this is
    # what lets SectionChunker detect sections on HTML pages (#97).
    extractor = WebExtractor()
    result = extractor.extract(_SECTIONED_HTML, "https://x.test/g")
    assert result is not None
    assert "## Installation" in result.text
    assert "## Usage" in result.text


def test_extract_strips_inline_emphasis_but_keeps_text():
    html = (
        "<html><head><title>T</title></head><body><article><h2>Notes</h2><p>"
        "This is <b>very</b> important and <i>clearly</i> stated. " * 10
        + "</p></article></body></html>"
    )
    result = WebExtractor().extract(html, "https://x.test/n")
    assert result is not None
    assert "**" not in result.text and "*" not in result.text  # emphasis unwrapped
    assert "very" in result.text and "clearly" in result.text  # words kept
    assert "## Notes" in result.text  # heading marker kept


def test_ingest_html_sections_set_section_title():
    # End-to-end (#97): an HTML page with <h2> sections produces chunks whose
    # section_title is the enclosing heading, and no chunk text carries markdown
    # heading/emphasis markers.
    fr = FetchResult(
        url="https://x.test/g", final_url="https://x.test/g",
        html=_SECTIONED_HTML, status_code=200, content_type="text/html",
    )
    records = WebPipeline(chunk_size=50, overlap=0).ingest(
        "https://x.test/g", _fetch_result=fr
    )
    titles = {r.get("section_title") for r in records}
    assert "Installation" in titles
    assert "Usage" in titles
    assert all("##" not in r["text"] and "**" not in r["text"] for r in records)
