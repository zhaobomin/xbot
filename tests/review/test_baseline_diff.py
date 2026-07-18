from scripts.review.baseline_diff import apply_diff
from scripts.review.common import Finding


def make_finding(sig_key="k1", cat="async_block"):
    return Finding(
        id="x",
        sig_key=sig_key,
        severity="P0",
        file="x.py",
        line=1,
        category=cat,
        title="t",
        detail="func: f",
        suggestion="s",
        confidence="high",
        scanner="test",
    )


def test_new_when_sig_key_not_in_baseline():
    current = [make_finding("k1")]
    baseline = {"findings": [], "fixed_history": []}
    diffed, _ = apply_diff(current, baseline)
    assert diffed[0].diff_status == "new"


def test_recurring_when_sig_key_in_baseline_findings():
    current = [make_finding("k1")]
    baseline = {"findings": [{"sig_key": "k1"}], "fixed_history": []}
    diffed, _ = apply_diff(current, baseline)
    assert diffed[0].diff_status == "recurring"


def test_regression_when_sig_key_in_fixed_history():
    current = [make_finding("k2")]
    baseline = {"findings": [], "fixed_history": [{"sig_key": "k2", "fixed_at": "2026-07-09"}]}
    diffed, _ = apply_diff(current, baseline)
    assert diffed[0].diff_status == "regression"


def test_fixed_written_to_new_fixed_history():
    baseline = {"findings": [{"sig_key": "k3"}], "fixed_history": []}
    diffed, new_fh = apply_diff([], baseline)
    assert any(e["sig_key"] == "k3" for e in new_fh)


def test_regression_removes_from_fixed_history():
    current = [make_finding("k2")]
    baseline = {"findings": [], "fixed_history": [{"sig_key": "k2", "fixed_at": "2026-07-09"}]}
    diffed, new_fh = apply_diff(current, baseline)
    assert diffed[0].diff_status == "regression"
    assert not any(e["sig_key"] == "k2" for e in new_fh)  # removed, it regressed


def test_fixed_history_ttl_drops_old_entries():
    # 5 distinct dates, k_old should be dropped (TTL=4)
    baseline = {
        "findings": [],
        "fixed_history": [
            {"sig_key": "k_old", "fixed_at": "2026-07-01"},
            {"sig_key": "k2", "fixed_at": "2026-07-09"},
            {"sig_key": "k3", "fixed_at": "2026-07-10"},
            {"sig_key": "k4", "fixed_at": "2026-07-11"},
            {"sig_key": "k5", "fixed_at": "2026-07-12"},
        ],
    }
    diffed, new_fh = apply_diff([], baseline)
    sigs = {e["sig_key"] for e in new_fh}
    assert "k_old" not in sigs  # dropped, >4 rounds old
    assert "k5" in sigs  # kept
