"""Unit tests for MetadataExtractor."""
from __future__ import annotations

import json

import pytest

from blue_data_web_source.source.web.metadata import MetadataExtractor, StructuredMetadata

_extractor = MetadataExtractor()


def _html(head: str) -> str:
    return f"<html><head>{head}</head><body>text</body></html>"


# ---------------------------------------------------------------------------
# Empty / broken input
# ---------------------------------------------------------------------------

class TestEmptyInput:
    def test_empty_string_returns_all_none(self):
        m = _extractor.extract("")
        assert m == StructuredMetadata()

    def test_malformed_html_does_not_raise(self):
        m = _extractor.extract("<html><head><script type='application/ld+json'>{bad</script>")
        assert m.schema_type is None

    def test_no_metadata_tags_returns_all_none(self):
        m = _extractor.extract("<html><head><title>Hi</title></head><body>text</body></html>")
        assert m == StructuredMetadata()


# ---------------------------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------------------------

class TestJsonLd:
    def _ld(self, obj: dict) -> str:
        blob = json.dumps(obj)
        return _html(f'<script type="application/ld+json">{blob}</script>')

    def test_schema_type_extracted(self):
        m = _extractor.extract(self._ld({"@type": "NewsArticle", "headline": "Hi"}))
        assert m.schema_type == "NewsArticle"

    def test_keywords_string_split_by_comma(self):
        m = _extractor.extract(self._ld({"@type": "Article", "keywords": "AI, ML, Python"}))
        assert m.keywords == ["AI", "ML", "Python"]

    def test_keywords_list_preserved(self):
        m = _extractor.extract(self._ld({"@type": "Article", "keywords": ["AI", "ML"]}))
        assert m.keywords == ["AI", "ML"]

    def test_publisher_dict_name(self):
        m = _extractor.extract(self._ld({"@type": "Article", "publisher": {"@type": "Organization", "name": "ACME"}}))
        assert m.publisher == "ACME"

    def test_publisher_string(self):
        m = _extractor.extract(self._ld({"@type": "Article", "publisher": "ACME Corp"}))
        assert m.publisher == "ACME Corp"

    def test_canonical_url_from_jsonld(self):
        m = _extractor.extract(self._ld({"@type": "Article", "url": "https://example.com/article"}))
        assert m.canonical_url == "https://example.com/article"

    def test_date_published(self):
        m = _extractor.extract(self._ld({"@type": "Article", "datePublished": "2026-01-01"}))
        assert m.date_published == "2026-01-01"

    def test_date_created_fallback(self):
        m = _extractor.extract(self._ld({"@type": "Article", "dateCreated": "2025-12-31"}))
        assert m.date_published == "2025-12-31"

    def test_author_dict_name(self):
        m = _extractor.extract(self._ld({"@type": "Article", "author": {"@type": "Person", "name": "Jane Doe"}}))
        assert m.author == "Jane Doe"

    def test_author_string(self):
        m = _extractor.extract(self._ld({"@type": "Article", "author": "Jane Doe"}))
        assert m.author == "Jane Doe"

    def test_author_list_first_item(self):
        m = _extractor.extract(self._ld({"@type": "Article", "author": [{"name": "A"}, {"name": "B"}]}))
        assert m.author == "A"

    def test_description(self):
        m = _extractor.extract(self._ld({"@type": "Article", "description": "A great piece."}))
        assert m.description == "A great piece."

    def test_article_type_preferred_over_generic(self):
        ld_list = [
            {"@type": "WebSite", "name": "My Site"},
            {"@type": "NewsArticle", "author": "Bob"},
        ]
        blob = json.dumps(ld_list)
        html = _html(f'<script type="application/ld+json">{blob}</script>')
        m = _extractor.extract(html)
        assert m.schema_type == "NewsArticle"
        assert m.author == "Bob"

    def test_multiple_jsonld_blocks_merged_by_preference(self):
        block1 = json.dumps({"@type": "WebSite", "url": "https://site.com"})
        block2 = json.dumps({"@type": "NewsArticle", "author": "Alice"})
        html = _html(
            f'<script type="application/ld+json">{block1}</script>'
            f'<script type="application/ld+json">{block2}</script>'
        )
        m = _extractor.extract(html)
        assert m.schema_type == "NewsArticle"
        assert m.author == "Alice"

    def test_invalid_jsonld_silently_skipped(self):
        html = _html('<script type="application/ld+json">{not json}</script>')
        m = _extractor.extract(html)
        assert m.schema_type is None


