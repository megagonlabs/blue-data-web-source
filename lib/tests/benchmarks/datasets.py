"""Synthetic HTML web corpus generation for benchmarking.

All generators are deterministic (fixed seed). Four page types:

- article_structured:   <article> with <h2> section headers, rich <head> metadata
                        (title, description, date, author). trafilatura extracts
                        the full body and populates all metadata fields.
- article_unstructured: Dense prose inside <article>, no headers anywhere.
                        No date or author metadata in <head>.
- article_mixed:        Numbered sections + prose + inline CMS nav fragments
                        ("Related Story:", "Read more:", etc.). Fragments are
                        stripped by WebExtractor before chunking.
- no_content:           JS redirect + meta-refresh. trafilatura returns None.
                        Pipeline returns []. Used to benchmark extraction rate.
"""
import random
from dataclasses import dataclass, field
from typing import List

# ── Vocabulary ────────────────────────────────────────────────────────────────

_NOUNS = [
    "customer", "policy", "coverage", "premium", "deductible", "claim",
    "benefit", "requirement", "payment", "term", "condition", "period",
    "renewal", "cancellation", "agreement", "provision", "liability",
    "beneficiary", "schedule", "amendment", "exclusion", "limitation",
    "obligation", "authority", "document", "procedure", "applicant",
    "holder", "insurer", "certificate", "declaration", "endorsement",
    "waiver", "notice", "regulation", "compliance", "assessment",
]

_VERBS = [
    "shall", "must", "may", "requires", "provides", "specifies",
    "determines", "establishes", "ensures", "maintains", "terminates",
    "renews", "cancels", "covers", "excludes", "applies", "constitutes",
    "represents", "warrants", "indemnifies", "reimburses", "designates",
]

_ADJECTIVES = [
    "applicable", "effective", "written", "specified", "required", "eligible",
    "valid", "current", "prior", "subsequent", "additional", "reasonable",
    "necessary", "appropriate", "financial", "legal", "general", "specific",
    "individual", "annual", "total", "material", "relevant", "binding",
]

_CONNECTORS = [
    "in accordance with", "pursuant to", "subject to", "as specified in",
    "in the event that", "provided that", "notwithstanding the foregoing",
    "in addition to", "except as otherwise provided", "with respect to",
    "for the purpose of", "as set forth in", "consistent with",
]

_SECTION_TITLES = [
    "General Provisions", "Definitions and Interpretations",
    "Eligibility Requirements", "Coverage Terms and Conditions",
    "Payment Obligations", "Claims Procedure",
    "Exclusions and Limitations", "Renewal and Cancellation",
    "Dispute Resolution", "Governing Law",
    "Notices and Communications", "Amendment Procedures",
    "Liability and Indemnification", "Effective Date and Term",
    "Schedule of Benefits", "Compliance Requirements",
    "Regulatory Provisions", "Administrative Procedures",
    "Record Keeping Requirements", "Termination Conditions",
]

_NAV_FRAGMENT_PREFIXES = [
    "Related Story",
    "Read more",
    "See also",
    "Also read",
    "Story continues",
]

_AUTHORS = ["Jane Smith", "John Doe", "Maria Garcia", "Aisha Patel", "Wei Chen"]
_DATES = ["2026-01-15", "2026-02-20", "2026-03-10", "2026-04-05", "2026-05-22"]


# ── Text primitives ──────────────────────────────────────────────────────────

def _sentence(rng: random.Random) -> str:
    n = rng.choice(_NOUNS).capitalize()
    v = rng.choice(_VERBS)
    adj = rng.choice(_ADJECTIVES)
    obj = rng.choice(_NOUNS)
    conn = rng.choice(_CONNECTORS)
    extra = rng.choice(_NOUNS)
    templates = [
        f"The {n} {v} {adj} {obj} {conn} this agreement.",
        f"Any {adj} {obj} {conn} the {extra} shall be documented in writing.",
        f"The {extra} {v} comply with all {adj} requirements governing the {obj}.",
        f"{conn.capitalize()} the {n}, the {extra} {v} meet all {adj} standards.",
        f"All {adj} {obj} provisions apply to the {extra} unless otherwise specified.",
        f"The {n} {v} notify the {extra} of any {adj} change to the {obj}.",
        f"Written {adj} consent {v} be obtained prior to any modification of the {obj}.",
    ]
    return rng.choice(templates)


