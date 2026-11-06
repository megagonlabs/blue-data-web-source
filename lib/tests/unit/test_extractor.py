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


# --- #123: literal characters must be stored verbatim (no markdown escaping) ---

_PAD = ("<p>This introductory paragraph exists so the extractor treats the page as "
        "an article with enough body text to keep.</p>")


@pytest.mark.parametrize("fragment,expected", [
    ("<p>Set the option max_retry_count now for the service to run.</p>", "max_retry_count"),
    ("<p>The first array element a[0] and the check 1 &lt; 2 hold here today.</p>", "a[0]"),
    ("<p>The first array element a[0] and the check 1 &lt; 2 hold here today.</p>", "1 < 2"),
    ("<p>The total area is 2*3*4 square metres in this particular room today.</p>", "2*3*4"),
    ("<p>The function signature is def run(*args, **kwargs) in the module today.</p>",
     "def run(*args, **kwargs)"),
    ("<p>Prices start at $10* on most of the orders in the region this year.</p>", "$10*"),
])
def test_extract_preserves_literal_characters(fragment, expected):
    html = (f"<html><head><title>T</title></head><body><article><h1>T</h1>"
            f"{_PAD}{fragment}{_PAD}</article></body></html>")
    result = WebExtractor().extract(html, "https://example.test/a")
    assert result is not None
    assert expected in result.text


def test_extract_keeps_code_block_verbatim():
    html = ("<html><head><title>T</title></head><body><article><h1>T</h1>"
            f"{_PAD}<pre>def area(w, h):\n    return w*h*2\nprint(area(*dims, **opts))</pre>"
            f"{_PAD}</article></body></html>")
    text = WebExtractor().extract(html, "https://example.test/a").text
    assert "return w*h*2" in text
    assert "print(area(*dims, **opts))" in text
    assert "`" not in text  # no markdown code fence/backticks


# --- #138: the XML renderer must not drop text outside head/p/code/list/table ---

from blue_data_web_source.source.web.extractor import _render_xml  # noqa: E402


@pytest.mark.parametrize("xml,expected", [
    ("<doc><main><quote>Sentinel quote text</quote></main></doc>", "Sentinel quote text"),
    ("<doc><main><item>Sentinel top level item</item></main></doc>", "Sentinel top level item"),
    ("<doc><main>Sentinel text directly in main</main></doc>", "Sentinel text directly in main"),
    ('<doc><main><head rend="h2">H</head>Sentinel tail after a heading</main></doc>',
     "Sentinel tail after a heading"),
    ("<doc><main><p>a</p><lb/>Sentinel tail after a line break</main></doc>",
     "Sentinel tail after a line break"),
])
def test_render_xml_keeps_text_outside_known_blocks(xml, expected):
    assert expected in _render_xml(xml)


def test_render_xml_line_break_separates_words():
    # <lb/> is a line break, not a word-join: "a<lb/>b" must not become "ab".
    out = _render_xml("<doc><main><p>alpha<lb/>beta</p></main></doc>")
    assert "alpha" in out and "beta" in out and "alphabeta" not in out


def test_render_xml_invariant_every_text_node_under_main_is_rendered():
    # Invariant (#138): every non-whitespace text node under <main> appears in the output.
    from lxml import etree

    xml = (
        '<doc><main>'
        'LeadMainText'
        '<head rend="h1">HeadingText</head>AfterHeadingTail'
        '<p>ParagraphText <hi>EmphasisText</hi> trailing</p>AfterParagraphTail'
        '<quote>QuoteText</quote>'
        '<list><item>ItemOneText</item><item>ItemTwo <ref target="/x">LinkText</ref></item></list>'
        '<item>TopLevelItemText</item>'
        '<code>CodeVerbatimText</code>'
        '</main></doc>'
    )
    rendered = _render_xml(xml)
    root = etree.fromstring(xml.encode("utf-8"))
    main = root.find("main")
    fragments = []
    for el in main.iter():
        for piece in (el.text, el.tail):
            if piece and piece.strip():
                fragments.append(piece.strip())
    missing = [f for f in fragments if f not in rendered]
    assert not missing, f"text nodes dropped by the renderer: {missing}\nrendered:\n{rendered}"


def test_extract_keeps_blockquote_without_inner_paragraph():
    # A <blockquote> with text directly inside (no inner <p>) was dropped (#138).
    html = ("<html><head><title>Q</title></head><body><article><h1>Q</h1>"
            f"{_PAD}<blockquote>Sentinelquote: a quoted passage sitting directly inside the "
            "blockquote element, with no inner paragraph wrapping it.</blockquote>"
            f"<p>A closing paragraph after the quotation, long enough to count as body.</p>"
            "</article></body></html>")
    result = WebExtractor().extract(html, "https://example.test/q")
    assert result is not None
    assert "Sentinelquote" in result.text


def test_extract_keeps_bare_list_item_page():
    # A page of bare <li> items (no <p>) ingested as no_content, losing every item (#138).
    items = "".join(
        f"<li><span>0{i}/26</span><a href='/p{i}'>Sentinelbare number {i} about "
        f"something rather interesting and long</a></li>"
        for i in range(1, 30)
    )
    html = f"<html><head><title>Index</title></head><body>{items}</body></html>"
    result = WebExtractor().extract(html, "https://example.test/idx")
    assert result is not None
    assert "Sentinelbare number 1" in result.text
    assert "Sentinelbare number 29" in result.text
