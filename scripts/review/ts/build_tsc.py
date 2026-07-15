from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys

from scripts.review.common import Category, Finding, make_sig_key

# tsc error line: ``path(line,col): error TS2322: message``
_TSC_ERR_RE = re.compile(
    r"^(?P<file>.+?)\((?P<line>\d+),(?P<col>\d+)\):\s+error\s+(?P<code>TS\d+):\s*(?P<msg>.*)$"
)


def _resolve_tsc() -> str:
    """Return the first available local tsc binary path, else '' (npx)."""
    candidates = [
        os.environ.get("TSC_BIN", ""),
        os.path.abspath("bridge/node_modules/.bin/tsc"),
        os.path.abspath("node_modules/.bin/tsc"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return ""


def _toolchain_error(reason: str) -> Finding:
    return Finding(
        id=f"build_tsc:{hashlib.md5(reason.encode()).hexdigest()[:8]}",
        sig_key=make_sig_key("toolchain_error", "tsc", "tsc build failure"),
        severity="P1",
        file="<tsc>",
        line=0,
        category=Category.TOOLCHAIN_ERROR.value,
        title="tsc build failure",
        detail=reason,
        suggestion="fix the TypeScript compiler errors or install typescript",
        confidence="high",
        scanner="build_tsc",
    )


def scan(path: str = "bridge") -> list[Finding]:
    """Run ``tsc --noEmit`` on *path* (a directory or single .ts file).

    Compiler errors become ``toolchain_error`` findings (P1); a missing or
    failing tsc invocation collapses to a single ``toolchain_error`` finding so
    the rest of the pipeline keeps running.
    """
    tsc = _resolve_tsc()
    if tsc:
        cmd = [tsc, "--noEmit"]
    else:
        cmd = ["npx", "--no-install", "tsc", "--noEmit"]

    cwd = path if os.path.isdir(path) else os.getcwd()
    if os.path.isfile(path):
        cmd.append(path)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=cwd)
    except FileNotFoundError:
        return [_toolchain_error(f"tsc binary not found: {cmd[0]}")]
    except Exception as exc:  # noqa: BLE001 - collapse any subprocess failure
        return [_toolchain_error(f"tsc invocation failed: {exc}")]

    combined = (proc.stdout + proc.stderr).splitlines()
    findings: list[Finding] = []
    for raw in combined:
        m = _TSC_ERR_RE.match(raw)
        if not m:
            continue
        file = m.group("file")
        line = int(m.group("line"))
        code = m.group("code")
        msg = m.group("msg")
        fid = hashlib.md5(f"{file}:{line}:{code}".encode()).hexdigest()[:8]
        findings.append(
            Finding(
                id=f"build_tsc:{fid}",
                sig_key=make_sig_key("toolchain_error", os.path.basename(file), code),
                severity="P1",
                file=file,
                line=line,
                category=Category.TOOLCHAIN_ERROR.value,
                title=code,
                detail=msg,
                suggestion="fix the TypeScript compiler error",
                confidence="high",
                scanner="build_tsc",
            )
        )

    if findings:
        return findings

    # No parseable errors but tsc failed to run properly: surface it.
    if proc.returncode not in (0, 1):
        summary = (proc.stderr or proc.stdout).strip().splitlines()
        snippet = summary[-1] if summary else f"exit {proc.returncode}"
        return [_toolchain_error(f"tsc exited {proc.returncode}: {snippet[:200]}")]
    return []


def main() -> int:
    path = " ".join(sys.argv[1:]) or "bridge"
    findings = scan(path)
    with open("findings_tsc.json", "w", encoding="utf-8") as fh:
        json.dump([f.to_dict() for f in findings], fh, indent=2, ensure_ascii=False)
    print(f"wrote {len(findings)} findings to findings_tsc.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
