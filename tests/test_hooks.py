"""Tests for agent hook handlers."""

from datetime import datetime
from unittest.mock import MagicMock


class TestCompactEvent:
    """Tests for CompactEvent dataclass."""

    def test_compact_event_creation(self) -> None:
        """Test creating a CompactEvent."""
        from xbot.agent.hooks import CompactEvent

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
        from xbot.agent.hooks import CompactEvent

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
        from xbot.agent.hooks import CompactHookHandler

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
        from xbot.agent.hooks import CompactHookHandler

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
        from xbot.agent.hooks import CompactHookHandler

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
        from xbot.agent.hooks import CompactHookHandler

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
        from xbot.agent.hooks import CompactHookHandler

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


class TestBuildCompactHook:
    """Tests for build_compact_hook function."""

    def test_build_compact_hook_enabled(self) -> None:
        """Test building hook configuration when enabled."""
        from xbot.agent.hooks import build_compact_hook

        hooks = build_compact_hook(enabled=True)
        assert hooks is not None
        assert "PreCompact" in hooks
        assert len(hooks["PreCompact"]) == 1
        assert "hooks" in hooks["PreCompact"][0]

    def test_build_compact_hook_disabled(self) -> None:
        """Test building hook configuration when disabled."""
        from xbot.agent.hooks import build_compact_hook

        hooks = build_compact_hook(enabled=False)
        assert hooks == {}
