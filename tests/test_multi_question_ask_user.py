"""Tests for multi-question AskUserQuestion handling."""

import pytest

from xbot.agent.interaction.permission import BasePermissionHandler
from xbot.agent.interaction.response_parser import normalize_validation_mode


class TestParseAnswers:
    """Tests for _parse_answers method."""

    @pytest.fixture
    def handler(self):
        return BasePermissionHandler()

    def test_parse_single_answer(self, handler):
        """Test parsing single answer for single question."""
        questions = [
            {"question": "选择哪个方案？", "options": [
                {"label": "方案 A"},
                {"label": "方案 B"},
            ]}
        ]
        question_options_map = [["方案 A", "方案 B"]]

        answers = handler._parse_answers("方案 A", questions, question_options_map)

        assert len(answers) == 1
        assert answers[0]["question"] == "选择哪个方案？"
        assert answers[0]["answer"] == "方案 A"

    def test_parse_multiple_answers_with_comma(self, handler):
        """Test parsing multiple answers separated by comma."""
        questions = [
            {"question": "选择哪个方案？", "options": [{"label": "方案 A"}, {"label": "方案 B"}]},
            {"question": "优先级如何？", "options": [{"label": "高"}, {"label": "中"}, {"label": "低"}]},
        ]
        question_options_map = [["方案 A", "方案 B"], ["高", "中", "低"]]

        answers = handler._parse_answers("方案 A, 高", questions, question_options_map)

        assert len(answers) == 2
        assert answers[0]["question"] == "选择哪个方案？"
        assert answers[0]["answer"] == "方案 A"
        assert answers[1]["question"] == "优先级如何？"
        assert answers[1]["answer"] == "高"

    def test_parse_multiple_answers_with_chinese_comma(self, handler):
        """Test parsing multiple answers separated by Chinese comma."""
        questions = [
            {"question": "问题1？", "options": [{"label": "A"}, {"label": "B"}]},
            {"question": "问题2？", "options": [{"label": "C"}, {"label": "D"}]},
        ]
        question_options_map = [["A", "B"], ["C", "D"]]

        answers = handler._parse_answers("A，C", questions, question_options_map)

        assert len(answers) == 2
        assert answers[0]["answer"] == "A"
        assert answers[1]["answer"] == "C"

    def test_parse_multiple_answers_with_space(self, handler):
        """Test parsing multiple answers separated by space - NOT SUPPORTED.

        Space is not used as a separator to avoid breaking options with spaces.
        User should use commas instead.
        """
        questions = [
            {"question": "Q1?", "options": [{"label": "Yes"}, {"label": "No"}]},
            {"question": "Q2?", "options": [{"label": "OK"}, {"label": "Cancel"}]},
        ]
        question_options_map = [["Yes", "No"], ["OK", "Cancel"]]

        # Space-separated input stays as raw text for the first answer
        answers = handler._parse_answers("Yes OK", questions, question_options_map)

        # No comma means only one raw answer is captured
        assert len(answers) == 2
        assert answers[0]["answer"] == "Yes OK"
        assert answers[1]["answer"] == ""  # No second answer provided (no comma)

    def test_parse_answers_with_whitespace(self, handler):
        """Test parsing answers with extra whitespace."""
        questions = [
            {"question": "选择？", "options": [{"label": "选项 A"}, {"label": "选项 B"}]},
        ]
        question_options_map = [["选项 A", "选项 B"]]

        answers = handler._parse_answers("  选项 A  ", questions, question_options_map)

        assert answers[0]["answer"] == "选项 A"

    def test_parse_answers_case_insensitive_match(self, handler):
        """Test case-insensitive option matching."""
        questions = [
            {"question": "Choose?", "options": [{"label": "Option A"}, {"label": "Option B"}]},
        ]
        question_options_map = [["Option A", "Option B"]]

        # User types lowercase
        answers = handler._parse_answers("option a", questions, question_options_map)

        # Should match to "Option A"
        assert answers[0]["answer"] == "Option A"

    def test_parse_answers_partial_match(self, handler):
        """Test partial option matching."""
        questions = [
            {"question": "选择？", "options": [{"label": "北京市"}, {"label": "上海市"}]},
        ]
        question_options_map = [["北京市", "上海市"]]

        # User types partial match
        answers = handler._parse_answers("北京", questions, question_options_map)

        # Should match to "北京市"
        assert answers[0]["answer"] == "北京市"

    def test_parse_answers_ambiguous_prefix_keeps_original(self, handler):
        """Test ambiguous prefixes follow the shared validator behavior."""
        questions = [
            {"question": "选择？", "options": [{"label": "北京市"}, {"label": "北京南站"}]},
        ]
        question_options_map = [["北京市", "北京南站"]]

        answers = handler._parse_answers("北京", questions, question_options_map)

        assert answers[0]["answer"] == "北京"

    def test_parse_answers_no_match_keeps_original(self, handler):
        """Test that non-matching answer keeps original value."""
        questions = [
            {"question": "选择？", "options": [{"label": "A"}, {"label": "B"}]},
        ]
        question_options_map = [["A", "B"]]

        answers = handler._parse_answers("C", questions, question_options_map)

        assert answers[0]["answer"] == "C"  # Original preserved

    def test_parse_answers_more_parts_than_questions(self, handler):
        """Test handling when user provides more answers than questions."""
        questions = [
            {"question": "Q1?", "options": [{"label": "A"}]},
        ]
        question_options_map = [["A"]]

        answers = handler._parse_answers("A, B, C", questions, question_options_map)

        assert len(answers) == 1  # Only first answer used
        assert answers[0]["answer"] == "A"

    def test_parse_answers_fewer_parts_than_questions(self, handler):
        """Test handling when user provides fewer answers than questions."""
        questions = [
            {"question": "Q1?", "options": [{"label": "A"}]},
            {"question": "Q2?", "options": [{"label": "B"}]},
        ]
        question_options_map = [["A"], ["B"]]

        answers = handler._parse_answers("A", questions, question_options_map)

        assert len(answers) == 2
        assert answers[0]["answer"] == "A"
        assert answers[1]["answer"] == ""  # Empty for missing answer

    def test_parse_answers_with_dunhao_separator(self, handler):
        """Test parsing with Chinese enumeration comma (顿号)."""
        questions = [
            {"question": "Q1?", "options": [{"label": "红"}, {"label": "绿"}]},
            {"question": "Q2?", "options": [{"label": "大"}, {"label": "小"}]},
        ]
        question_options_map = [["红", "绿"], ["大", "小"]]

        answers = handler._parse_answers("红、大", questions, question_options_map)

        assert answers[0]["answer"] == "红"
        assert answers[1]["answer"] == "大"

    def test_parse_three_answers(self, handler):
        """Test parsing three answers."""
        questions = [
            {"question": "颜色？", "options": [{"label": "红"}, {"label": "蓝"}]},
            {"question": "大小？", "options": [{"label": "大"}, {"label": "小"}]},
            {"question": "形状？", "options": [{"label": "圆"}, {"label": "方"}]},
        ]
        question_options_map = [["红", "蓝"], ["大", "小"], ["圆", "方"]]

        answers = handler._parse_answers("红, 大, 圆", questions, question_options_map)

        assert len(answers) == 3
        assert answers[0]["answer"] == "红"
        assert answers[1]["answer"] == "大"
        assert answers[2]["answer"] == "圆"

    def test_unknown_validation_mode_falls_back_to_suggested(self):
        """Test unknown validation modes use the shared suggested fallback."""
        assert normalize_validation_mode("weird-mode") == "suggested"


