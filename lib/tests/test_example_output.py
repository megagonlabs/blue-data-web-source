"""Smoke-test every example script: run it and compare stdout against the
checked-in .expected file alongside the script.

Scripts that require external services (OpenAI, Redis, Tavily, MCP) are
listed in SKIP_REQUIRES so they are excluded from the default test run with
a clear reason, rather than failing silently.

To update a .expected file after an intentional output change:
    python <script>.py > <script>.expected
and commit both files together.
"""
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent.parent
_EXAMPLES = _REPO / "examples"


def _run(script: Path) -> str:
    """Run *script* and return combined stdout+stderr as a string."""
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=str(_REPO),
    )
    return result.stdout + result.stderr, result.returncode


# ── Offline scripts — have .expected files, run in CI with no deps ────────────

OFFLINE_SCRIPTS = [
    _EXAMPLES / "backends" / "memory_quickstart.py",
    _EXAMPLES / "demo_web.py",
    _EXAMPLES / "feature-demos" / "extractor_demo.py",
    _EXAMPLES / "feature-demos" / "content_addressing_demo.py",
    _EXAMPLES / "scenario-demos" / "extraction_demo.py",
    _EXAMPLES / "scenario-demos" / "crawl_vs_single_demo.py",
    _EXAMPLES / "integration-demos" / "multi_url_demo.py",
    _EXAMPLES / "integration-demos" / "crawler_demo.py",
]

# Scripts whose captured output depends on the NPR HTML fixture (served via
# examples/mock_fetcher.py). That fixture is third-party content and is not
# committed to the repo — it is fetched on demand (see README "Disclosures" and
# scripts/fetch_fixtures.py). When it is absent these scripts cannot reproduce
# their .expected output, so skip them with a clear reason rather than fail. #80
_FIXTURE = _REPO / "data" / "fixtures" / "npr_article.html"
_FIXTURE_DEPENDENT = {
    "memory_quickstart.py",
    "demo_web.py",
    "crawl_vs_single_demo.py",
    "multi_url_demo.py",
    "crawler_demo.py",
}


@pytest.mark.parametrize("script", OFFLINE_SCRIPTS, ids=lambda p: p.stem)
def test_example_output(script: Path):
    if script.name in _FIXTURE_DEPENDENT and not _FIXTURE.exists():
        pytest.skip(
            f"{script.name} needs the NPR fixture, which is not committed. "
            "Run `python scripts/fetch_fixtures.py` to download it "
            "(third-party content; see README 'Disclosures')."
        )

    expected_file = script.with_suffix(".expected")
    assert expected_file.exists(), (
        f"Missing captured output file: {expected_file.relative_to(_REPO)}\n"
        f"Run:  python {script.relative_to(_REPO)} > {expected_file.relative_to(_REPO)}"
    )

    actual, returncode = _run(script)
    expected = expected_file.read_text(encoding="utf-8")

    assert returncode == 0, (
        f"{script.name} exited with code {returncode}.\nOutput:\n{actual}"
    )
    assert actual == expected, (
        f"Output of {script.name} does not match {expected_file.name}.\n"
        f"To update: python {script.relative_to(_REPO)} > {expected_file.relative_to(_REPO)}"
    )


# ── External-dep scripts — skipped by default ─────────────────────────────────

SKIP_REQUIRES = [
    (_EXAMPLES / "demo.py",                                      "OPENAI_API_KEY"),
    (_EXAMPLES / "ingest_url.py",                                "live network"),
    (_EXAMPLES / "backends" / "openai_embeddings_demo.py",       "OPENAI_API_KEY"),
    (_EXAMPLES / "backends" / "redis_backend_demo.py",           "Redis Stack"),
    (_EXAMPLES / "integration-demos" / "search_web_demo.py",     "TAVILY_API_KEY + OPENAI_API_KEY"),
    (_EXAMPLES / "framework-demos" / "langgraph_rag_demo.py",    "OPENAI_API_KEY + langgraph"),
    (_EXAMPLES / "framework-demos" / "mcp_demo.py",              "OPENAI_API_KEY + langchain-mcp-adapters (spawns its own MCP server)"),
]


@pytest.mark.parametrize("script,reason", SKIP_REQUIRES, ids=lambda x: x.stem if isinstance(x, Path) else x)
def test_external_dep_scripts_are_documented(script: Path, reason: str):
    """Verify that every external-dep script at least exists in the repo."""
    assert script.exists(), f"Script not found: {script.relative_to(_REPO)}"
