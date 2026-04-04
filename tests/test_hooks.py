"""Tests for agent hook handlers."""

import pytest
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

    def test_build_compact_hook_with_pre_compact_callbacks(self) -> None:
        """Test building hook with pre_compact_callbacks."""
        from xbot.agent.hooks import build_compact_hook

        cb = MagicMock()
        hooks = build_compact_hook(enabled=True, pre_compact_callbacks=[cb])
        assert "PreCompact" in hooks
        handler = hooks["PreCompact"][0]["hooks"][0]
        assert len(handler.pre_compact_callbacks) == 1


class TestCompactHookPreCompactCallbacks:
    """Tests for CompactHookHandler pre_compact_callbacks."""

    def test_pre_compact_callback_is_called(self) -> None:
        """Async pre_compact_callback should be invoked during __call__."""
        import asyncio
        from xbot.agent.hooks import CompactHookHandler

        called_with: list[str] = []

        async def track_cb(session_key: str) -> None:
            called_with.append(session_key)

        handler = CompactHookHandler(
            enabled=True, pre_compact_callbacks=[track_cb]
        )
        mock_input = MagicMock()
        mock_input.trigger = "auto"
        mock_input.session_id = "sess_abc"
        mock_context = MagicMock()
        mock_context.signal = None

        asyncio.run(handler(mock_input, None, mock_context))
        assert called_with == ["sess_abc"]

    def test_pre_compact_callback_error_does_not_block(self) -> None:
        """A failing callback must not prevent the hook from returning."""
        import asyncio
        from xbot.agent.hooks import CompactHookHandler

        async def bad_cb(session_key: str) -> None:
            raise RuntimeError("extraction exploded")

        handler = CompactHookHandler(
            enabled=True, pre_compact_callbacks=[bad_cb]
        )
        mock_input = MagicMock()
        mock_input.trigger = "auto"
        mock_input.session_id = "sess_fail"
        mock_context = MagicMock()
        mock_context.signal = None

        result = asyncio.run(handler(mock_input, None, mock_context))
        assert result is not None
        assert "systemMessage" in result

    def test_multiple_pre_compact_callbacks(self) -> None:
        """Multiple callbacks should all be invoked in order."""
        import asyncio
        from xbot.agent.hooks import CompactHookHandler

        order: list[int] = []

        async def cb1(sk: str) -> None:
            order.append(1)

        async def cb2(sk: str) -> None:
            order.append(2)

        handler = CompactHookHandler(
            enabled=True, pre_compact_callbacks=[cb1, cb2]
        )
        mock_input = MagicMock()
        mock_input.trigger = "auto"
        mock_input.session_id = "multi"
        mock_context = MagicMock()
        mock_context.signal = None

        asyncio.run(handler(mock_input, None, mock_context))
        assert order == [1, 2]


class TestMemoryTurnHooksForceExtract:
    """Tests for MemoryTurnHooks.force_extract()."""

    def test_force_extract_calls_extractor(self) -> None:
        """force_extract should delegate to extractor.request_run."""
        import asyncio
        from pathlib import Path
        from xbot.memory.integration.turn_hooks import MemoryTurnHooks

        mock_extractor = MagicMock()

        async def mock_run(sk, messages=None, direct_memory_write=False):
            pass
        mock_extractor.request_run = MagicMock(side_effect=mock_run)

        hooks = MemoryTurnHooks(
            Path("/tmp/test_ws"),
            extractor=mock_extractor,
            dreamer=MagicMock(),
        )
        asyncio.run(hooks.force_extract("test:session", messages=[{"role": "user", "content": "hi"}]))
        mock_extractor.request_run.assert_called_once_with(
            "test:session",
            messages=[{"role": "user", "content": "hi"}],
            direct_memory_write=False,
        )

    def test_force_extract_disabled_is_noop(self) -> None:
        """force_extract should be no-op when extract_enabled=False."""
        import asyncio
        from pathlib import Path
        from xbot.memory.integration.turn_hooks import MemoryTurnHooks

        mock_extractor = MagicMock()
        hooks = MemoryTurnHooks(
            Path("/tmp/test_ws"),
            extractor=mock_extractor,
            dreamer=MagicMock(),
            extract_enabled=False,
        )
        asyncio.run(hooks.force_extract("test:session"))
        mock_extractor.request_run.assert_not_called()

    def test_force_extract_error_is_swallowed(self) -> None:
        """force_extract should swallow exceptions."""
        import asyncio
        from pathlib import Path
        from xbot.memory.integration.turn_hooks import MemoryTurnHooks

        mock_extractor = MagicMock()

        async def explode(*a, **kw):
            raise RuntimeError("boom")
        mock_extractor.request_run = MagicMock(side_effect=explode)

        hooks = MemoryTurnHooks(
            Path("/tmp/test_ws"),
            extractor=mock_extractor,
            dreamer=MagicMock(),
        )
        # Should not raise
        asyncio.run(hooks.force_extract("test:session"))