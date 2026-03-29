import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path("scripts/gateway_integration_smoke.py").resolve()
SPEC = importlib.util.spec_from_file_location("gateway_integration_smoke", SCRIPT_PATH)
assert SPEC and SPEC.loader
gateway_integration_smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gateway_integration_smoke)


def test_gateway_integration_smoke_success(monkeypatch, capsys) -> None:
    launch_output = "\n".join([
        "state = running",
        "program = /tmp/python",
        "working directory = /tmp/workdir",
    ])

    monkeypatch.setattr(gateway_integration_smoke, "_run", lambda cmd: "501\n" if cmd == ["id", "-u"] else launch_output)
    monkeypatch.setattr(
        gateway_integration_smoke,
        "_fetch_json",
        lambda url: {
            "http://127.0.0.1:18080/health/live": (200, {"status": "alive"}),
            "http://127.0.0.1:18080/health": (200, {"healthy": True}),
            "http://127.0.0.1:18080/status": (
                200,
                {
                    "uptime_seconds": 1.0,
                    "agent": "running",
                    "channels": ["feishu"],
                    "cron": {"enabled": True},
                    "memory": "unknown",
                },
            ),
        }[url],
    )
    monkeypatch.setattr(
        gateway_integration_smoke,
        "_tail",
        lambda path, lines=80: "🐈 Starting xbot gateway version 0.3.20 on port 18790...",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gateway_integration_smoke.py",
            "--expect-program",
            "/tmp/python",
            "--expect-workdir",
            "/tmp/workdir",
        ],
    )

    exit_code = gateway_integration_smoke.main()

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["failures"] == []
    assert report["launch_target"] == "gui/501/com.xbot.gateway"


def test_gateway_integration_smoke_reports_failures(monkeypatch, capsys) -> None:
    monkeypatch.setattr(gateway_integration_smoke, "_run", lambda cmd: "501\n" if cmd == ["id", "-u"] else "state = exited")
    monkeypatch.setattr(gateway_integration_smoke, "_fetch_json", lambda url: (503, {"error": "down"}))
    monkeypatch.setattr(gateway_integration_smoke, "_tail", lambda path, lines=80: "no startup marker")
    monkeypatch.setattr(sys, "argv", ["gateway_integration_smoke.py"])

    exit_code = gateway_integration_smoke.main()

    assert exit_code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["failures"]
    assert any("launchd service is not running" in item for item in report["failures"])
    assert any("startup marker" in item for item in report["failures"])
