"""Tests for agent hook handlers."""

from datetime import datetime
from unittest.mock import MagicMock


class TestCompactEvent:
    """Tests for CompactEvent dataclass."""

    def test_compact_event_creation(self) -> None:
        """Test creating a CompactEvent."""
        from xbot.runtime.core.hooks import CompactEvent

        event = CompactEvent(
            session_key="test:123",
            trigger="auto",
            messages_count=10,
            tokens_before=5000,
            timestamp=datetime.now(),
        )
        assert event.session_key == "test:123"
        assert event.trigger == "auto"
        assert event.messages_count == 10
        assert event.tokens_before == 5000
        assert event.tokens_after is None
        assert event.summary is None

    def test_compact_event_with_results(self) -> None:
        """Test CompactEvent with post-compaction results."""
        from xbot.runtime.core.hooks import CompactEvent

        event = CompactEvent(
            session_key="test:456",
            trigger="token_limit",
            messages_count=20,
            tokens_before=10000,
            timestamp=datetime.now(),
            tokens_after=3000,
            summary="Consolidated 15 messages",
        )
        assert event.tokens_after == 3000
        assert event.summary == "Consolidated 15 messages"


class TestCompactHookHandler:
    """Tests for CompactHookHandler."""

    def test_handler_disabled(self) -> None:
        """Test that disabled handler returns None."""
        from xbot.runtime.core.hooks import CompactHookHandler

        handler = CompactHookHandler(enabled=False)
        # Create mock input and context
        # NOTE: session_id is in INPUT (PreCompactHookInput), not in context (HookContext)
        mock_input = MagicMock()
        mock_input.messages = []
        mock_input.token_count = 0
        mock_input.trigger = "auto"
        mock_input.session_id = "test_session"  # session_id is in input
        mock_context = MagicMock()
        mock_context.signal = None  # HookContext only has 'signal'

        import asyncio
        result = asyncio.run(handler(mock_input, None, mock_context))
        assert result is None

    def test_handler_enabled_returns_message(self) -> None:
        """Test that enabled handler returns notification message dict."""
        from xbot.runtime.core.hooks import CompactHookHandler

        handler = CompactHookHandler(enabled=True)
        # Create mock input and context
        # NOTE: session_id is in INPUT (PreCompactHookInput), not in context (HookContext)
        mock_input = MagicMock()
        mock_input.trigger = "auto"
        mock_input.session_id = "test_session"  # session_id is in input
        mock_context = MagicMock()
        mock_context.signal = None

        import asyncio
        result = asyncio.run(handler(mock_input, None, mock_context))
        assert result is not None
        assert isinstance(result, dict)
        assert "systemMessage" in result
        assert "Compressing context" in result["systemMessage"]
        assert "auto" in result["systemMessage"]

    def test_handler_stores_recent_events(self) -> None:
        """Test that handler stores recent events for debugging."""
        from xbot.runtime.core.hooks import CompactHookHandler

        handler = CompactHookHandler(enabled=True)
        mock_input = MagicMock()
        mock_input.trigger = "manual"
        mock_input.session_id = "test_session"  # session_id is in input
        mock_context = MagicMock()
        mock_context.signal = None

        import asyncio
        asyncio.run(handler(mock_input, None, mock_context))

        events = handler.get_recent_events()
        assert len(events) == 1
        assert events[0]["session_key"] == "test_session"
        assert events[0]["trigger"] == "manual"

    def test_handler_limits_recent_events(self) -> None:
        """Test that handler limits recent events to 50."""
        from xbot.runtime.core.hooks import CompactHookHandler

        handler = CompactHookHandler(enabled=True)

        # Simulate 60 events
        for i in range(60):
            mock_input = MagicMock()
            mock_input.trigger = "auto"
            mock_input.session_id = f"session_{i}"  # session_id is in input
            mock_context = MagicMock()
            mock_context.signal = None

            import asyncio
            asyncio.run(handler(mock_input, None, mock_context))

        # Internal list should be limited to 50
        assert len(handler._recent_events) == 50

        # get_recent_events() returns last 10 by default
        events = handler.get_recent_events()
        assert len(events) == 10

        # Can request more
        all_events = handler.get_recent_events(limit=60)
        assert len(all_events) == 50

    def test_handler_zero_tokens(self) -> None:
        """Test handler returns proper message format."""
        from xbot.runtime.core.hooks import CompactHookHandler

        handler = CompactHookHandler(enabled=True)
        mock_input = MagicMock()
        mock_input.trigger = "auto"
        mock_input.session_id = "test_session"  # session_id is in input
        mock_context = MagicMock()
        mock_context.signal = None

        import asyncio
        result = asyncio.run(handler(mock_input, None, mock_context))
        assert isinstance(result, dict)
        assert "systemMessage" in result
        assert "Compressing context" in result["systemMessage"]

    def test_handler_tolerates_broken_get_on_input_object(self) -> None:
        """Input objects with broken .get() should still be handled safely."""
        from xbot.runtime.core.hooks import CompactHookHandler

        class _BrokenInput:
            session_id = "sess-1"
            trigger = "auto"

            def get(self, *_args, **_kwargs):  # pragma: no cover - defensive path
                raise RuntimeError("broken get")

        handler = CompactHookHandler(enabled=True)
        mock_context = MagicMock()
        mock_context.signal = None

        import asyncio
        result = asyncio.run(handler(_BrokenInput(), None, mock_context))

        assert isinstance(result, dict)
        assert "systemMessage" in result