# ---------------------------------------------------------------------------
# OpenGraph
# ---------------------------------------------------------------------------

class TestOpenGraph:
    def test_og_description(self):
        html = _html('<meta property="og:description" content="OG desc">')
        m = _extractor.extract(html)
        assert m.description == "OG desc"

    def test_og_url_canonical(self):
        html = _html('<meta property="og:url" content="https://example.com/og-page">')
        m = _extractor.extract(html)
        assert m.canonical_url == "https://example.com/og-page"

    def test_og_site_name_as_publisher(self):
        html = _html('<meta property="og:site_name" content="Example News">')
        m = _extractor.extract(html)
        assert m.publisher == "Example News"

    def test_og_property_case_insensitive(self):
        html = _html('<meta property="OG:DESCRIPTION" content="desc">')
        m = _extractor.extract(html)
        assert m.description == "desc"


# ---------------------------------------------------------------------------
# Standard meta tags
# ---------------------------------------------------------------------------

class TestMetaTags:
    def test_meta_keywords(self):
        html = _html('<meta name="keywords" content="foo, bar, baz">')
        m = _extractor.extract(html)
        assert m.keywords == ["foo", "bar", "baz"]

    def test_meta_author(self):
        html = _html('<meta name="author" content="Jane">')
        m = _extractor.extract(html)
        assert m.author == "Jane"

    def test_meta_description(self):
        html = _html('<meta name="description" content="page desc">')
        m = _extractor.extract(html)
        assert m.description == "page desc"

    def test_meta_name_case_insensitive(self):
        html = _html('<meta name="AUTHOR" content="Alice">')
        m = _extractor.extract(html)
        assert m.author == "Alice"


# ---------------------------------------------------------------------------
# Canonical link
# ---------------------------------------------------------------------------

class TestCanonicalLink:
    def test_canonical_link_tag(self):
        html = _html('<link rel="canonical" href="https://example.com/canonical">')
        m = _extractor.extract(html)
        assert m.canonical_url == "https://example.com/canonical"

    def test_canonical_link_case_insensitive_rel(self):
        html = _html('<link rel="Canonical" href="https://example.com/canonical">')
        m = _extractor.extract(html)
        assert m.canonical_url == "https://example.com/canonical"


# ---------------------------------------------------------------------------
# Priority / fallback ordering
# ---------------------------------------------------------------------------

class TestPriority:
    def test_jsonld_canonical_beats_link_tag(self):
        ld = json.dumps({"@type": "Article", "url": "https://jsonld.example.com/"})
        html = _html(
            f'<script type="application/ld+json">{ld}</script>'
            '<link rel="canonical" href="https://link.example.com/">'
        )
        m = _extractor.extract(html)
        assert m.canonical_url == "https://jsonld.example.com/"

    def test_link_tag_beats_og_url(self):
        html = _html(
            '<link rel="canonical" href="https://link.example.com/">'
            '<meta property="og:url" content="https://og.example.com/">'
        )
        m = _extractor.extract(html)
        assert m.canonical_url == "https://link.example.com/"

    def test_jsonld_author_beats_meta_author(self):
        ld = json.dumps({"@type": "Article", "author": "JSON-LD Author"})
        html = _html(
            f'<script type="application/ld+json">{ld}</script>'
            '<meta name="author" content="Meta Author">'
        )
        m = _extractor.extract(html)
        assert m.author == "JSON-LD Author"

    def test_meta_author_used_when_no_jsonld(self):
        html = _html('<meta name="author" content="Meta Author">')
        m = _extractor.extract(html)
        assert m.author == "Meta Author"

    def test_jsonld_keywords_beat_meta_keywords(self):
        ld = json.dumps({"@type": "Article", "keywords": ["jsonld"]})
        html = _html(
            f'<script type="application/ld+json">{ld}</script>'
            '<meta name="keywords" content="meta">'
        )
        m = _extractor.extract(html)
        assert m.keywords == ["jsonld"]

    def test_jsonld_publisher_beats_og_site_name(self):
        ld = json.dumps({"@type": "Article", "publisher": {"name": "JSON-LD Pub"}})
        html = _html(
            f'<script type="application/ld+json">{ld}</script>'
            '<meta property="og:site_name" content="OG Pub">'
        )
        m = _extractor.extract(html)
        assert m.publisher == "JSON-LD Pub"

    def test_og_description_used_when_no_jsonld(self):
        html = _html(
            '<meta property="og:description" content="OG desc">'
            '<meta name="description" content="meta desc">'
        )
        m = _extractor.extract(html)
        assert m.description == "OG desc"
