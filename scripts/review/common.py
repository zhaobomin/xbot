from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from enum import Enum


class Category(str, Enum):
    ASYNC_BLOCK = "async_block"
    ASYNC_RACE = "async_race"
    DEADLOCK = "deadlock"
    PRIVATE_API = "private_api"
    FAIL_OPEN = "fail_open"
    DEAD_CODE = "dead_code"
    TASK_LIFECYCLE = "task_lifecycle"
    SSRF = "ssrf"
    RETRY_JITTER = "retry_jitter"
    MUTABLE_DEFAULTS = "mutable_defaults"
    NAMING_REMNANTS = "naming_remnants"
    AUTH_BYPASS = "auth_bypass"
    INJECTION = "injection"
    SECRETS = "secrets"
    CONSOLE_LOG = "console_log"
    RECONNECT_RACE = "reconnect_race"
    ANY_TYPE = "any_type"
    UNHANDLED_PROMISE = "unhandled_promise"
    UNUSED_EXPORTS = "unused_exports"
    FRONTEND_A11Y = "frontend_a11y"
    CODEGRAPH_REACHABILITY = "codegraph_reachability"
    TOOLCHAIN_ERROR = "toolchain_error"


@dataclass
class Finding:
    id: str
    sig_key: str
    severity: str
    file: str
    line: int
    category: str
    title: str
    detail: str
    suggestion: str
    confidence: str
    scanner: str
    verdict: str = "inconclusive"
    verify_note: str = ""
    diff_status: str = ""
    def to_dict(self):
        return asdict(self)
    @staticmethod
    def from_dict(d):
        return Finding(**d)


def slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^\w\s]", "", s)
    return re.sub(r"\s+", "_", s)


def make_sig_key(category: str, symbol: str, title: str) -> str:
    return f"{category}:{symbol}:{slugify(title)}"


_CONF_RANK = {"high": 3, "medium": 2, "low": 1}
_SEV_RANK = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}


def _finding_rank(f: Finding):
    # Numeric rank so "high" > "low" and "P0" > "P2"; raw string compare inverts both.
    return (_CONF_RANK.get(f.confidence, 0), _SEV_RANK.get(f.severity, 0), f.scanner)


def dedup(findings: list[Finding]) -> list[Finding]:
    by_key = {}
    for f in findings:
        k = (f.file, f.line, f.category)
        if k not in by_key:
            by_key[k] = f
        else:
            ex = by_key[k]
            if _finding_rank(f) > _finding_rank(ex):
                by_key[k] = f
    return list(by_key.values())


def validate_agent_finding(raw: dict, module_name: str) -> Finding | None:
    try:
        cat = raw["category"]
        if cat not in {c.value for c in Category}:
            return None
        return Finding(
            id=f"agent:{module_name}:{hashlib.md5(str(raw).encode()).hexdigest()[:8]}",
            sig_key=make_sig_key(cat, raw.get("file", ""), raw.get("title", "")),
            severity=raw.get("severity", "P2"),
            file=raw["file"],
            line=raw["line"],
            category=cat,
            title=raw["title"],
            detail=raw.get("detail", ""),
            suggestion=raw.get("suggestion", ""),
            confidence=raw.get("confidence", "low"),
            scanner=f"agent:{module_name}",
            verdict="inconclusive",
        )
    except KeyError:
        return None
