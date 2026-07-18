"""Top-level orchestrator for the xbot review toolchain.

Ties the stages together end to end:

  1. preflight()  -> dependency/freshness status; emit toolchain_error findings
  2. 3-track scan -> py(xbot/) + ts(bridge/, frontend/) + security(xbot/)
  3. dedup        -> cross-track merge by (file, line, category)
  4. verify       -> baseline + coverage + regression -> verdicts
  5. baseline_diff-> new/recurring/regression + fixed_history
  6. render       -> markdown report
  7. write        -> docs/reviews/auto/<date>_review.md + findings_final.json

Critical deps (ruff, pytest) missing blocks the scan. ``--dry-run`` runs
preflight only.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date

from scripts.review import baseline_diff, preflight
from scripts.review.common import Finding, dedup
from scripts.review.py import runner as py_runner
from scripts.review.render import render_report
from scripts.review.security import runner as security_runner
from scripts.review.ts import runner as ts_runner
from scripts.review.verify import runner as verify

BASELINE_PATH = "findings_baseline.json"
REPORT_DIR = "docs/reviews/auto"
FINAL_PATH = "findings_final.json"

# Scan roots. The TS track lints both bridge/ and frontend/ source trees.
PY_ROOT = "xbot/"
TS_ROOTS = ("bridge/", "frontend/")
SECURITY_ROOT = "xbot/"


def _run_tracks() -> list[Finding]:
    """Run all three scanner tracks and return their combined findings.

    Isolated so tests can monkeypatch it to avoid the real (slow) scanners.
    """
    findings: list[Finding] = []
    findings.extend(py_runner.run(PY_ROOT))
    findings.extend(security_runner.run(SECURITY_ROOT))
    for root in TS_ROOTS:
        if os.path.isdir(root):
            findings.extend(ts_runner.run(root))
    return findings


def _load_baseline() -> dict:
    if os.path.exists(BASELINE_PATH):
        try:
            with open(BASELINE_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"findings": [], "fixed_history": []}


def _write_outputs(report: str, final: dict) -> str:
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_DIR, f"{date.today().isoformat()}_review.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(report)
    with open(FINAL_PATH, "w", encoding="utf-8") as fh:
        json.dump(final, fh, indent=2, ensure_ascii=False)
    return report_path


def _detect_version() -> str:
    try:
        from xbot import __version__  # noqa: PLC0415 - lazy import
    except Exception:  # noqa: BLE001 - best-effort, never block
        return "v?"
    return f"v{__version__}"


def orchestrate(version: str | None = None, *, dry_run: bool = False) -> dict:
    """Run the full pipeline (or preflight-only when *dry_run*).

    Returns a dict with: dry_run, preflight, findings, report, report_path,
    fixed_history, baseline_failures.
    """
    if version is None:
        version = _detect_version()

    status = preflight.preflight()
    toolchain_findings = preflight.preflight_findings(status)

    # Dry run: preflight only, no scanning/verify/write.
    if dry_run:
        return {
            "dry_run": True,
            "preflight": status,
            "findings": toolchain_findings,
            "report": None,
            "report_path": None,
            "fixed_history": [],
            "baseline_failures": None,
        }

    # Critical deps missing -> block the scan, surface the gaps.
    if not preflight.critical_ok(status):
        return {
            "dry_run": False,
            "preflight": status,
            "findings": toolchain_findings,
            "report": None,
            "report_path": None,
            "fixed_history": [],
            "baseline_failures": None,
            "blocked": True,
        }

    # 2-3. Tracks + cross-track dedup (preflight toolchain errors merge in).
    all_findings = _run_tracks()
    all_findings.extend(toolchain_findings)
    merged = dedup(all_findings)

    # 4. Dynamic verification (baseline + coverage + regression).
    verified = verify.run(merged)

    # 5. Baseline diff.
    baseline = _load_baseline()
    diffed, new_fixed_history = baseline_diff.apply_diff(verified, baseline)

    # 6. Render.
    report = render_report(diffed, baseline, version)

    # baseline_failures: read the cached result set by verify.run (avoids a
    # second full-suite pytest run).
    baseline_result = getattr(verify.run, "_baseline_result", None)
    baseline_failures = None
    if baseline_result is not None:
        baseline_failures = {
            "total": baseline_result.total,
            "passed": baseline_result.passed,
            "failed": baseline_result.failed,
            "skipped": baseline_result.skipped,
        }

    # 7. Write outputs.
    final = {
        "version": version,
        "preflight": status,
        "findings": [f.to_dict() for f in diffed],
        "fixed_history": new_fixed_history,
        "baseline_failures": baseline_failures,
    }
    report_path = _write_outputs(report, final)

    return {
        "dry_run": False,
        "preflight": status,
        "findings": diffed,
        "report": report,
        "report_path": report_path,
        "fixed_history": new_fixed_history,
        "baseline_failures": baseline_failures,
    }


def main() -> int:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    version = None
    for a in args:
        if a.startswith("--version="):
            version = a.split("=", 1)[1]
    result = orchestrate(version=version, dry_run=dry_run)
    status = result["preflight"]
    print("preflight:", json.dumps(status, ensure_ascii=False))
    if result.get("blocked"):
        print("BLOCKED: critical deps missing (ruff/pytest)")
        return 1
    if result["dry_run"]:
        print("dry-run complete")
        return 0
    print(f"report: {result['report_path']}")
    print(
        f"findings: {len(result['findings'])} | "
        f"fixed_history: {len(result['fixed_history'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
