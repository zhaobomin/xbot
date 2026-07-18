from __future__ import annotations

import ast
import hashlib
import re

from scripts.review.common import Category, Finding, make_sig_key

# Assignment targets whose name signals a secret. Matched case-insensitively.
_SECRET_NAME_RE = re.compile(
    r"(?:^|_)(api_?key|apikey|secret|password|passwd|pwd|token|access_?token|"
    r"private_?key|client_?secret|auth_?token|refresh_?token)(?:$|_)",
    re.IGNORECASE,
)
# Known live-secret prefixes; a literal starting with one of these is a hit
# even when the variable name does not look secret-ish.
_SECRET_PREFIXES = ("sk-", "sk_", "ghp_", "gho_", "AKIA", "xoxb-", "xoxp-")


def _is_env_read(value: ast.AST) -> bool:
    """True when *value* pulls the secret from the environment rather than a literal."""
    # os.environ["KEY"] / os.environ.get("KEY")
    if isinstance(value, ast.Subscript) and isinstance(value.value, ast.Attribute):
        if isinstance(value.value.value, ast.Name) and value.value.value.id == "os":
            return value.value.attr in {"environ", "getenv"}
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute):
        if isinstance(value.func.value, ast.Name) and value.func.value.id == "os":
            return value.func.attr == "getenv"
    return False


def _looks_secret(name: str, value: ast.AST) -> bool:
    """True when the assignment ``name = value`` looks like a hardcoded secret."""
    if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
        return False
    raw = value.value
    if not raw or raw.startswith("os."):
        return False
    if raw in {"", "None", "null", "changeme", "placeholder", "your-key-here"}:
        return False
    if any(raw.startswith(p) for p in _SECRET_PREFIXES):
        return True
    if _SECRET_NAME_RE.search(name):
        # Avoid trivial/empty values flagged solely by the name.
        return len(raw) >= 6
    return False


def scan(path: str) -> list[Finding]:
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    findings: list[Finding] = []
    title = "hardcoded secret literal in source"
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if _is_env_read(node.value):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if not _looks_secret(target.id, node.value):
                continue
            detail = f"hardcoded secret assigned to `{target.id}`"
            fid = hashlib.md5(f"{path}:{node.lineno}:{target.id}".encode()).hexdigest()[:8]
            findings.append(
                Finding(
                    id=f"secrets:{fid}",
                    sig_key=make_sig_key("secrets", target.id, title),
                    severity="P0",
                    file=path,
                    line=node.lineno,
                    category=Category.SECRETS.value,
                    title=title,
                    detail=detail,
                    suggestion="load the value from an env var or secret manager",
                    confidence="high",
                    scanner="scan_secrets",
                )
            )
    return findings
