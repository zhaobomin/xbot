#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

pytest \
  tests/unit/runtime/test_agent_service_v2_integration.py \
  tests/unit/runtime/test_busy_spin_recovery_dispatch.py \
  tests/unit/runtime/test_runtime_registry_v2.py \
  tests/unit/runtime/test_session_state_v2.py

# State-machine core coverage gate (hard gate).
pytest \
  tests/unit/runtime/test_runtime_registry_v2.py \
  tests/unit/runtime/test_session_state_v2.py \
  --cov=xbot.runtime.state.coordinator \
  --cov=xbot.runtime.state.runtime_registry \
  --cov-report=term-missing \
  --cov-fail-under=90
