#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${1:-root@192.161.53.80}"
REMOTE_APP_DIR="${REMOTE_APP_DIR:-/root/xbot}"
REMOTE_HOME_DIR="${REMOTE_HOME_DIR:-/root/.xbot}"
REMOTE_LOG_DIR="${REMOTE_LOG_DIR:-$REMOTE_HOME_DIR/logs}"
REMOTE_CONDA_DIR="${REMOTE_CONDA_DIR:-/root/miniconda3}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCHIVE_DIR="$(mktemp -d)"
DEPLOY_TGZ="$ARCHIVE_DIR/xbot-deploy.tgz"
HOME_TGZ="$ARCHIVE_DIR/xbot-home.tgz"

cleanup() {
  rm -rf "$ARCHIVE_DIR"
}
trap cleanup EXIT

echo "[1/6] Packing repo from $SCRIPT_DIR"
tar \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='.DS_Store' \
  -czf "$DEPLOY_TGZ" \
  -C "$SCRIPT_DIR" \
  Dockerfile docker-compose.yml pyproject.toml README.md LICENSE xbot bridge usa_install.sh

echo "[2/6] Packing local xbot home from $HOME/.xbot"
tar \
  --exclude='.DS_Store' \
  -czf "$HOME_TGZ" \
  -C "$HOME" \
  .xbot

echo "[3/6] Uploading bundles to $REMOTE_HOST"
scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  "$DEPLOY_TGZ" "$HOME_TGZ" "$REMOTE_HOST:/root/"

echo "[4/6] Restoring files on $REMOTE_HOST"
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$REMOTE_HOST" "
  set -euo pipefail
  rm -rf '$REMOTE_APP_DIR'
  mkdir -p '$REMOTE_APP_DIR' '$REMOTE_HOME_DIR' '$REMOTE_LOG_DIR'
  tar xzf /root/xbot-deploy.tgz -C '$REMOTE_APP_DIR'
  tar xzf /root/xbot-home.tgz -C /root
  find '$REMOTE_APP_DIR' '$REMOTE_HOME_DIR' -name '._*' -delete || true
"

echo "[5/6] Installing/upgrading runtime on $REMOTE_HOST"
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$REMOTE_HOST" "
  set -euo pipefail

  if [ ! -x '$REMOTE_CONDA_DIR/bin/python' ]; then
    curl -L -o /root/miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
    bash /root/miniconda.sh -b -p '$REMOTE_CONDA_DIR'
  fi

  cd '$REMOTE_APP_DIR'
  '$REMOTE_CONDA_DIR/bin/python' -m venv .venv
  . .venv/bin/activate
  python -m pip install -U pip
  python -m pip install -e .

  python - <<'PY'
import json
from pathlib import Path

config_path = Path('$REMOTE_HOME_DIR/config.json')
if not config_path.exists():
    raise SystemExit(f'Missing config: {config_path}')

data = json.loads(config_path.read_text(encoding='utf-8'))
agents = data.setdefault('agents', {})
if agents.get('type') == 'claude_sdk':
    sdk = agents.setdefault('claude_sdk', {})
    if sdk.get('permission_mode') == 'bypassPermissions':
        sdk['permission_mode'] = 'acceptEdits'
        config_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + '\\n',
            encoding='utf-8',
        )
        print('Adjusted claude_sdk.permission_mode: bypassPermissions -> acceptEdits')
PY
"

echo "[6/6] Restarting gateway on $REMOTE_HOST"
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$REMOTE_HOST" "
  set -euo pipefail
  pkill -f '$REMOTE_APP_DIR/.venv/bin/xbot gateway' || true
  cd '$REMOTE_APP_DIR'
  nohup '$REMOTE_APP_DIR/.venv/bin/xbot' gateway > '$REMOTE_LOG_DIR/gateway.log' 2>&1 < /dev/null &
  sleep 5
  '$REMOTE_APP_DIR/.venv/bin/xbot' status
  echo '--- gateway.log ---'
  tail -n 80 '$REMOTE_LOG_DIR/gateway.log' || true
"

echo
echo "Done. Follow logs with:"
echo "ssh $REMOTE_HOST 'tail -f $REMOTE_LOG_DIR/gateway.log'"
