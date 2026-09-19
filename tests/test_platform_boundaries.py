"""Structural test: platform/ services must be thin adapters over lib/.

Rules enforced for every .py file under platform/*/src/ (tests excluded):

1. No imports from lib-internal packages:
   - blue_data_doc_source  (lib's own dependency — platforms use from_env())
   - blue_data.source.*    (lib's internal vector/graph abstractions)
   - blue_data_web_source.source.*  (lib's internal submodules)

2. No access to private pipeline internals via ._pipeline on the source object.
   The public API (execute_query, default_collection, from_env) is sufficient.

Add new forbidden patterns here as the lib's public surface grows.
"""
import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent

_FORBIDDEN_IMPORT_PREFIXES = [
    "blue_data_doc_source",
    "blue_data.source",
    "blue_data_web_source.source",
]

_FORBIDDEN_ATTR_RE = re.compile(r"\._pipeline\b")


def _platform_source_files():
    src_root = _REPO_ROOT / "platform"
    for src_dir in src_root.glob("*/src"):
        for path in src_dir.rglob("*.py"):
            if "__pycache__" in path.parts or ".egg-info" in str(path):
                continue
            yield path


def _rel(path: Path) -> str:
    return str(path.relative_to(_REPO_ROOT))


# ── Import check ──────────────────────────────────────────────────────────────

def _imports_in_file(path: Path):
    """Yield (lineno, module_name) for every import statement, including deferred ones."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            yield node.lineno, node.module or ""
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name


def test_no_lib_internal_imports():
    violations = []
    for path in _platform_source_files():
        for lineno, module in _imports_in_file(path):
            for prefix in _FORBIDDEN_IMPORT_PREFIXES:
                if module == prefix or module.startswith(prefix + "."):
                    violations.append(f"  {_rel(path)}:{lineno}  imports {module!r}")
    assert not violations, (
        "Platform source files must not import lib internals.\n"
        "Move the logic into lib/ or use the public WebDataSource API instead.\n\n"
        + "\n".join(violations)
    )


# ── Private attribute check ───────────────────────────────────────────────────

def test_no_private_pipeline_access():
    violations = []
    for path in _platform_source_files():
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _FORBIDDEN_ATTR_RE.search(line):
                violations.append(f"  {_rel(path)}:{i}  {line.strip()}")
    assert not violations, (
        "Platform source files must not access ._pipeline on the source object.\n"
        "Use source.default_collection or add a property to WebDataSource instead.\n\n"
        + "\n".join(violations)
    )
