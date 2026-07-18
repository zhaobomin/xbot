from scripts.review.preflight import preflight
from scripts.review.verify.baseline_tests import run_baseline
from scripts.review.verify.coverage_gaps import check_coverage


def test_baseline_returns_counts_and_failures():
    result = run_baseline()
    assert result.total > 100  # xbot has 2491 tests
    assert isinstance(result.failed, list)


def test_baseline_ignores_toolchain_self_tests():
    result = run_baseline()
    assert all("tests/review" not in n for n in result.failed)


def test_baseline_passed_and_skipped_are_ints():
    result = run_baseline()
    assert isinstance(result.passed, int)
    assert isinstance(result.skipped, int)
    assert result.passed + len(result.failed) + result.skipped == result.total


def test_coverage_skips_when_pytest_cov_missing(monkeypatch):
    # Force the "pytest-cov absent" branch so the skip path is exercised
    # regardless of whether pytest-cov is actually installed in this venv.
    import scripts.review.verify.coverage_gaps as cg

    monkeypatch.setattr(cg, "_pytest_cov_installed", lambda: False)
    result = check_coverage([])
    assert result.get("skipped") is True
    assert "pytest-cov" in result.get("reason", "")


def test_preflight_reports_dependency_status(monkeypatch):
    # Preflight must surface both the installed and the missing/stale cases.
    # Pin the env-dependent detectors so the assertion holds on any machine,
    # not just one without pytest-cov / with a stale codegraph.
    import scripts.review.preflight as pf

    monkeypatch.setattr(pf, "_check_pytest_cov", lambda: False)
    monkeypatch.setattr(pf, "_check_codegraph_stale", lambda: True)
    status = preflight()
    assert "ruff" in status and "pytest" in status
    assert status["ruff"] is True
    assert status["pytest_cov"] is False  # forced absent
    assert status["codegraph_stale"] is True  # forced stale
