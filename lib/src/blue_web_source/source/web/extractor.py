from __future__ import annotations

import re
from dataclasses import dataclass

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
        text = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=True,
            output_format="txt",
        )
        if not text:
            return None

        text = _strip_nav_fragments(text)
        if not text:
            return None

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
