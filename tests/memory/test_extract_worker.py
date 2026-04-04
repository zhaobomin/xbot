import asyncio
from pathlib import Path

import pytest

from xbot.memory.workers.extract_memories import ExtractMemoriesWorker


def _msg(role: str, content: str, uuid: str | None = None) -> dict[str, str]:
    m: dict[str, str] = {"role": role, "content": content}
    if uuid is not None:
        m["uuid"] = uuid
    return m


@pytest.mark.asyncio
async def test_extract_worker_uses_cursor_and_only_sends_new_messages(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    async def runner(session_key: str, messages: list[dict[str, str]], direct_memory_write: bool) -> bool:
        calls.append([m["content"] for m in messages])
        return True

    worker = ExtractMemoriesWorker(tmp_path, runner=runner)
    messages = [_msg("user", "one"), _msg("assistant", "two")]
    await worker.request_run("telegram:1", messages=messages)
    await worker.request_run("telegram:1", messages=messages + [_msg("user", "three")])

    assert calls == [["one", "two"], ["three"]]


@pytest.mark.asyncio
async def test_extract_worker_skips_runner_on_direct_memory_write_but_advances_cursor(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    async def runner(session_key: str, messages: list[dict[str, str]], direct_memory_write: bool) -> bool:
        calls.append([m["content"] for m in messages])
        return True

    worker = ExtractMemoriesWorker(tmp_path, runner=runner)
    messages = [_msg("user", "one"), _msg("assistant", "two")]
    await worker.request_run("telegram:1", messages=messages, direct_memory_write=True)
    await worker.request_run("telegram:1", messages=messages + [_msg("user", "three")])

    assert calls == [["three"]]


@pytest.mark.asyncio
async def test_extract_worker_coalesces_trailing_run(tmp_path: Path) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[list[str]] = []

    async def runner(session_key: str, messages: list[dict[str, str]], direct_memory_write: bool) -> bool:
        calls.append([m["content"] for m in messages])
        started.set()
        await release.wait()
        return True

    worker = ExtractMemoriesWorker(tmp_path, runner=runner)
    first = [_msg("user", "one"), _msg("assistant", "two")]
    second = first + [_msg("user", "three")]

    task1 = asyncio.create_task(worker.request_run("telegram:1", messages=first))
    await started.wait()
    task2 = asyncio.create_task(worker.request_run("telegram:1", messages=second))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(task1, task2)

    assert calls == [["one", "two"], ["three"]]


@pytest.mark.asyncio
async def test_extract_worker_does_not_advance_cursor_on_runner_failure(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    async def runner(session_key: str, messages: list[dict[str, str]], direct_memory_write: bool) -> bool:
        calls.append([m["content"] for m in messages])
        return False

    worker = ExtractMemoriesWorker(tmp_path, runner=runner)
    messages = [_msg("user", "one"), _msg("assistant", "two")]

    await worker.request_run("telegram:1", messages=messages)
    await worker.request_run("telegram:1", messages=messages)

    assert calls == [["one", "two"], ["one", "two"]]
    state = worker._load_state()
    assert state["sessions"]["telegram:1"]["cursor"] == 0
    assert state["sessions"]["telegram:1"]["failures"] == 2


@pytest.mark.asyncio
async def test_extract_worker_uuid_cursor_tracks_position(tmp_path: Path) -> None:
    """UUID cursor should anchor new-message slicing even if integer offset is stale."""
    calls: list[list[str]] = []

    async def runner(session_key: str, messages: list[dict[str, str]], direct_memory_write: bool) -> bool:
        calls.append([m["content"] for m in messages])
        return True

    worker = ExtractMemoriesWorker(tmp_path, runner=runner)
    messages = [
        _msg("user", "one", uuid="uuid-1"),
        _msg("assistant", "two", uuid="uuid-2"),
    ]
    await worker.request_run("telegram:1", messages=messages)

    state = worker._load_state()
    assert state["sessions"]["telegram:1"]["cursor_uuid"] == "uuid-2"

    # Second turn: add new messages. UUID cursor anchors to uuid-2.
    messages2 = messages + [
        _msg("user", "three", uuid="uuid-3"),
        _msg("assistant", "four", uuid="uuid-4"),
    ]
    await worker.request_run("telegram:1", messages=messages2)
    assert calls == [["one", "two"], ["three", "four"]]

    state2 = worker._load_state()
    assert state2["sessions"]["telegram:1"]["cursor_uuid"] == "uuid-4"


@pytest.mark.asyncio
async def test_extract_worker_uuid_cursor_survives_message_insertion(tmp_path: Path) -> None:
    """If a message is inserted before the cursor, UUID-based cursor still works correctly."""
    calls: list[list[str]] = []

    async def runner(session_key: str, messages: list[dict[str, str]], direct_memory_write: bool) -> bool:
        calls.append([m["content"] for m in messages])
        return True

    worker = ExtractMemoriesWorker(tmp_path, runner=runner)
    original = [
        _msg("user", "one", uuid="uuid-1"),
        _msg("assistant", "two", uuid="uuid-2"),
    ]
    await worker.request_run("s1", messages=original)

    # Simulate an insertion before the cursor position (e.g. system message prepended)
    shifted = [
        _msg("system", "injected", uuid="uuid-0"),
        _msg("user", "one", uuid="uuid-1"),
        _msg("assistant", "two", uuid="uuid-2"),
        _msg("user", "three", uuid="uuid-3"),
    ]
    await worker.request_run("s1", messages=shifted)

    # Integer cursor would be 2 -> slice from index 2 = ["two", "three"] (wrong)
    # UUID cursor anchors to uuid-2 -> slice after it = ["three"] (correct)
    assert calls == [["one", "two"], ["three"]]


@pytest.mark.asyncio
async def test_extract_worker_falls_back_to_int_cursor_without_uuid(tmp_path: Path) -> None:
    """Messages without UUID should still work via integer cursor fallback."""
    calls: list[list[str]] = []

    async def runner(session_key: str, messages: list[dict[str, str]], direct_memory_write: bool) -> bool:
        calls.append([m["content"] for m in messages])
        return True

    worker = ExtractMemoriesWorker(tmp_path, runner=runner)
    messages = [_msg("user", "one"), _msg("assistant", "two")]
    await worker.request_run("s2", messages=messages)
    await worker.request_run("s2", messages=messages + [_msg("user", "three")])

    assert calls == [["one", "two"], ["three"]]
