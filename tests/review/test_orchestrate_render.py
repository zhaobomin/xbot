from __future__ import annotations

from scripts.review import orchestrate
from scripts.review.common import Finding
from scripts.review.render import render_report


def _make_finding(**kw) -> Finding:
    base: dict = dict(
        id="x",
        sig_key="k",
        severity="P0",
        file="a.py",
        line=1,
        category="async_block",
        title="t",
        detail="func: f",
        suggestion="s",
        confidence="high",
        scanner="test",
        verdict="confirmed",
        diff_status="new",
    )
    base.update(kw)
    return Finding(**base)


# --- render_report ---------------------------------------------------------


def test_report_has_summary_and_sections():
    f = _make_finding()
    report = render_report([f], baseline={}, version="v2.0.39")
    assert "xbot" in report and "摘要" in report
    assert "P0" in report and "[NEW]" in report


def test_report_recurring_regression_and_refuted_tags():
    findings = [
        _make_finding(id="r", sig_key="r", diff_status="recurring"),
        _make_finding(id="g", sig_key="g", diff_status="regression"),
        _make_finding(id="rf", sig_key="rf", verdict="refuted", diff_status="new"),
    ]
    report = render_report(findings, baseline={}, version="v2.0.39")
    assert "[RECURRING]" in report
    assert "[REGRESSION]" in report
    assert "已排除误报" in report  # refuted appendix table


def test_report_fixed_and_toolchain_appendices():
    baseline = {
        "findings": [{"sig_key": "gone", "severity": "P1"}],
        "fixed_history": [{"sig_key": "gone", "fixed_at": "2026-07-09"}],
    }
    toolchain = _make_finding(
        id="tc",
        sig_key="tc",
        category="toolchain_error",
        severity="P2",
        file="<eslint>",
        line=0,
        title="eslint broken",
        diff_status="new",
        scanner="preflight",
    )
    report = render_report([toolchain], baseline=baseline, version="v2.0.39")
    assert "已修复" in report
    assert "工具链错误" in report


# --- orchestrate -----------------------------------------------------------


def _patch_pipeline(monkeypatch, tracks_findings):
    monkeypatch.setattr(orchestrate, "_run_tracks", lambda: list(tracks_findings))
    monkeypatch.setattr(orchestrate.verify, "run", lambda fs: fs)  # passthrough verify
    monkeypatch.setattr(
        orchestrate.preflight,
        "preflight",
        lambda: {
            "ruff": True,
            "pytest": True,
            "tsc": True,
            "eslint": False,
            "pytest_cov": False,
            "codegraph_stale": True,
        },
    )
    monkeypatch.setattr(
        orchestrate, "_load_baseline", lambda: {"findings": [], "fixed_history": []}
    )
    monkeypatch.setattr(orchestrate, "_write_outputs", lambda *a, **k: None)


def test_dry_run_does_not_scan_or_verify(monkeypatch):
    state = {"tracks": False, "verify": False}

    def fake_tracks():
        state["tracks"] = True
        return []

    monkeypatch.setattr(orchestrate, "_run_tracks", fake_tracks)
    monkeypatch.setattr(
        orchestrate.verify,
        "run",
        lambda fs: state.__setitem__("verify", True) or fs,
    )
    monkeypatch.setattr(
        orchestrate.preflight,
        "preflight",
        lambda: {
            "ruff": True,
            "pytest": True,
            "tsc": True,
            "eslint": False,
            "pytest_cov": False,
            "codegraph_stale": True,
        },
    )
    result = orchestrate.orchestrate(version="v2.0.39", dry_run=True)
    assert result["dry_run"] is True
    assert "preflight" in result
    assert state["tracks"] is False
    assert state["verify"] is False


def test_orchestrate_pipeline_monkeypatched(monkeypatch):
    canned = [
        _make_finding(id="a", sig_key="a", severity="P0", diff_status="new"),
        _make_finding(id="b", sig_key="b", severity="P1", diff_status="recurring"),
    ]
    _patch_pipeline(monkeypatch, canned)
    result = orchestrate.orchestrate(version="v2.0.39")
    assert result["dry_run"] is False
    assert "report" in result
    assert "xbot" in result["report"]
    assert "findings" in result
    assert "fixed_history" in result
    assert "baseline_failures" in result
    assert "[NEW]" in result["report"]
    assert "P0" in result["report"]
