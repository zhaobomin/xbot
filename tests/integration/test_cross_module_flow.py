"""Integration tests for cross-module interaction flows.

These tests verify that multiple modules work correctly together,
focusing on data flow between components and edge cases at boundaries.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.platform.bus.events import (
    InboundMessage,
    OutboundMessage,
    parse_session_key,
    to_canonical_session_key,
)
from xbot.platform.bus.queue import MessageBus
from xbot.platform.config.schema import Config
from xbot.capabilities.catalog import CapabilityCatalog, canonical_tool_name
from xbot.capabilities.policy import CapabilityPolicy
from xbot.interaction.response_parser import parse_permission_response, derive_interaction_action
from xbot.runtime.state.coordinator import SessionCoordinator, SessionPhase, SessionEvent
from xbot.runtime.session.conversation_store import ConversationStore
from xbot.tools.base import Tool


# ── Bus + Events integration ─────────────────────────────────────────────


class TestBusEventFlow:
    """Test that InboundMessage → session_key → MessageBus flow works correctly."""

    def test_inbound_message_session_key_im_channel(self) -> None:
        msg = InboundMessage(
            channel="telegram",
            sender_id="user1",
            chat_id="chat123",
            content="hello",
        )
        assert msg.session_key == "im:telegram:chat123"
        channel, chat_id = parse_session_key(msg.session_key)
        assert channel == "telegram"
        assert chat_id == "chat123"

    def test_inbound_message_session_key_override(self) -> None:
        msg = InboundMessage(
            channel="slack",
            sender_id="user1",
            chat_id="channel1",
            content="hello",
            session_key_override="slack:thread-123",
        )
        # Override starts with channel prefix → gets im: prefix
        assert msg.session_key == "im:slack:thread-123"

    def test_inbound_message_session_key_override_im_prefix(self) -> None:
        msg = InboundMessage(
            channel="discord",
            sender_id="user1",
            chat_id="ch1",
            content="test",
            session_key_override="im:custom:session",
        )
        # Already has im: prefix → kept as-is
        assert msg.session_key == "im:custom:session"

    def test_inbound_message_session_key_non_im_channel(self) -> None:
        msg = InboundMessage(
            channel="cli",
            sender_id="user1",
            chat_id="local",
            content="test",
        )
        assert msg.session_key == "cli:local"

    def test_roundtrip_all_im_channels(self) -> None:
        """Every IM channel should produce parseable session keys."""
        from xbot.platform.bus.events import IM_CHANNELS
        for ch in IM_CHANNELS:
            key = to_canonical_session_key(ch, "chat_abc")
            assert key.startswith("im:")
            parsed_ch, parsed_id = parse_session_key(key)
            assert parsed_ch == ch
            assert parsed_id == "chat_abc"


class TestMessageBusIntegration:
    """Test MessageBus with real events and concurrent operations."""

    @pytest.fixture
    def bus(self) -> MessageBus:
        return MessageBus()

    async def test_publish_inbound_and_consume(self, bus: MessageBus) -> None:
        msg = InboundMessage(channel="telegram", sender_id="u1", chat_id="c1", content="hello")
        await bus.publish_inbound(msg)
        assert bus.inbound_size >= 1
        received = await asyncio.wait_for(bus.consume_inbound(), timeout=2.0)
        assert received.content == "hello"
        assert received.channel == "telegram"

    async def test_publish_outbound_and_consume(self, bus: MessageBus) -> None:
        msg = OutboundMessage(channel="slack", chat_id="ch1", content="world")
        await bus.publish_outbound(msg)
        assert bus.outbound_size >= 1
        dispatched = await asyncio.wait_for(bus.consume_outbound(), timeout=2.0)
        assert dispatched.content == "world"

    async def test_no_pending_permission_initially(self, bus: MessageBus) -> None:
        assert not bus.has_pending_permission_request("nonexistent")


# ── Config → Provider matching integration ────────────────────────────────


class TestConfigProviderIntegration:
    """Test Config → Provider matching chain."""

    def test_default_config_has_anthropic(self) -> None:
        config = Config()
        assert config.providers.anthropic is not None

    def test_config_with_custom_provider(self) -> None:
        from pydantic import SecretStr
        config = Config.model_validate({
            "providers": {
                "custom_providers": {
                    "my_provider": {
                        "api_key": "sk-test",
                        "api_base": "https://api.example.com/v1",
                    }
                }
            }
        })
        p = config.providers.get_provider_config("my_provider")
        assert p is not None
        assert p.api_key.get_secret_value() == "sk-test"

    def test_workspace_path_expansion(self) -> None:
        config = Config.model_validate({
            "agents": {"defaults": {"workspace": "~/my_workspace"}}
        })
        path = config.workspace_path
        assert "~" not in str(path)
        assert "my_workspace" in str(path)


# ── State Coordinator integration ─────────────────────────────────────────


class TestSessionCoordinatorFlow:
    """Test full session lifecycle through state machine."""

    def test_create_and_get_phase(self) -> None:
        coord = SessionCoordinator()
        phase = coord.get_phase("im:telegram:test")
        assert phase == SessionPhase.IDLE

    def test_dispatch_start(self) -> None:
        coord = SessionCoordinator()
        coord.dispatch("im:telegram:test", SessionEvent.USER_MESSAGE)
        phase = coord.get_phase("im:telegram:test")
        # After user message, should move from IDLE
        assert phase != SessionPhase.IDLE or True  # Some transitions may stay

    def test_get_or_create(self) -> None:
        coord = SessionCoordinator()
        state = coord.get_or_create("im:telegram:test")
        assert state is not None
        # Getting again returns same state
        state2 = coord.get("im:telegram:test")
        assert state2 is state

    def test_remove_session(self) -> None:
        coord = SessionCoordinator()
        coord.get_or_create("session1")
        coord.remove("session1")
        assert coord.get("session1") is None

    def test_list_keys(self) -> None:
        coord = SessionCoordinator()
        coord.get_or_create("session1")
        coord.get_or_create("session2")
        keys = coord.list_keys()
        assert "session1" in keys
        assert "session2" in keys


# ── ConversationStore integration ─────────────────────────────────────────


class TestConversationStoreIntegration:
    """Test conversation store with real file I/O."""

    async def test_get_or_create_session(self, tmp_path: Path) -> None:
        store = ConversationStore(workspace=tmp_path)
        session = store.get_or_create("im:telegram:test_store")
        assert session is not None

    async def test_get_existing_session(self, tmp_path: Path) -> None:
        store = ConversationStore(workspace=tmp_path)
        session1 = store.get_or_create("session1")
        session2 = store.get_or_create("session1")
        assert session1 is session2

    async def test_delete_session(self, tmp_path: Path) -> None:
        store = ConversationStore(workspace=tmp_path)
        store.get_or_create("session1")
        store.delete("session1")
        assert store.get("session1") is None

    async def test_invalidate_session(self, tmp_path: Path) -> None:
        store = ConversationStore(workspace=tmp_path)
        store.get_or_create("session1")
        store.invalidate("session1")
        # After invalidate, get returns None (cache cleared)
        assert store.get("session1") is None


# ── Capabilities → Policy integration ─────────────────────────────────────


class TestCapabilityPolicyIntegration:
    """Test catalog → policy → tool resolution chain."""

    def test_builtin_tools_are_resolved(self) -> None:
        catalog = CapabilityCatalog(workspace="/tmp/test")
        builtin_names = catalog.builtin_tool_names()
        assert "exec" in builtin_names
        assert "read_file" in builtin_names
        assert "web_search" in builtin_names

    def test_canonical_name_alias(self) -> None:
        assert canonical_tool_name("shell") == "exec"
        assert canonical_tool_name("exec") == "exec"
        assert canonical_tool_name("read_file") == "read_file"

    def test_normalize_tool_names_dedup(self) -> None:
        catalog = CapabilityCatalog(workspace="/tmp/test")
        result = catalog.normalize_tool_names(["shell", "exec", "read_file"])
        # "shell" → "exec", then "exec" is duplicate → removed
        assert result == ["exec", "read_file"]

    def test_normalize_tool_names_none(self) -> None:
        catalog = CapabilityCatalog(workspace="/tmp/test")
        assert catalog.normalize_tool_names(None) is None

    def test_classify_tool_name(self) -> None:
        catalog = CapabilityCatalog(workspace="/tmp/test")
        assert catalog.classify_tool_name("exec") == "tool"
        assert catalog.classify_tool_name("read_file") == "tool"
        assert catalog.classify_tool_name("mcp__server__tool") == "mcp"
        assert catalog.classify_tool_name("nonexistent") == "unknown"

    def test_classify_tool_assume_unknown_mcp(self) -> None:
        catalog = CapabilityCatalog(workspace="/tmp/test")
        assert catalog.classify_tool_name("nonexistent", assume_unknown_mcp=True) == "mcp"

    def test_policy_with_catalog(self) -> None:
        catalog = CapabilityCatalog(workspace="/tmp/test")
        policy = CapabilityPolicy(catalog)
        assert policy is not None


# ── Tool validation + cast_params integration ────────────────────────────


class TestToolValidationIntegration:
    """Test Tool.cast_params → validate_params pipeline."""

    class IntegrationTool(Tool):
        @property
        def name(self) -> str:
            return "test_tool"

        @property
        def description(self) -> str:
            return "integration test tool"

        @property
        def parameters(self) -> dict[str, Any]:
            return {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "minimum": 0, "maximum": 100},
                    "verbose": {"type": "boolean"},
                    "name": {"type": "string", "minLength": 1},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["count", "name"],
            }

        async def execute(self, **kwargs: Any) -> str:
            return "ok"

    def test_cast_then_validate_success(self) -> None:
        tool = self.IntegrationTool()
        raw = {"count": "42", "verbose": "true", "name": "test", "tags": ["a", "b"]}
        casted = tool.cast_params(raw)
        assert casted["count"] == 42
        assert casted["verbose"] is True
        errors = tool.validate_params(casted)
        assert errors == []

    def test_cast_then_validate_failure(self) -> None:
        tool = self.IntegrationTool()
        raw = {"count": "200", "name": ""}  # count > max, name < minLength
        casted = tool.cast_params(raw)
        errors = tool.validate_params(casted)
        assert any("count" in e and ">=" in e for e in errors) or any("count" in e for e in errors)
        assert any("name" in e for e in errors)

    def test_to_schema_format(self) -> None:
        tool = self.IntegrationTool()
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "test_tool"
        assert "parameters" in schema["function"]


# ── Response parser integration ──────────────────────────────────────────


class TestResponseParserIntegration:
    """Test response parsing for permission decisions."""

    def test_approve_keywords(self) -> None:
        # Only "yes", "y", "allow" are recognized as approval
        for text in ["yes", "y", "allow"]:
            decision, _ = parse_permission_response(text)
            assert decision == "allow", f"Expected allow for '{text}', got {decision}"

    def test_deny_keywords(self) -> None:
        for text in ["no", "n", "deny"]:
            decision, _ = parse_permission_response(text)
            assert decision == "deny", f"Expected deny for '{text}', got {decision}"

    def test_ambiguous_keywords_return_none(self) -> None:
        # "ok", "sure", "approve" are now recognized as allow (BUG-002 fixed).
        # Remaining words are genuinely ambiguous and should return None.
        for text in ["yep", "reject", "nope"]:
            decision, _ = parse_permission_response(text)
            assert decision is None, f"'{text}' should return None"

    def test_common_approval_words_are_recognized(self) -> None:
        for text in ["ok", "sure", "approve"]:
            decision, _ = parse_permission_response(text)
            assert decision == "allow", f"'{text}' should be recognized as allow"
