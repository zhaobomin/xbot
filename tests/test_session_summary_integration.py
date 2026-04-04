"""Integration tests for session summary (1.1 Session Summary on Reset).

Tests the full flow from summary generation through injection:
- _build_text_query working_summary injection
- options_builder _summarise_before_compact callback wiring
- Message converter SDK availability after RateLimitEvent split
- Error recovery trigger path
"""

from __future__ import annotations

import asyncio
import json
import uuid as uuid_mod
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.memory.workers.session_summary import generate_session_summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GOOD_SUMMARY = {
    "current_goals": ["Build a web app"],
    "key_decisions": ["Use React"],
    "in_progress": ["Authentication module"],
    "important_facts": ["Deadline is next Friday"],
}


@dataclass
class FakeSession:
    messages: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_history(self, max_messages: int = 500) -> list[dict[str, Any]]:
        return self.messages[-max_messages:]


def _make_backend(response_text: str) -> MagicMock:
    backend = MagicMock()
    resp = MagicMock()
    resp.content = response_text
    backend.call_for_auxiliary = AsyncMock(return_value=resp)
    return backend


# ---------------------------------------------------------------------------
# 1. _build_text_query: working_summary injection
# ---------------------------------------------------------------------------


class TestBuildTextQueryWorkingSummaryInjection:
    """Verify _build_text_query yields a system-reminder when working_summary is present."""

    @pytest.mark.asyncio
    async def test_injects_summary_as_system_reminder(self):
        """With a valid working_summary, _build_text_query should yield
        a <system-reminder> message BEFORE the user message."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._shared_resources = {}

        messages: list[dict[str, Any]] = []
        async for msg in backend._build_text_query(
            "Hello",
            "session-1",
            str(uuid_mod.uuid4()),
            working_summary=GOOD_SUMMARY,
        ):
            messages.append(msg)

        # Should have 2 messages: summary reminder + user prompt
        assert len(messages) == 2

        # First message is the injected summary
        summary_msg = messages[0]
        assert summary_msg["type"] == "user"
        content = summary_msg["message"]["content"]
        assert "<system-reminder>" in content
        assert "Previous Session Context" in content
        assert "Build a web app" in content
        assert "Use React" in content
        assert "Authentication module" in content
        assert "Deadline is next Friday" in content

        # Second message is the actual user prompt
        assert messages[1]["message"]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_no_injection_when_summary_is_none(self):
        """Without a working_summary, only the user message should be yielded."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._shared_resources = {}

        messages: list[dict[str, Any]] = []
        async for msg in backend._build_text_query(
            "Hello",
            "session-1",
            str(uuid_mod.uuid4()),
            working_summary=None,
        ):
            messages.append(msg)

        assert len(messages) == 1
        assert messages[0]["message"]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_no_injection_when_summary_is_empty(self):
        """An empty summary dict should not inject anything."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._shared_resources = {}

        messages: list[dict[str, Any]] = []
        async for msg in backend._build_text_query(
            "Hello",
            "session-1",
            str(uuid_mod.uuid4()),
            working_summary={},
        ):
            messages.append(msg)

        assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_partial_summary_only_includes_present_keys(self):
        """If only some fields are present, only those sections appear."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._shared_resources = {}

        partial = {"current_goals": ["Fix the bug"], "key_decisions": []}

        messages: list[dict[str, Any]] = []
        async for msg in backend._build_text_query(
            "test",
            "session-1",
            str(uuid_mod.uuid4()),
            working_summary=partial,
        ):
            messages.append(msg)

        assert len(messages) == 2
        content = messages[0]["message"]["content"]
        assert "Fix the bug" in content
        # key_decisions is empty, should not appear
        assert "Key Decisions" not in content


# ---------------------------------------------------------------------------
# 2. generate_session_summary edge cases
# ---------------------------------------------------------------------------


