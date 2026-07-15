"""Preflight checks for the review toolchain.

Verifies that each dependency the pipeline relies on is actually usable before
scanning starts. Critical deps (ruff, pytest) block the run; non-critical deps
(tsc, eslint, pytest-cov, a fresh codegraph) degrade gracefully and surface as
``toolchain_error`` findings instead.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from datetime import datetime, timedelta

from scripts.review.common import Category, Finding, make_sig_key

_STALE_THRESHOLD = timedelta(weeks=2)

# Local binary candidates, mirroring build_tsc._resolve_tsc / lint_eslint.
_TSC_CANDIDATES = (
    os.environ.get("TSC_BIN", ""),
    "bridge/node_modules/.bin/tsc",
    "node_modules/.bin/tsc",
)
_ESLINT_CANDIDATES = (
    os.environ.get("ESLINT_BIN", ""),
    "bridge/node_modules/.bin/eslint",
    "node_modules/.bin/eslint",
)
_ESLINT_CONFIGS = (
    "eslint.config.js",
    "eslint.config.mjs",
    "eslint.config.cjs",
    ".eslintrc.js",
    ".eslintrc.json",
    ".eslintrc.cjs",
    ".eslintrc.yml",
    ".eslintrc",
)


def _local_binary(candidates) -> str:
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return ""


def _run_ok(cmd, *, cwd=None, timeout=30) -> bool:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=False, cwd=cwd, timeout=timeout
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    except Exception:  # noqa: BLE001 - never let preflight crash the pipeline
        return False
    return proc.returncode == 0


def _check_ruff() -> bool:
    # .venv/bin/ruff is the project-local install; fall back to PATH.
    for cand in (".venv/bin/ruff", "ruff"):
        if "/" in cand:
            if os.path.exists(cand) and _run_ok([cand, "--version"]):
                return True
        elif _run_ok([cand, "--version"]):
            return True
    return False


def _check_pytest() -> bool:
    return importlib.util.find_spec("pytest") is not None


def _check_tsc() -> bool:
    if _local_binary(_TSC_CANDIDATES):
        return True
    return _run_ok(["npx", "--no-install", "tsc", "--version"], cwd="bridge")


def _check_eslint() -> bool:
    # "Broken" = no usable local binary OR no config to lint against. A bare
    # `npx eslint --version` is avoided: npx's own cache can mask the breakage
    # (it fetches eslint even though the repo has no ESLint 9 config).
    if not _local_binary(_ESLINT_CANDIDATES):
        return False
    for d in ("bridge", "."):
        if any(os.path.exists(os.path.join(d, c)) for c in _ESLINT_CONFIGS):
            return True
    return False


def _check_pytest_cov() -> bool:
    return importlib.util.find_spec("pytest_cov") is not None


def _check_codegraph_stale() -> bool:
    path = ".codegraph/codegraph.db"
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return True  # absent == stale
    return datetime.now() - mtime > _STALE_THRESHOLD


def preflight() -> dict:
    """Return a dict of dependency freshness/availability booleans.

    True means "available/fresh", False means "missing/stale". ``ruff`` and
    ``pytest`` are critical; the rest degrade to toolchain_error findings.
    """
    return {
        "ruff": _check_ruff(),
        "pytest": _check_pytest(),
        "tsc": _check_tsc(),
        "eslint": _check_eslint(),
        "pytest_cov": _check_pytest_cov(),
        "codegraph_stale": _check_codegraph_stale(),
    }


CRITICAL = ("ruff", "pytest")


def critical_ok(status: dict) -> bool:
    return all(status.get(k) for k in CRITICAL)


def _toolchain(name: str, detail: str, severity: str = "P2") -> Finding:
    return Finding(
        id=f"preflight:{name}",
        sig_key=make_sig_key("toolchain_error", name, "preflight"),
        severity=severity,
        file=f"<{name}>",
        line=0,
        category=Category.TOOLCHAIN_ERROR.value,
        title=f"{name} unavailable",
        detail=detail,
        suggestion="install/repair the dependency or run with --dry-run",
        confidence="high",
        scanner="preflight",
        verdict="confirmed",
        diff_status="new",
    )


def preflight_findings(status: dict) -> list[Finding]:
    """Emit toolchain_error findings for missing non-critical deps.

    Critical deps (ruff/pytest) are the caller's responsibility to block on;
    they never appear here.
    """
    findings: list[Finding] = []
    if not status.get("tsc"):
        findings.append(_toolchain("tsc", "tsc not available; TS build/type checks skipped"))
    if not status.get("eslint"):
        findings.append(
            _toolchain("eslint", "eslint broken (no local binary or no ESLint 9 config)")
        )
    if not status.get("pytest_cov"):
        findings.append(
            _toolchain(
                "pytest_cov", "pytest-cov not installed; coverage gap analysis skipped"
            )
        )
    if status.get("codegraph_stale"):
        findings.append(
            _toolchain(
                "codegraph",
                "codegraph.db stale (>2wk); reachability findings may be incomplete",
                severity="P1",
            )
        )
    return findings
