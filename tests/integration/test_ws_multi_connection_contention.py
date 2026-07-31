"""Integration tests: WS multi-connection contention and cleanup.

Verifies that the WebSocket gateway correctly handles:
1. Two connections contending for the same session slot (busy rejection).
2. Half-closed connections freeing slots via the finally-block cleanup.
3. Rapid open/close cycles not leaking task state in webui_active_tasks.
4. Shutdown (connection close) cancelling all WS tasks within a bounded timeout.

These tests use the real gateway create_app with controlled mock runtimes to
exercise the production code paths without relying on brittle internal wiring.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from xbot.interfaces.gateway.app import (
    _cancel_tasks_and_wait,
    _clear_login_rate_limit,
    _remove_active_task_if_current,
    create_app,
)
from xbot.interfaces.gateway.auth import set_password
from xbot.interfaces.gateway.services import ServiceContainer
from xbot.platform.bus.queue import MessageBus
from xbot.platform.config.schema import Config
from xbot.runtime.session.conversation_store import ConversationStore


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Mock runtimes
# ---------------------------------------------------------------------------

_TEST_PASSWORD = "test-ws-contention-pw"


class _FakeRuntime:
    """Minimal runtime that returns immediately."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.model = "test-model"
        self.router = type("R", (), {"backend_type": "test"})()
        self.tools = type("T", (), {"tool_names": []})()

    async def process_managed_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Any = None,
        media: list[str] | None = None,
    ) -> str:
        self.calls.append({"content": content, "session_key": session_key})
        return f"echo:{content}"

    def describe_runtime(self) -> str:
        return "test"

    async def reset_session(self, *args: Any, **kwargs: Any) -> None:
        pass


class _BlockingRuntime(_FakeRuntime):
    """Runtime that blocks until explicitly released via threading events.

    Used to hold a task in-flight so we can test contention scenarios.
    """

    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancelled = False

    async def process_managed_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Any = None,
        media: list[str] | None = None,
    ) -> str:
        self.calls.append({"content": content, "session_key": session_key})
        self.started.set()
        try:
            while not self.release.is_set():
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return f"done:{content}"


class _FailOnFirstCallRuntime(_FakeRuntime):
    """Runtime that raises on the first call but succeeds on subsequent ones.

    Simulates a half-closed connection causing an error mid-task, while
    allowing later calls to succeed (proving the slot was freed).
    """

    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0
        self.first_call_started = threading.Event()

    async def process_managed_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Any = None,
        media: list[str] | None = None,
    ) -> str:
        self.call_count += 1
        self.calls.append({"content": content, "session_key": session_key})
        if self.call_count == 1:
            self.first_call_started.set()
            # Simulate an error that would occur if the WS send path raises
            # (e.g., ConnectionResetError propagating from on_progress).
            raise ConnectionResetError("simulated half-closed WS send failure")
        return f"success:{content}"


class _SlowRuntime(_FakeRuntime):
    """Runtime that sleeps for a long time (simulating a long agent turn)."""

    def __init__(self, sleep_seconds: float = 60) -> None:
        super().__init__()
        self._sleep = sleep_seconds
        self.started_count = 0
        self.cancelled_count = 0
        self.all_started = threading.Event()
        self._expected_starts = 1

    def expect_starts(self, n: int) -> None:
        self._expected_starts = n

    async def process_managed_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Any = None,
        media: list[str] | None = None,
    ) -> str:
        self.calls.append({"content": content, "session_key": session_key})
        self.started_count += 1
        if self.started_count >= self._expected_starts:
            self.all_started.set()
        try:
            await asyncio.sleep(self._sleep)
        except asyncio.CancelledError:
            self.cancelled_count += 1
            raise
        return f"done:{content}"


# ---------------------------------------------------------------------------
# Fake services (minimal stubs)
# ---------------------------------------------------------------------------


class _FakeCronService:
    def list_jobs(self) -> list:
        return []

    def status(self) -> dict[str, int]:
        return {"jobs": 0}


