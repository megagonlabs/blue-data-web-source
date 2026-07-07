import pytest

from blue_web_source.source.web.extractor import ExtractResult, WebExtractor

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
