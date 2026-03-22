"""状态一致性检查器。

此模块提供状态一致性检查功能，用于检测 xbot 各组件之间的状态不一致问题。

一致性规则:
1. RUNNING 状态必须有 backend client
2. WAITING_PERMISSION 状态必须有 pending permission request
3. WAITING_INTERACTION 状态必须有 pending interaction request
4. IDLE 状态不应该有活跃任务
5. IDLE 状态不应该有 backend task_id
6. 有 backend client 就应该有 session lock

使用方式:
    from xbot.agent.state_checker import StateConsistencyChecker

    checker = StateConsistencyChecker(runtime)
    snapshot = checker.check_session("telegram:123")
    if not snapshot.is_consistent():
        print(f"Inconsistencies: {snapshot.inconsistencies}")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable
from loguru import logger

from xbot.agent.state_snapshot import StateSnapshot

if TYPE_CHECKING:
    from xbot.agent.runtime import AgentRuntime


# 状态一致性规则定义
# 每个规则包含:
# - id: 规则唯一标识
# - condition: 触发条件（快照满足此条件时检查）
# - requirement: 一致性要求（必须满足）
# - message: 不一致时的错误消息
CONSISTENCY_RULES: list[dict] = [
    {
        "id": "running_requires_client",
        "condition": lambda s: s.runtime_phase == "running",
        "requirement": lambda s: s.backend_has_client,
        "message": "RUNNING but no backend client",
    },
    {
        "id": "waiting_permission_requires_request",
        "condition": lambda s: s.runtime_phase == "waiting_permission",
        "requirement": lambda s: s.bus_pending_permission,
        "message": "WAITING_PERMISSION but no pending request",
    },
    {
        "id": "waiting_interaction_requires_request",
        "condition": lambda s: s.runtime_phase == "waiting_interaction",
        "requirement": lambda s: s.bus_pending_interaction,
        "message": "WAITING_INTERACTION but no pending request",
    },
    {
        "id": "idle_no_active_tasks",
        "condition": lambda s: s.runtime_phase == "idle",
        "requirement": lambda s: s.runtime_active_tasks == 0,
        "message": "IDLE but has active tasks",
    },
    {
        "id": "idle_no_backend_task",
        "condition": lambda s: s.runtime_phase == "idle",
        "requirement": lambda s: s.backend_task_id is None,
        "message": "IDLE but has backend task_id",
    },
    {
        "id": "client_requires_lock",
        "condition": lambda s: s.backend_has_client,
        "requirement": lambda s: s.runtime_has_lock,
        "message": "Has client but no session lock",
    },
]


class StateConsistencyChecker:
    """状态一致性检查器。

    用于检查 AgentRuntime 中各组件状态的一致性，检测以下问题:
    - 状态机状态与实际资源不匹配
    - 资源泄漏（如无锁的 client）
    - 孤立资源（如 IDLE 状态的活跃任务）

    Attributes:
        runtime: AgentRuntime 实例
        rules: 一致性规则列表
    """

    def __init__(self, runtime: AgentRuntime):
        """初始化检查器。

        Args:
            runtime: AgentRuntime 实例
        """
        self._runtime = runtime
        self._rules = CONSISTENCY_RULES

    def check_session(self, session_key: str) -> StateSnapshot:
        """检查单个 session 的状态一致性。

        捕获当前状态快照并应用所有一致性规则。

        Args:
            session_key: 会话标识符

        Returns:
            StateSnapshot 包含状态信息和检测结果
        """
        snapshot = self._capture_snapshot(session_key)
        self._detect_inconsistencies(snapshot)
        return snapshot

    def _capture_snapshot(self, session_key: str) -> StateSnapshot:
        """捕获当前状态快照。

        从 Runtime、Backend、Bus 各组件收集状态信息。

        Args:
            session_key: 会话标识符

        Returns:
            StateSnapshot 状态快照
        """
        # 获取 Runtime 状态
        phase = self._runtime._state_machine.get_phase(session_key)
        state = self._runtime._state_machine.get_state(session_key)
        tasks = self._runtime._active_tasks.get(session_key, [])
        has_lock = session_key in self._runtime._session_locks

        # 获取 Backend 状态
        backend = self._runtime.router._backend
        has_client = session_key in backend._clients if backend else False
        task_id = backend._active_task_ids.get(session_key) if backend else None
        last_used = backend._client_last_used.get(session_key) if backend else None

        # 获取 Bus 状态
        bus = self._runtime.bus
        pending_permission_id = None
        pending_interaction_id = None

        if bus:
            pending_permission_id = bus.get_pending_request_for_session(session_key)
            pending_interaction_id = bus.get_pending_interaction_for_session(session_key)

        return StateSnapshot(
            session_key=session_key,
            runtime_phase=phase.value,
            runtime_phase_reason=state.reason,
            runtime_active_tasks=len([t for t in tasks if not t.done()]),
            runtime_has_lock=has_lock,
            backend_has_client=has_client,
            backend_task_id=task_id,
            backend_last_used=last_used,
            bus_pending_permission=pending_permission_id is not None,
            bus_pending_permission_id=pending_permission_id,
            bus_pending_interaction=pending_interaction_id is not None,
            bus_pending_interaction_id=pending_interaction_id,
        )

    def _detect_inconsistencies(self, snapshot: StateSnapshot) -> None:
        """检测状态不一致。

        对快照应用所有一致性规则，将发现的问题添加到 inconsistencies 列表。

        Args:
            snapshot: 状态快照（会被修改）
        """
        for rule in self._rules:
            try:
                # 检查规则条件是否满足
                if rule["condition"](snapshot):
                    # 检查一致性要求
                    if not rule["requirement"](snapshot):
                        snapshot.inconsistencies.append(rule["message"])
                        logger.debug(
                            f"State inconsistency detected: {rule['id']} "
                            f"for session {snapshot.session_key}"
                        )
            except Exception as e:
                logger.debug(
                    f"Rule {rule['id']} check failed for {snapshot.session_key}: {e}"
                )

    def check_all_sessions(self) -> list[StateSnapshot]:
        """检查所有 session 的状态一致性。

        Returns:
            包含不一致问题的快照列表
        """
        inconsistent_snapshots = []

        for session_key in self._get_all_session_keys():
            snapshot = self.check_session(session_key)
            if not snapshot.is_consistent():
                inconsistent_snapshots.append(snapshot)

        return inconsistent_snapshots

    def _get_all_session_keys(self) -> set[str]:
        """获取所有已知的 session key。

        从各组件收集所有已知的 session key，确保不会遗漏。

        Returns:
            session key 集合
        """
        keys: set[str] = set()

        # 从状态机获取
        keys.update(self._runtime._state_machine._states.keys())

        # 从活跃任务获取
        keys.update(self._runtime._active_tasks.keys())

        # 从 session locks 获取
        keys.update(self._runtime._session_locks.keys())

        # 从 backend 获取
        backend = self._runtime.router._backend
        if backend:
            keys.update(backend._clients.keys())
            keys.update(backend._active_task_ids.keys())

        return keys

    def get_rule_count(self) -> int:
        """获取一致性规则数量。

        Returns:
            规则数量
        """
        return len(self._rules)

    def get_rules_info(self) -> list[dict]:
        """获取所有规则的信息。

        Returns:
            规则信息列表，每个元素包含 id 和 message
        """
        return [
            {"id": rule["id"], "message": rule["message"]}
            for rule in self._rules
        ]