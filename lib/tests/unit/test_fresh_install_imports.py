"""Fresh-install import guard (#80).

``trafilatura`` pulls in ``lxml`` and imports ``lxml.html.clean``. Since lxml 5.2
that module no longer ships in the core package — it moved to the separate
``lxml_html_clean`` distribution (a.k.a. the ``lxml[html_clean]`` extra). trafilatura
does not declare the dependency, so on a fresh install that resolves a recent lxml
(e.g. 6.1.x) ``import trafilatura`` raises and every page-extraction op
(``WEB.INGEST``, crawl, feed, search-web) breaks.

``blue-data-web-source`` declares ``lxml_html_clean`` so the install is complete.
These imports must succeed with only the declared dependencies present — they are a
regression guard against the dependency silently dropping out again.
"""


def test_import_lxml_html_clean():
    """The module trafilatura needs from the now-separate lxml html-clean package."""
    import lxml_html_clean  # noqa: F401


def test_import_trafilatura():
    """Must not raise ImportError for a missing lxml.html.clean."""
    import trafilatura  # noqa: F401


def test_import_web_extractor():
    """The real entry point: WebExtractor imports trafilatura at module load."""
    from blue_data_web_source.source.web.extractor import WebExtractor  # noqa: F401
