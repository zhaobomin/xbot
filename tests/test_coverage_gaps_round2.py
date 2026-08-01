"""Comprehensive tests for coverage gaps across multiple modules (round 2)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# 1. xbot/tools/registry.py
# ---------------------------------------------------------------------------

from xbot.tools.registry import ToolRegistry


class _FakeTool:
    """Minimal mock tool for registry tests."""

    def __init__(self, name: str, result: str = "ok"):
        self.name = name
        self._result = result

    async def execute(self, **kwargs: Any) -> str:
        return self._result

    def to_schema(self) -> dict:
        return {"name": self.name}

    def cast_params(self, params: dict) -> dict:
        return params

    def validate_params(self, params: dict) -> list[str]:
        return []


class _ValidatingFailTool(_FakeTool):
    """Tool that always fails validation."""

    def validate_params(self, params: dict) -> list[str]:
        return ["field is required"]


class _RaisingTool(_FakeTool):
    """Tool that raises a generic exception during execute."""

    async def execute(self, **kwargs: Any) -> str:
        raise RuntimeError("boom")


class TestToolRegistry:
    def test_register_and_get(self):
        reg = ToolRegistry()
        tool = _FakeTool("my_tool")
        reg.register(tool)
        assert reg.get("my_tool") is tool
        assert reg.has("my_tool")

    def test_register_duplicate_raises(self):
        reg = ToolRegistry()
        reg.register(_FakeTool("dup"))
        with pytest.raises(ValueError, match="already registered"):
            reg.register(_FakeTool("dup"))

    def test_unregister_existing(self):
        reg = ToolRegistry()
        reg.register(_FakeTool("x"))
        reg.unregister("x")
        assert reg.get("x") is None
        assert not reg.has("x")

    def test_unregister_missing_no_error(self):
        reg = ToolRegistry()
        reg.unregister("nonexistent")  # should not raise

    def test_len(self):
        reg = ToolRegistry()
        assert len(reg) == 0
        reg.register(_FakeTool("a"))
        reg.register(_FakeTool("b"))
        assert len(reg) == 2

    def test_contains(self):
        reg = ToolRegistry()
        reg.register(_FakeTool("present"))
        assert "present" in reg
        assert "absent" not in reg

    def test_tool_names_property(self):
        reg = ToolRegistry()
        reg.register(_FakeTool("alpha"))
        reg.register(_FakeTool("beta"))
        assert set(reg.tool_names) == {"alpha", "beta"}

    def test_get_definitions(self):
        reg = ToolRegistry()
        reg.register(_FakeTool("t1"))
        defs = reg.get_definitions()
        assert len(defs) == 1
        assert defs[0] == {"name": "t1"}

    @pytest.mark.asyncio
    async def test_execute_success(self):
        reg = ToolRegistry()
        reg.register(_FakeTool("ok", result="hello"))
        out = await reg.execute("ok", {})
        assert out == "hello"

    @pytest.mark.asyncio
    async def test_execute_tool_not_found(self):
        from xbot.exceptions import ToolNotFoundError

        reg = ToolRegistry()
        with pytest.raises(ToolNotFoundError):
            await reg.execute("missing", {})

    @pytest.mark.asyncio
    async def test_execute_validation_error(self):
        from xbot.exceptions import ToolExecutionError

        reg = ToolRegistry()
        reg.register(_ValidatingFailTool("bad"))
        with pytest.raises(ToolExecutionError, match="Invalid parameters"):
            await reg.execute("bad", {"x": 1})

    @pytest.mark.asyncio
    async def test_execute_generic_error_wraps_in_tool_execution_error(self):
        from xbot.exceptions import ToolExecutionError

        reg = ToolRegistry()
        reg.register(_RaisingTool("err"))
        with pytest.raises(ToolExecutionError, match="boom"):
            await reg.execute("err", {})


# ---------------------------------------------------------------------------
# 2. xbot/tools/web.py  — pure helper functions
# ---------------------------------------------------------------------------

from xbot.tools.web import (
    _format_results,
    _normalize,
    _strip_markdown,
    _strip_tags,
    _validate_url,
    WebFetchTool,
)


class TestStripTags:
    def test_removes_simple_tags(self):
        assert _strip_tags("<b>hello</b>") == "hello"

    def test_removes_script_blocks(self):
        result = _strip_tags('before<script>alert("x")</script>after')
        assert "script" not in result
        assert "before" in result and "after" in result

    def test_removes_style_blocks(self):
        result = _strip_tags("text<style>.x{}</style>end")
        assert "style" not in result

    def test_decodes_html_entities(self):
        assert _strip_tags("&amp; &lt;") == "& <"

    def test_empty_string(self):
        assert _strip_tags("") == ""


class TestNormalize:
    def test_collapses_spaces(self):
        assert _normalize("a   b") == "a b"

    def test_collapses_multiple_newlines(self):
        assert _normalize("a\n\n\n\nb") == "a\n\nb"

    def test_strips_leading_trailing(self):
        assert _normalize("  hi  ") == "hi"


class TestStripMarkdown:
    def test_removes_images(self):
        assert _strip_markdown("![alt](http://x/y.png)") == "alt"

    def test_removes_links_but_keeps_text(self):
        assert _strip_markdown("[click](http://x.com)") == "click"

    def test_removes_headings(self):
        assert _strip_markdown("# Title") == "Title"

    def test_removes_emphasis(self):
        assert _strip_markdown("**bold** and _italic_") == "bold and italic"


class TestValidateUrl:
    def test_valid_https(self):
        ok, err = _validate_url("https://example.com")
        assert ok and err == ""

    def test_valid_http(self):
        ok, err = _validate_url("http://example.com")
        assert ok

    def test_rejects_ftp(self):
        ok, err = _validate_url("ftp://example.com")
        assert not ok
        assert "http/https" in err

    def test_rejects_empty_scheme(self):
        ok, err = _validate_url("example.com")
        assert not ok

    def test_rejects_missing_domain(self):
        ok, err = _validate_url("http://")
        assert not ok
        assert "Missing domain" in err


class TestFormatResults:
    def test_no_results(self):
        out = _format_results("q", [], 5)
        assert "No results" in out

    def test_with_results(self):
        items = [{"title": "T", "url": "http://u", "content": "desc"}]
        out = _format_results("q", items, 5)
        assert "Results for: q" in out
        assert "T" in out
        assert "http://u" in out
        assert "desc" in out

    def test_truncates_to_n(self):
        items = [{"title": f"T{i}", "url": "", "content": ""} for i in range(10)]
        out = _format_results("q", items, 3)
        # Should only include 3 results
        assert "T0" in out
        assert "T2" in out
        assert "T3" not in out


class TestWebFetchToMarkdown:
    def test_links_converted(self):
        tool = WebFetchTool.__new__(WebFetchTool)
        html = '<a href="http://x.com">click</a>'
        md = tool._to_markdown(html)
        assert "[click]" in md
        assert "http://x.com" in md

    def test_headings_converted(self):
        tool = WebFetchTool.__new__(WebFetchTool)
        html = "<h1>Title</h1>"
        md = tool._to_markdown(html)
        assert "# Title" in md

    def test_list_items_converted(self):
        tool = WebFetchTool.__new__(WebFetchTool)
        html = "<li>item1</li><li>item2</li>"
        md = tool._to_markdown(html)
        assert "- item1" in md
        assert "- item2" in md


# ---------------------------------------------------------------------------
# 3. xbot/crew/cli/role_cmd.py  — Typer commands via CliRunner
# ---------------------------------------------------------------------------

from typer.testing import CliRunner

from xbot.crew.cli.role_cmd import app as role_app


runner = CliRunner()


class TestRoleCmdList:
    @patch("xbot.crew.cli.role_cmd.RolePoolManager")
    def test_roles_list_no_roles(self, MockManager):
        pool = MockManager.return_value.get_pool.return_value
        pool.get_available_roles.return_value = []
        result = runner.invoke(role_app, ["list"])
        assert result.exit_code == 0
        assert "No roles found" in result.output

    @patch("xbot.crew.cli.role_cmd.RolePoolManager")
    def test_roles_list_with_roles(self, MockManager):
        from xbot.crew.planner import Capability, RoleDefinition, RoleTier

        role = RoleDefinition(
            name="tester",
            display_name="Tester",
            tier=RoleTier.CORE,
            capabilities=[Capability.REVIEW],
            description="Test role for unit tests",
            goal="Test goal",
            backstory="Test backstory",
        )
        pool = MockManager.return_value.get_pool.return_value
        pool.get_available_roles.return_value = [role]
        result = runner.invoke(role_app, ["list"])
        assert result.exit_code == 0
        assert "tester" in result.output

    @patch("xbot.crew.cli.role_cmd.RolePoolManager")
    def test_roles_list_invalid_tier(self, MockManager):
        result = runner.invoke(role_app, ["list", "--tier", "bogus"])
        assert result.exit_code == 1
        assert "Invalid tier" in result.output


class TestRoleCmdShow:
    @patch("xbot.crew.cli.role_cmd.RolePoolManager")
    def test_show_role_not_found(self, MockManager):
        pool = MockManager.return_value.get_pool.return_value
        pool.get_role.return_value = None
        pool.get_available_roles.return_value = []
        result = runner.invoke(role_app, ["show", "missing"])
        assert result.exit_code == 1
        assert "not found" in result.output

    @patch("xbot.crew.cli.role_cmd.RolePoolManager")
    def test_show_role_found(self, MockManager):
        from xbot.crew.planner import Capability, RoleDefinition, RoleTier

        role = RoleDefinition(
            name="dev",
            display_name="Developer",
            tier=RoleTier.CORE,
            capabilities=[Capability.REVIEW],
            description="A developer role",
            goal="Write code",
            backstory="Experienced developer",
        )
        pool = MockManager.return_value.get_pool.return_value
        pool.get_role.return_value = role
        result = runner.invoke(role_app, ["show", "dev"])
        assert result.exit_code == 0
        assert "Developer" in result.output


class TestRoleCmdCreate:
    @patch("xbot.crew.cli.role_cmd.RoleCreator")
    def test_create_non_interactive_all_fields(self, MockCreator):
        result_obj = MagicMock()
        result_obj.success = True
        result_obj.warnings = []
        role = MagicMock()
        role.name = "new_role"
        role.display_name = "New Role"
        role.description = "desc"
        role.capabilities = [MagicMock(value="review")]
        role.tools = None
        result_obj.role = role
        MockCreator.return_value.create_role_from_definition.return_value = result_obj

        result = runner.invoke(role_app, [
            "create",
            "--no-interactive",
            "--name", "new_role",
            "--description", "desc",
            "--goal", "goal",
            "--capabilities", "review",
        ])
        assert result.exit_code == 0
        assert "created successfully" in result.output

    @patch("xbot.crew.cli.role_cmd.RoleCreator")
    def test_create_missing_name_exits(self, MockCreator):
        result = runner.invoke(role_app, [
            "create",
            "--no-interactive",
            "--capabilities", "review",
        ])
        assert result.exit_code == 1
        assert "name is required" in result.output

    @patch("xbot.crew.cli.role_cmd.RoleCreator")
    def test_create_from_file_not_found(self, MockCreator):
        MockCreator.return_value.load_role_from_file.return_value = None
        result = runner.invoke(role_app, [
            "create",
            "--from-file", "/nonexistent/file.yaml",
        ])
        assert result.exit_code == 1
        assert "Failed to load" in result.output


class TestRoleCmdValidate:
    @patch("xbot.crew.cli.role_cmd.validate_role_file")
    def test_validate_valid(self, mock_validate, tmp_path):
        from xbot.crew.planner import Capability, RoleDefinition, RoleTier

        role = RoleDefinition(
            name="valid",
            display_name="Valid",
            tier=RoleTier.CORE,
            capabilities=[Capability.REVIEW],
            description="d",
            goal="g",
            backstory="b",
        )
        mock_validate.return_value = (True, [], role)
        f = tmp_path / "role.yaml"
        f.write_text("x")
        result = runner.invoke(role_app, ["validate", str(f)])
        assert result.exit_code == 0
        assert "valid" in result.output.lower()

    @patch("xbot.crew.cli.role_cmd.validate_role_file")
    def test_validate_invalid(self, mock_validate, tmp_path):
        mock_validate.return_value = (False, ["missing field"], None)
        f = tmp_path / "bad.yaml"
        f.write_text("x")
        result = runner.invoke(role_app, ["validate", str(f)])
        assert result.exit_code == 1
        assert "validation failed" in result.output.lower()

    def test_validate_file_not_found(self):
        result = runner.invoke(role_app, ["validate", "/nonexistent/file.yaml"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestRoleCmdCopy:
    @patch("xbot.crew.cli.role_cmd.RoleCreator")
    @patch("xbot.crew.cli.role_cmd.RolePoolManager")
    def test_copy_no_output(self, MockManager, MockCreator):
        from xbot.crew.planner import Capability, RoleDefinition, RoleTier

        role = RoleDefinition(
            name="src",
            display_name="Src",
            tier=RoleTier.CORE,
            capabilities=[Capability.REVIEW],
            description="d",
            goal="g",
            backstory="b",
        )
        pool = MockManager.return_value.get_pool.return_value
        pool.get_role.return_value = role
        result = runner.invoke(role_app, ["copy", "src", "dst"])
        assert result.exit_code == 0
        assert "No output directory" in result.output

    @patch("xbot.crew.cli.role_cmd.RolePoolManager")
    def test_copy_source_not_found(self, MockManager):
        pool = MockManager.return_value.get_pool.return_value
        pool.get_role.return_value = None
        result = runner.invoke(role_app, ["copy", "nope", "dst"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestRoleCmdDelete:
    def test_delete_invalid_name_slash(self):
        result = runner.invoke(role_app, ["delete", "../etc", "--custom-dir", "/tmp"])
        assert result.exit_code == 1
        assert "Invalid role name" in result.output

    def test_delete_invalid_name_format(self):
        result = runner.invoke(role_app, ["delete", "123bad", "--custom-dir", "/tmp"])
        assert result.exit_code == 1
        assert "Invalid role name format" in result.output

    def test_delete_file_not_found(self, tmp_path):
        result = runner.invoke(role_app, ["delete", "myrole", "--custom-dir", str(tmp_path)])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestRoleCmdExport:
    @patch("xbot.crew.cli.role_cmd.RoleCreator")
    @patch("xbot.crew.cli.role_cmd.RolePoolManager")
    def test_export_not_found(self, MockManager, MockCreator):
        pool = MockManager.return_value.get_pool.return_value
        pool.get_role.return_value = None
        result = runner.invoke(role_app, ["export", "missing", "--output", "/tmp/out.yaml"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestRoleCmdImport:
    def test_import_file_not_found(self):
        result = runner.invoke(role_app, ["import", "/no/file.yaml"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    @patch("xbot.crew.cli.role_cmd.RoleCreator")
    def test_import_load_failure(self, MockCreator, tmp_path):
        MockCreator.return_value.load_role_from_file.return_value = None
        f = tmp_path / "role.yaml"
        f.write_text("x")
        result = runner.invoke(role_app, ["import", str(f)])
        assert result.exit_code == 1
        assert "Failed to load" in result.output


# ---------------------------------------------------------------------------
# 4. xbot/interaction/permission.py
# ---------------------------------------------------------------------------

from xbot.interaction.permission import (
    BasePermissionHandler,
    CLIPermissionHandler,
    PermissionRequestHandler,
    create_permission_handler,
)


class TestCleanupExpiredContexts:
    def test_expires_old_contexts(self):
        bus = MagicMock()
        handler = PermissionRequestHandler(bus=bus)
        handler._context_ttl = 0  # expire immediately
        handler.set_session_context("s1", "ch", "cid")
        time.sleep(0.01)
        handler.set_session_context("s2", "ch2", "cid2")
        # s1 should be cleaned up
        handler._cleanup_expired_contexts()
        assert "s1" not in handler._session_context
        # s2 was just added but also expired because TTL=0
        # Let's test with a non-zero TTL instead

    def test_capacity_eviction(self):
        bus = MagicMock()
        handler = PermissionRequestHandler(bus=bus)
        handler._context_ttl = 3600
        handler._max_contexts = 2
        handler.set_session_context("s1", "ch", "cid")
        time.sleep(0.01)
        handler.set_session_context("s2", "ch", "cid")
        time.sleep(0.01)
        handler.set_session_context("s3", "ch", "cid")
        # s1 should be evicted as oldest
        assert len(handler._session_context) <= 2
        assert "s3" in handler._session_context


class TestParseAnswers:
    def test_single_answer_single_question(self):
        questions = [{"question": "Pick one?"}]
        options_map = [["A", "B", "C"]]
        answers = BasePermissionHandler._parse_answers("A", questions, options_map)
        assert len(answers) == 1
        assert answers[0]["answer"] == "A"

    def test_multiple_answers(self):
        questions = [{"question": "Q1?"}, {"question": "Q2?"}]
        options_map = [["X", "Y"], ["P", "Q"]]
        answers = BasePermissionHandler._parse_answers("X, P", questions, options_map)
        assert len(answers) == 2

    def test_answer_count_mismatch_logs_warning(self):
        questions = [{"question": "Q1?"}, {"question": "Q2?"}]
        options_map = [["A"], ["B"]]
        # Only 1 answer for 2 questions
        answers = BasePermissionHandler._parse_answers("A", questions, options_map)
        assert len(answers) == 2
        assert answers[1]["answer"] == ""

    def test_unmatched_answer_uses_raw(self):
        questions = [{"question": "Pick?"}]
        options_map = [["A", "B"]]
        answers = BasePermissionHandler._parse_answers("Z", questions, options_map)
        assert answers[0]["answer"] == "Z"


class TestFormatAskUserQuestion:
    def test_empty_questions_falls_back_to_json(self):
        result = BasePermissionHandler._format_ask_user_question({"questions": []})
        parsed = json.loads(result)
        assert "questions" in parsed

    def test_single_question_formatted(self):
        tool_input = {
            "questions": [
                {
                    "header": "Choose",
                    "question": "Which?",
                    "options": [
                        {"label": "A", "description": "first"},
                        {"label": "B"},
                    ],
                    "multiSelect": False,
                }
            ]
        }
        out = BasePermissionHandler._format_ask_user_question(tool_input)
        assert "[Choose]" in out
        assert "Which?" in out
        assert "first" in out

    def test_multi_select_label(self):
        tool_input = {
            "questions": [
                {
                    "header": "H",
                    "question": "Q",
                    "options": [{"label": "X"}],
                    "multiSelect": True,
                }
            ]
        }
        out = BasePermissionHandler._format_ask_user_question(tool_input)
        assert "可多选" in out


class TestCreatePermissionHandler:
    def test_channel_mode_requires_bus(self):
        with pytest.raises(ValueError, match="MessageBus"):
            create_permission_handler("channel", bus=None)

    def test_channel_mode_returns_handler(self):
        bus = MagicMock()
        handler = create_permission_handler("channel", bus=bus)
        assert isinstance(handler, PermissionRequestHandler)

    def test_cli_mode_returns_handler(self):
        handler = create_permission_handler("cli")
        assert isinstance(handler, CLIPermissionHandler)

    def test_cli_non_interactive(self):
        handler = create_permission_handler("cli", non_interactive=True)
        assert isinstance(handler, CLIPermissionHandler)
        assert handler.interactive is False

    def test_interactive_mode_returns_handler(self):
        from xbot.interaction.permission import InteractivePermissionHandler

        handler = create_permission_handler("interactive")
        assert isinstance(handler, InteractivePermissionHandler)


class TestCLIPermissionHandlerBasic:
    @pytest.mark.asyncio
    async def test_non_interactive_denies_unsafe_tool(self):
        handler = CLIPermissionHandler(auto_approve_safe_tools=True, interactive=False)
        decision, detail = await handler.can_use_tool("Bash", {"cmd": "rm -rf /"}, None)
        assert decision == "deny"
        assert "Non-interactive" in detail

    @pytest.mark.asyncio
    async def test_auto_approves_safe_tool(self):
        handler = CLIPermissionHandler(auto_approve_safe_tools=True, interactive=False)
        decision, detail = await handler.can_use_tool("Read", {"file": "/etc/hosts"}, None)
        assert decision == "allow"

    @pytest.mark.asyncio
    async def test_request_interaction_non_interactive(self):
        handler = CLIPermissionHandler(interactive=False)
        resp = await handler.request_interaction(prompt="hi")
        assert resp.action == "cancel"

    @pytest.mark.asyncio
    async def test_base_request_interaction_returns_cancel(self):
        handler = BasePermissionHandler()
        resp = await handler.request_interaction(prompt="test")
        assert resp.action == "cancel"


class TestBasePermissionHandlerSummarizeInput:
    def test_empty_input(self):
        assert BasePermissionHandler.summarize_input({}) == ""

    def test_short_input(self):
        out = BasePermissionHandler.summarize_input({"a": 1})
        assert '"a"' in out

    def test_long_input_truncated(self):
        big = {"x": "y" * 200}
        out = BasePermissionHandler.summarize_input(big, max_len=50)
        assert out.endswith("...")
        assert len(out) <= 53  # 50 + "..."

    def test_ask_user_question_delegates(self):
        tool_input = {"questions": [{"header": "H", "question": "Q?", "options": []}]}
        out = BasePermissionHandler.summarize_input(tool_input, tool_name="AskUserQuestion")
        assert "[H]" in out


class TestIsSafeTool:
    def test_known_safe(self):
        handler = BasePermissionHandler()
        assert handler.is_safe_tool("Read")
        assert handler.is_safe_tool("read_file")

    def test_unknown_unsafe(self):
        handler = BasePermissionHandler()
        assert not handler.is_safe_tool("Bash")

    def test_add_safe_tool(self):
        handler = BasePermissionHandler()
        handler.add_safe_tool("CustomTool")
        assert handler.is_safe_tool("CustomTool")


# ---------------------------------------------------------------------------
# 5. xbot/memory/reme.py
# ---------------------------------------------------------------------------

from xbot.memory.reme import ReMeMemoryStore


class TestBuildReMeConfig:
    def test_default_config(self, tmp_path):
        store = ReMeMemoryStore(workspace=tmp_path)
        config = store._build_reme_config()
        assert config["llm"]["model_name"] == "gpt-4.1-nano"

    def test_custom_llm_config(self, tmp_path):
        store = ReMeMemoryStore(
            workspace=tmp_path,
            llm_config={"model": "gpt-4", "backend": "openai"},
        )
        config = store._build_reme_config()
        assert config["llm"]["model_name"] == "gpt-4"
        assert config["llm"]["backend"] == "openai"

    def test_embedding_config(self, tmp_path):
        store = ReMeMemoryStore(
            workspace=tmp_path,
            embedding_config={"model": "text-embedding-3-small"},
        )
        config = store._build_reme_config()
        assert config["embedding"]["model_name"] == "text-embedding-3-small"

    def test_no_embedding_config(self, tmp_path):
        store = ReMeMemoryStore(workspace=tmp_path)
        config = store._build_reme_config()
        assert "embedding" not in config


class TestFormatMessages:
    def test_formats_messages(self):
        messages = [
            {"role": "user", "content": "hello", "timestamp": "2024-01-01T10:00:00"},
            {"role": "assistant", "content": "hi there", "timestamp": "2024-01-01T10:00:01", "tools_used": ["search"]},
        ]
        out = ReMeMemoryStore._format_messages(messages)
        assert "USER" in out
        assert "hello" in out
        assert "ASSISTANT" in out
        assert "[tools: search]" in out

    def test_skips_empty_content(self):
        messages = [
            {"role": "user", "content": "", "timestamp": "t"},
            {"role": "assistant", "content": "ok", "timestamp": "t"},
        ]
        out = ReMeMemoryStore._format_messages(messages)
        assert "USER" not in out
        assert "ok" in out

    def test_empty_list(self):
        assert ReMeMemoryStore._format_messages([]) == ""


class TestReMeMemoryStoreCompat:
    def test_read_write_long_term(self, tmp_path):
        store = ReMeMemoryStore(workspace=tmp_path)
        store.write_long_term("fact1")
        assert store.read_long_term() == "fact1"

    def test_read_long_term_missing(self, tmp_path):
        store = ReMeMemoryStore(workspace=tmp_path)
        assert store.read_long_term() == ""

    def test_get_memory_context_with_content(self, tmp_path):
        store = ReMeMemoryStore(workspace=tmp_path)
        store.write_long_term("important fact")
        ctx = store.get_memory_context()
        assert "important fact" in ctx
        assert "Long-term Memory" in ctx

    def test_get_memory_context_empty(self, tmp_path):
        store = ReMeMemoryStore(workspace=tmp_path)
        assert store.get_memory_context() == ""

    def test_append_history(self, tmp_path):
        store = ReMeMemoryStore(workspace=tmp_path)
        store.append_history("entry1")
        store.append_history("entry2")
        content = store.history_file.read_text()
        assert "entry1" in content
        assert "entry2" in content


class TestCompactContext:
    @pytest.mark.asyncio
    async def test_returns_messages_when_not_initialized(self, tmp_path):
        store = ReMeMemoryStore(workspace=tmp_path)
        # Force initialized state without ReMe
        store._initialized = True
        store._reme = None
        msgs = [{"role": "user", "content": "hi"}]
        result, summary = await store.compact_context(msgs)
        assert result is msgs
        assert summary is None


class TestSummarizeToMemory:
    @pytest.mark.asyncio
    async def test_returns_false_when_not_initialized(self, tmp_path):
        store = ReMeMemoryStore(workspace=tmp_path)
        store._initialized = True
        store._reme = None
        result = await store.summarize_to_memory([{"role": "user", "content": "hi"}])
        assert result is False


class TestConsolidate:
    @pytest.mark.asyncio
    async def test_empty_messages_returns_true(self, tmp_path):
        store = ReMeMemoryStore(workspace=tmp_path)
        store._initialized = True
        result = await store.consolidate([], MagicMock())
        assert result is True

    @pytest.mark.asyncio
    async def test_fallback_when_reme_not_available(self, tmp_path):
        store = ReMeMemoryStore(workspace=tmp_path)
        store._initialized = True
        store._reme = None
        backend = MagicMock()
        # The fallback tries to create MemoryStore and call consolidate
        with patch("xbot.memory.reme.ReMeMemoryStore._fallback_consolidate", new_callable=AsyncMock) as mock_fb:
            mock_fb.return_value = True
            result = await store.consolidate([{"role": "user", "content": "x"}], backend)
            assert result is True
            mock_fb.assert_called_once()


class TestClose:
    @pytest.mark.asyncio
    async def test_close_with_reme(self, tmp_path):
        store = ReMeMemoryStore(workspace=tmp_path)
        mock_reme = AsyncMock()
        store._reme = mock_reme
        await store.close()
        mock_reme.close.assert_called_once()
        assert store._reme is None

    @pytest.mark.asyncio
    async def test_close_without_reme(self, tmp_path):
        store = ReMeMemoryStore(workspace=tmp_path)
        store._reme = None
        await store.close()  # should not raise


class TestFallbackSearch:
    def test_finds_in_memory_file(self, tmp_path):
        store = ReMeMemoryStore(workspace=tmp_path)
        store.write_long_term("the quick brown fox")
        results = store._fallback_search("fox", 5)
        assert len(results) >= 1
        assert results[0]["source"] == "MEMORY.md"

    def test_no_match_returns_empty(self, tmp_path):
        store = ReMeMemoryStore(workspace=tmp_path)
        results = store._fallback_search("nonexistent", 5)
        assert results == []


# ---------------------------------------------------------------------------
# 6. xbot/interaction/response_handlers.py
# ---------------------------------------------------------------------------

from xbot.interaction.response_handlers import RuntimeResponseHandlers
from xbot.platform.bus.events import InboundMessage
from xbot.runtime.state import SessionPhase


def _make_runtime(
    phase: SessionPhase = SessionPhase.IDLE,
    bus: Any = None,
) -> MagicMock:
    rt = MagicMock()
    registry = MagicMock()
    registry.get_phase.return_value = phase
    rt._shared_resources = {"bus": bus, "runtime_registry": registry}
    rt.runtime_registry = registry
    rt._interaction_retry_counts = {}
    rt._is_local_runtime_command = MagicMock(return_value=False)
    return rt


class TestInteractionRetryKey:
    def test_key_format(self):
        handlers = RuntimeResponseHandlers(MagicMock())
        key = handlers._interaction_retry_key("sess", "req")
        assert key == "sess:req"


class TestClearInteractionRetryState:
    def test_clears_session_key(self):
        handlers = RuntimeResponseHandlers(MagicMock())
        handlers._interaction_retry_counts["sess"] = 5
        handlers._clear_interaction_retry_state("sess")
        assert "sess" not in handlers._interaction_retry_counts

    def test_clears_stale_prefixed_keys(self):
        handlers = RuntimeResponseHandlers(MagicMock())
        handlers._interaction_retry_counts["sess:abc"] = 1
        handlers._interaction_retry_counts["sess:def"] = 2
        handlers._interaction_retry_counts["other:xyz"] = 3
        handlers._clear_interaction_retry_state("sess", clear_session=True)
        assert "sess:abc" not in handlers._interaction_retry_counts
        assert "sess:def" not in handlers._interaction_retry_counts
        assert "other:xyz" in handlers._interaction_retry_counts


class TestMatchInteractionOption:
    def test_single_question_match(self):
        handlers = RuntimeResponseHandlers(MagicMock())
        metadata = {"valid_options": ["A", "B"], "question_count": 0}
        result = handlers._match_interaction_option("A", metadata)
        assert result == "A"

    def test_single_question_no_match(self):
        handlers = RuntimeResponseHandlers(MagicMock())
        metadata = {"valid_options": ["A", "B"], "question_count": 0}
        result = handlers._match_interaction_option("Z", metadata)
        assert result is None

    def test_multi_question_match(self):
        handlers = RuntimeResponseHandlers(MagicMock())
        metadata = {
            "valid_options": ["A", "B", "X", "Y"],
            "question_options_map": [["A", "B"], ["X", "Y"]],
            "question_count": 2,
        }
        result = handlers._match_interaction_option("A, X", metadata)
        assert result is not None
        assert "A" in result
        assert "X" in result

    def test_multi_question_wrong_count(self):
        handlers = RuntimeResponseHandlers(MagicMock())
        metadata = {
            "valid_options": ["A", "B"],
            "question_options_map": [["A", "B"]],
            "question_count": 1,
        }
        # Single answer for 1 question, should work
        result = handlers._match_interaction_option("A", metadata)
        assert result == "A"


class TestHandlePermissionResponse:
    @pytest.mark.asyncio
    async def test_returns_false_when_no_bus(self):
        rt = MagicMock(spec=[])  # spec=[] prevents auto-attr creation
        handlers = RuntimeResponseHandlers(rt)
        msg = InboundMessage(channel="ch", chat_id="cid", sender_id="u", content="allow")
        result = await handlers.handle_permission_response(msg)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_not_permission(self):
        bus = MagicMock()
        rt = _make_runtime(bus=bus)
        handlers = RuntimeResponseHandlers(rt)
        msg = InboundMessage(channel="ch", chat_id="cid", sender_id="u", content="hello world")
        result = await handlers.handle_permission_response(msg)
        assert result is False


class TestHandleInteractionResponse:
    @pytest.mark.asyncio
    async def test_returns_false_when_no_bus(self):
        rt = MagicMock(spec=[])
        handlers = RuntimeResponseHandlers(rt)
        msg = InboundMessage(channel="ch", chat_id="cid", sender_id="u", content="reply")
        result = await handlers.handle_interaction_response(msg)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_for_local_command(self):
        bus = MagicMock()
        rt = _make_runtime(bus=bus)
        rt._is_local_runtime_command.return_value = True
        handlers = RuntimeResponseHandlers(rt)
        msg = InboundMessage(channel="ch", chat_id="cid", sender_id="u", content="/status")
        result = await handlers.handle_interaction_response(msg)
        assert result is False


class TestBusProperty:
    def test_bus_from_shared(self):
        bus = MagicMock()
        rt = MagicMock()
        rt._shared_resources = {"bus": bus}
        handlers = RuntimeResponseHandlers(rt)
        assert handlers._bus is bus

    def test_bus_fallback_to_runtime(self):
        bus = MagicMock()
        rt = MagicMock()
        rt._shared_resources = {}
        rt.bus = bus
        handlers = RuntimeResponseHandlers(rt)
        assert handlers._bus is bus

    def test_bus_none(self):
        rt = MagicMock(spec=[])
        handlers = RuntimeResponseHandlers(rt)
        assert handlers._bus is None


class TestStateCoordinator:
    def test_from_shared_resources(self):
        registry = MagicMock()
        rt = MagicMock()
        rt._shared_resources = {"runtime_registry": registry}
        handlers = RuntimeResponseHandlers(rt)
        assert handlers._state_coordinator is registry

    def test_fallback_to_runtime_attr(self):
        registry = MagicMock()
        rt = MagicMock()
        rt._shared_resources = {}
        rt.runtime_registry = registry
        handlers = RuntimeResponseHandlers(rt)
        assert handlers._state_coordinator is registry

    def test_raises_when_missing(self):
        rt = MagicMock(spec=[])
        handlers = RuntimeResponseHandlers(rt)
        with pytest.raises(RuntimeError, match="runtime_registry"):
            _ = handlers._state_coordinator


# ---------------------------------------------------------------------------
# 7. xbot/tools/web_http_transport.py
# ---------------------------------------------------------------------------

from xbot.tools.web_http_transport import (
    PinnedAsyncHTTPTransport,
    PinnedAsyncNetworkBackend,
)


class TestPinnedAsyncNetworkBackend:
    @pytest.mark.asyncio
    async def test_connect_tcp_uses_pinned_ip(self):
        mock_backend = AsyncMock()
        mock_conn = MagicMock()
        mock_backend.connect_tcp.return_value = mock_conn
        pinned = {"example.com": "1.2.3.4"}
        backend = PinnedAsyncNetworkBackend(pinned, backend=mock_backend)
        result = await backend.connect_tcp("example.com", 443)
        mock_backend.connect_tcp.assert_called_once_with(
            "1.2.3.4", 443, timeout=None, local_address=None, socket_options=None
        )
        assert result is mock_conn

    @pytest.mark.asyncio
    async def test_connect_tcp_unknown_host_passes_through(self):
        mock_backend = AsyncMock()
        pinned = {}
        backend = PinnedAsyncNetworkBackend(pinned, backend=mock_backend)
        await backend.connect_tcp("unknown.com", 80)
        mock_backend.connect_tcp.assert_called_once_with(
            "unknown.com", 80, timeout=None, local_address=None, socket_options=None
        )

    @pytest.mark.asyncio
    async def test_connect_unix_socket_delegates(self):
        mock_backend = AsyncMock()
        backend = PinnedAsyncNetworkBackend({}, backend=mock_backend)
        await backend.connect_unix_socket("/tmp/sock")
        mock_backend.connect_unix_socket.assert_called_once()

    @pytest.mark.asyncio
    async def test_sleep_delegates(self):
        mock_backend = AsyncMock()
        backend = PinnedAsyncNetworkBackend({}, backend=mock_backend)
        await backend.sleep(0.1)
        mock_backend.sleep.assert_called_once_with(0.1)


class TestPinnedAsyncHTTPTransport:
    def test_proxy_with_pinned_raises(self):
        with pytest.raises(ValueError, match="Proxy mode cannot preserve"):
            PinnedAsyncHTTPTransport({"h": "1.2.3.4"}, proxy="http://proxy:8080")

    def test_no_proxy_creates_pool(self):
        transport = PinnedAsyncHTTPTransport({"h": "1.2.3.4"})
        assert transport._pool is not None
        assert transport._max_response_bytes == 10 * 1024 * 1024

    def test_custom_max_response_bytes(self):
        transport = PinnedAsyncHTTPTransport({}, max_response_bytes=1024)
        assert transport._max_response_bytes == 1024

    def test_http_proxy(self):
        transport = PinnedAsyncHTTPTransport({}, proxy="http://proxy:8080")
        assert transport._pool is not None

    def test_socks5_proxy(self):
        transport = PinnedAsyncHTTPTransport({}, proxy="socks5://proxy:1080")
        assert transport._pool is not None

    def test_unsupported_proxy_scheme(self):
        with pytest.raises(ValueError, match="Unsupported proxy scheme"):
            PinnedAsyncHTTPTransport({}, proxy="ftp://proxy:21")


# ---------------------------------------------------------------------------
# 8. xbot/tools/cron.py
# ---------------------------------------------------------------------------

from xbot.tools.cron import CronTool
from xbot.runtime.system.cron.types import CronJobState, CronSchedule


class TestCronToolFormatTiming:
    def test_cron_kind(self):
        s = CronSchedule(kind="cron", expr="0 9 * * *", tz="UTC")
        assert CronTool._format_timing(s) == "cron: 0 9 * * * (UTC)"

    def test_cron_kind_no_tz(self):
        s = CronSchedule(kind="cron", expr="0 9 * * *")
        assert CronTool._format_timing(s) == "cron: 0 9 * * *"

    def test_every_hours(self):
        s = CronSchedule(kind="every", every_ms=7_200_000)
        assert CronTool._format_timing(s) == "every 2h"

    def test_every_minutes(self):
        s = CronSchedule(kind="every", every_ms=300_000)
        assert CronTool._format_timing(s) == "every 5m"

    def test_every_seconds(self):
        s = CronSchedule(kind="every", every_ms=30_000)
        assert CronTool._format_timing(s) == "every 30s"

    def test_every_ms(self):
        s = CronSchedule(kind="every", every_ms=500)
        assert CronTool._format_timing(s) == "every 500ms"

    def test_at_kind(self):
        s = CronSchedule(kind="at", at_ms=1700000000000)
        out = CronTool._format_timing(s)
        assert out.startswith("at ")

    def test_fallback_kind(self):
        s = CronSchedule(kind="unknown")
        assert CronTool._format_timing(s) == "unknown"


class TestCronToolFormatState:
    def test_with_last_and_next(self):
        state = CronJobState(
            last_run_at_ms=1700000000000,
            last_status="ok",
            next_run_at_ms=1700003600000,
        )
        lines = CronTool._format_state(state)
        assert any("Last run" in l for l in lines)
        assert any("Next run" in l for l in lines)

    def test_with_last_error(self):
        state = CronJobState(
            last_run_at_ms=1700000000000,
            last_status="error",
            last_error="timeout",
        )
        lines = CronTool._format_state(state)
        assert any("timeout" in l for l in lines)

    def test_no_state(self):
        state = CronJobState()
        lines = CronTool._format_state(state)
        assert lines == []


class TestCronToolExecute:
    @pytest.mark.asyncio
    async def test_unknown_action(self):
        cron_service = MagicMock()
        tool = CronTool(cron_service)
        result = await tool.execute("bogus")
        assert "Unknown action" in result

    @pytest.mark.asyncio
    async def test_add_no_message(self):
        cron_service = MagicMock()
        tool = CronTool(cron_service)
        result = await tool.execute("add", message="")
        assert "message is required" in result

    @pytest.mark.asyncio
    async def test_add_no_context(self):
        cron_service = MagicMock()
        tool = CronTool(cron_service)
        result = await tool.execute("add", message="remind me")
        assert "no session context" in result

    @pytest.mark.asyncio
    async def test_add_with_cron_context(self):
        cron_service = MagicMock()
        tool = CronTool(cron_service)
        token = tool.set_cron_context(True)
        try:
            result = await tool.execute("add", message="test")
            assert "cannot schedule" in result
        finally:
            tool.reset_cron_context(token)

    @pytest.mark.asyncio
    async def test_add_tz_without_cron_expr(self):
        cron_service = MagicMock()
        tool = CronTool(cron_service)
        tool.set_context("ch", "cid")
        result = await tool.execute("add", message="test", tz="UTC")
        assert "tz can only be used with cron_expr" in result

    @pytest.mark.asyncio
    async def test_add_invalid_timezone(self):
        cron_service = MagicMock()
        tool = CronTool(cron_service)
        tool.set_context("ch", "cid")
        result = await tool.execute("add", message="test", cron_expr="0 9 * * *", tz="Fake/Zone")
        assert "unknown timezone" in result

    @pytest.mark.asyncio
    async def test_add_no_schedule(self):
        cron_service = MagicMock()
        tool = CronTool(cron_service)
        tool.set_context("ch", "cid")
        result = await tool.execute("add", message="test")
        assert "is required" in result

    @pytest.mark.asyncio
    async def test_add_invalid_at_format(self):
        cron_service = MagicMock()
        tool = CronTool(cron_service)
        tool.set_context("ch", "cid")
        result = await tool.execute("add", message="test", at="not-a-date")
        assert "invalid ISO datetime" in result

    @pytest.mark.asyncio
    async def test_list_no_jobs(self):
        cron_service = MagicMock()
        cron_service.list_jobs.return_value = []
        tool = CronTool(cron_service)
        result = await tool.execute("list")
        assert "No scheduled jobs" in result

    @pytest.mark.asyncio
    async def test_remove_no_job_id(self):
        cron_service = MagicMock()
        tool = CronTool(cron_service)
        result = await tool.execute("remove")
        assert "job_id is required" in result

    @pytest.mark.asyncio
    async def test_remove_not_found(self):
        cron_service = MagicMock()
        cron_service.remove_job.return_value = False
        tool = CronTool(cron_service)
        result = await tool.execute("remove", job_id="xyz")
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_remove_success(self):
        cron_service = MagicMock()
        cron_service.remove_job.return_value = True
        tool = CronTool(cron_service)
        result = await tool.execute("remove", job_id="j1")
        assert "Removed" in result

    @pytest.mark.asyncio
    async def test_remove_with_conversation_store(self):
        cron_service = MagicMock()
        cron_service.remove_job.return_value = True
        conv_store = MagicMock()
        tool = CronTool(cron_service, conversation_store=conv_store)
        result = await tool.execute("remove", job_id="j1")
        assert "Removed" in result
        conv_store.delete.assert_called_once_with("cron:j1")

    @pytest.mark.asyncio
    async def test_add_every_seconds(self):
        cron_service = MagicMock()
        job = MagicMock()
        job.name = "remind"
        job.id = "abc"
        cron_service.add_job.return_value = job
        tool = CronTool(cron_service)
        tool.set_context("ch", "cid")
        result = await tool.execute("add", message="remind me", every_seconds=60)
        assert "Created job" in result
        assert "abc" in result

    @pytest.mark.asyncio
    async def test_add_at_with_tz(self):
        cron_service = MagicMock()
        job = MagicMock()
        job.name = "x"
        job.id = "id1"
        cron_service.add_job.return_value = job
        tool = CronTool(cron_service)
        tool.set_context("ch", "cid")
        result = await tool.execute("add", message="test", at="2026-12-01T10:00:00")
        assert "Created job" in result


class TestCronToolContext:
    def test_set_and_resolve_context(self):
        tool = CronTool(MagicMock())
        tool.set_context("telegram", "12345", session_key="s1")
        tool.set_active_session("s1")
        ch, cid = tool._resolve_context()
        assert ch == "telegram"
        assert cid == "12345"

    def test_clear_context(self):
        tool = CronTool(MagicMock())
        tool.set_context("ch", "cid", session_key="s1")
        tool.clear_context("s1")
        assert "s1" not in tool._contexts

    def test_fallback_to_global(self):
        tool = CronTool(MagicMock())
        tool.set_context("ch", "cid")
        tool.set_active_session("nonexistent")
        ch, cid = tool._resolve_context()
        assert ch == "ch"
        assert cid == "cid"


# ---------------------------------------------------------------------------
# 9. xbot/interfaces/gateway/session_keys.py
# ---------------------------------------------------------------------------

from xbot.interfaces.gateway.session_keys import (
    runtime_route_from_session_key,
    to_internal_session_key,
)


class TestToInternalSessionKey:
    def test_empty_string(self):
        key = to_internal_session_key("")
        assert key.startswith("web:admin:")

    def test_whitespace_only(self):
        key = to_internal_session_key("   ")
        assert key.startswith("web:admin:")

    def test_known_namespace_web(self):
        assert to_internal_session_key("web:user1") == "web:user1"

    def test_known_namespace_app(self):
        assert to_internal_session_key("app:user1") == "app:user1"

    def test_known_namespace_cli(self):
        assert to_internal_session_key("cli:admin") == "cli:admin"

    def test_known_namespace_im(self):
        assert to_internal_session_key("im:telegram:123") == "im:telegram:123"

    def test_known_namespace_cron(self):
        assert to_internal_session_key("cron:job1") == "cron:job1"

    def test_heartbeat(self):
        assert to_internal_session_key("heartbeat") == "heartbeat"

    def test_im_channel_gets_prefixed(self):
        from xbot.platform.bus.events import IM_CHANNELS

        # Use the first available IM channel
        if IM_CHANNELS:
            channel = next(iter(IM_CHANNELS))
            key = to_internal_session_key(f"{channel}:chat123")
            assert key == f"im:{channel}:chat123"

    def test_unknown_passthrough(self):
        assert to_internal_session_key("custom:stuff") == "custom:stuff"


class TestRuntimeRouteFromSessionKey:
    def test_web_namespace(self):
        ns, user = runtime_route_from_session_key("web:user1", "fallback")
        assert ns == "web"
        assert user == "user1"

    def test_app_namespace(self):
        ns, user = runtime_route_from_session_key("app:admin", "fallback")
        assert ns == "app"
        assert user == "admin"

    def test_cli_namespace(self):
        ns, user = runtime_route_from_session_key("cli:local", "fallback")
        assert ns == "cli"
        assert user == "local"

    def test_im_namespace(self):
        ns, user = runtime_route_from_session_key("im:telegram:12345", "fallback")
        assert ns == "telegram"
        assert user == "12345"

    def test_empty_fallback(self):
        ns, user = runtime_route_from_session_key("", "default_user")
        assert ns == "web"
        assert user == "default_user"

    def test_unknown_namespace_fallback(self):
        ns, user = runtime_route_from_session_key("bogus", "fallback")
        assert ns == "web"
        assert user == "fallback"

    def test_im_without_chat_id(self):
        ns, user = runtime_route_from_session_key("im:telegram", "fallback")
        # "telegram" has no sub-partition so provider_sep is empty
        assert ns == "web"
        assert user == "fallback"

    def test_web_empty_rest(self):
        ns, user = runtime_route_from_session_key("web:", "fallback")
        assert ns == "web"
        assert user == "fallback"


# ---------------------------------------------------------------------------
# 10. xbot/crew/agent_pool.py
# ---------------------------------------------------------------------------

from xbot.crew.agent_pool import AgentPool, TaskProgress


class TestTaskProgress:
    def test_defaults(self):
        p = TaskProgress()
        assert p.delta_content == ""
        assert p.total_content == ""
        assert p.is_final is False

    def test_custom_values(self):
        p = TaskProgress(delta_content="hi", total_content="hi world", is_final=True)
        assert p.delta_content == "hi"
        assert p.is_final is True


class TestAgentPoolBuildRoleConfig:
    def test_inherits_model(self):
        from xbot.crew.models import AgentRole, CrewConfig

        xbot_config = MagicMock()
        agents_config = MagicMock()
        agents_config.defaults.model = "gpt-4"
        agents_config.model_copy.return_value = agents_config
        xbot_config.agents = agents_config

        crew_config = MagicMock()
        role = AgentRole(name="dev", description="d", goal="g", model="inherit", max_iterations=50)
        pool = AgentPool(crew_config, xbot_config, MagicMock())
        result = pool._build_role_config(role)
        # Model stays as global default since role says "inherit"
        assert result.defaults.model == "gpt-4"

    def test_overrides_model(self):
        from xbot.crew.models import AgentRole, CrewConfig

        xbot_config = MagicMock()
        agents_config = MagicMock()
        agents_config.defaults.model = "gpt-4"
        agents_config.model_copy.return_value = agents_config
        xbot_config.agents = agents_config

        crew_config = MagicMock()
        role = AgentRole(name="dev", description="d", goal="g", model="claude-3", max_iterations=100)
        pool = AgentPool(crew_config, xbot_config, MagicMock())
        result = pool._build_role_config(role)
        assert result.defaults.model == "claude-3"
        assert result.claude_sdk.max_turns == 100


class TestAgentPoolGetRoles:
    def test_get_initialised_roles(self):
        pool = AgentPool(MagicMock(), MagicMock(), MagicMock())
        pool._backends = {"dev": MagicMock(), "test": MagicMock()}
        assert set(pool.get_initialised_roles()) == {"dev", "test"}

    def test_get_failed_roles(self):
        pool = AgentPool(MagicMock(), MagicMock(), MagicMock())
        pool._failed_roles = {"bad": "error msg"}
        result = pool.get_failed_roles()
        assert result == {"bad": "error msg"}
        # Ensure it's a copy
        result["new"] = "x"
        assert "new" not in pool._failed_roles


class TestAgentPoolRunTaskStreaming:
    @pytest.mark.asyncio
    async def test_role_not_found_raises(self):
        pool = AgentPool(MagicMock(), MagicMock(), MagicMock())
        with pytest.raises(KeyError, match="not initialised"):
            async for _ in pool.run_task_streaming("missing", "prompt", "s1"):
                pass

    @pytest.mark.asyncio
    async def test_role_failed_raises_with_error(self):
        pool = AgentPool(MagicMock(), MagicMock(), MagicMock())
        pool._failed_roles = {"bad_role": "init failed"}
        with pytest.raises(KeyError, match="init failed"):
            async for _ in pool.run_task_streaming("bad_role", "prompt", "s1"):
                pass

    @pytest.mark.asyncio
    async def test_streaming_yields_progress(self):
        pool = AgentPool(MagicMock(), MagicMock(), MagicMock())
        mock_backend = MagicMock()

        # Mock process to yield some responses
        async def mock_process(ctx):
            yield SimpleNamespace(is_delta=True, delta_content="hello ", content="hello ", is_final=False)
            yield SimpleNamespace(is_delta=True, delta_content="world", content="hello world", is_final=False)

        mock_backend.process = mock_process
        pool._backends = {"dev": mock_backend}

        events = []
        async for progress in pool.run_task_streaming("dev", "do stuff", "s1"):
            events.append(progress)

        # Should have at least the delta events + final event
        assert len(events) >= 2
        assert events[-1].is_final is True
        assert events[-1].total_content == "hello world"

    @pytest.mark.asyncio
    async def test_run_task_collects_full_response(self):
        pool = AgentPool(MagicMock(), MagicMock(), MagicMock())
        mock_backend = MagicMock()

        async def mock_process(ctx):
            yield SimpleNamespace(is_delta=True, delta_content="part1", content="part1", is_final=False)
            yield SimpleNamespace(is_delta=True, delta_content="part2", content="part1part2", is_final=False)

        mock_backend.process = mock_process
        pool._backends = {"dev": mock_backend}

        result = await pool.run_task("dev", "prompt", "s1")
        assert result == "part1part2"


class TestAgentPoolShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_all_backends(self):
        pool = AgentPool(MagicMock(), MagicMock(), MagicMock())
        b1 = AsyncMock()
        b2 = AsyncMock()
        pool._backends = {"r1": b1, "r2": b2}
        await pool.shutdown()
        b1.shutdown.assert_called_once()
        b2.shutdown.assert_called_once()
        assert pool._backends == {}

    @pytest.mark.asyncio
    async def test_shutdown_handles_error(self):
        pool = AgentPool(MagicMock(), MagicMock(), MagicMock())
        b1 = AsyncMock()
        b1.shutdown.side_effect = RuntimeError("fail")
        pool._backends = {"r1": b1}
        await pool.shutdown()  # should not raise
        assert pool._backends == {}

    @pytest.mark.asyncio
    async def test_shutdown_reraises_cancelled(self):
        pool = AgentPool(MagicMock(), MagicMock(), MagicMock())
        b1 = AsyncMock()
        b1.shutdown.side_effect = asyncio.CancelledError()
        pool._backends = {"r1": b1}
        with pytest.raises(asyncio.CancelledError):
            await pool.shutdown()


class TestAgentPoolInitialize:
    @pytest.mark.asyncio
    async def test_all_fail_raises(self):
        from xbot.crew.models import AgentRole, CrewConfig

        crew_config = MagicMock()
        crew_config.agents = {"dev": AgentRole(name="dev", description="d", goal="g", model="x", max_iterations=10)}
        crew_config.workspace = "/tmp/test"

        xbot_config = MagicMock()
        xbot_config.agents = MagicMock()

        pool = AgentPool(crew_config, xbot_config, MagicMock())

        with patch("xbot.runtime.core.service.AgentService") as MockService:
            MockService.return_value.initialize = AsyncMock(side_effect=RuntimeError("init fail"))
            with pytest.raises(RuntimeError, match="All backend"):
                await pool.initialize()

    @pytest.mark.asyncio
    async def test_partial_success_warns(self):
        from xbot.crew.models import AgentRole, CrewConfig

        crew_config = MagicMock()
        crew_config.agents = {
            "good": AgentRole(name="good", description="d", goal="g", model="x", max_iterations=10),
            "bad": AgentRole(name="bad", description="d", goal="g", model="y", max_iterations=10),
        }
        crew_config.workspace = "/tmp/test"
        xbot_config = MagicMock()
        xbot_config.agents = MagicMock()

        pool = AgentPool(crew_config, xbot_config, MagicMock())

        call_count = 0

        async def side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("bad role failed")

        with patch("xbot.runtime.core.service.AgentService") as MockService:
            MockService.return_value.initialize = AsyncMock(side_effect=side_effect)
            await pool.initialize()

        assert "good" in pool._backends or "bad" in pool._failed_roles
        assert len(pool._backends) >= 1
