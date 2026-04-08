"""Tests for AskUserQuestion formatting in permission_handler."""

import pytest

from xbot.interaction.permission import BasePermissionHandler


class TestAskUserQuestionFormatting:
    """Tests for AskUserQuestion special formatting."""

    @pytest.fixture
    def handler(self):
        return BasePermissionHandler()

    def test_format_ask_user_question_single_question(self, handler):
        """Test formatting single question with options."""
        tool_input = {
            "questions": [
                {
                    "header": "方案选择",
                    "question": "Phase 1 的实施范围选择哪个？",
                    "options": [
                        {"label": "方案 A", "description": "最小可行产品"},
                        {"label": "方案 B", "description": "标准产品"},
                        {"label": "方案 C", "description": "完整产品"},
                    ],
                    "multiSelect": False,
                }
            ]
        }

        result = handler._format_ask_user_question(tool_input)

        assert "[方案选择]" in result
        assert "Phase 1 的实施范围选择哪个？" in result
        assert "方案 A" in result
        assert "最小可行产品" in result
        assert "方案 B" in result
        assert "方案 C" in result

    def test_format_ask_user_question_multiple_questions(self, handler):
        """Test formatting multiple questions."""
        tool_input = {
            "questions": [
                {
                    "header": "问题 1",
                    "question": "第一个问题？",
                    "options": [{"label": "选项 A"}],
                },
                {
                    "header": "问题 2",
                    "question": "第二个问题？",
                    "options": [{"label": "选项 B"}],
                },
            ]
        }

        result = handler._format_ask_user_question(tool_input)

        assert "[问题 1]" in result
        assert "第一个问题？" in result
        assert "[问题 2]" in result
        assert "第二个问题？" in result

    def test_format_ask_user_question_no_header(self, handler):
        """Test formatting question without header."""
        tool_input = {
            "questions": [
                {
                    "question": "没有标题的问题？",
                    "options": [{"label": "选项"}],
                }
            ]
        }

        result = handler._format_ask_user_question(tool_input)

        # Should use default header
        assert "[问题 1]" in result
        assert "没有标题的问题？" in result

    def test_format_ask_user_question_no_question_text(self, handler):
        """Test formatting with only options (no question text)."""
        tool_input = {
            "questions": [
                {
                    "header": "选择",
                    "options": [
                        {"label": "是"},
                        {"label": "否"},
                    ],
                }
            ]
        }

        result = handler._format_ask_user_question(tool_input)

        assert "[选择]" in result
        assert "是" in result
        assert "否" in result

    def test_format_ask_user_question_empty_options(self, handler):
        """Test formatting with empty options."""
        tool_input = {
            "questions": [
                {
                    "header": "开放问题",
                    "question": "请自由回答",
                    "options": [],
                }
            ]
        }

        result = handler._format_ask_user_question(tool_input)

        assert "[开放问题]" in result
        assert "请自由回答" in result
        assert "可选：" not in result  # No options section

    def test_format_ask_user_question_multi_select(self, handler):
        """Test formatting with multiSelect enabled."""
        tool_input = {
            "questions": [
                {
                    "header": "多选",
                    "question": "选择你喜欢的",
                    "options": [
                        {"label": "苹果"},
                        {"label": "香蕉"},
                    ],
                    "multiSelect": True,
                }
            ]
        }

        result = handler._format_ask_user_question(tool_input)

        assert "[多选]" in result
        assert "(可多选)" in result
        assert "苹果" in result
        assert "香蕉" in result

    def test_format_ask_user_question_option_without_description(self, handler):
        """Test formatting options without descriptions."""
        tool_input = {
            "questions": [
                {
                    "header": "简单选择",
                    "options": [
                        {"label": "选项 1"},
                        {"label": "选项 2"},
                    ],
                }
            ]
        }

        result = handler._format_ask_user_question(tool_input)

        assert "• 选项 1" in result
        assert "• 选项 2" in result
        # No colon since no description
        assert ": " not in result or "选项 1:" not in result

    def test_format_ask_user_question_option_with_description(self, handler):
        """Test formatting options with descriptions."""
        tool_input = {
            "questions": [
                {
                    "header": "详细选择",
                    "options": [
                        {"label": "快速", "description": "速度快但质量一般"},
                        {"label": "优质", "description": "质量好但速度慢"},
                    ],
                }
            ]
        }

        result = handler._format_ask_user_question(tool_input)

        # Implementation uses English colon ": "
        assert "快速: 速度快但质量一般" in result
        assert "优质: 质量好但速度慢" in result

    def test_format_ask_user_question_empty_questions(self, handler):
        """Test formatting with empty questions array."""
        tool_input = {"questions": []}

        result = handler._format_ask_user_question(tool_input)

        # Should fall back to JSON
        import json
        expected = json.dumps(tool_input, ensure_ascii=False)
        assert result == expected

    def test_format_ask_user_question_missing_questions_key(self, handler):
        """Test formatting without questions key."""
        tool_input = {"other_key": "value"}

        result = handler._format_ask_user_question(tool_input)

        # Should fall back to JSON
        import json
        expected = json.dumps(tool_input, ensure_ascii=False)
        assert result == expected

    def test_format_ask_user_question_complex_nested(self, handler):
        """Test formatting with complex nested structure."""
        tool_input = {
            "questions": [
                {
                    "header": "技术栈选择",
                    "question": "选择主要开发语言",
                    "options": [
                        {
                            "label": "Python",
                            "description": "适合数据分析和 AI",
                        },
                        {
                            "label": "TypeScript",
                            "description": "适合前端和全栈",
                        },
                        {
                            "label": "Go",
                            "description": "适合后端服务",
                        },
                    ],
                    "multiSelect": False,
                }
            ]
        }

        result = handler._format_ask_user_question(tool_input)

        assert "[技术栈选择]" in result
        assert "选择主要开发语言" in result
        assert "Python" in result
        assert "适合数据分析和 AI" in result
        assert "TypeScript" in result
        assert "Go" in result


