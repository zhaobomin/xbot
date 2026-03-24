import pytest

from xbot.agent.response_parser import (
    ALLOW_RESPONSE_KEYWORDS,
    DENY_RESPONSE_KEYWORDS,
    derive_interaction_action,
    is_response_keyword,
    parse_permission_response,
)


def test_permission_allow_keywords_parse_as_allow():
    for kw in ALLOW_RESPONSE_KEYWORDS:
        decision, reason = parse_permission_response(kw)
        assert decision == "allow"
        assert reason == ""


def test_permission_deny_keywords_parse_as_deny_with_reason():
    for kw in DENY_RESPONSE_KEYWORDS:
        decision, reason = parse_permission_response(kw)
        assert decision == "deny"
        assert reason == "User denied"


def test_permission_non_keyword_returns_none():
    decision, reason = parse_permission_response("maybe")
    assert decision is None
    assert reason == ""


def test_is_response_keyword_supports_trim_and_casefold():
    assert is_response_keyword("  YES  ") is True
    assert is_response_keyword("\tNo\n") is True
    assert is_response_keyword("something else") is False


@pytest.mark.parametrize(
    "kind,content,expected",
    [
        ("confirmation", "yes", "confirm"),
        ("confirmation", "no", "cancel"),
        ("approval", "yes", "allow"),
        ("approval", "no", "deny"),
        ("approval", "anything", "reply"),
        ("text", "yes", "reply"),
    ],
)
def test_derive_interaction_action(kind, content, expected):
    assert derive_interaction_action(kind=kind, content=content) == expected
