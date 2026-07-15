from scripts.review.common import (
    Category,
    Finding,
    dedup,
    make_sig_key,
    slugify,
    validate_agent_finding,
)


def test_finding_serializes_to_json():
    f = Finding(id="async_block:a3f2", sig_key="x", severity="P0",
                file="xbot/x.py", line=1, category="async_block",
                title="t", detail="func: foo", suggestion="s",
                confidence="high", scanner="test")
    d = f.to_dict()
    assert d["category"] == "async_block"
    assert Finding.from_dict(d) == f

def test_slugify_normalizes_title():
    assert slugify("Session ID Inconsistency!") == "session_id_inconsistency"
    assert slugify("  Multi  Word  ") == "multi_word"

def test_sig_key_uses_slugified_title():
    k = make_sig_key("async_block", "xbot.x.foo", "Session ID Bug")
    assert k == "async_block:xbot.x.foo:session_id_bug"

def test_category_enum_covers_all_tracks():
    vals = {c.value for c in Category}
    assert "async_block" in vals and "toolchain_error" in vals
    assert "event_loop_block" not in vals

def test_dedup_keeps_highest_confidence():
    a = Finding(id="s1:1", sig_key="k", severity="P0", file="x", line=1,
                category="ssrf", title="t", detail="d", suggestion="s",
                confidence="low", scanner="py")
    b = Finding(id="s2:1", sig_key="k", severity="P0", file="x", line=1,
                category="ssrf", title="t", detail="d2", suggestion="s",
                confidence="high", scanner="security")
    out = dedup([a, b])
    assert len(out) == 1 and out[0].scanner == "security"

def test_validate_agent_finding_drops_invalid_category():
    raw = {"category": "not_real", "file": "x", "line": 1, "title": "t", "detail": "d"}
    assert validate_agent_finding(raw, "runtime") is None

def test_validate_agent_finding_normalizes_valid():
    raw = {"category": "async_block", "file": "x.py", "line": 1,
            "severity": "P0", "confidence": "medium", "title": "t", "detail": "func: f"}
    f = validate_agent_finding(raw, "runtime")
    assert f and f.scanner == "agent:runtime" and f.verdict == "inconclusive"
