from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


def _to_datetime(time_struct) -> Optional[datetime]:
    """Convert feedparser's time.struct_time (UTC) to a timezone-aware datetime."""
    if time_struct is None:
        return None
    return datetime.fromtimestamp(calendar.timegm(time_struct), tz=timezone.utc)


@dataclass
class FeedEntry:
    url: str
    title: Optional[str] = None
    published: Optional[str] = None
    summary: Optional[str] = None
    published_parsed: Optional[datetime] = None


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

    def parse(
        self,
        content: "str | bytes",
        feed_url: str,
        content_type: Optional[str] = None,
    ) -> FeedParseResult:
        """Parse an already-fetched feed body into a structured result.

        The body is always treated as **data**. feedparser's ``parse()`` would
        otherwise interpret a bare string as "a URL, a file path, or a document" —
        so a feed server returning a body that is just a local path or a URL would
        make feedparser open that file or issue its own second request, bypassing
        the fetcher (issue #43). We hand feedparser a byte stream (``io.BytesIO``)
        instead, which it can only read as data, never resolve as a location. The
        feed URL is passed as ``content-location`` so relative entry links still
        resolve against it.

        Args:
            content: Raw feed body as fetched (bytes preferred; a ``str`` is encoded
                to UTF-8). Never a path or URL — it is parsed as-is.
            feed_url: URL the feed was fetched from (base for relative links; stored
                on the result).
            content_type: The response Content-Type, passed to feedparser as a hint.

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

        import io

        if isinstance(content, str):
            content = content.encode("utf-8")
        response_headers = {"content-location": feed_url}
        if content_type:
            response_headers["content-type"] = content_type
        parsed = feedparser.parse(io.BytesIO(content), response_headers=response_headers)

        entries: List[FeedEntry] = []
        for entry in parsed.entries:
            url = entry.get("link")
            if not url:
                continue
            published_raw = entry.get("published_parsed") or entry.get("updated_parsed")
            entries.append(FeedEntry(
                url=url,
                title=entry.get("title"),
                published=entry.get("published"),
                summary=entry.get("summary"),
                published_parsed=_to_datetime(published_raw),
            ))

        feed_title: Optional[str] = None
        if parsed.feed:
            feed_title = parsed.feed.get("title")

        return FeedParseResult(
            feed_url=feed_url,
            feed_title=feed_title,
            entries=entries,
        )