class _FakeHeartbeatService:
    def __init__(self) -> None:
        self.enabled = False
        self.interval_s = 300
        self._running = False

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "interval_s": self.interval_s, "running": self._running}

    async def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled


# ---------------------------------------------------------------------------
# Test app builder
# ---------------------------------------------------------------------------


def _build_test_app(
    tmp_path: Path, runtime: _FakeRuntime | None = None
) -> tuple[TestClient, Any, _FakeRuntime]:
    """Build a test app with the specified runtime.

    Returns (client, fastapi_app, runtime).
    """
    import xbot.interfaces.gateway.auth as auth_module

    _clear_login_rate_limit()

    # Isolate password file per test
    test_pw_file = tmp_path / "test-password"
    test_pw_file.parent.mkdir(parents=True, exist_ok=True)
    auth_module.PASSWORD_FILE = test_pw_file
    set_password(_TEST_PASSWORD)

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    workspace = config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)

    conversation_store = ConversationStore(workspace)

    if runtime is None:
        runtime = _FakeRuntime()

    services = ServiceContainer(
        config=config,
        bus=MessageBus(),
        agent=runtime,
        conversation_store=conversation_store,
        cron=_FakeCronService(),
        heartbeat=_FakeHeartbeatService(),
    )

    app = create_app(services, data_dir=tmp_path / "data", skip_lifecycle=True)
    client = TestClient(app)
    return client, app, runtime


def _login(client: TestClient) -> str:
    """Authenticate and return a valid JWT token."""
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": _TEST_PASSWORD},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# Test 1: Second WS sending message to same busy session gets rejected
# ---------------------------------------------------------------------------


class TestSecondWsSendingMessageToSameSessionRejected:
    """When connection A has a running task for a session, connection B
    sending 'message' to the same session must receive an explicit error
    indicating the session is busy (not silently queued)."""

    def test_second_ws_sending_message_to_same_session_rejected(self, tmp_path: Path) -> None:
        runtime = _BlockingRuntime()
        client, app, _ = _build_test_app(tmp_path, runtime=runtime)
        token = _login(client)
        session = "web:admin:s1"

        try:
            with client.websocket_connect(f"/ws/chat?token={token}&session={session}") as ws1:
                # Receive the session_info greeting
                info1 = ws1.receive_json()
                assert info1["type"] == "session_info"

                # Connection A sends a message → starts a long-running task
                ws1.send_json({"type": "message", "content": "task-a", "session_key": session})

                # Wait for the runtime to confirm it started processing
                assert runtime.started.wait(timeout=5), "Runtime did not start within 5s"

                # Connection B tries to send a message to the same session
                with client.websocket_connect(f"/ws/chat?token={token}&session={session}") as ws2:
                    info2 = ws2.receive_json()
                    assert info2["type"] == "session_info"

                    ws2.send_json({"type": "message", "content": "task-b", "session_key": session})
                    error_resp = ws2.receive_json()

                    # Connection B must receive an error, not a queued execution
                    assert error_resp["type"] == "error"
                    assert "already running" in error_resp["error"].lower()
        finally:
            # Release the blocking runtime so the task can complete
            runtime.release.set()


# ---------------------------------------------------------------------------
# Test 2: Half-closed WS frees slot on send failure
# ---------------------------------------------------------------------------


