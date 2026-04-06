from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from xbot.agent.state.store import SessionStore


@pytest.mark.asyncio
async def test_session_state_adapter_prefers_session_store_for_context_and_sdk_id() -> None:
    from xbot.agent.state.session_state_adapter import SessionStateAdapter

    store = SessionStore()
    shared_resources: dict[str, object] = {"_session_contexts": {}}
    adapter = SessionStateAdapter(
        session_store=store,
        use_session_store=True,
        shared_resources=shared_resources,
        sessions=None,
    )

    adapter.set_context("telegram:1", "telegram", "chat-1")
    await adapter.set_sdk_session_id("telegram:1", "sdk-1")

    entry = store.get("telegram:1")
    assert entry is not None
    assert entry.channel == "telegram"
    assert entry.chat_id == "chat-1"
    assert entry.sdk_session_id == "sdk-1"
    assert adapter.get_context_by_session_key("telegram:1") == ("telegram", "chat-1")
    assert adapter.get_context_by_sdk_id("sdk-1") == ("telegram", "chat-1")


@pytest.mark.asyncio
async def test_session_state_adapter_syncs_persistent_metadata_when_sdk_id_changes() -> None:
    from xbot.agent.state.session_state_adapter import SessionStateAdapter

    store = SessionStore()
    session = MagicMock()
    session.metadata = {}
    sessions = MagicMock()
    sessions.get.return_value = session

    adapter = SessionStateAdapter(
        session_store=store,
        use_session_store=True,
        shared_resources={"_session_contexts": {}},
        sessions=sessions,
    )

    adapter.set_context("cli:1", "cli", "chat-1")
    await adapter.set_sdk_session_id("cli:1", "sdk-1")
    await adapter.set_sdk_session_id("cli:1", None)

    assert "sdk_session_id" not in session.metadata
    assert sessions.save.call_count == 2


def test_session_state_adapter_uses_store_backed_scalar_fields() -> None:
    from xbot.agent.state.session_state_adapter import SessionStateAdapter

    store = SessionStore()
    adapter = SessionStateAdapter(
        session_store=store,
        use_session_store=True,
        shared_resources={"_session_contexts": {}},
        sessions=None,
    )

    adapter.set_model("cli:2", "gpt-5")
    adapter.set_task_id("cli:2", "task-1")
    adapter.set_request_id("cli:2", "req-1")
    adapter.set_commands("cli:2", ["/help"])

    assert adapter.get_model("cli:2") == "gpt-5"
    assert adapter.get_task_id("cli:2") == "task-1"
    assert adapter.get_request_id("cli:2") == "req-1"
    assert adapter.get_commands("cli:2") == ["/help"]


@pytest.mark.asyncio
async def test_session_state_adapter_clear_context_removes_session_and_sdk_mappings() -> None:
    from xbot.agent.state.session_state_adapter import SessionStateAdapter

    store = SessionStore()
    adapter = SessionStateAdapter(
        session_store=store,
        use_session_store=True,
        shared_resources={"_session_contexts": {}},
        sessions=None,
    )

    adapter.set_context("cli:clear", "cli", "chat-clear")
    await adapter.set_sdk_session_id("cli:clear", "sdk-clear")
    adapter.set_sdk_context_mapping("sdk-clear", "cli", "chat-clear")

    adapter.clear_context("cli:clear")

    assert adapter.get_context_by_session_key("cli:clear") is None
    assert adapter.get_context_by_sdk_id("sdk-clear") is None


def test_session_state_adapter_enforces_legacy_context_limit() -> None:
    from xbot.agent.state.session_state_adapter import SessionStateAdapter

    shared_resources = {"_session_contexts": {}}
    adapter = SessionStateAdapter(
        session_store=None,
        use_session_store=False,
        shared_resources=shared_resources,
        sessions=None,
    )

    for i in range(4):
        adapter.set_context(f"session:{i}", "cli", str(i))

    adapter.enforce_legacy_context_limit(2)

    contexts = shared_resources["_session_contexts"]
    assert len(contexts) == 2
    assert "session:0" not in contexts
    assert "session:1" not in contexts


@pytest.mark.asyncio
async def test_session_state_adapter_resolves_compact_notification_target_via_sdk_id() -> None:
    from xbot.agent.state.session_state_adapter import SessionStateAdapter

    store = SessionStore()
    adapter = SessionStateAdapter(
        session_store=store,
        use_session_store=True,
        shared_resources={"_session_contexts": {}},
        sessions=None,
    )

    adapter.set_context("telegram:1", "telegram", "chat-1")
    await adapter.set_sdk_session_id("telegram:1", "sdk-1")
    adapter.set_sdk_context_mapping("sdk-1", "telegram", "chat-1")

    assert adapter.resolve_compact_notification_target("sdk-1") == (
        "telegram:1",
        "telegram",
        "chat-1",
    )


@pytest.mark.asyncio
async def test_session_state_adapter_clear_tracking_state_resets_store_and_legacy() -> None:
    from xbot.agent.state.session_state_adapter import SessionStateAdapter

    store = SessionStore()
    shared_resources = {"_session_contexts": {"cli:1": ("cli", "chat-1"), "sdk-1": ("cli", "chat-1")}}
    adapter = SessionStateAdapter(
        session_store=store,
        use_session_store=True,
        shared_resources=shared_resources,
        sessions=None,
    )

    adapter.set_context("cli:1", "cli", "chat-1")
    await adapter.set_sdk_session_id("cli:1", "sdk-1")
    adapter.set_model("cli:1", "gpt-5")
    adapter.set_task_id("cli:1", "task-1")
    adapter.set_request_id("cli:1", "req-1")
    adapter.set_commands("cli:1", ["/help"])
    adapter.set_client("cli:1", object())

    adapter.clear_tracking_state(
        "cli:1",
        sdk_session_id="sdk-1",
        clear_sdk_session_id=True,
        clear_context=True,
    )

    entry = store.get("cli:1")
    assert entry is not None
    assert entry.client is None
    assert entry.model == ""
    assert entry.commands == []
    assert entry.task_id is None
    assert entry.request_id is None
    assert entry.sdk_session_id is None
    assert adapter.get_context_by_session_key("cli:1") is None
    assert adapter.get_context_by_sdk_id("sdk-1") is None