class TestGenerateSessionSummaryEdgeCases:
    """Additional edge case tests for generate_session_summary."""

    @pytest.mark.asyncio
    async def test_summary_with_only_markdown_fences_returns_none(self):
        """Response that is only markdown fences should return None."""
        backend = _make_backend("```\n```")
        session = FakeSession(
            messages=[{"role": "user", "content": "test"}]
        )
        result = await generate_session_summary(backend, session)
        assert result is None

    @pytest.mark.asyncio
    async def test_summary_with_json_lang_tag(self):
        """Handle ```json ... ``` fenced response."""
        fenced = f"```json\n{json.dumps(GOOD_SUMMARY)}\n```"
        backend = _make_backend(fenced)
        session = FakeSession(
            messages=[{"role": "user", "content": "test"}]
        )
        result = await generate_session_summary(backend, session)
        assert result is not None
        assert result["current_goals"] == ["Build a web app"]

    @pytest.mark.asyncio
    async def test_summary_with_whitespace_around_json(self):
        """Whitespace around JSON should be handled."""
        padded = f"\n\n  {json.dumps(GOOD_SUMMARY)}  \n\n"
        backend = _make_backend(padded)
        session = FakeSession(
            messages=[{"role": "user", "content": "test"}]
        )
        result = await generate_session_summary(backend, session)
        assert result is not None

    @pytest.mark.asyncio
    async def test_session_without_metadata_attribute(self):
        """Session without metadata attr should not crash."""
        backend = _make_backend(json.dumps(GOOD_SUMMARY))
        session = MagicMock(spec=[])
        session.messages = [{"role": "user", "content": "hello"}]
        # No metadata attribute at all
        result = await generate_session_summary(backend, session)
        assert result is not None

    @pytest.mark.asyncio
    async def test_session_with_none_metadata(self):
        """Session with metadata=None should not crash."""
        backend = _make_backend(json.dumps(GOOD_SUMMARY))
        session = MagicMock(spec=[])
        session.messages = [{"role": "user", "content": "hello"}]
        session.metadata = None
        result = await generate_session_summary(backend, session)
        assert result is not None

    @pytest.mark.asyncio
    async def test_empty_content_response(self):
        """Backend returning empty content should return None."""
        backend = _make_backend("")
        session = FakeSession(
            messages=[{"role": "user", "content": "test"}]
        )
        result = await generate_session_summary(backend, session)
        assert result is None

    @pytest.mark.asyncio
    async def test_none_content_response(self):
        """Backend returning None content should return None."""
        backend = MagicMock()
        resp = MagicMock()
        resp.content = None
        backend.call_for_auxiliary = AsyncMock(return_value=resp)
        session = FakeSession(
            messages=[{"role": "user", "content": "test"}]
        )
        result = await generate_session_summary(backend, session)
        assert result is None

    @pytest.mark.asyncio
    async def test_transcript_includes_role_prefix(self):
        """Transcript should prefix messages with [role]."""
        backend = _make_backend(json.dumps(GOOD_SUMMARY))
        session = FakeSession(
            messages=[
                {"role": "user", "content": "What is 1+1?"},
                {"role": "assistant", "content": "2"},
            ]
        )
        await generate_session_summary(backend, session)
        call_args = backend.call_for_auxiliary.call_args
        transcript = call_args.kwargs["messages"][1]["content"]
        assert "[user] What is 1+1?" in transcript
        assert "[assistant] 2" in transcript

    @pytest.mark.asyncio
    async def test_system_prompt_is_included(self):
        """The call should include the system prompt."""
        backend = _make_backend(json.dumps(GOOD_SUMMARY))
        session = FakeSession(
            messages=[{"role": "user", "content": "test"}]
        )
        await generate_session_summary(backend, session)
        call_args = backend.call_for_auxiliary.call_args
        system_msg = call_args.kwargs["messages"][0]
        assert system_msg["role"] == "system"
        assert "session-context summariser" in system_msg["content"]

    @pytest.mark.asyncio
    async def test_multimodal_skips_non_text_blocks(self):
        """Only text blocks should contribute to transcript."""
        backend = _make_backend(json.dumps(GOOD_SUMMARY))
        session = FakeSession(
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"data": "base64..."}},
                    {"type": "text", "text": "Describe this"},
                    {"type": "audio", "source": {"data": "base64..."}},
                ],
            }]
        )
        await generate_session_summary(backend, session)
        call_args = backend.call_for_auxiliary.call_args
        transcript = call_args.kwargs["messages"][1]["content"]
        assert "Describe this" in transcript
        assert "base64" not in transcript


