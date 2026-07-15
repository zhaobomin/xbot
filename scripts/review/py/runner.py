from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Callable

from scripts.review.common import Category, Finding, dedup, make_sig_key
from scripts.review.py import lint_ruff
from scripts.review.py.scan_async_blocks import scan as scan_async_blocks
from scripts.review.py.scan_codegraph_reachability import scan as scan_codegraph_reachability
from scripts.review.py.scan_dead_code import scan as scan_dead_code
from scripts.review.py.scan_fail_open import scan as scan_fail_open
from scripts.review.py.scan_mutable_defaults import scan as scan_mutable_defaults
from scripts.review.py.scan_naming_remnants import scan as scan_naming_remnants
from scripts.review.py.scan_private_api import scan as scan_private_api
from scripts.review.py.scan_retry_jitter import scan as scan_retry_jitter
from scripts.review.py.scan_ssrf import scan as scan_ssrf
from scripts.review.py.scan_task_lifecycle import scan as scan_task_lifecycle

# AST scanners that take a single source file path. Codegraph is handled
# separately since it operates on a graph DB rather than source files.
_FILE_SCANNERS: list[tuple[str, Callable[[str], list[Finding]]]] = [
    ("scan_async_blocks", scan_async_blocks),
    ("scan_private_api", scan_private_api),
    ("scan_fail_open", scan_fail_open),
    ("scan_dead_code", scan_dead_code),
    ("scan_task_lifecycle", scan_task_lifecycle),
    ("scan_mutable_defaults", scan_mutable_defaults),
    ("scan_naming_remnants", scan_naming_remnants),
    ("scan_ssrf", scan_ssrf),
    ("scan_retry_jitter", scan_retry_jitter),
]


def _collect_py_files(path: str) -> list[str]:
    """Return absolute paths to every ``*.py`` file under *path*.

    Absolute paths are returned on purpose: ruff reports absolute filenames, so
    normalizing scanner inputs the same way lets ``dedup`` merge findings that
    different scanners emit for the same (file, line, category).
    """
    if os.path.isdir(path):
        files = []
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
    """Run every Python scanner plus ruff on *path*, then cross-track dedup.

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

    try:
        findings.extend(scan_codegraph_reachability())
    except Exception as exc:  # noqa: BLE001 - surface, don't crash
        findings.append(_scanner_error("scan_codegraph_reachability", exc))

    try:
        findings.extend(lint_ruff.scan(path))
    except Exception as exc:  # noqa: BLE001 - surface, don't crash
        findings.append(_scanner_error("lint_ruff", exc))

    return dedup(findings)


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "."
    findings = run(path)
    out_path = "findings_py.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump([f.to_dict() for f in findings], fh, indent=2, ensure_ascii=False)
    print(f"wrote {len(findings)} findings to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
