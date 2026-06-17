#!/usr/bin/env bash
set -euo pipefail

# Verify dual-track (legacy/full coordinator) behavior from runtime logs.
#
# Checks:
# 1) Coordinator mode has been toggled on/off at least once (optional but reported)
# 2) For target session, state transitions eventually converge from running to idle
# 3) No "stuck running" tail condition (last running appears after last idle)
#
# Usage:
#   ./scripts/verify_dual_track.sh --session feishu:ou_xxx
#   ./scripts/verify_dual_track.sh --session feishu:ou_xxx --log ~/.xbot/logs/xbot-gateway-error.log
#   ./scripts/verify_dual_track.sh --session feishu:ou_xxx --strict-toggle

LOG_PATH="${HOME}/.xbot/logs/xbot-gateway-error.log"
SESSION_KEY=""
STRICT_TOGGLE="0"
TAIL_LINES="2000"

usage() {
  cat <<EOF
Usage: $0 --session <session_key> [options]

Required:
  --session <key>          target session key (e.g. feishu:ou_xxx)

Options:
  --log <path>             log file path (default: ${LOG_PATH})
  --tail-lines <n>         only analyze latest N lines (default: 2000)
  --strict-toggle          fail if both on/off toggles are not found
  -h, --help               show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session)
      SESSION_KEY="${2:-}"
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
    --strict-toggle)
      STRICT_TOGGLE="1"
      shift
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

python - "$LOG_PATH" "$SESSION_KEY" "$TAIL_LINES" "$STRICT_TOGGLE" <<'PY'
import json
import re
import sys
from pathlib import Path

log_path = Path(sys.argv[1])
session = sys.argv[2]
tail_lines = int(sys.argv[3])
strict_toggle = sys.argv[4] == "1"

all_lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
lines = all_lines[-tail_lines:] if tail_lines > 0 else all_lines

enable_lines = [l for l in lines if "Full coordinator mode enabled" in l]
disable_lines = [l for l in lines if "disable_full_coordinator_mode" in l]

sess_lines = [l for l in lines if session in l]
transition_lines = [l for l in sess_lines if "Session state transition" in l]
sync_idle_lines = [l for l in sess_lines if "sync_idle" in l]
sync_active_lines = [l for l in sess_lines if "sync_active_tasks" in l]

run_to_idle_pairs = 0
seen_running = 0
last_running_idx = -1
last_idle_idx = -1

for idx, line in enumerate(transition_lines):
    if "-> running" in line:
        seen_running += 1
        last_running_idx = idx
    if "-> idle" in line:
        if seen_running > run_to_idle_pairs:
            run_to_idle_pairs += 1
        last_idle_idx = idx

stuck_running_tail = last_running_idx > last_idle_idx

ok = True
fail_reasons = []

if not transition_lines:
    ok = False
    fail_reasons.append("No session transition lines found")

if seen_running == 0:
    ok = False
    fail_reasons.append("No running transition found")

if not sync_idle_lines:
    ok = False
    fail_reasons.append("No sync_idle transition found")

if stuck_running_tail:
    ok = False
    fail_reasons.append("Last running transition appears after last idle (stuck tail)")

if strict_toggle and (not enable_lines or not disable_lines):
    ok = False
    fail_reasons.append("Strict toggle check failed: both on/off mode toggles not found")

report = {
    "ok": ok,
    "log_path": str(log_path),
    "session": session,
    "toggle": {
        "enabled_count": len(enable_lines),
        "disabled_count": len(disable_lines),
    },
    "transitions": {
        "total": len(transition_lines),
        "running_count": seen_running,
        "sync_active_count": len(sync_active_lines),
        "sync_idle_count": len(sync_idle_lines),
        "run_to_idle_pairs": run_to_idle_pairs,
        "stuck_running_tail": stuck_running_tail,
    },
    "fail_reasons": fail_reasons,
}

print(json.dumps(report, ensure_ascii=False, indent=2))
sys.exit(0 if ok else 1)
PY