class TestFormatPermissionMessageForAskUserQuestion:
    """Tests for format_permission_message with AskUserQuestion."""

    @pytest.fixture
    def handler(self):
        return BasePermissionHandler()

    def test_format_permission_message_ask_user_question(self, handler):
        """Test AskUserQuestion gets special formatting."""
        tool_input = {
            "questions": [
                {
                    "header": "范围选择",
                    "question": "选择实施范围",
                    "options": [
                        {"label": "方案 A", "description": "MVP"},
                        {"label": "方案 B", "description": "完整版"},
                    ],
                }
            ]
        }

        result = handler.format_permission_message("AskUserQuestion", tool_input)

        # Should start with emoji
        assert result.startswith("📝")
        # Should contain formatted question
        assert "[范围选择]" in result
        assert "方案 A" in result
        assert "方案 B" in result

    def test_format_permission_message_other_tool(self, handler):
        """Test other tools get standard formatting."""
        tool_input = {"command": "ls -la"}

        result = handler.format_permission_message("exec", tool_input)

        # Should not start with 📝
        assert not result.startswith("📝")
        # Should contain tool name
        assert "exec" in result
        # Should contain command
        assert "ls -la" in result

    def test_format_permission_message_ask_user_question_emoji(self, handler):
        """Test AskUserQuestion has correct emoji."""
        tool_input = {"questions": [{"header": "测试", "question": "问题？", "options": []}]}

        result = handler.format_permission_message("AskUserQuestion", tool_input)

        # 📝 emoji for questions
        assert "📝" in result


class TestSummarizeInputForAskUserQuestion:
    """Tests for summarize_input with AskUserQuestion tool_name parameter."""

    @pytest.fixture
    def handler(self):
        return BasePermissionHandler()

    def test_summarize_input_ask_user_question_uses_special_format(self, handler):
        """Test summarize_input with tool_name='AskUserQuestion' uses special format."""
        tool_input = {
            "questions": [
                {
                    "header": "测试",
                    "question": "测试问题？",
                    "options": [{"label": "选项"}],
                }
            ]
        }

        # With tool_name parameter
        result = handler.summarize_input(tool_input, tool_name="AskUserQuestion")

        # Should use special formatting
        assert "[测试]" in result
        assert "测试问题？" in result

    def test_summarize_input_without_tool_name_truncates(self, handler):
        """Test summarize_input without tool_name uses standard truncation."""
        tool_input = {
            "questions": [
                {
                    "header": "测试",
                    "question": "测试问题？",
                    "options": [{"label": "选项"}],
                }
            ]
        }

        # Without tool_name parameter - should use JSON truncation
        result = handler.summarize_input(tool_input)

        # Standard JSON format (might be truncated if long)
        assert "questions" in result
