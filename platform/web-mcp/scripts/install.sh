#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(dirname "$SCRIPT_DIR")"                      # platform/web-mcp/
REPO_ROOT="$(dirname "$(dirname "$PACKAGE_DIR")")"          # blue-data-web-source/

# Install lib (and its sibling deps) first
bash "$REPO_ROOT/lib/scripts/install.sh"
pip install -e "$PACKAGE_DIR"