class TestHalfClosedWsFreesSlotOnSendFailure:
    """When a task encounters an error (simulating a half-closed WS where
    sends fail with ConnectionResetError), the task's finally block must
    clean up the session slot so a subsequent connection can use it."""

    def test_half_closed_ws_frees_slot_on_send_failure(self, tmp_path: Path) -> None:
        runtime = _FailOnFirstCallRuntime()
        client, app, _ = _build_test_app(tmp_path, runtime=runtime)
        token = _login(client)
        session = "web:admin:s1"

        # Connection A: sends a message; the runtime will raise, triggering
        # the error path + finally cleanup in _run_agent_turn.
        with client.websocket_connect(f"/ws/chat?token={token}&session={session}") as ws1:
            info = ws1.receive_json()
            assert info["type"] == "session_info"

            ws1.send_json({"type": "message", "content": "will-fail", "session_key": session})

            # The runtime raises ConnectionResetError on the first call.
            # _run_agent_turn catches it and sends an error response, then
            # the finally block removes the task from active_tasks.
            error_resp = ws1.receive_json()
            assert error_resp["type"] == "error"
            assert "ConnectionResetError" in error_resp["error"] or "half-closed" in error_resp["error"]

        # Brief pause to ensure the async finally block has completed
        time.sleep(0.2)

        # Verify the slot is freed
        active_tasks = app.state.webui_active_tasks
        assert session not in active_tasks, (
            f"Session slot '{session}' should be freed after task error, "
            f"but active_tasks still contains: {list(active_tasks.keys())}"
        )

        # Connection B: should be able to send to the same session without
        # getting "already running" error.
        with client.websocket_connect(f"/ws/chat?token={token}&session={session}") as ws2:
            info2 = ws2.receive_json()
            assert info2["type"] == "session_info"

            ws2.send_json({"type": "message", "content": "should-succeed", "session_key": session})
            resp = ws2.receive_json()

            # The second call succeeds (runtime returns normally on call #2)
            assert resp["type"] == "done", (
                f"Expected 'done' but got '{resp['type']}': {resp}"
            )
            assert "success:" in resp.get("content", "")


# ---------------------------------------------------------------------------
# Test 3: Rapid open/close does not leak owned task state
# ---------------------------------------------------------------------------


class TestRapidOpenCloseNoOwnedTaskLeak:
    """Rapidly opening a WS, sending a message + cancel, then closing
    should not leave any residual entries in webui_active_tasks."""

    def test_rapid_open_close_no_owned_task_leak(self, tmp_path: Path) -> None:
        runtime = _SlowRuntime(sleep_seconds=60)
        client, app, _ = _build_test_app(tmp_path, runtime=runtime)
        token = _login(client)
        session = "web:admin:rapid"

        for cycle in range(10):
            with client.websocket_connect(f"/ws/chat?token={token}&session={session}") as ws:
                info = ws.receive_json()
                assert info["type"] == "session_info"

                # Start a task (runtime will block indefinitely)
                ws.send_json({"type": "message", "content": f"cycle-{cycle}", "session_key": session})

                # Immediately cancel it
                ws.send_json({"type": "cancel", "session_key": session})

                # Receive the cancel_ok response
                resp = ws.receive_json()
                assert resp["type"] == "cancel_ok", (
                    f"Cycle {cycle}: expected cancel_ok but got {resp}"
                )

            # On context exit, the WS closes. The finally block in ws_chat
            # will cancel any remaining owned tasks (should be none after
            # explicit cancel).

        # After all cycles, no residual entries should remain
        active_tasks = app.state.webui_active_tasks
        assert len(active_tasks) == 0, (
            f"Expected empty active_tasks after 10 rapid cycles, "
            f"but found: {list(active_tasks.keys())}"
        )


# ---------------------------------------------------------------------------
# Test 4: Shutdown (disconnect) cancels all WS tasks within timeout
# ---------------------------------------------------------------------------


