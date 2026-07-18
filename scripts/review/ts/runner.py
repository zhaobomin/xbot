from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Callable

from scripts.review.common import (
    IGNORED_DIRS,
    IGNORED_FILES,
    Category,
    Finding,
    dedup,
    make_sig_key,
)
from scripts.review.ts import build_tsc, lint_eslint
from scripts.review.ts.scan_any_type import scan as scan_any_type
from scripts.review.ts.scan_console_log import scan as scan_console_log
from scripts.review.ts.scan_frontend_a11y import scan as scan_frontend_a11y
from scripts.review.ts.scan_reconnect_race import scan as scan_reconnect_race
from scripts.review.ts.scan_unhandled_promise import scan as scan_unhandled_promise
from scripts.review.ts.scan_unused_exports import scan as scan_unused_exports

# Per-file regex scanners. Each takes a single .ts/.tsx source path.
_FILE_SCANNERS: list[tuple[str, Callable[[str], list[Finding]]]] = [
    ("scan_console_log", scan_console_log),
    ("scan_reconnect_race", scan_reconnect_race),
    ("scan_any_type", scan_any_type),
    ("scan_unhandled_promise", scan_unhandled_promise),
    ("scan_frontend_a11y", scan_frontend_a11y),
]

# Tree-scoped scanner: takes a directory and cross-references imports.
_TREE_SCANNERS: list[tuple[str, Callable[[str], list[Finding]]]] = [
    ("scan_unused_exports", scan_unused_exports),
]


def _collect_ts_files(path: str) -> list[str]:
    """Return absolute paths to every ``*.ts``/``*.tsx`` file under *path*."""
    if os.path.isdir(path):
        files: list[str] = []
        for root, _dirs, names in os.walk(path):
            # Skip vendored/build dirs: their .d.ts files legitimately use
            # `any` and console.log, and are not xbot's own source code.
            _dirs[:] = [d for d in _dirs if d not in IGNORED_DIRS]
            for name in names:
                if name in IGNORED_FILES:
                    continue
                if name.endswith((".ts", ".tsx")):
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
    """Run every TS scanner plus tsc/eslint on *path*, then cross-track dedup.

    *path* may be a single file or a directory. One scanner raising on a single
    file never aborts the whole run: unreadable files are skipped, and
    unexpected errors collapse to a ``toolchain_error`` finding.
    """
    files = _collect_ts_files(path)
    findings: list[Finding] = []

    for scanner_name, scanner in _FILE_SCANNERS:
        for source_file in files:
            try:
                findings.extend(scanner(source_file))
            except OSError:
                continue
            except Exception as exc:  # noqa: BLE001 - surface, don't crash
                findings.append(_scanner_error(scanner_name, exc))

    for scanner_name, scanner in _TREE_SCANNERS:
        try:
            findings.extend(scanner(path))
        except Exception as exc:  # noqa: BLE001 - surface, don't crash
            findings.append(_scanner_error(scanner_name, exc))

    try:
        findings.extend(build_tsc.scan(path))
    except Exception as exc:  # noqa: BLE001 - surface, don't crash
        findings.append(_scanner_error("build_tsc", exc))

    try:
        findings.extend(lint_eslint.scan(path))
    except Exception as exc:  # noqa: BLE001 - surface, don't crash
        findings.append(_scanner_error("lint_eslint", exc))

    return dedup(findings)


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "."
    findings = run(path)
    out_path = "findings_ts.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump([f.to_dict() for f in findings], fh, indent=2, ensure_ascii=False)
    print(f"wrote {len(findings)} findings to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
