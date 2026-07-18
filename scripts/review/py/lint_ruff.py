from __future__ import annotations

import hashlib
import json
import subprocess

from scripts.review.common import Category, Finding, make_sig_key

_RUFF_BIN = ".venv/bin/ruff"


def _toolchain_error(reason: str) -> Finding:
    return Finding(
        id=f"lint_ruff:{hashlib.md5(reason.encode()).hexdigest()[:8]}",
        sig_key=make_sig_key("toolchain_error", "ruff", "ruff unavailable"),
        severity="P2",
        file="<ruff>",
        line=0,
        category=Category.TOOLCHAIN_ERROR.value,
        title="ruff unavailable",
        detail=reason,
        suggestion="install ruff into the project venv",
        confidence="high",
        scanner="lint_ruff",
    )


def _row(entry: dict) -> int:
    # ruff nests the line number under ``location.row``; tolerate a top-level
    # ``row`` for alternate output shapes.
    loc = entry.get("location") or {}
    return int(loc.get("row") or entry.get("row") or 1)


def scan(path: str) -> list[Finding]:
    """Run ``ruff check --output-format=json`` on *path* and map each issue to a Finding.

    Ruff exits 0 when clean (empty JSON list) and 1 when lint issues exist; both
    are valid. Any other exit, missing binary, or unparseable JSON collapses to
    a single ``toolchain_error`` finding so the rest of the pipeline keeps running.
    """
    cmd = [_RUFF_BIN, "check", "--output-format=json", path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return [_toolchain_error(f"ruff binary not found: {_RUFF_BIN}")]
    except Exception as exc:  # noqa: BLE001 - collapse any subprocess failure
        return [_toolchain_error(f"ruff invocation failed: {exc}")]

    if proc.returncode not in (0, 1):
        return [_toolchain_error(f"ruff exited {proc.returncode}: {proc.stderr[:200]}")]

    raw = proc.stdout.strip()
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        return [_toolchain_error("ruff output was not valid JSON")]

    findings: list[Finding] = []
    for entry in entries:
        code = entry.get("code", "RUFF")
        message = entry.get("message", "")
        filename = entry.get("filename", path)
        line = _row(entry)
        fix = entry.get("fix") or {}
        suggestion = fix.get("message") or message
        fid = hashlib.md5(f"{filename}:{line}:{code}".encode()).hexdigest()[:8]
        findings.append(
            Finding(
                id=f"lint_ruff:{fid}",
                sig_key=make_sig_key("dead_code", filename, str(line)),
                severity="P2",
                file=filename,
                line=line,
                category=Category.DEAD_CODE.value,
                title=code,
                detail=message,
                suggestion=suggestion,
                confidence="high",
                scanner="lint_ruff",
            )
        )
    return findings
