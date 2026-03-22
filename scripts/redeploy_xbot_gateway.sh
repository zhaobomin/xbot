#!/usr/bin/env bash
set -euo pipefail

# One-click rebuild/install/restart script for xbot gateway (launchd).
#
# Default behavior:
# 1) Compile-check source
# 2) Reinstall current repo into .venv (editable)
# 3) Restart launchd service
# 4) Verify service + optional health endpoint
#
# Usage examples:
#   ./scripts/redeploy_xbot_gateway.sh
#   ./scripts/redeploy_xbot_gateway.sh --label com.xbot.gateway --health-url http://127.0.0.1:18080/health
#   ./scripts/redeploy_xbot_gateway.sh --skip-health

LABEL="com.xbot.gateway"
HEALTH_URL="http://127.0.0.1:18080/health"
WAIT_SECONDS=20
SKIP_HEALTH="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --label)
      LABEL="${2:-}"
      shift 2
      ;;
    --health-url)
      HEALTH_URL="${2:-}"
      shift 2
      ;;
    --wait-seconds)
      WAIT_SECONDS="${2:-}"
      shift 2
      ;;
    --skip-health)
      SKIP_HEALTH="1"
      shift
      ;;
    -h|--help)
      cat <<'EOF'
Usage: redeploy_xbot_gateway.sh [options]

Options:
  --label <label>            launchd label (default: com.xbot.gateway)
  --health-url <url>         health endpoint (default: http://127.0.0.1:18080/health)
  --wait-seconds <n>         max wait seconds for running state (default: 20)
  --skip-health              skip HTTP health check
  -h, --help                 show help
EOF
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PY="${REPO_ROOT}/.venv/bin/python"
PLIST_PATH="${HOME}/Library/LaunchAgents/${LABEL}.plist"
UID_NUM="$(id -u)"
LAUNCH_TARGET="gui/${UID_NUM}/${LABEL}"

log() {
  echo "[redeploy] $*"
}

die() {
  echo "[ERROR] $*" >&2
  exit 1
}

[[ -x "${VENV_PY}" ]] || die "Python not found: ${VENV_PY}"
[[ -f "${PLIST_PATH}" ]] || die "LaunchAgent plist not found: ${PLIST_PATH}"

log "Repo root: ${REPO_ROOT}"
log "Python: ${VENV_PY}"
log "Launch label: ${LABEL}"
log "Plist: ${PLIST_PATH}"

log "Step 1/4: compile check"
cd "${REPO_ROOT}"
"${VENV_PY}" -m compileall -q xbot

log "Step 2/4: reinstall package (editable)"
"${VENV_PY}" -m pip install -e .

log "Step 3/4: restart launchd service"
launchctl bootout "gui/${UID_NUM}" "${PLIST_PATH}" 2>/dev/null || true
launchctl bootstrap "gui/${UID_NUM}" "${PLIST_PATH}"
launchctl kickstart -k "${LAUNCH_TARGET}"

log "Step 4/4: verify launchd state"
ok="0"
for ((i=0; i<WAIT_SECONDS; i++)); do
  if launchctl print "${LAUNCH_TARGET}" 2>/dev/null | rg -q "state = running"; then
    ok="1"
    break
  fi
  sleep 1
done

if [[ "${ok}" != "1" ]]; then
  launchctl print "${LAUNCH_TARGET}" 2>/dev/null || true
  die "Service did not reach running state within ${WAIT_SECONDS}s"
fi

launchctl print "${LAUNCH_TARGET}" | sed -n '1,60p'

if [[ "${SKIP_HEALTH}" == "1" ]]; then
  log "Health check skipped"
  exit 0
fi

log "Health check: ${HEALTH_URL}"
for ((i=0; i<WAIT_SECONDS; i++)); do
  if curl -fsS "${HEALTH_URL}" >/tmp/xbot-health.json 2>/dev/null; then
    cat /tmp/xbot-health.json
    echo
    rm -f /tmp/xbot-health.json
    log "Redeploy completed"
    exit 0
  fi
  sleep 1
done

rm -f /tmp/xbot-health.json
die "Health check failed: ${HEALTH_URL}"