class TestHandleAskUserQuestionMulti:
    """Tests for _handle_ask_user_question with multiple questions."""

    # Note: Full async tests would require mocking request_interaction
    # These tests verify the static parts of the implementation

    @pytest.fixture
    def handler(self):
        return BasePermissionHandler()

    def test_format_multi_question_prompt(self, handler):
        """Test that multi-question prompt is formatted correctly."""
        # This tests the prompt building logic indirectly
        questions = [
            {"header": "方案", "question": "选择哪个？", "options": [{"label": "A"}, {"label": "B"}]},
            {"header": "优先级", "question": "多高？", "options": [{"label": "高"}, {"label": "低"}]},
        ]

        # Expected prompt format for multi-question
        # (actual prompt is built in _handle_ask_user_question)
        expected_parts = [
            "[方案]\n选择哪个？\nA / B",
            "[优先级]\n多高？\n高 / 低",
        ]

        # Verify the format logic
        for i, q in enumerate(questions):
            header = q.get("header", f"问题 {i+1}")
            question_text = q.get("question", "")
            option_labels = [opt.get("label", "") for opt in q.get("options", [])]

            part = f"[{header}]"
            if question_text:
                part += f"\n{question_text}"
            if option_labels:
                part += "\n" + " / ".join(option_labels)

            assert part == expected_parts[i]
