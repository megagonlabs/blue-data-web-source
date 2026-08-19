import sys

import pytest

from blue_data_web_source.source.web.feed import FeedEntry, FeedParseResult, FeedParser

feedparser = pytest.importorskip("feedparser", reason="feedparser not installed")

RSS_FEED = """\
<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Test RSS Feed</title>
    <link>https://example.com</link>
    <item>
      <title>Article One</title>
      <link>https://example.com/article-1</link>
      <pubDate>Mon, 01 Jan 2026 00:00:00 GMT</pubDate>
      <description>Summary of article one.</description>
    </item>
    <item>
      <title>Article Two</title>
      <link>https://example.com/article-2</link>
    </item>
    <item>
      <title>Entry Without URL</title>
    </item>
  </channel>
</rss>
"""

ATOM_FEED = """\
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Test Feed</title>
  <entry>
    <title>Atom Article</title>
    <link href="https://example.com/atom-1"/>
    <published>2026-01-01T00:00:00Z</published>
    <summary>Atom summary text.</summary>
  </entry>
</feed>
"""

EMPTY_FEED = """\
<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Empty Feed</title>
  </channel>
</rss>
"""

_FEED_URL = "https://example.com/feed.xml"


class TestFeedParser:
    def test_rss_entries_count(self):
        result = FeedParser().parse(RSS_FEED, _FEED_URL)
        # item without <link> is skipped
        assert len(result.entries) == 2

    def test_rss_entry_urls(self):
        result = FeedParser().parse(RSS_FEED, _FEED_URL)
        assert result.entries[0].url == "https://example.com/article-1"
        assert result.entries[1].url == "https://example.com/article-2"

    def test_rss_entry_title(self):
        result = FeedParser().parse(RSS_FEED, _FEED_URL)
        assert result.entries[0].title == "Article One"
        assert result.entries[1].title == "Article Two"

    def test_rss_entry_published(self):
        result = FeedParser().parse(RSS_FEED, _FEED_URL)
        assert result.entries[0].published is not None

    def test_rss_entry_summary(self):
        result = FeedParser().parse(RSS_FEED, _FEED_URL)
        assert result.entries[0].summary is not None

    def test_rss_feed_title(self):
        result = FeedParser().parse(RSS_FEED, _FEED_URL)
        assert result.feed_title == "Test RSS Feed"

    def test_rss_feed_url_stored(self):
        result = FeedParser().parse(RSS_FEED, _FEED_URL)
        assert result.feed_url == _FEED_URL

    def test_atom_feed_parsed(self):
        result = FeedParser().parse(ATOM_FEED, "https://example.com/atom.xml")
        assert len(result.entries) == 1
        assert result.entries[0].url == "https://example.com/atom-1"
        assert result.entries[0].title == "Atom Article"

    def test_atom_feed_title(self):
        result = FeedParser().parse(ATOM_FEED, "https://example.com/atom.xml")
        assert result.feed_title == "Atom Test Feed"

    def test_atom_entry_published(self):
        result = FeedParser().parse(ATOM_FEED, "https://example.com/atom.xml")
        assert result.entries[0].published is not None

    def test_atom_entry_summary(self):
        result = FeedParser().parse(ATOM_FEED, "https://example.com/atom.xml")
        assert result.entries[0].summary is not None

    def test_empty_feed_returns_no_entries(self):
        result = FeedParser().parse(EMPTY_FEED, _FEED_URL)
        assert result.entries == []

    def test_entry_without_link_skipped(self):
        result = FeedParser().parse(RSS_FEED, _FEED_URL)
        # "Entry Without URL" has no <link>; should not appear
        assert all(e.url for e in result.entries)

    def test_result_type(self):
        result = FeedParser().parse(RSS_FEED, _FEED_URL)
        assert isinstance(result, FeedParseResult)
        assert all(isinstance(e, FeedEntry) for e in result.entries)


def test_feedparser_not_installed_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "feedparser", None)
    with pytest.raises(ImportError, match="feedparser"):
        FeedParser().parse("<rss/>", _FEED_URL)