class TestBuildCompactHook:
    """Tests for build_compact_hook function."""

    def test_build_compact_hook_enabled(self) -> None:
        """Test building hook configuration when enabled."""
        from xbot.runtime.core.hooks import build_compact_hook

        hooks = build_compact_hook(enabled=True)
        assert hooks is not None
        assert "PreCompact" in hooks
        assert len(hooks["PreCompact"]) == 1
        assert "hooks" in hooks["PreCompact"][0]

    def test_build_compact_hook_disabled(self) -> None:
        """Test building hook configuration when disabled."""
        from xbot.runtime.core.hooks import build_compact_hook

        hooks = build_compact_hook(enabled=False)
        assert hooks == {}


class TestSubagentModelCompatHookHandler:
    """Tests for SubagentModelCompatHookHandler."""

    def test_rewrites_unsupported_typed_subagent_model(self) -> None:
        """Unsupported typed subagent model should keep type and use inherit."""
        from xbot.runtime.core.hooks import SubagentModelCompatHookHandler

        handler = SubagentModelCompatHookHandler(
            enabled=True,
            provider_name="alrun",
            is_model_supported=lambda model: model.lower() == "glm-5",
        )

        mock_input = {
            "session_id": "cli:direct",
            "tool_name": "Agent",
            "tool_input": {
                "description": "查询北京天气",
                "prompt": "查询今天北京天气",
                "model": "haiku",
                "subagent_type": "Explore",
            },
        }
        mock_context = MagicMock()

        import asyncio
        result = asyncio.run(handler(mock_input, None, mock_context))

        assert result is not None
        updated = result["hookSpecificOutput"]["updatedInput"]
        assert updated["model"] == "inherit"
        assert updated["subagent_type"] == "Explore"
        assert "Keeping subagent_type and falling back to model=inherit" in result["systemMessage"]

    def test_keeps_supported_typed_subagent_model(self) -> None:
        """Supported model should not be rewritten."""
        from xbot.runtime.core.hooks import SubagentModelCompatHookHandler

        handler = SubagentModelCompatHookHandler(
            enabled=True,
            provider_name="alrun",
            is_model_supported=lambda model: model.lower() in {"glm-5", "haiku"},
        )

        mock_input = {
            "session_id": "cli:direct",
            "tool_name": "Agent",
            "tool_input": {
                "model": "haiku",
                "subagent_type": "Explore",
            },
        }
        mock_context = MagicMock()

        import asyncio
        result = asyncio.run(handler(mock_input, None, mock_context))

        assert result is None

    def test_ignores_non_typed_agent_call(self) -> None:
        """No rewrite when subagent_type is absent."""
        from xbot.runtime.core.hooks import SubagentModelCompatHookHandler

        handler = SubagentModelCompatHookHandler(
            enabled=True,
            is_model_supported=lambda model: False,
        )

        mock_input = {
            "session_id": "cli:direct",
            "tool_name": "Agent",
            "tool_input": {
                "model": "haiku",
            },
        }
        mock_context = MagicMock()

        import asyncio
        result = asyncio.run(handler(mock_input, None, mock_context))

        assert result is None