def _paragraph(rng: random.Random, n_sentences: int = None) -> str:
    n = n_sentences or rng.randint(3, 6)
    return " ".join(_sentence(rng) for _ in range(n))


def _word_count(text: str) -> int:
    return len(text.split())


def _nav_fragment_line(rng: random.Random) -> str:
    prefix = rng.choice(_NAV_FRAGMENT_PREFIXES)
    noun = rng.choice(_NOUNS).title()
    return f"{prefix}: The {noun} {rng.choice(_ADJECTIVES)} {rng.choice(_NOUNS)}"


# ── HTML generators ──────────────────────────────────────────────────────────

def _html_head(title: str, desc: str = None, author: str = None, date: str = None) -> str:
    parts = [f"  <title>{title}</title>"]
    if desc:
        parts.append(f'  <meta name="description" content="{desc}">')
    if author:
        parts.append(f'  <meta name="author" content="{author}">')
    if date:
        parts.append(f'  <meta property="article:published_time" content="{date}">')
    return "\n".join(parts)


def generate_article_structured(target_words: int, idx: int, rng: random.Random) -> "WebPage":
    """Article with <h2> section headers and full <head> metadata."""
    title = _SECTION_TITLES[idx % len(_SECTION_TITLES)]
    author = _AUTHORS[idx % len(_AUTHORS)]
    date = _DATES[idx % len(_DATES)]
    desc = _sentence(rng)

    sections_html = ""
    words = 0
    sec_idx = 0
    while words < target_words:
        sec_title = _SECTION_TITLES[(idx + sec_idx + 1) % len(_SECTION_TITLES)]
        sections_html += f"  <h2>{sec_title}</h2>\n"
        for _ in range(rng.randint(2, 4)):
            para = _paragraph(rng)
            sections_html += f"  <p>{para}</p>\n"
            words += _word_count(para)
            if words >= target_words:
                break
        sec_idx += 1

    html = (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        + _html_head(title, desc, author, date)
        + "\n</head>\n<body>\n<article>\n"
        + f"  <h1>{title}</h1>\n"
        + sections_html
        + "</article>\n</body>\n</html>"
    )
    return WebPage(
        name=f"structured_{idx:03d}",
        page_type="article_structured",
        url=f"https://news.example.com/articles/structured-{idx:03d}",
        html=html,
        word_count=words,
        nav_fragment_count=0,
    )


def generate_article_unstructured(target_words: int, idx: int, rng: random.Random) -> "WebPage":
    """Dense prose article — no section headers, minimal metadata."""
    title = _SECTION_TITLES[(idx + 5) % len(_SECTION_TITLES)]

    paras_html = ""
    words = 0
    while words < target_words:
        para = _paragraph(rng, n_sentences=rng.randint(4, 7))
        paras_html += f"  <p>{para}</p>\n"
        words += _word_count(para)

    html = (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        + _html_head(title)
        + "\n</head>\n<body>\n<article>\n"
        + paras_html
        + "</article>\n</body>\n</html>"
    )
    return WebPage(
        name=f"unstructured_{idx:03d}",
        page_type="article_unstructured",
        url=f"https://news.example.com/articles/unstructured-{idx:03d}",
        html=html,
        word_count=words,
        nav_fragment_count=0,
    )


