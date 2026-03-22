#!/usr/bin/env bash
set -euo pipefail

# Soak monitoring for dual-track cutover readiness.
# Monitors service health + process liveness + incremental error log signals.
#
# Usage:
#   ./scripts/run_dual_track_soak.sh --session feishu:ou_xxx --duration-min 30

SESSION_KEY=""
DURATION_MIN="30"
INTERVAL_SEC="30"
HEALTH_URL="http://127.0.0.1:18080/health"
LOG_PATH="logs/xbot-gateway-error.log"
SERVICE_LABEL="com.xbot.gateway"
REPORT_DIR="logs"
REPORT_FILE=""

usage() {
  cat <<EOF
Usage: $0 --session <session_key> [options]

Required:
  --session <key>          target session key (e.g. feishu:ou_xxx)

Options:
  --duration-min <n>       soak duration in minutes (default: 30)
  --interval-sec <n>       sampling interval in seconds (default: 30)
  --health-url <url>       health endpoint
  --log <path>             error log path (default: logs/xbot-gateway-error.log)
  --service-label <label>  launchd label (default: com.xbot.gateway)
  --report-file <path>     output markdown report
  -h, --help               show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session)
      SESSION_KEY="${2:-}"
      shift 2
      ;;
    --duration-min)
      DURATION_MIN="${2:-}"
      shift 2
      ;;
    --interval-sec)
      INTERVAL_SEC="${2:-}"
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
    --service-label)
      SERVICE_LABEL="${2:-}"
      shift 2
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

if [[ ! -f "${LOG_PATH}" ]]; then
  echo "ERROR: log file not found: ${LOG_PATH}" >&2
  exit 2
fi

timestamp="$(date '+%Y%m%d-%H%M%S')"
if [[ -z "${REPORT_FILE}" ]]; then
  REPORT_FILE="${REPORT_DIR}/dual_track_soak_report_${timestamp}.md"
fi
mkdir -p "$(dirname "${REPORT_FILE}")"

start_line="$(wc -l < "${LOG_PATH}" | tr -d ' ')"
samples=$(( (DURATION_MIN * 60 + INTERVAL_SEC - 1) / INTERVAL_SEC ))

health_failures=0
proc_failures=0
health_samples=()
proc_samples=()

echo "== Soak start =="
echo "session=${SESSION_KEY}"
echo "duration_min=${DURATION_MIN}, interval_sec=${INTERVAL_SEC}, samples=${samples}"
echo "service_label=${SERVICE_LABEL}"
echo "log_path=${LOG_PATH}"

for ((i=1; i<=samples; i++)); do
  ts="$(date '+%Y-%m-%d %H:%M:%S')"

  if health_json="$(curl -fsS "${HEALTH_URL}" 2>/dev/null)"; then
    health_samples+=("[${ts}] ok ${health_json}")
  else
    health_failures=$((health_failures + 1))
    health_samples+=("[${ts}] FAIL health endpoint")
  fi

  pid="$(launchctl list | awk -v lbl="${SERVICE_LABEL}" '$3==lbl{print $1}')"
  if [[ -n "${pid}" && "${pid}" != "-" ]]; then
    proc_samples+=("[${ts}] ok pid=${pid}")
  else
    proc_failures=$((proc_failures + 1))
    proc_samples+=("[${ts}] FAIL service not running")
  fi

  if (( i < samples )); then
    sleep "${INTERVAL_SEC}"
  fi
done

tmp_newlog="$(mktemp)"
sed -n "$((start_line + 1)),\$p" "${LOG_PATH}" > "${tmp_newlog}" || true

verify_json="$(./scripts/verify_dual_track.sh --session "${SESSION_KEY}" --log "${LOG_PATH}" --tail-lines 4000 || true)"

analysis_json="$(python - "${tmp_newlog}" "${SESSION_KEY}" <<'PY'
import json
import re
import sys
from pathlib import Path

newlog = Path(sys.argv[1]).read_text(encoding="utf-8", errors="ignore").splitlines()
session = sys.argv[2]

fatal_patterns = [
    "Gateway crashed unexpectedly",
    "Traceback (most recent call last):",
    "AttributeError:",
    "RuntimeError:",
]
warn_patterns = [
    "State inconsistency at",
    "Invalid state transition",
]

fatal_hits = [l for l in newlog if any(p in l for p in fatal_patterns)]
warn_hits = [l for l in newlog if any(p in l for p in warn_patterns)]
session_lines = [l for l in newlog if session in l and "Session state transition" in l]
session_running = sum("-> running" in l for l in session_lines)
session_idle = sum("-> idle" in l for l in session_lines)

out = {
    "new_log_lines": len(newlog),
    "fatal_count": len(fatal_hits),
    "warning_count": len(warn_hits),
    "session_transition_count": len(session_lines),
    "session_running_count": session_running,
    "session_idle_count": session_idle,
    "fatal_examples": fatal_hits[:5],
    "warning_examples": warn_hits[:10],
}
print(json.dumps(out, ensure_ascii=False))
PY
)"

overall="PASS"
if [[ "${health_failures}" -gt 0 || "${proc_failures}" -gt 0 ]]; then
  overall="FAIL"
fi

if [[ "$(python - <<'PY' "${analysis_json}"
import json,sys
d=json.loads(sys.argv[1]); print(1 if d["fatal_count"]>0 else 0)
PY
)" == "1" ]]; then
  overall="FAIL"
fi

{
  echo "# Dual Track Soak Report"
  echo
  echo "- Generated at: $(date '+%Y-%m-%d %H:%M:%S %z')"
  echo "- Session: \`${SESSION_KEY}\`"
  echo "- Duration: ${DURATION_MIN} min"
  echo "- Interval: ${INTERVAL_SEC} sec"
  echo "- Health URL: \`${HEALTH_URL}\`"
  echo "- Service Label: \`${SERVICE_LABEL}\`"
  echo "- Log Path: \`${LOG_PATH}\`"
  echo "- Result: **${overall}**"
  echo
  echo "## Health Samples"
  echo '```text'
  printf '%s\n' "${health_samples[@]}"
  echo '```'
  echo
  echo "## Process Samples"
  echo '```text'
  printf '%s\n' "${proc_samples[@]}"
  echo '```'
  echo
  echo "## Incremental Log Analysis"
  echo '```json'
  python - <<'PY' "${analysis_json}"
import json,sys
print(json.dumps(json.loads(sys.argv[1]), ensure_ascii=False, indent=2))
PY
  echo '```'
  echo
  echo "## Session Convergence Check (full log tail)"
  echo '```json'
  printf '%s\n' "${verify_json}"
  echo '```'
} > "${REPORT_FILE}"

rm -f "${tmp_newlog}"

echo "== REPORT: ${REPORT_FILE} =="
echo "== RESULT: ${overall} =="

[[ "${overall}" == "PASS" ]]
