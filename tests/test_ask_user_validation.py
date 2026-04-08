"""Tests for shared AskUserQuestion validation utilities."""

from xbot.interaction.ask_user_validation import (
    match_option,
    normalize_validation_mode,
    split_answers,
)


def test_normalize_validation_mode_aliases():
    assert normalize_validation_mode("open") == "suggested"
    assert normalize_validation_mode("loose") == "suggested"


def test_normalize_validation_mode_passthrough_and_fallback():
    assert normalize_validation_mode("strict") == "strict"
    assert normalize_validation_mode("suggested") == "suggested"
    assert normalize_validation_mode("unknown") == "suggested"


def test_split_answers_uses_only_comma_like_separators():
    assert split_answers("A，B,C、D") == ["A", "B", "C", "D"]
    assert split_answers("A B") == ["A B"]


def test_match_option_prefers_unique_case_insensitive_prefix():
    options = ["北京市", "上海市"]
    assert match_option("北京", options) == "北京市"
    assert match_option("上海", options) == "上海市"


def test_match_option_returns_none_for_ambiguous_prefix():
    options = ["Alpha", "Alpine"]
    assert match_option("A", options) is None


def test_match_option_prefers_exact_match_over_prefix():
    options = ["A", "Alpha", "Alpine"]
    assert match_option("A", options) == "A"
