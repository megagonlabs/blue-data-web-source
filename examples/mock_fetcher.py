"""Shared helper: build a mock WebFetcher that serves local HTML without network.

Used by demos that need to run fully offline (no HTTP, no API keys).

Usage:
    from mock_fetcher import build_mock_fetcher, NPR_URL, CLIMATE_URL, CLIMATE_HTML

    pipeline._fetcher = build_mock_fetcher({NPR_URL: npr_html, CLIMATE_URL: CLIMATE_HTML})
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from blue_data_web_source.source.web.fetcher import FetchResult

_FIXTURES_DIR = Path(__file__).parent.parent / "data" / "fixtures"

NPR_URL     = "https://text.npr.org/nx-s1-5884537"
CLIMATE_URL = "https://climate.example.com/report"

CLIMATE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Climate Policy Report 2026</title>
  <meta name="author" content="Research Team">
  <meta name="description" content="An overview of global climate policy developments in 2026.">
</head>
<body>
  <article>
    <h1>Climate Policy Report 2026</h1>
    <p>Global greenhouse gas emissions continued to rise in 2025 despite international
    agreements. The Paris Accord signatories convened in March to discuss revised targets.
    Scientists warned that without immediate action, warming could exceed 1.5°C by 2035.</p>

    <h2>National Commitments</h2>
    <p>Several major economies announced updated nationally determined contributions.
    The European Union pledged a 65 percent reduction by 2030. The United States committed
    to net-zero by 2050. China indicated peak emissions would occur no later than 2028.</p>

    <h2>Technology Outlook</h2>
    <p>Renewable energy capacity additions reached a record high globally in 2025. Solar
    PV costs dropped twelve percent year-on-year. Battery storage deployment doubled.
    Carbon capture projects received substantial new investment across G7 nations.</p>
  </article>
</body>
</html>"""

POLICY_URL  = "https://insurance.example.com/policy"
POLICY_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Health Insurance Policy Summary</title>
  <meta name="description" content="Summary of health plan benefits and coverage details.">
</head>
<body>
  <article>
    <h1>Health Insurance Policy Summary</h1>
    <p>This policy provides comprehensive health coverage for eligible members and their
    dependents. Coverage begins on the first day of the month following enrollment.</p>

    <h2>Eligibility</h2>
    <p>Coverage is available to full-time employees working 30 or more hours per week.
    Dependents including spouses and children up to age 26 may be added during open
    enrollment or within 30 days of a qualifying life event.</p>

    <h2>Deductible and Cost Sharing</h2>
    <p>The annual deductible is $1,500 per individual and $3,000 per family. After the
    deductible is met, the plan covers 80 percent of in-network costs. The out-of-pocket
    maximum is $6,000 per individual per year.</p>

    <h2>Prescription Drugs</h2>
    <p>Prescription drugs are covered under a four-tier formulary. Generic drugs carry a
    $10 copay. Preferred brand drugs cost $35. Non-preferred brands cost $60.
    Specialty drugs require prior authorization and cost $100 per fill.</p>
  </article>
</body>
</html>"""


def load_npr_html() -> str | None:
    """Load the NPR fixture from disk. Returns None if not found."""
    path = _FIXTURES_DIR / "npr_article.html"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def build_mock_fetcher(url_map: dict[str, str]) -> MagicMock:
    """Return a mock WebFetcher that serves HTML from url_map without network access."""
    mock = MagicMock()

    def _fetch(url: str) -> FetchResult:
        html = url_map.get(url)
        if html is None:
            raise ValueError(f"No fixture registered for URL: {url!r}")
        return FetchResult(
            url=url,
            final_url=url,
            html=html,
            status_code=200,
            content_type="text/html; charset=utf-8",
        )

    mock.fetch.side_effect = _fetch
    return mock
