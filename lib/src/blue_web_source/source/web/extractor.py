from __future__ import annotations

from dataclasses import dataclass

try:
    import trafilatura
except ImportError as exc:
    raise ImportError(
        "trafilatura is required for web content extraction. "
        "Install it with: pip install trafilatura"
    ) from exc


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
