#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(dirname "$SCRIPT_DIR")"          # lib/
REPO_ROOT="$(dirname "$PACKAGE_DIR")"           # blue-data-web-source/
PROJECTS_DIR="$(dirname "$REPO_ROOT")"          # parent dir containing sibling repos

# lib depends on the blue-data and blue-data-doc-source packages, which live in
# each sibling repo's lib/ subdirectory (the repo root is not a Python project).
# Install them first so that `pip install -e lib` finds its requirements already
# satisfied and does not reach for a package index.
pip install -e "$PROJECTS_DIR/blue-data/lib"
pip install -e "$PROJECTS_DIR/blue-data-doc-source/lib"
pip install -e "$PACKAGE_DIR"
