"""Scenario: What does the extractor actually strip and keep?

Shows how WebExtractor uses trafilatura to pull the main article body and
metadata, and how nav-fragment stripping prevents CMS cross-reference noise
from polluting chunks.

Deps: none (no API key, no network)
Run:  python3 examples/scenario-demos/extraction_demo.py
"""
from __future__ import annotations

from blue_web_source.source.web.extractor import WebExtractor

W = 68

NAV_HEAVY_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Energy Transition: Key Milestones</title>
  <meta name="author" content="J. Smith">
  <meta name="description" content="Tracking global progress on energy transition.">
</head>
<body>
  <nav>
    <ul><li><a href="/">Home</a></li><li><a href="/about">About</a></li></ul>
  </nav>
  <article>
    <h1>Energy Transition: Key Milestones</h1>
    <p>Global investment in renewable energy reached $500 billion in 2025,
    up 18 percent from the previous year. Solar and wind capacity additions
    set a record of 450 GW combined across 180 countries.</p>

    Related Story: How Coal Plants Are Being Repurposed
    Read more: The Economics of Green Hydrogen

    <h2>Solar Growth</h2>
    <p>Utility-scale solar projects accounted for 60 percent of new capacity.
    Module costs fell to $0.18 per watt, making solar the cheapest electricity
    source in history in most markets.</p>

    See also: Offshore Wind Financing Trends
    Advertisement

    <h2>Policy Drivers</h2>
    <p>Government subsidies and carbon pricing mechanisms accelerated deployment.
    The EU Carbon Border Adjustment Mechanism raised costs for high-emission
    imports, creating strong domestic manufacturing incentives.</p>

    Story continues below advertisement

    <h2>Grid Integration Challenges</h2>
    <p>Rapid intermittent capacity additions stressed transmission networks.
    Battery storage and demand response programs became critical for
    grid stability as coal plants retired ahead of schedule.</p>
  </article>
  <footer>Copyright 2026 Energy Journal</footer>
</body>
</html>"""

NAV_ONLY_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <title>Session Expired</title>
  <meta http-equiv="refresh" content="0; url=/login">
</head>
<body>
  <script>window.location.replace("/login");</script>
</body>
</html>"""

METADATA_RICH_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Ocean Acidification Report</title>
  <meta name="author" content="Marine Research Institute">
  <meta name="description" content="Impact of CO2 on ocean chemistry and marine life.">
  <meta property="article:published_time" content="2026-03-15">
</head>
<body>
  <article>
    <h1>Ocean Acidification: 2026 Update</h1>
    <p>The average ocean pH has dropped from 8.2 to 8.1 over the past century —
    a 26 percent increase in acidity. Coral reef bleaching events have tripled
    in frequency since 2000, threatening biodiversity in tropical marine ecosystems.</p>

    <h2>Biological Impacts</h2>
    <p>Shell-forming organisms including oysters, mussels, and sea urchins face
    reduced calcification rates. Pteropods — small sea snails — show shell
    dissolution at current pH levels in polar oceans.</p>

    <h2>Mitigation Research</h2>
    <p>Experiments with ocean alkalinity enhancement show promise for local pH
    buffering. Carbon capture and kelp farming are also under active study as
    complementary approaches to slowing acidification rates.</p>
  </article>
</body>
</html>"""


def _hr():
    print("─" * W)


def _excerpt(text: str, n: int = 120) -> str:
    text = text.replace("\n", " ").strip()
    return text[:n] + ("…" if len(text) > n else "")


def _show_result(label: str, result):
    print(f"  {label}")
    _hr()
    if result is None:
        print("  → None  (no extractable content — page returned [])")
        return
    print(f"  title   : {result.title or '—'}")
    print(f"  author  : {result.author or '—'}")
    print(f"  date    : {result.date or '—'}")
    print(f"  language: {result.language or '—'}")
    print(f"  desc    : {result.description or '—'}")
    print()
    print(f"  extracted text ({len(result.text)} chars):")
    for line in result.text.splitlines()[:10]:
        line = line.strip()
        if line:
            print(f"    {line[:W - 4]}")
    if result.text.count("\n") > 10:
        print("    …")
    print()

    # Check what was stripped
    stripped_markers = [
        "Related Story:", "Read more:", "See also:", "Also read:",
        "Advertisement", "Story continues",
    ]
    found = [m for m in stripped_markers if m in result.text]
    if found:
        print(f"  WARNING: nav fragment still present: {found}")
    else:
        print("  ✓  Nav fragments stripped (none of the 6 CMS markers remain)")
    print()


def main():
    extractor = WebExtractor()

    print()
    print("═" * W)
    print("  blue-data-web-source — Extraction & Nav-Strip Demo")
    print("═" * W)

    # ── Case 1: Nav-heavy article ────────────────────────────
    print()
    result1 = extractor.extract(NAV_HEAVY_HTML, "https://energy.example.com/article")
    _show_result("CASE 1 — Article with nav fragments (should strip CMS noise)", result1)

    # ── Case 2: Nav-only page → None ────────────────────────
    print()
    result2 = extractor.extract(NAV_ONLY_HTML, "https://app.example.com/dashboard")
    _show_result("CASE 2 — Login-wall redirect (JS + meta-refresh → returns None)", result2)

    # ── Case 3: Rich metadata ────────────────────────────────
    print()
    result3 = extractor.extract(METADATA_RICH_HTML, "https://marine.example.com/report")
    _show_result("CASE 3 — Metadata-rich article (author + date + description)", result3)

    # ── What gets stripped ───────────────────────────────────
    print()
    print("  NAV FRAGMENT PATTERNS (stripped by WebExtractor)")
    _hr()
    patterns = [
        "Related Story:",
        "Read more:",
        "See also:",
        "Also read:",
        "Advertisement",
        "Story continues",
    ]
    for p in patterns:
        print(f"    • {p}")
    print()
    print("  These CMS cross-reference lines appear inline in article text")
    print("  after trafilatura extraction. WebExtractor removes any line")
    print("  that starts with one of these prefixes (case-sensitive).")
    print()
    print("  WebPipeline returns [] when extract() returns None — the")
    print("  vector store is never called for nav-only pages or login walls.")
    print()
    print("═" * W)
    print()


if __name__ == "__main__":
    main()
