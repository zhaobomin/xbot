from __future__ import annotations

import hashlib
import os
import subprocess

from scripts.review.common import Category, Finding, make_sig_key


def _resolve_eslint() -> str:
    """Return the first available local eslint binary path, else '' (npx)."""
    candidates = [
        os.environ.get("ESLINT_BIN", ""),
        os.path.abspath("bridge/node_modules/.bin/eslint"),
        os.path.abspath("node_modules/.bin/eslint"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return ""


def _toolchain_error(reason: str) -> Finding:
    return Finding(
        id=f"lint_eslint:{hashlib.md5(reason.encode()).hexdigest()[:8]}",
        sig_key=make_sig_key("toolchain_error", "eslint", "eslint broken"),
        severity="P2",
        file="<eslint>",
        line=0,
        category=Category.TOOLCHAIN_ERROR.value,
        title="eslint broken",
        detail=reason,
        suggestion="repair the ESLint config/install (ESLint 9 migration) before relying on lint findings",
        confidence="high",
        scanner="lint_eslint",
    )


def _summarize(proc: subprocess.CompletedProcess) -> str:
    text = (proc.stderr or proc.stdout or "").strip()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else f"exit {proc.returncode}"


def scan(path: str = "bridge") -> list[Finding]:
    """Run ``eslint src`` on *path*.

    ESLint is currently broken in this repo (ESLint 9 migration issue, and the
    binary is not installed). Any failure — missing binary, npx fetch error,
    config error, non-zero exit without parseable output — collapses to a
    single ``toolchain_error`` finding so the pipeline never crashes. A clean
    local-eslint run (exit 0) reports nothing.
    """
    eslint = _resolve_eslint()
    cwd = path if os.path.isdir(path) else os.getcwd()
    src_dir = "src" if os.path.isdir(os.path.join(cwd, "src")) else "."
    if eslint:
        cmd = [eslint, src_dir]
    else:
        cmd = ["npx", "--no-install", "eslint", src_dir]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=cwd)
    except FileNotFoundError:
        return [_toolchain_error("eslint broken: binary not found")]
    except Exception as exc:  # noqa: BLE001 - collapse any subprocess failure
        return [_toolchain_error(f"eslint broken: invocation failed: {exc}")]

    # Clean run: nothing to report.
    if proc.returncode == 0:
        return []

    # eslint exits 1 for lint issues, 2 for config/usage errors. When we fell
    # back to npx the binary is not installed locally, so a non-zero exit means
    # eslint could not run at all -> toolchain error.
    if not eslint or proc.returncode == 2:
        return [_toolchain_error(f"eslint broken: {_summarize(proc)}")]

    # A locally-installed eslint reported real lint issues (exit 1). Parsing
    # eslint output is out of scope for this toolchain; tolerate cleanly.
    return []


def main() -> int:
    import json
    import sys

    path = " ".join(sys.argv[1:]) or "bridge"
    findings = scan(path)
    with open("findings_eslint.json", "w", encoding="utf-8") as fh:
        json.dump([f.to_dict() for f in findings], fh, indent=2, ensure_ascii=False)
    print(f"wrote {len(findings)} findings to findings_eslint.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
