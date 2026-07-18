#!/usr/bin/env bash
# TS review runner: runs all 6 regex scanners + build_tsc + lint_eslint,
# dedups findings, and writes findings_ts.json. Delegates the collection and
# merge to the Python runner module so dedup/contract logic stays in one place.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"

PY="${REPO_ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
    PY="$(command -v python3 || command -v python)"
fi

TARGET="${1:-.}"
exec "$PY" -m scripts.review.ts.runner "$TARGET"
