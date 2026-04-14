from __future__ import annotations

import pytest

from xbot.runtime.state import RuntimeSessionRegistry, SessionEvent, SessionPhase


@pytest.mark.asyncio
async def test_registry_lifecycle_and_routing_resolution() -> None:
    reg = RuntimeSessionRegistry()
    key = "feishu:c1"

    assert reg.get(key) is None
    assert reg.get_phase(key) == SessionPhase.IDLE
    assert reg.has_session(key) is False

    state = reg.get_or_create(key)
    assert state.session_key == key
    assert reg.has_session(key) is True

    reg.set_routing(key, "feishu", "c1")
    assert reg.get_routing(key) == ("feishu", "c1")
    assert reg.get_context_by_session_key(key) == ("feishu", "c1")
    assert reg.resolve_routing(key) == (key, "feishu", "c1")
    assert reg.resolve_compact_notification_target(key) == (key, "feishu", "c1")

    await reg.cleanup_session(key)
    assert reg.has_session(key) is False


def test_registry_sdk_mapping_and_metadata_accessors() -> None:
    reg = RuntimeSessionRegistry()
    key = "feishu:c2"

    reg.set_context(key, "feishu", "c2")
    reg.set_execution_cwd(key, "/tmp/work")
    reg.set_workspace_dir(key, "/tmp/ws")
    reg.set_commands(key, ["/help", "/clear"])
    reg.set_sdk_capabilities(
        key,
        skills=["a", "b"],
        tools=["t1"],
        slash_commands=["/x"],
        skill_source="sdk_only",
    )
    reg.set_task_id(key, "task-1")
    reg.set_request_id(key, "req-1")

    assert reg.get_execution_cwd(key) == "/tmp/work"
    assert reg.get_workspace_dir(key) == "/tmp/ws"
    assert reg.get_commands(key) == ["/help", "/clear"]
    caps = reg.get_sdk_capabilities(key)
    assert caps["skills"] == ["a", "b"]
    assert caps["tools"] == ["t1"]
    assert caps["slash_commands"] == ["/x"]
    assert caps["skill_source"] == "sdk_only"
    assert reg.get_task_id(key) == "task-1"
    assert reg.get_request_id(key) == "req-1"

    reg._set_sdk_session_id_impl(key, "sdk-1")
    assert reg.resolve_sdk_session_id(key) == "sdk-1"
    by_sdk = reg.get_by_sdk_id("sdk-1")
    assert by_sdk is not None
    assert by_sdk.session_key == key
    assert reg.get_context_by_sdk_id("sdk-1") == ("feishu", "c2")
    assert reg.resolve_routing("sdk-1") == (key, "feishu", "c2")

    # overwrite mapping should evict old index
    reg._set_sdk_session_id_impl(key, "sdk-2")
    assert reg.get_by_sdk_id("sdk-1") is None
    assert reg.get_by_sdk_id("sdk-2") is not None

    reg.clear_context(key)
    assert reg.get_routing(key) == ("", "")
    reg.clear_all_contexts()
    assert reg.get_routing(key) == ("", "")


def test_registry_recovery_and_diagnostics_snapshot() -> None:
    reg = RuntimeSessionRegistry()
    key = "feishu:c3"

    reg.dispatch(key, SessionEvent.USER_MESSAGE, strict=False)
    reg.dispatch(key, SessionEvent.CLIENT_ACQUIRED, strict=False)
    reg.dispatch(key, SessionEvent.QUERY_SENT, strict=False)

    # illegal transition under strict mode should be counted
    ok = reg.dispatch(key, SessionEvent.CLIENT_ACQUIRED, strict=True)
    assert ok is False

    assert reg.note_recovery_failure(key) == 1
    assert reg.note_recovery_failure(key) == 2
    assert reg.get_recovery_failures(key) == 2
    reg.reset_recovery_failures(key)
    assert reg.get_recovery_failures(key) == 0

    check = reg.check_session(key)
    assert check["exists"] is True
    assert check["phase"] == SessionPhase.RECEIVING_STREAM.value
    assert check["illegal_transition_count"] >= 1

    snap = reg.snapshot()
    assert snap["sessions"] >= 1
    assert snap["illegal_transition_total"] >= 1
    assert isinstance(reg.list_keys(), list)
    assert isinstance(reg.list_sessions(), list)


@pytest.mark.asyncio
async def test_registry_delete_cleans_sdk_index() -> None:
    reg = RuntimeSessionRegistry()
    key = "feishu:c4"
    reg._set_sdk_session_id_impl(key, "sdk-del")
    assert reg.get_by_sdk_id("sdk-del") is not None
    deleted = await reg.delete(key)
    assert deleted is True
    assert reg.get_by_sdk_id("sdk-del") is None