class TestShutdownCancelsAllWsWithinTimeout:
    """When multiple WebSocket connections are closed simultaneously
    (as happens during server shutdown), all running tasks must be
    cancelled and cleanup must complete within a reasonable timeout
    (not hang indefinitely)."""

    def test_shutdown_cancels_all_ws_within_timeout(self, tmp_path: Path) -> None:
        num_connections = 3
        runtime = _SlowRuntime(sleep_seconds=60)
        runtime.expect_starts(num_connections)
        client, app, _ = _build_test_app(tmp_path, runtime=runtime)
        token = _login(client)

        # Event: signals all threads to close their WS connection
        close_signal = threading.Event()
        errors: list[str] = []

        def worker(idx: int) -> None:
            session = f"web:admin:shutdown-{idx}"
            try:
                with client.websocket_connect(f"/ws/chat?token={token}&session={session}") as ws:
                    info = ws.receive_json()
                    assert info["type"] == "session_info"

                    ws.send_json({
                        "type": "message",
                        "content": f"long-task-{idx}",
                        "session_key": session,
                    })

                    # Hold the connection open until the close signal fires
                    close_signal.wait(timeout=15)

                # Exiting the context manager closes the WS connection,
                # triggering the finally block that cancels owned tasks.
            except Exception as exc:
                errors.append(f"Worker {idx}: {exc}")

        # Launch worker threads sequentially with a small delay to allow
        # the TestClient's event loop to process each connection.
        threads = []
        for i in range(num_connections):
            t = threading.Thread(target=worker, args=(i,), daemon=True)
            threads.append(t)
            t.start()
            # Small delay to let the event loop handle the new connection
            time.sleep(0.3)

        # Wait for all tasks to actually start in the runtime
        assert runtime.all_started.wait(timeout=15), (
            f"Only {runtime.started_count}/{num_connections} tasks started"
        )

        # Simulate shutdown: signal all connections to close simultaneously
        t_start = time.time()
        close_signal.set()

        # Wait for all threads to finish (cleanup should happen within 5s)
        for t in threads:
            t.join(timeout=5)

        elapsed = time.time() - t_start

        # Verify: all threads completed
        alive = [t for t in threads if t.is_alive()]
        assert not alive, (
            f"{len(alive)} worker thread(s) still alive after 5s timeout"
        )

        # Verify: cleanup completed within reasonable time
        # (_WS_TASK_CANCEL_TIMEOUT_SECONDS is 2.0 in the gateway)
        assert elapsed < 5.0, (
            f"Cleanup took {elapsed:.2f}s, expected < 5s"
        )

        # Verify: no thread errors
        assert not errors, f"Worker errors: {errors}"

        # Verify: all active tasks are cleaned up
        active_tasks = app.state.webui_active_tasks
        assert len(active_tasks) == 0, (
            f"Expected empty active_tasks after shutdown, "
            f"but found {len(active_tasks)} entries: {list(active_tasks.keys())}"
        )

        # Verify: all tasks in the runtime were actually cancelled
        assert runtime.cancelled_count == num_connections, (
            f"Expected {num_connections} cancellations, got {runtime.cancelled_count}"
        )


# ---------------------------------------------------------------------------
# Supplemental: verify _remove_active_task_if_current atomicity
# ---------------------------------------------------------------------------


class TestRemoveActiveTaskIfCurrent:
    """_remove_active_task_if_current must only remove a slot when the
    finishing task still owns it (prevents races with a replacement task)."""

    async def test_does_not_remove_if_task_replaced(self) -> None:
        """If another task has taken the slot, the old task's cleanup must
        not evict the new one."""
        active_tasks: dict[str, asyncio.Task] = {}
        lock = asyncio.Lock()

        async def noop():
            pass

        old_task = asyncio.create_task(noop())
        await old_task

        new_task = asyncio.create_task(noop())
        await new_task

        # Simulate: new_task has replaced old_task in the slot
        active_tasks["session"] = new_task

        # old_task tries to clean up — should NOT remove new_task
        removed = await _remove_active_task_if_current(
            active_tasks, lock, "session", old_task
        )
        assert not removed
        assert active_tasks.get("session") is new_task

    async def test_removes_when_task_still_owns_slot(self) -> None:
        """When the finishing task still owns the slot, it must be removed."""
        active_tasks: dict[str, asyncio.Task] = {}
        lock = asyncio.Lock()

        async def noop():
            pass

        task = asyncio.create_task(noop())
        await task

        active_tasks["session"] = task

        removed = await _remove_active_task_if_current(
            active_tasks, lock, "session", task
        )
        assert removed
        assert "session" not in active_tasks