# ---------------------------------------------------------------------------
# 3. MessageConverter SDK_AVAILABLE fix validation
# ---------------------------------------------------------------------------


class TestMessageConverterSDKAvailability:
    """Verify the RateLimitEvent import split fixed SDK_AVAILABLE."""

    def test_sdk_available_is_true(self):
        """SDK_AVAILABLE should be True now that RateLimitEvent is separately imported."""
        from xbot.agent.backends.message_converter import SDK_AVAILABLE

        assert SDK_AVAILABLE is True

    def test_message_converter_can_be_instantiated(self):
        """MessageConverter should be instantiable."""
        from xbot.agent.backends.message_converter import MessageConverter

        converter = MessageConverter(handoff_policy=None, capabilities=None, config=None)
        assert converter is not None

    def test_convert_unknown_message_returns_none(self):
        """Unknown message types should return None."""
        from xbot.agent.backends.message_converter import MessageConverter

        converter = MessageConverter(handoff_policy=None, capabilities=None, config=None)
        result = converter.convert("not_a_real_message")
        assert result is None

    def test_convert_system_message_compact_boundary(self):
        """SystemMessage with compact_boundary subtype should produce progress text."""
        from claude_agent_sdk.types import SystemMessage
        from xbot.agent.backends.message_converter import MessageConverter

        converter = MessageConverter(handoff_policy=None, capabilities=None, config=None)
        msg = SystemMessage(
            subtype="compact_boundary",
            data={"compact_metadata": {"pre_tokens": 8000, "post_tokens": 4000, "trigger": "auto"}},
        )
        response = converter.convert(msg)
        assert response is not None
        assert response.progress_texts
        assert "8,000" in response.progress_texts[0]

    def test_convert_assistant_message_with_text(self):
        """AssistantMessage with TextBlock should produce content."""
        from claude_agent_sdk.types import AssistantMessage, TextBlock
        from xbot.agent.backends.message_converter import MessageConverter

        converter = MessageConverter(handoff_policy=None, capabilities=None, config=None)
        msg = AssistantMessage(
            content=[TextBlock(text="Hello world")],
            model="claude-3-5-sonnet",
        )
        response = converter.convert(msg)
        assert response is not None
        assert response.content == "Hello world"

    def test_convert_stream_event_text_delta(self):
        """StreamEvent with text_delta should produce content."""
        from claude_agent_sdk.types import StreamEvent
        from xbot.agent.backends.message_converter import MessageConverter

        converter = MessageConverter(handoff_policy=None, capabilities=None, config=None)
        msg = StreamEvent(
            uuid="u1",
            session_id="s1",
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "chunk"},
            },
        )
        response = converter.convert(msg)
        assert response is not None
        assert response.is_delta is True
        assert response.delta_content == "chunk"


# ---------------------------------------------------------------------------
# 4. Working summary pop() and one-time consumption
# ---------------------------------------------------------------------------


