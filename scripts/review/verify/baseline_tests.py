"""Baseline pytest run: capture pass/fail/skip counts ignoring toolchain self-tests."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field

VENV_PYTHON = ".venv/bin/python"
# pyproject.toml sets testpaths=["tests"], so tests/review IS collected by a
# plain `pytest -q`. Ignore it so toolchain self-tests do not pollute baseline.
BASELINE_ARGS = [
    VENV_PYTHON,
    "-m",
    "pytest",
    "-q",
    "--tb=no",
    "--ignore=tests/review",
]

# Matches the pytest summary line, e.g. "3 passed, 1 failed" or "2491 passed".
_SUMMARY_RE = re.compile(
    r"(\d+)\s+passed(?:[,\s]+(\d+)\s+failed)?(?:[,\s]+(\d+)\s+skipped)?"
)
# Matches an individual FAILED line, e.g. "FAILED tests/x.py::test_y".
_FAILED_RE = re.compile(r"^FAILED\s+(.+)$")


@dataclass
class BaselineResult:
    total: int = 0
    passed: int = 0
    failed: list[str] = field(default_factory=list)
    skipped: int = 0


def run_baseline() -> BaselineResult:
    """Run the full xbot test suite and return parsed counts/failures.

    Does not raise when pytest exits non-zero (it will, on failures); instead
    parses stdout for the summary line and individual FAILED lines.
    """
    proc = subprocess.run(
        BASELINE_ARGS,
        capture_output=True,
        text=True,
        check=False,
    )
    out = proc.stdout

    failed_nodeids: list[str] = []
    for line in out.splitlines():
        m = _FAILED_RE.match(line.strip())
        if m:
            failed_nodeids.append(m.group(1).strip())

    passed = failed = skipped = 0
    summary_match = _SUMMARY_RE.search(out)
    if summary_match:
        passed = int(summary_match.group(1))
        failed = int(summary_match.group(2) or 0)
        skipped = int(summary_match.group(3) or 0)

    # If we collected FAILED lines but the summary failed/errored, fall back to
    # the line count so callers still see the failures.
    if failed == 0 and failed_nodeids:
        failed = len(failed_nodeids)

    total = passed + failed + skipped
    return BaselineResult(
        total=total,
        passed=passed,
        failed=failed_nodeids,
        skipped=skipped,
    )


if __name__ == "__main__":  # pragma: no cover - manual sanity entrypoint
    r = run_baseline()
    sys.stdout.write(
        f"total={r.total} passed={r.passed} "
        f"failed={len(r.failed)} skipped={r.skipped}\n"
    )
    for n in r.failed:
        sys.stdout.write(f"  FAILED {n}\n")
