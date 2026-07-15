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


def test_coverage_skips_when_pytest_cov_missing():
    # pytest-cov is NOT installed in this venv
    result = check_coverage([])
    assert result.get("skipped") is True
    assert "pytest-cov" in result.get("reason", "")
