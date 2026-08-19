from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class FeedEntry:
    url: str
    title: Optional[str] = None
    published: Optional[str] = None
    summary: Optional[str] = None


@dataclass
class FeedParseResult:
    feed_url: str
    feed_title: Optional[str]
    entries: List[FeedEntry] = field(default_factory=list)


class FeedParser:
    """Parses RSS 0.9x/1.0/2.0 and Atom feeds using feedparser.

    The feedparser import is deferred to ``parse()`` so the rest of the library
    works without it installed. Requires ``pip install 'blue-data-web-source[feed]'``.
    """

    def parse(self, content: str, feed_url: str) -> FeedParseResult:
        """Parse feed XML content into a structured result.

        Args:
            content: Raw feed content (RSS or Atom XML string).
            feed_url: URL the feed was fetched from (stored on the result).

        Returns:
            ``FeedParseResult`` with a list of entries. Entries without a ``link``
            field are silently skipped.

        Raises:
            ImportError: If ``feedparser`` is not installed.
        """
        try:
            import feedparser
        except ImportError as exc:
            raise ImportError(
                "feedparser is required for WEB.INGEST_FEED. "
                "Install with: pip install 'blue-data-web-source[feed]'"
            ) from exc

        parsed = feedparser.parse(content)

        entries: List[FeedEntry] = []
        for entry in parsed.entries:
            url = entry.get("link")
            if not url:
                continue
            entries.append(FeedEntry(
                url=url,
                title=entry.get("title"),
                published=entry.get("published"),
                summary=entry.get("summary"),
            ))

        feed_title: Optional[str] = None
        if parsed.feed:
            feed_title = parsed.feed.get("title")

        return FeedParseResult(
            feed_url=feed_url,
            feed_title=feed_title,
            entries=entries,
        )
