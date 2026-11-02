from __future__ import annotations

import logging
import re
from dataclasses import dataclass

_log = logging.getLogger(__name__)

try:
    import trafilatura
except ImportError as exc:
    raise ImportError(
        "trafilatura is required for web content extraction. "
        "Install it with: pip install trafilatura"
    ) from exc


# Lines that start with these prefixes are inline navigation/cross-reference
# fragments inserted by CMSes (e.g. "Related Story: NPR"). They are not part
# of the article body and should be removed before chunking.
_NAV_FRAGMENT_RE = re.compile(
    r"^(?:Related Story|Read more|See also|Also read|Advertisement|Story continues)(\W|$)",
    re.IGNORECASE,
)


def _strip_nav_fragments(text: str) -> str:
    lines = [line for line in text.splitlines() if not _NAV_FRAGMENT_RE.match(line.strip())]
    cleaned = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


# Inline emphasis/code markers trafilatura leaves in markdown body text. We keep
# the ATX heading markers (``#``) — SectionChunker needs them to detect sections
# (#97) — but unwrap bold/italic/code so the stored chunk text reads cleanly.
_EMPHASIS_RES = (
    (re.compile(r"\*\*\*(.+?)\*\*\*"), r"\1"),  # ***bold italic***
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),       # **bold**
    (re.compile(r"\*(.+?)\*"), r"\1"),           # *italic*
    (re.compile(r"`(.+?)`"), r"\1"),             # `code`
)


def _strip_markdown_emphasis(text: str) -> str:
    """Unwrap inline emphasis/code markers, preserving heading and list markers."""
    for pattern, repl in _EMPHASIS_RES:
        text = pattern.sub(repl, text)
    return text


@dataclass
class ExtractResult:
    url: str
    text: str
    title: str | None = None
    author: str | None = None
    date: str | None = None
    description: str | None = None
    language: str | None = None


class WebExtractor:
    def extract(self, html: str, url: str) -> ExtractResult | None:
        # Markdown (not "txt") so headings survive as ``## Heading`` lines —
        # doc-source's SectionChunker only detects markdown/numbered/ALL-CAPS/
        # underlined headers, and trafilatura's plain-text output drops or
        # flattens headings, so section-aware chunking never fired on HTML
        # pages (#97). Supported by trafilatura 1.9+ and 2.x.
        text = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=True,
            output_format="markdown",
        )
        if not text:
            _log.debug("no extractable content from %s", url)
            return None

        text = _strip_nav_fragments(text)
        # Unwrap inline emphasis/code so stored chunk text is clean; heading
        # markers are preserved for the chunker.
        text = _strip_markdown_emphasis(text)
        if not text:
            _log.debug("no content after nav-fragment stripping: %s", url)
            return None

        _log.debug("extracted %d chars from %s", len(text), url)

        metadata = trafilatura.extract_metadata(html, default_url=url)

        return ExtractResult(
            url=url,
            text=text,
            title=metadata.title if metadata else None,
            author=metadata.author if metadata else None,
            date=metadata.date if metadata else None,
            description=metadata.description if metadata else None,
            language=metadata.language if metadata else None,
        )