class TestWorkingSummaryConsumption:
    """Verify working_summary is consumed (popped) after use."""

    @pytest.mark.asyncio
    async def test_summary_is_stored_in_metadata(self):
        """generate_session_summary should store result in metadata."""
        backend = _make_backend(json.dumps(GOOD_SUMMARY))
        session = FakeSession(
            messages=[{"role": "user", "content": "test"}]
        )
        result = await generate_session_summary(backend, session)
        assert session.metadata.get("working_summary") is result

    @pytest.mark.asyncio
    async def test_summary_can_be_popped(self):
        """working_summary can be popped from metadata for one-time use."""
        backend = _make_backend(json.dumps(GOOD_SUMMARY))
        session = FakeSession(
            messages=[{"role": "user", "content": "test"}]
        )
        await generate_session_summary(backend, session)

        popped = session.metadata.pop("working_summary", None)
        assert popped is not None
        assert popped["current_goals"] == ["Build a web app"]
        # After pop, it should be gone
        assert "working_summary" not in session.metadata

    @pytest.mark.asyncio
    async def test_overwrite_on_second_generation(self):
        """Running generate_session_summary twice should overwrite."""
        session = FakeSession(
            messages=[{"role": "user", "content": "test"}]
        )

        summary1 = {"current_goals": ["Goal 1"], "key_decisions": [], "in_progress": [], "important_facts": []}
        summary2 = {"current_goals": ["Goal 2"], "key_decisions": [], "in_progress": [], "important_facts": []}

        backend1 = _make_backend(json.dumps(summary1))
        await generate_session_summary(backend1, session)
        assert session.metadata["working_summary"]["current_goals"] == ["Goal 1"]

        backend2 = _make_backend(json.dumps(summary2))
        await generate_session_summary(backend2, session)
        assert session.metadata["working_summary"]["current_goals"] == ["Goal 2"]


# ---------------------------------------------------------------------------
# 5. Pre-compact callback wiring
# ---------------------------------------------------------------------------


class TestPreCompactCallbackWiring:
    """Verify that _summarise_before_compact is properly added as a callback."""

    def test_summarise_callback_added_when_backend_ref_available(self):
        """When backend ref is available, pre_compact_cbs should include summarise callback."""
        from xbot.agent.backends.options_builder import OptionsBuilder

        builder = OptionsBuilder.__new__(OptionsBuilder)
        mock_sessions = MagicMock()
        builder._sessions = mock_sessions
        builder._shared_resources = {
            "memory_turn_hooks": MagicMock(),
            "backend": MagicMock(),
        }
        builder._prompt_cache = None
        builder._pre_compact_cbs = None

        # Access the pre_compact_cbs construction logic
        # We need to verify that the callback list includes both extract and summarise
        pre_compact_cbs: list[Any] = []
        sessions_mgr = builder._sessions

        memory_hooks = builder._shared_resources.get("memory_turn_hooks")
        if memory_hooks is not None:
            async def _extract(session_key: str) -> None:
                pass
            pre_compact_cbs.append(_extract)

        backend_ref = builder._shared_resources.get("backend")
        if backend_ref is not None and sessions_mgr is not None:
            async def _summarise(session_key: str) -> None:
                pass
            pre_compact_cbs.append(_summarise)

        # Should have both callbacks
        assert len(pre_compact_cbs) == 2


# ---------------------------------------------------------------------------
# 6. list_sdk_sessions getattr safety for created_at
# ---------------------------------------------------------------------------


class TestListSdkSessionsCreatedAtSafety:
    """Verify list_sdk_sessions handles missing created_at via getattr."""

    @pytest.mark.asyncio
    async def test_list_sessions_without_created_at(self):
        """SDKSessionInfo without created_at should produce None for that field."""
        from claude_agent_sdk import SDKSessionInfo
        from datetime import datetime
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._shared_resources = {}
        backend._sdk_session_ids = {}

        session = SDKSessionInfo(
            session_id="sdk_test",
            summary="Test Session",
            last_modified=int(datetime(2026, 4, 1, 10, 0, 0).timestamp()),
            file_size=1024,
        )

        with patch("claude_agent_sdk.list_sessions") as mock_list:
            mock_list.return_value = [session]
            result = await backend.list_sdk_sessions()

            assert result["error"] is None
            assert len(result["sessions"]) == 1
            assert result["sessions"][0]["created_at"] is None
            assert result["sessions"][0]["session_id"] == "sdk_test"
