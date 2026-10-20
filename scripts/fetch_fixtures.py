#!/usr/bin/env python
"""Download third-party HTML test fixtures that are not committed to the repo.

Per the Disclosures section of the README, this repository does not redistribute
actual third-party datasets — only links to their original sources. The
integration and e2e suites exercise the pipeline against saved HTML; the
third-party fixtures are git-ignored and downloaded on demand by this script.

Run once before the fixture-based tests:

    python scripts/fetch_fixtures.py
    pytest lib/tests/integration/ -v -m integration
    pytest lib/tests/e2e/ -v -m e2e

Tests that need a fixture which is not present skip themselves automatically, so
this step is only required to exercise the fixture-based assertions.

Note: these fixtures are pinned to a specific article snapshot. If the upstream
page has changed since the fixture was first captured, the exact title/date/
keyword assertions may need updating — the content is third-party and outside
this project's control.

Keep the ``_FIXTURES`` table below in sync with the Disclosures > Datasets table
in the README.
"""
from __future__ import annotations

from pathlib import Path

import httpx

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "data" / "fixtures"

# fixture filename -> upstream source URL
_FIXTURES = {
    "npr_article.html": "https://text.npr.org/nx-s1-5884537",
}

_USER_AGENT = "blue-data-web-source-fixtures/0.1 (compatible; httpx)"


def fetch_all() -> None:
    _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(
        timeout=30.0, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
    ) as client:
        for name, url in _FIXTURES.items():
            dest = _FIXTURES_DIR / name
            print(f"fetching {url} -> {dest}")
            response = client.get(url)
            response.raise_for_status()
            dest.write_text(response.text, encoding="utf-8")
            print(f"  saved {len(response.text):,} chars")


if __name__ == "__main__":
    fetch_all()
