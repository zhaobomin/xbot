"""Verify-layer orchestrator.

Pipeline: baseline tests -> coverage gaps -> run generated regression tests
(via run_regression, which calls gen_regression.write_test internally) ->
confidence_updater applies dynamic verdicts and static confirmation for
no-template categories.
"""
from __future__ import annotations

from scripts.review.common import Finding
from scripts.review.verify import confidence_updater
from scripts.review.verify.baseline_tests import run_baseline
from scripts.review.verify.coverage_gaps import check_coverage
from scripts.review.verify.run_regression import run_with_results


def run(findings_raw: list[Finding]) -> list[Finding]:
    # 1. Baseline pytest run (informational; surfaces pre-existing failures).
    # Cached on the function so the top-level orchestrator can report
    # baseline_failures in findings_final.json without a second pytest run.
    run._baseline_result = run_baseline()
    # 2. Coverage gap analysis (informational; skipped without pytest-cov).
    check_coverage(findings_raw)
    # 3. Generate + run regression tests for template-eligible findings,
    #    mapping pass/fail/error -> confirmed/refuted/inconclusive.
    findings_dyn, verify_results = run_with_results(findings_raw)
    # 4. Finalize: dynamic verdicts (re-affirmed, idempotent) + static rules
    #    for no-template categories (dead_code, naming_remnants, ...).
    findings_verified = confidence_updater.update(findings_dyn, verify_results)
    return findings_verified
