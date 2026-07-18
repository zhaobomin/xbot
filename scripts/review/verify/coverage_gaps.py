"""Coverage gap analysis; skips gracefully when pytest-cov is unavailable."""

from __future__ import annotations

import json
import subprocess
from importlib import util

VENV_PYTHON = ".venv/bin/python"
COVERAGE_ARGS = [
    VENV_PYTHON,
    "-m",
    "pytest",
    "--cov=xbot",
    "--cov-report=json",
    "--ignore=tests/review",
    "-q",
]


def _pytest_cov_installed() -> bool:
    """Detect pytest-cov without importing the plugin into our process."""
    return util.find_spec("pytest_cov") is not None


def _map_files_to_coverage(findings: list, cov: dict) -> dict:
    files = cov.get("files", {})
    totals = cov.get("totals", {})
    out: dict = {}
    for f in findings:
        # Findings are dicts (from Finding.to_dict) or Finding objects.
        path = f.get("file", "") if isinstance(f, dict) else getattr(f, "file", "")
        if not path:
            continue
        fdata = files.get(path)
        if fdata is None:
            out[path] = {"coverage_pct": None, "reason": "file not in coverage report"}
        else:
            out[path] = {"coverage_pct": fdata.get("percent_covered")}
    out["_totals"] = {
        "coverage_pct": totals.get("percent_covered"),
    }
    return out


def check_coverage(findings: list) -> dict:
    """If pytest-cov is installed, map each finding's file to coverage %.

    If pytest-cov is NOT installed, returns a skipped marker so callers can
    degrade gracefully instead of crashing.
    """
    if not _pytest_cov_installed():
        return {"skipped": True, "reason": "pytest-cov not installed"}

    subprocess.run(
        COVERAGE_ARGS,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        with open("coverage.json", encoding="utf-8") as fh:
            cov = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"skipped": True, "reason": "coverage.json not readable"}
    return _map_files_to_coverage(findings, cov)


if __name__ == "__main__":  # pragma: no cover - manual sanity entrypoint
    print(json.dumps(check_coverage([]), indent=2))
