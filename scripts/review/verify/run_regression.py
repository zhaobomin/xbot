"""Run generated regression tests and map pytest outcomes to finding verdicts.

Verdict inversion (the generated test asserts the *correct* behavior):
  * test FAILED  (assertion failure) -> verdict="confirmed", confidence >= "medium"
  * test PASSED                    -> verdict="refuted",    confidence="low"
  * test ERROR   (import/syntax)   -> verdict="inconclusive", keep confidence
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import replace
from pathlib import Path

from scripts.review.common import Finding
from scripts.review.verify.gen_regression import (
    TEMPLATE_CATEGORIES,
    _sanitize_identifier,
    write_test,
)

VENV_PYTHON = ".venv/bin/python"
_TEMP_DIR = Path("tests/review_temp")

# Matches pytest short-summary lines: "FAILED tests/...::test_x - reason"
# or file-level collection errors: "ERROR tests/.../test_x.py".
_OUTCOME_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+?)(?:\s+-\s+(.+))?$")

_CONF_RANK = {"high": 3, "medium": 2, "low": 1}


def _rank(confidence: str) -> int:
    return _CONF_RANK.get(confidence, 0)


def _file_key(finding: Finding) -> str:
    """The test-file path (no test name) write_test produces for *finding*.

    Outcomes are attributed by file prefix so that collection/import errors
    (reported at the module level, e.g. ``ERROR tests/.../test_x.py`` with no
    ``::test_x`` suffix) are still matched to their finding.
    """
    sid = _sanitize_identifier(finding.id)
    return f"{_TEMP_DIR}/test_{sid}.py"


def _parse_outcomes(output: str) -> list[tuple[str, str, str]]:
    """Return [(status, nodeid, reason)] for every FAILED/ERROR summary line."""
    out: list[tuple[str, str, str]] = []
    for raw in output.splitlines():
        line = raw.strip()
        m = _OUTCOME_RE.match(line)
        if not m:
            continue
        status, nodeid, reason = m.group(1), m.group(2), (m.group(3) or "")
        out.append((status, nodeid, reason))
    return out


def run_with_results(findings: list[Finding]) -> tuple[list[Finding], dict[str, str]]:
    """Generate, run, and map outcomes; also return raw per-finding statuses.

    Returns (updated_findings, {finding.id: "failed"|"passed"|"error"}).
    """
    # 1. Write a generated test for every template-eligible finding that has a func.
    written: dict[str, Finding] = {}
    for f in findings:
        if f.category not in TEMPLATE_CATEGORIES:
            continue
        path = write_test(f)
        if path is not None:
            written[_file_key(f)] = f

    # Nothing to run -> nothing to change.
    if not written:
        return findings, {}

   # 2. Run pytest once over the temp directory.
    #    --continue-on-collection-errors keeps a single bad import (one finding's
    #    target missing/syntactically broken) from interrupting every other test.
    proc = subprocess.run(
        [
            VENV_PYTHON,
            "-m",
            "pytest",
            str(_TEMP_DIR),
            "--tb=line",
            "-q",
            "--continue-on-collection-errors",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    outcomes = _parse_outcomes(proc.stdout + "\n" + proc.stderr)

    # 3. Attribute each outcome to its finding by file prefix.
    #    A finding owns exactly one file (tests/review_temp/test_<sid>.py), so
    #    prefix-matching is unambiguous. ERROR (collection/import) takes
    #    precedence over FAILED.
    file_status: dict[str, str] = {}
    file_error_reason: dict[str, str] = {}
    for status, nodeid, reason in outcomes:
        for fkey in written:
            if nodeid == fkey or nodeid.startswith(fkey + "::") or nodeid.startswith(fkey):
                if status == "ERROR":
                    file_status[fkey] = "error"
                    file_error_reason[fkey] = reason or "test error"
                elif fkey not in file_status or file_status[fkey] != "error":
                    file_status[fkey] = "failed"
                break

    # 4. Map each written finding to a verdict.
    results: dict[str, str] = {}
    out: list[Finding] = []
    for f in findings:
        fkey = _file_key(f)
        if fkey not in written:
            out.append(f)  # no test generated; untouched (static path handles it)
            continue
        status = file_status.get(fkey)
        if status == "error":
            results[f.id] = "error"
            out.append(
                replace(
                    f,
                    verdict="inconclusive",
                    verify_note=f"verification failed: {file_error_reason.get(fkey, 'test error')}",
                )
            )
        elif status == "failed":
            results[f.id] = "failed"
            conf = f.confidence
            if _rank(conf) < _rank("medium"):
                conf = "medium"
            out.append(
                replace(f, verdict="confirmed", confidence=conf, verify_note="dynamic-confirmed")
            )
        else:
            results[f.id] = "passed"
            out.append(
                replace(f, verdict="refuted", confidence="low", verify_note="dynamic-refuted")
            )
    return out, results


def run(findings: list[Finding]) -> list[Finding]:
    """Generate, run, and map outcomes; return the updated findings list."""
    return run_with_results(findings)[0]