def generate_article_mixed(target_words: int, idx: int, rng: random.Random) -> "WebPage":
    """Numbered sections + prose + embedded CMS nav fragments."""
    title = _SECTION_TITLES[(idx + 10) % len(_SECTION_TITLES)]
    date = _DATES[idx % len(_DATES)]

    body_html = ""
    words = 0
    sec_idx = 0
    nav_count = 0

    while words < target_words:
        sec_title = _SECTION_TITLES[(idx + sec_idx) % len(_SECTION_TITLES)]
        body_html += f"  <p>{sec_idx + 1}. {sec_title}</p>\n"
        for _ in range(rng.randint(2, 4)):
            para = _paragraph(rng)
            body_html += f"  <p>{para}</p>\n"
            words += _word_count(para)
            if rng.random() < 0.25:
                frag = _nav_fragment_line(rng)
                body_html += f"  <p>{frag}</p>\n"
                nav_count += 1
            if words >= target_words:
                break
        sec_idx += 1

    html = (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        + _html_head(title, date=date)
        + "\n</head>\n<body>\n<article>\n"
        + f"  <h1>{title}</h1>\n"
        + body_html
        + "</article>\n</body>\n</html>"
    )
    return WebPage(
        name=f"mixed_{idx:03d}",
        page_type="article_mixed",
        url=f"https://news.example.com/articles/mixed-{idx:03d}",
        html=html,
        word_count=words,
        nav_fragment_count=nav_count,
    )


def generate_no_content(idx: int) -> "WebPage":
    """JS redirect + meta-refresh — WebExtractor returns None."""
    html = (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        "  <meta http-equiv=\"refresh\" content=\"0; url=/login\">\n"
        "  <script>window.location.replace(\"/login\");</script>\n"
        "</head>\n<body></body>\n</html>"
    )
    return WebPage(
        name=f"no_content_{idx:03d}",
        page_type="no_content",
        url=f"https://news.example.com/redirect-{idx:03d}",
        html=html,
        word_count=0,
        nav_fragment_count=0,
    )


# ── Dataset types ────────────────────────────────────────────────────────────

@dataclass
class WebPage:
    name: str
    page_type: str
    url: str
    html: str
    word_count: int
    nav_fragment_count: int = 0

    def __repr__(self) -> str:
        return f"WebPage({self.name!r}, {self.page_type!r}, {self.word_count:,} words)"


@dataclass
class WebCorpus:
    pages: List[WebPage]
    total_content_pages: int
    total_words: int

    def by_type(self, page_type: str) -> List[WebPage]:
        return [p for p in self.pages if p.page_type == page_type]

    def __repr__(self) -> str:
        types = {}
        for p in self.pages:
            types[p.page_type] = types.get(p.page_type, 0) + 1
        breakdown = ", ".join(f"{t}={n}" for t, n in sorted(types.items()))
        return f"WebCorpus({len(self.pages)} pages: {breakdown}, {self.total_words:,} content words)"


# ── Target page counts ───────────────────────────────────────────────────────

SMALL  = 10    # total pages
MEDIUM = 50
LARGE  = 200

# Approximate words per article — realistic news article length.
_WORDS_PER_ARTICLE = 500


def build_corpus(n_pages: int = MEDIUM, seed: int = 42) -> WebCorpus:
    """Build a mixed corpus with a fixed page-type distribution.

    Distribution:
      40% article_structured  (h2 headers + rich metadata)
      30% article_unstructured (dense prose only)
      20% article_mixed       (sections + CMS nav fragments)
      10% no_content          (returns None from extractor)
    """
    rng = random.Random(seed)
    n_structured   = max(1, round(n_pages * 0.40))
    n_unstructured = max(1, round(n_pages * 0.30))
    n_mixed        = max(1, round(n_pages * 0.20))
    n_no_content   = max(1, n_pages - n_structured - n_unstructured - n_mixed)

    pages: List[WebPage] = []

    for i in range(n_structured):
        pages.append(generate_article_structured(_WORDS_PER_ARTICLE, i, rng))
    for i in range(n_unstructured):
        pages.append(generate_article_unstructured(_WORDS_PER_ARTICLE, i, rng))
    for i in range(n_mixed):
        pages.append(generate_article_mixed(_WORDS_PER_ARTICLE, i, rng))
    for i in range(n_no_content):
        pages.append(generate_no_content(i))

    rng.shuffle(pages)

    content_pages = sum(1 for p in pages if p.page_type != "no_content")
    total_words = sum(p.word_count for p in pages)
    return WebCorpus(pages=pages, total_content_pages=content_pages, total_words=total_words)
