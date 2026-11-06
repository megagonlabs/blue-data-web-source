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


# Render trafilatura's XML (not markdown) so page text stays literal. Markdown
# output on trafilatura >= 2.2 backslash-escapes literal ``_ [ ] < *`` and wraps
# code in backticks, and there is no reliable way to tell a literal ``*`` from
# emphasis — corrupting identifiers, code and search/content_hash (#123). The XML
# keeps ``<p>``/``<code>``/``<item>`` text verbatim and marks headings with a
# ``rend="hN"`` level, which we turn into an ATX ``## Heading`` line so
# SectionChunker still detects sections (#97).


def _head_level(rend: str | None) -> int:
    """Map a ``<head rend="hN">`` value to a heading level (default 2)."""
    if rend and len(rend) >= 2 and rend[0] in "hH" and rend[1:].isdigit():
        return max(1, min(6, int(rend[1:])))
    return 2


def _inline_text(el) -> str:
    """Flatten an element's text, unwrapping inline ``<hi>`` spans. Restores a
    single space between adjacent inline children, which trafilatura's XML drops
    (#123)."""
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    children = list(el)
    for i, child in enumerate(children):
        if child.tag == "lb":  # a line break separates words (#138)
            parts.append("\n" + (child.tail or ""))
            continue
        parts.append("".join(child.itertext()))
        tail = child.tail or ""
        if child.tag in ("quote", "list", "p"):  # a block child inside an item: keep it apart
            parts.insert(len(parts) - 1, " ")
        if not tail and i + 1 < len(children):
            parts.append(" ")  # adjacent inline spans with no separator in the XML
        else:
            parts.append(tail)
    return "".join(parts).strip()


def _render_block(el, out: list) -> None:
    """Render one TEI-ish block element to text line(s), appended to *out*."""
    tag = el.tag
    if tag == "head":
        text = _inline_text(el)
        if text:
            out.append("#" * _head_level(el.get("rend")) + " " + text)
    elif tag == "p":
        text = _inline_text(el)
        if text:
            out.append(text)
    elif tag == "code":
        code = "".join(el.itertext())  # verbatim — never reflow or strip code
        if code.strip():
            out.append(code)
    elif tag == "list":
        for item in el.findall("item"):
            text = _inline_text(item)
            if text:
                out.append(text)
    elif tag == "table":
        for row in el.iter("row"):
            cells = [_inline_text(c) for c in row.findall("cell")]
            line = " | ".join(c for c in cells if c)
            if line:
                out.append(line)
    elif (el.text or "").strip() or any(c.tag in ("hi", "ref", "lb", "code") for c in el):
        # Any other text-bearing element — a <quote>, a top-level <item>, or a block
        # carrying its own text/inline spans — was silently dropped by the bare
        # descend below, losing page text on a default install (#138).
        text = _inline_text(el)
        if text:
            out.append(text)
    else:
        for child in el:  # unknown container — descend
            _render_block(child, out)
    if (el.tail or "").strip():  # text that follows a block, directly under its parent (#138)
        out.append(el.tail.strip())


def _render_xml(xml: str) -> str:
    """Render trafilatura XML to plain text, keeping literal characters and turning
    headings into ATX lines. Blocks are separated by a blank line."""
    from lxml import etree

    try:
        root = etree.fromstring(xml.encode("utf-8"))
    except etree.XMLSyntaxError:
        return ""
    body = root.find("main")
    if body is None:
        return ""
    out: list = []
    if (body.text or "").strip():  # text directly inside <main>, before any block (#138)
        out.append(body.text.strip())
    for child in body:
        _render_block(child, out)
    return "\n\n".join(out)


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
        # XML (not "markdown"/"txt") so page text is stored verbatim: markdown
        # escapes literal characters and mangles code, and txt drops headings that
        # SectionChunker needs. We render the XML ourselves — headings become ATX
        # lines, body/code/list text stays literal (#123, keeping #97's sections).
        xml = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=True,
            output_format="xml",
            include_formatting=True,  # keep <head> levels (and <hi> spans we unwrap)
        )
        if not xml:
            _log.debug("no extractable content from %s", url)
            return None

        text = _strip_nav_fragments(_render_xml(xml))
        if not text:
            _log.debug("no content after rendering/nav-fragment stripping: %s", url)
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
