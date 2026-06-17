from pathlib import Path


def test_gateway_smoke_defaults_to_user_log_directory() -> None:
    source = Path("scripts/gateway_integration_smoke.py").read_text(encoding="utf-8")

    assert 'default="~/.xbot/logs/xbot-gateway.log"' in source
    assert 'default="logs/xbot-gateway.log"' not in source


def test_dual_track_scripts_default_to_user_log_directory() -> None:
    for script in (
        Path("scripts/run_dual_track_validation.sh"),
        Path("scripts/run_dual_track_soak.sh"),
        Path("scripts/verify_dual_track.sh"),
    ):
        source = script.read_text(encoding="utf-8")

        assert 'LOG_PATH="${HOME}/.xbot/logs/xbot-gateway-error.log"' in source
        assert 'LOG_PATH="logs/xbot-gateway-error.log"' not in source
