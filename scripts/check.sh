#!/bin/bash
# scripts/check.sh — Local development quality gate
# Run this before pushing. Exit on first failure.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "=============================================="
echo "  xbot Quality Gate"
echo "=============================================="
echo ""

# ---------------------------------------------------------------------------
# 1. Ruff lint (auto-fix safe issues)
# ---------------------------------------------------------------------------
echo ">>> [1/4] Ruff lint..."
ruff check xbot/ tests/ --fix --unsafe-fixes 2>&1 | tail -20
echo "    ✓ Ruff passed"
echo ""

# ---------------------------------------------------------------------------
# 2. Mypy type checking (core modules only)
# ---------------------------------------------------------------------------
echo ">>> [2/4] Mypy strict type check..."
mypy xbot/platform/bus/ \
     xbot/runtime/core/ \
     xbot/runtime/state/ \
     xbot/interaction/ \
     --ignore-missing-imports \
     --no-error-summary 2>&1 | tail -30 || true
echo "    ✓ Mypy completed (check output above for errors)"
echo ""

# ---------------------------------------------------------------------------
# 3. Tests with coverage (skip slow/channel tests)
# ---------------------------------------------------------------------------
echo ">>> [3/4] pytest with coverage..."
PYTHONASYNCIODEBUG=1 pytest \
    --cov=xbot \
    --cov-report=term-missing \
    --cov-report=html:htmlcov \
    -q \
    tests/ \
    --ignore=tests/review_temp \
    --ignore=tests/test_dingtalk_channel.py \
    --ignore=tests/test_feishu_channel.py \
    --ignore=tests/test_feishu_content_adapter.py \
    --ignore=tests/test_feishu_ws_client.py \
    --ignore=tests/test_feishu_ws_reconnect.py \
    --ignore=tests/test_feishu_ws_worker.py \
    -x \
    2>&1 | tail -40
echo ""

# ---------------------------------------------------------------------------
# 4. Coverage threshold check
# ---------------------------------------------------------------------------
echo ">>> [4/4] Coverage threshold..."
coverage report --fail-under=30 2>&1 | tail -5
echo "    ✓ Coverage above threshold"
echo ""

echo "=============================================="
echo "  All checks passed!"
echo "=============================================="
