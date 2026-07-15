"""Markdown report renderer for the review toolchain.

Turns a list of (diffed, verified) findings plus the loaded baseline into a
human-readable review document: header, summary table, per-severity sections
tagged [NEW]/[RECURRING]/[REGRESSION]/[REFUTED], and three appendix tables
(refuted, fixed, toolchain errors).
"""

from __future__ import annotations

from scripts.review.common import Category, Finding

_SEVERITIES = ("P0", "P1", "P2")
_TAG = {
    "new": "[NEW]",
    "recurring": "[RECURRING]",
    "regression": "[REGRESSION]",
}
_DEFAULT_SCAN_RANGE = "xbot/ · bridge/ · frontend/"


def _tag_for(f: Finding) -> str:
    if f.verdict == "refuted":
        return "[REFUTED]"
    return _TAG.get(f.diff_status, "[NEW]")


def _count(findings: list[Finding], sev: str, *statuses: str) -> int:
    return sum(1 for f in findings if f.severity == sev and f.diff_status in statuses)


def _newly_fixed_keys(findings: list[Finding], baseline: dict) -> set:
    current = {f.sig_key for f in findings}
    base = {
        e.get("sig_key")
        for e in (baseline.get("findings") or [])
        if e.get("sig_key")
    }
    return base - current


def _toolchain_errors(findings: list[Finding]) -> list[Finding]:
    tc = Category.TOOLCHAIN_ERROR.value
    return [f for f in findings if f.category == tc]


def render_report(
    findings: list[Finding], baseline: dict, version: str
) -> str:
    """Render the full review markdown for *findings* against *baseline*."""
    out: list[str] = []

    # --- Header -----------------------------------------------------------
    bdate = baseline.get("baseline_date") or baseline.get("date") or "—"
    scan_range = baseline.get("scan_range") or _DEFAULT_SCAN_RANGE
    tc = _toolchain_errors(findings)
    tc_status = "全部正常" if not tc else "；".join(f.title for f in tc)
    out.append("# xbot 代码审查报告")
    out.append("")
    out.append(f"- 版本: {version}")
    out.append(f"- 基线日期: {bdate}")
    out.append(f"- 扫描范围: {scan_range}")
    out.append(f"- 工具链状态: {tc_status}")
    out.append("")

    # --- Summary table ----------------------------------------------------
    fixed_keys = _newly_fixed_keys(findings, baseline)
    fixed_by_sev: dict[str, int] = {}
    for e in baseline.get("findings") or []:
        if e.get("sig_key") in fixed_keys:
            sev = e.get("severity", "P2")
            fixed_by_sev[sev] = fixed_by_sev.get(sev, 0) + 1

    out.append("## 摘要")
    out.append("")
    out.append("| 严重度 | new | recurring | fixed | unfixed |")
    out.append("|---|---|---|---|---|")
    sev_rows = list(_SEVERITIES) + [s for s in fixed_by_sev if s not in _SEVERITIES]
    for sev in sev_rows:
        new = _count(findings, sev, "new")
        rec = _count(findings, sev, "recurring", "regression")
        fixed = fixed_by_sev.get(sev, 0)
        unfixed = sum(
            1 for f in findings if f.severity == sev and f.verdict != "refuted"
        )
        out.append(f"| {sev} | {new} | {rec} | {fixed} | {unfixed} |")
    out.append("")

    # --- Per-severity sections --------------------------------------------
    for sev in _SEVERITIES:
        sev_findings = [f for f in findings if f.severity == sev]
        if not sev_findings:
            continue
        out.append(f"## {sev}")
        out.append("")
        for f in sorted(sev_findings, key=lambda x: (x.file, x.line)):
            out.append(f"### {_tag_for(f)} {f.title}")
            out.append(f"- 位置: `{f.file}:{f.line}`")
            out.append(f"- 详情: {f.detail}")
            out.append(f"- 建议: {f.suggestion}")
            out.append(
                f"- 置信度: {f.confidence} · 扫描器: {f.scanner} · 判定: {f.verdict}"
            )
            out.append("")

    # --- Refuted appendix -------------------------------------------------
    refuted = [f for f in findings if f.verdict == "refuted"]
    out.append("## 已排除误报")
    out.append("")
    if refuted:
        out.append("| 位置 | 标题 | 类别 |")
        out.append("|---|---|---|")
        for f in sorted(refuted, key=lambda x: (x.file, x.line)):
            out.append(f"| `{f.file}:{f.line}` | {f.title} | {f.category} |")
    else:
        out.append("无")
    out.append("")

    # --- Fixed appendix ---------------------------------------------------
    out.append("## 已修复")
    out.append("")
    fixed_history = baseline.get("fixed_history") or []
    if fixed_history:
        out.append("| sig_key | fixed_at |")
        out.append("|---|---|")
        for e in fixed_history:
            out.append(f"| {e.get('sig_key', '')} | {e.get('fixed_at', '—')} |")
    else:
        out.append("无")
    out.append("")

    # --- Toolchain errors appendix ---------------------------------------
    out.append("## 工具链错误")
    out.append("")
    if tc:
        out.append("| 组件 | 详情 |")
        out.append("|---|---|")
        for f in tc:
            out.append(f"| {f.file} | {f.detail} |")
    else:
        out.append("无")
    out.append("")

    return "\n".join(out)
