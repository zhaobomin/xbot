#!/usr/bin/env bash
set -euo pipefail

# One-shot dual-track validation:
# 1) Health check of running gateway service
# 2) Run regression tests for dual-track/session-state logic
# 3) Verify runtime logs for a target session (toggle + convergence)
#
# Usage:
#   ./scripts/run_dual_track_validation.sh --session feishu:ou_xxx
#   ./scripts/run_dual_track_validation.sh --session feishu:ou_xxx --skip-tests

SESSION_KEY=""
HEALTH_URL="http://127.0.0.1:18080/health"
SKIP_TESTS="0"
LOG_PATH="${HOME}/.xbot/logs/xbot-gateway-error.log"
TAIL_LINES="3000"
REPORT_DIR="logs"
REPORT_FILE=""

usage() {
  cat <<EOF
Usage: $0 --session <session_key> [options]

Required:
  --session <key>          target session key (e.g. feishu:ou_xxx)

Options:
  --health-url <url>       health endpoint (default: ${HEALTH_URL})
  --log <path>             log path for dual-track verification
  --tail-lines <n>         lines analyzed from tail of log (default: ${TAIL_LINES})
  --skip-tests             skip pytest regression suite
  --report-file <path>     output markdown report file
  -h, --help               show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session)
      SESSION_KEY="${2:-}"
      shift 2
      ;;
    --health-url)
      HEALTH_URL="${2:-}"
      shift 2
      ;;
    --log)
      LOG_PATH="${2:-}"
      shift 2
      ;;
    --tail-lines)
      TAIL_LINES="${2:-}"
      shift 2
      ;;
    --skip-tests)
      SKIP_TESTS="1"
      shift
      ;;
    --report-file)
      REPORT_FILE="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "${SESSION_KEY}" ]]; then
  echo "ERROR: --session is required" >&2
  usage
  exit 2
fi

timestamp="$(date '+%Y%m%d-%H%M%S')"
if [[ -z "${REPORT_FILE}" ]]; then
  REPORT_FILE="${REPORT_DIR}/dual_track_validation_report_${timestamp}.md"
fi

mkdir -p "$(dirname "${REPORT_FILE}")"

health_json=""
pytest_output=""
verify_json=""
status="PASS"

echo "== Step 1/3: Health check =="
if ! health_json="$(curl -fsS "${HEALTH_URL}")"; then
  echo "Health check failed: ${HEALTH_URL}" >&2
  status="FAIL"
fi
echo "health: ${health_json}"

if [[ "${SKIP_TESTS}" != "1" ]]; then
  echo "== Step 2/3: Pytest dual-track suite =="
  if ! pytest_output="$(
    .venv/bin/python -m pytest -q \
    tests/test_runtime_run_dispatch_sequence.py \
    tests/test_runtime_cutover_readiness.py \
    tests/test_atomic_dispatch.py \
    tests/test_full_coordinator_mode.py \
    tests/test_runtime_coordinator.py \
    tests/test_runtime_permission.py \
    tests/test_state_transaction.py
  )"; then
    status="FAIL"
  fi
  echo "${pytest_output}"
else
  echo "== Step 2/3: Pytest skipped =="
  pytest_output="(skipped)"
fi

echo "== Step 3/3: Runtime log verification =="
if ! verify_json="$(
  ./scripts/verify_dual_track.sh \
  --session "${SESSION_KEY}" \
  --log "${LOG_PATH}" \
  --tail-lines "${TAIL_LINES}" \
  --strict-toggle
)"; then
  status="FAIL"
fi
echo "${verify_json}"

cat >"${REPORT_FILE}" <<EOF
# Dual Track Validation Report

- Generated at: $(date '+%Y-%m-%d %H:%M:%S %z')
- Session: \`${SESSION_KEY}\`
- Health URL: \`${HEALTH_URL}\`
- Log Path: \`${LOG_PATH}\`
- Result: **${status}**

## Health
\`\`\`json
${health_json}
\`\`\`

## Pytest
\`\`\`text
${pytest_output}
\`\`\`

## Runtime Log Verification
\`\`\`json
${verify_json}
\`\`\`
EOF

echo "== REPORT: ${REPORT_FILE} =="
echo "== RESULT: ${status} =="

[[ "${status}" == "PASS" ]]
