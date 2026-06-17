#!/usr/bin/env python3
"""Launchd-backed xbot gateway smoke checks.

Verifies:
- launchd service is running
- program/working directory match expectations
- health endpoints respond
- status endpoint contains expected top-level fields
- recent gateway log tail contains a startup marker
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout


def _fetch_json(url: str) -> tuple[int, dict]:
    try:
        with urlopen(url, timeout=5) as response:
            status = response.status
            data = json.loads(response.read().decode("utf-8"))
            return status, data
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            data = {"raw": body}
        return exc.code, data
    except URLError as exc:
        raise RuntimeError(f"Failed to reach {url}: {exc}") from exc


def _tail(path: Path, lines: int = 80) -> str:
    if not path.exists():
        raise RuntimeError(f"Log file not found: {path}")
    text = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(text[-lines:])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="com.xbot.gateway")
    parser.add_argument("--health-url", default="http://127.0.0.1:18080/health")
    parser.add_argument("--live-url", default="http://127.0.0.1:18080/health/live")
    parser.add_argument("--status-url", default="http://127.0.0.1:18080/status")
    parser.add_argument("--expect-program", default="")
    parser.add_argument("--expect-workdir", default="")
    parser.add_argument("--log-path", default="~/.xbot/logs/xbot-gateway.log")
    args = parser.parse_args()

    uid = str(_run(["id", "-u"]).strip())
    launch_target = f"gui/{uid}/{args.label}"
    launch_output = _run(["launchctl", "print", launch_target])

    failures: list[str] = []
    if "state = running" not in launch_output:
        failures.append("launchd service is not running")
    if args.expect_program and args.expect_program not in launch_output:
        failures.append(f"expected program not found in launchctl output: {args.expect_program}")
    if args.expect_workdir and args.expect_workdir not in launch_output:
        failures.append(f"expected working directory not found in launchctl output: {args.expect_workdir}")

    live_status, live_payload = _fetch_json(args.live_url)
    if live_status != 200 or live_payload.get("status") != "alive":
        failures.append(f"live check failed: status={live_status} payload={live_payload}")

    health_status, health_payload = _fetch_json(args.health_url)
    if health_status != 200 or "healthy" not in health_payload:
        failures.append(f"health check failed: status={health_status} payload={health_payload}")

    status_status, status_payload = _fetch_json(args.status_url)
    if status_status != 200:
        failures.append(f"status endpoint failed: status={status_status}")
    else:
        for key in ("uptime_seconds", "agent", "channels", "cron", "memory"):
            if key not in status_payload:
                failures.append(f"status payload missing key: {key}")

    log_tail = _tail(Path(args.log_path).expanduser())
    if "Starting xbot gateway version" not in log_tail:
        failures.append("gateway startup marker not found in recent log tail")

    report = {
        "launch_target": launch_target,
        "live": {"status": live_status, "payload": live_payload},
        "health": {"status": health_status, "payload": health_payload},
        "status": {"status": status_status, "payload": status_payload},
        "failures": failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
