from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from collections import deque

from scripts.review.common import Category, Finding, make_sig_key

# qualified_name substrings that mark a node as a genuine network sink.
# Bare method names (get, post, delete, fetch) are far too generic and
# also match business methods like ConversationStore::delete, ToolRegistry::get.
# Only match nodes whose qualified_name contains a known HTTP-client module.
_NET_SINK_QUALIFIED_RE = re.compile(
    r"\b(?:httpx|requests|urllib|urlopen|aiohttp|http\.client)\b",
    re.IGNORECASE,
)

_STALE_SECS = 14 * 24 * 3600  # two weeks


def _db_is_stale(db_path: str) -> bool:
    """True when *db_path* is older than two weeks relative to now."""
    try:
        mtime = os.path.getmtime(db_path)
    except OSError:
        return True
    return (time.time() - mtime) > _STALE_SECS


def _toolchain_error(db_path: str) -> Finding:
    """Single finding emitted when the codegraph DB is missing or stale."""
    return Finding(
        id=f"codegraph_reachability:{hashlib.md5(db_path.encode()).hexdigest()[:8]}",
        sig_key=make_sig_key("toolchain_error", ".codegraph", "codegraph.db missing or stale"),
        severity="P2",
        file=db_path,
        line=0,
        category=Category.TOOLCHAIN_ERROR.value,
        title="codegraph.db missing or stale",
        detail="codegraph.db missing or stale",
        suggestion="re-index the repository so .codegraph/codegraph.db is fresh",
        confidence="low",
        scanner="scan_codegraph_reachability",
    )


def _net_sink_node_ids(cur: sqlite3.Cursor) -> set[str]:
    """Return ids of nodes whose qualified_name marks them as a network sink.

    Only matches nodes whose ``qualified_name`` contains a known HTTP-client
    module (httpx, requests, urllib, aiohttp, http.client). Bare method names
    like ``get``/``post``/``delete``/``fetch`` are NOT matched because they
    also correspond to ordinary business methods (ConversationStore::delete,
    ToolRegistry::get, etc.) that have nothing to do with networking.
    """
    ids: set[str] = set()
    for row in cur.execute("SELECT id, qualified_name FROM nodes"):
        if _NET_SINK_QUALIFIED_RE.search(row[1] or ""):
            ids.add(row[0])
    return ids


def _reverse_reach(cur: sqlite3.Cursor, sinks: set[str]) -> dict[str, str]:
    """Map reachable caller node id -> first sink id it can reach via ``calls``.

    BFS over reversed ``calls`` edges starting from *sinks*. Only ``calls`` edges
    are traversed (call-reachability, not taint propagation).
    """
    rev: dict[str, list[str]] = {}
    for src, dst in cur.execute("SELECT source, target FROM edges WHERE kind='calls'"):
        rev.setdefault(dst, []).append(src)

    reachable: dict[str, str] = {s: s for s in sinks}
    dq = deque(sinks)
    while dq:
        cur_id = dq.popleft()
        for caller in rev.get(cur_id, []):
            if caller not in reachable:
                reachable[caller] = reachable[cur_id]
                dq.append(caller)
    return reachable


def scan(path: str = ".codegraph/codegraph.db", db_path: str | None = None) -> list[Finding]:
    db = db_path or path
    if not os.path.exists(db) or _db_is_stale(db):
        return [_toolchain_error(db)]

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        sinks = _net_sink_node_ids(cur)
        if not sinks:
            return []
        reachable = _reverse_reach(cur, sinks)
        caller_ids = [i for i in reachable if i not in sinks]
        if not caller_ids:
            return []
        meta: dict[str, tuple] = {}
        for i in range(0, len(caller_ids), 500):
            chunk = caller_ids[i : i + 500]
            placeholders = ",".join("?" * len(chunk))
            for row in cur.execute(
                f"SELECT id, name, qualified_name, file_path, start_line FROM nodes WHERE id IN ({placeholders})",
                tuple(chunk),
            ):
                meta[row[0]] = row
        findings: list[Finding] = []
        title = "function reaches a network sink via call graph"
        for nid, row in meta.items():
            _id, name, qname, file_path, start_line = row
            sink = reachable[nid]
            detail = f"func: {qname}\nreverse call-reachability to network sink {sink}"
            fid = hashlib.md5(f"{file_path}:{start_line}:{qname}".encode()).hexdigest()[:8]
            findings.append(
                Finding(
                    id=f"codegraph_reachability:{fid}",
                    sig_key=make_sig_key("codegraph_reachability", qname, title),
                    severity="P2",
                    file=file_path,
                    line=start_line,
                    category=Category.CODEGRAPH_REACHABILITY.value,
                    title=title,
                    detail=detail,
                    suggestion="review whether reaching this network sink is intended",
                    confidence="low",
                    scanner="scan_codegraph_reachability",
                )
            )
        return findings
    finally:
        con.close()
