"""Feature demo: WebExtractor — trafilatura extraction and metadata fields.

Drills into what WebExtractor produces from raw HTML:
  - Text cleaning and boilerplate removal
  - Metadata extraction (title, author, date, description, language)
  - Nav-fragment stripping
  - None return for nav-only pages

Deps: none (no API key, no network)
Run:  python3 examples/feature-demos/extractor_demo.py
"""
from __future__ import annotations

from blue_web_source.source.web.extractor import ExtractResult, WebExtractor

W = 68

HTML_CASES: list[tuple[str, str, str]] = [
    (
        "Rich metadata article",
        "https://research.example.com/paper",
        """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Advances in Protein Folding Prediction</title>
  <meta name="author" content="Dr. Alice Chen, Dr. Bob Kim">
  <meta name="description" content="Review of AlphaFold improvements and lab validation.">
  <meta property="article:published_time" content="2026-02-20">
</head>
<body>
  <article>
    <h1>Advances in Protein Folding Prediction</h1>
    <p>Machine learning models now predict protein 3D structures with near-crystallographic
    accuracy for most known protein families. AlphaFold3 extended coverage to protein-DNA
    and protein-RNA complexes, enabling new drug target identification workflows.</p>

    <h2>Experimental Validation</h2>
    <p>Wet-lab teams at four major research institutes validated 200 novel predictions
    using cryo-EM. Agreement rates exceeded 90 percent for proteins under 500 residues.
    Discrepancies correlated with flexible loop regions absent from training data.</p>

    <h2>Future Directions</h2>
    <p>Integrating dynamics simulation with static structure prediction is the next
    frontier. Combining molecular dynamics with ML scoring functions is expected to
    improve binding affinity estimates for small-molecule drug candidates.</p>
  </article>
</body>
</html>""",
    ),
    (
        "Article with inline nav fragments",
        "https://news.example.com/tech",
        """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Semiconductor Shortage Update</title>
</head>
<body>
  <article>
    <h1>Semiconductor Shortage Update</h1>
    <p>Global chip supply returned to balance in mid-2025 after a three-year shortfall
    triggered by pandemic-era demand spikes. Fab capacity added in the US, EU, and
    Japan under industrial policy programmes came online ahead of schedule.</p>

    Related Story: How TSMC Plans Its 2nm Roadmap
    Read more: Intel Foundry Services Latest Orders
    Advertisement

    <h2>Regional Dynamics</h2>
    <p>Asian fabs retained a dominant market share in leading-edge nodes. However,
    European specialty chip capacity for automotive grew 40 percent year-on-year,
    reducing dependence on single-source suppliers for safety-critical components.</p>

    See also: EV Battery Chip Demand Forecast
    Story continues below advertisement

    <h2>Outlook</h2>
    <p>Analysts expect oversupply in mature nodes (28nm and above) through 2027 as
    capacity additions outpace demand recovery in consumer electronics.</p>
  </article>
</body>
</html>""",
    ),
    (
        "Login-wall redirect (returns None)",
        "https://app.example.com/dashboard",
        """\
<!DOCTYPE html>
<html lang="en">
<head>
  <title>Session Expired</title>
  <meta http-equiv="refresh" content="0; url=/login">
</head>
<body>
  <script>window.location.replace("/login");</script>
</body>
</html>""",
    ),
    (
        "Multilingual article (French)",
        "https://fr.example.com/article",
        """\
<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Rapport sur l'Énergie Solaire</title>
  <meta name="author" content="Équipe Scientifique">
</head>
<body>
  <article>
    <h1>Rapport sur l'Énergie Solaire</h1>
    <p>La capacité solaire mondiale a dépassé 3 000 gigawatts en 2025, avec une
    croissance annuelle de 25 pour cent. L'Europe du Sud a enregistré les taux
    d'installation les plus élevés, portée par les subventions gouvernementales.</p>

    <h2>Perspectives</h2>
    <p>Les analystes prévoient que le solaire représentera 40 pour cent de la production
    mondiale d'électricité d'ici 2030. La réduction des coûts de stockage est le
    principal facteur accélérant l'adoption dans les marchés émergents.</p>
  </article>
</body>
</html>""",
    ),
]


def _hr():
    print("─" * W)


def _show(label: str, url: str, result: "ExtractResult | None"):  # noqa: F821
    print(f"\n  CASE: {label}")
    print(f"  URL : {url}")
    _hr()

    if result is None:
        print("  result   : None")
        print("  pipeline : WebPipeline.ingest() returns [] — nothing stored")
        return

    print(f"  title    : {result.title or '—'}")
    print(f"  author   : {result.author or '—'}")
    print(f"  date     : {result.date or '—'}")
    print(f"  language : {result.language or '—'}")
    print(f"  desc     : {(result.description or '—')[:70]}")
    print(f"  text len : {len(result.text)} chars")
    print()

    markers = ["Related Story:", "Read more:", "See also:", "Also read:",
               "Advertisement", "Story continues"]
    found = [m for m in markers if m in result.text]
    if found:
        print(f"  WARN: nav fragment still present: {found}")
    else:
        print("  ✓  No nav fragments in output")

    print()
    print("  First 3 non-empty lines of extracted text:")
    lines = [l.strip() for l in result.text.splitlines() if l.strip()]
    for line in lines[:3]:
        print(f"    {line[:W - 4]}")


def main():
    extractor = WebExtractor()

    print()
    print("═" * W)
    print("  blue-data-web-source — WebExtractor Feature Demo")
    print("═" * W)

    for label, url, html in HTML_CASES:
        result = extractor.extract(html, url)
        _show(label, url, result)

    print()
    print()
    print("  EXTRACTOR FIELD REFERENCE")
    _hr()
    fields = [
        ("title",       "str | None", "Page <title> or og:title; None if not found"),
        ("author",      "str | None", "meta[name=author]; None if absent"),
        ("date",        "str | None", "article:published_time or heuristic parse; None if absent"),
        ("description", "str | None", "meta[name=description]; None if absent"),
        ("language",    "str | None", "html[lang] attribute; None if absent"),
        ("text",        "str",        "Main article body, boilerplate stripped, nav lines removed"),
        ("url",         "str",        "Canonical URL passed to extract()"),
    ]
    print(f"  {'Field':<13} {'Type':<14} Description")
    print(f"  {'─' * 13} {'─' * 14} {'─' * 35}")
    for name, typ, desc in fields:
        print(f"  {name:<13} {typ:<14} {desc}")

    print()
    print("  NOTE: description is attached to chunk 0 only by WebPipeline.")
    print("        It is NOT repeated on every chunk — avoids metadata noise")
    print("        in later chunks that cover different sub-topics.")
    print()
    print("═" * W)
    print()


if __name__ == "__main__":
    main()
