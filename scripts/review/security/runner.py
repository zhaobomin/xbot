from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Callable

from scripts.review.common import Category, Finding, dedup, make_sig_key
from scripts.review.security.scan_async_race import scan as scan_async_race
from scripts.review.security.scan_auth_bypass import scan as scan_auth_bypass
from scripts.review.security.scan_deadlock import scan as scan_deadlock
from scripts.review.security.scan_event_loop_block import scan as scan_event_loop_block
from scripts.review.security.scan_injection import scan as scan_injection
from scripts.review.security.scan_secrets import scan as scan_secrets
from scripts.review.security.scan_ssrf import scan as scan_ssrf

# AST/regex scanners that each take a single source file path.
_FILE_SCANNERS: list[tuple[str, Callable[[str], list[Finding]]]] = [
    ("scan_auth_bypass", scan_auth_bypass),
    ("scan_ssrf", scan_ssrf),
    ("scan_injection", scan_injection),
    ("scan_secrets", scan_secrets),
    ("scan_async_race", scan_async_race),
    ("scan_deadlock", scan_deadlock),
    ("scan_event_loop_block", scan_event_loop_block),
]


def _collect_py_files(path: str) -> list[str]:
    """Return absolute paths to every ``.py`` file under *path*.

    Absolute paths are returned on purpose so that ``dedup`` can merge findings
    from different scanners that emit for the same (file, line, category).
    """
    if os.path.isdir(path):
        files: list[str] = []
        for root, _dirs, names in os.walk(path):
            for name in names:
                if name.endswith(".py"):
                    files.append(os.path.abspath(os.path.join(root, name)))
        return sorted(files)
    if os.path.isfile(path):
        return [os.path.abspath(path)]
    return []


def _scanner_error(scanner_name: str, exc: Exception) -> Finding:
    return Finding(
        id=f"runner:{hashlib.md5(f'{scanner_name}:{exc}'.encode()).hexdigest()[:8]}",
        sig_key=make_sig_key("toolchain_error", scanner_name, "scanner raised"),
        severity="P2",
        file=f"<{scanner_name}>",
        line=0,
        category=Category.TOOLCHAIN_ERROR.value,
        title="scanner raised",
        detail=f"{scanner_name} raised {type(exc).__name__}: {exc}",
        suggestion="inspect the scanner and the offending source file",
        confidence="high",
        scanner=scanner_name,
    )


def run(path: str) -> list[Finding]:
    """Run every security/concurrency scanner on *path*, then cross-track dedup.

    *path* may be a single file or a directory. One scanner raising on a single
    file never aborts the whole run: unparseable/missing files are skipped,
    and unexpected errors collapse to a ``toolchain_error`` finding.
    """
    files = _collect_py_files(path)
    findings: list[Finding] = []

    for scanner_name, scanner in _FILE_SCANNERS:
        for source_file in files:
            try:
                findings.extend(scanner(source_file))
            except (SyntaxError, OSError):
                continue
            except Exception as exc:  # noqa: BLE001 - surface, don't crash
                findings.append(_scanner_error(scanner_name, exc))

    return dedup(findings)


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "."
    findings = run(path)
    out_path = "findings_security.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump([f.to_dict() for f in findings], fh, indent=2, ensure_ascii=False)
    print(f"wrote {len(findings)} findings to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
