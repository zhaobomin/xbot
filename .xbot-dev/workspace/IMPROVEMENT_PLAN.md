# Crew 错误处理改进方案

## 问题根因

### 反复出现的 Bug 模式

| 模式 | 描述 | 示例 |
|------|------|------|
| 异步陷阱 | async for 阻塞，超时检查不执行 | 软超时无输出时卡死 |
| 变量作用域 | try 块赋值，except 块引用时不存在 | extended_count 未定义 |
| falsy 混淆 | 0 是合法值但被当作 falsy | timeout=0 被忽略 |
| 缺少防御 | 直接字典访问，无存在性检查 | 未知 agent 崩溃 |
| 清理中断 | raise 后清理代码不执行 | CancelledError 时 finalize 未调用 |

### 设计问题

1. **错误处理分散**：每个函数自己处理，不一致
2. **清理代码位置错误**：不在 finally 块中
3. **缺少输入验证**：执行时才发现无效输入
4. **测试先行不足**：先写代码后补测试

---

## 短期方案：输入验证层

### 目标

- 在执行前验证所有输入
- 快速失败，提前发现错误
- 返回友好错误信息

### 设计

```python
class TaskValidationError(Exception):
    """任务验证失败"""
    def __init__(self, task_name: str, field: str, message: str):
        self.task_name = task_name
        self.field = field
        self.message = message
        super().__init__(f"Task '{task_name}': {field} - {message}")


class CrewValidator:
    """Crew 配置和任务验证器"""

    @classmethod
    def validate_crew_config(cls, config: CrewConfig) -> list[str]:
        """验证 crew 配置，返回警告列表"""
        warnings = []
        # 检查 agents
        if not config.agents:
            warnings.append("No agents defined")
        # 检查 tasks
        if not config.tasks:
            warnings.append("No tasks defined")
        return warnings

    @classmethod
    def validate_task(cls, task: TaskDefinition, available_agents: set[str]) -> TaskValidationError | None:
        """验证单个任务，返回错误或 None"""
        # 检查 agent 存在
        if task.agent not in available_agents:
            return TaskValidationError(
                task.name, "agent",
                f"Agent '{task.agent}' not found. Available: {available_agents}"
            )

        # 检查 timeout 合法
        if task.timeout is not None and task.timeout < 0:
            return TaskValidationError(
                task.name, "timeout",
                f"Timeout must be non-negative, got {task.timeout}"
            )

        # 检查 context_from 中的任务存在
        # ... 更多验证

        return None
```

### 集成点

```python
# process.py
async def _execute_single_task(self, task: TaskDefinition) -> TaskResult:
    # 1. 验证输入（新增）
    validation_error = CrewValidator.validate_task(
        task, set(self.crew_config.agents.keys())
    )
    if validation_error:
        return TaskResult(
            task_name=task.name,
            agent_name=task.agent,
            output=f"Validation failed: {validation_error.message}",
            status="failed",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

    # 2. 执行任务（原有逻辑）
    role = self.crew_config.agents[task.agent]
    ...
```

---

## 中期方案：统一清理流程

### 目标

- 所有清理代码在 finally 块中
- 集中处理 CancelledError
- 使用 Context Manager 模式

### 设计

```python
class CrewExecutionContext:
    """Crew 执行上下文管理器"""

    def __init__(self, crew_config: CrewConfig, xbot_config: Config,
                 permission_handler, state_manager: CrewStateManager):
        self.crew_config = crew_config
        self.xbot_config = xbot_config
        self.permission_handler = permission_handler
        self.state_manager = state_manager

        self.pool: AgentPool | None = None
        self.process: BaseProcess | None = None
        self.cancelled_error: asyncio.CancelledError | None = None
        self.final_status: str = "completed"

    async def __aenter__(self):
        """初始化资源"""
        self.pool = AgentPool(self.crew_config, self.xbot_config, self.permission_handler)
        await self.pool.initialize()
        self.state_manager.transition_crew(CrewPhase.RUNNING)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """清理资源 - 保证执行"""
        try:
            # 1. 设置最终状态
            if exc_type is asyncio.CancelledError:
                self.state_manager.transition_crew(CrewPhase.ABORTING)
                self.state_manager.transition_crew(CrewPhase.ABORTED)
                self.final_status = "aborted"
                self.cancelled_error = exc_val
            elif exc_type is not None:
                self.state_manager.transition_crew(CrewPhase.FAILED)
                self.final_status = "failed"

            # 2. 清理 pool
            if self.pool:
                await self.pool.shutdown()

            # 3. 完成输出
            if self.process:
                self.process.finalize_output(self.final_status)

        except Exception:
            logger.exception("[crew] Error during cleanup")

        # 不抑制异常，但延迟 CancelledError 的传播
        if exc_type is asyncio.CancelledError:
            return False  # 让 __aexit__ 后的代码执行，但最终会 raise

        return False  # 不抑制任何异常

    def set_process(self, process: BaseProcess):
        self.process = process

    def get_cancelled_error(self) -> asyncio.CancelledError | None:
        return self.cancelled_error
```

### 使用方式

```python
# orchestrator.py
async def run(self, checkpoint_path: Path | None = None) -> CrewResult:
    started_at = datetime.now()
    wall_start = time.perf_counter()

    # 验证配置
    warnings = CrewValidator.validate_crew_config(self.crew_config)
    for w in warnings:
        logger.warning(f"[crew] {w}")

    # 使用上下文管理器
    state_manager = CrewStateManager(...)
    context = CrewExecutionContext(
        self.crew_config, self.xbot_config,
        self.permission_handler, state_manager
    )

    try:
        async with context:
            process = self._create_process(context.pool, ...)
            context.set_process(process)
            results = await process.execute(self.crew_config.tasks)
    except asyncio.CancelledError:
        # 清理已完成，这里只是捕获以便返回结果
        pass

    # 返回结果（即使取消也返回）
    return CrewResult(
        crew_name=self.crew_config.name,
        task_results=results,
        status=context.final_status,
        ...
    )
```

---

## 长期方案：测试驱动开发

### 目标

- 测试先行，覆盖所有边界情况
- 建立测试模板和规范
- 自动化测试覆盖率检查

### 测试分类

```python
# tests/test_crew_edge_cases.py

class TestInputValidation:
    """输入验证测试"""

    @pytest.mark.asyncio
    async def test_unknown_agent_returns_failure(self):
        """未知 agent 应返回失败结果"""
        ...

    @pytest.mark.asyncio
    async def test_timeout_zero_is_respected(self):
        """timeout=0 应被正确处理"""
        ...

    @pytest.mark.asyncio
    async def test_timeout_negative_rejected(self):
        """负数 timeout 应被拒绝"""
        ...


class TestCancellationHandling:
    """取消处理测试"""

    @pytest.mark.asyncio
    async def test_cancelled_error_cleanup_complete(self):
        """取消时所有清理应完成"""
        ...

    @pytest.mark.asyncio
    async def test_cancelled_during_task_execution(self):
        """任务执行中取消的处理"""
        ...


class TestTimeoutEdgeCases:
    """超时边界测试"""

    @pytest.mark.asyncio
    async def test_no_output_timeout(self):
        """无输出时超时"""
        ...

    @pytest.mark.asyncio
    async def test_output_then_stall_timeout(self):
        """输出后停滞超时"""
        ...

    @pytest.mark.asyncio
    async def test_max_extensions_reached(self):
        """达到最大延长次数"""
        ...


class TestStateTransitions:
    """状态转换测试"""

    def test_valid_transition_succeeds(self):
        """有效转换成功"""
        ...

    def test_invalid_transition_raises(self):
        """无效转换抛出异常"""
        ...
```

### 测试模板

```python
# 边界值测试模板
class Test{Feature}BoundaryValues:
    """{功能} 边界值测试"""

    def test_minimum_value(self):
        """最小值: {value}"""
        ...

    def test_maximum_value(self):
        """最大值: {value}"""
        ...

    def test_below_minimum(self):
        """低于最小值"""
        ...

    def test_above_maximum(self):
        """高于最大值"""
        ...

    def test_zero_value(self):
        """零值（特殊 falsy）"""
        ...

    def test_none_value(self):
        """None 值"""
        ...


# 异常路径测试模板
class Test{Feature}ErrorPaths:
    """{功能} 异常路径测试"""

    @pytest.mark.asyncio
    async def test_cancelled_error_handling(self):
        """CancelledError 处理"""
        ...

    @pytest.mark.asyncio
    async def test_timeout_error_handling(self):
        """TimeoutError 处理"""
        ...

    @pytest.mark.asyncio
    async def test_generic_exception_handling(self):
        """通用异常处理"""
        ...
```

### 测试清单（每次修改必查）

```
□ 输入验证
  □ 空/None 输入
  □ 边界值（0, -1, 最大值）
  □ 无效引用（未知 agent/task）
  □ 类型错误

□ 异步处理
  □ CancelledError 传播和清理
  □ TimeoutError 处理
  □ async for 阻塞情况
  □ 并发访问

□ 清理流程
  □ 正常完成时清理
  □ 异常时清理
  □ 取消时清理
  □ 多重异常时清理

□ 状态管理
  □ 有效状态转换
  □ 无效状态转换被拒绝
  □ 终态不可变更
```

---

## 实施计划

### Phase 1: 短期（1-2 天）

1. 创建 `CrewValidator` 类
2. 在 `_execute_single_task` 和 `_redo_task` 中添加验证
3. 为验证逻辑添加测试

### Phase 2: 中期（3-5 天）

1. 创建 `CrewExecutionContext` 上下文管理器
2. 重构 `orchestrator.run()` 使用上下文管理器
3. 重构 `process.py` 中的清理逻辑
4. 添加集成测试

### Phase 3: 长期（持续）

1. 完善测试覆盖，达到 90%+
2. 建立 PR 检查清单
3. 添加测试覆盖率 CI 检查
4. 编写开发者文档

---

## 预期效果

| 指标 | 当前 | 目标 |
|------|------|------|
| 测试覆盖率 | ~70% | 90%+ |
| 边界情况测试 | 部分覆盖 | 完全覆盖 |
| 清理代码位置 | 分散 | 集中在 finally |
| 输入验证时机 | 执行时 | 执行前 |
| Bug 发现阶段 | 运行时 | 开发时（测试） |

---

## 实施状态

### v0.3.14-0.3.17 (2026-03-28)

| 阶段 | 方案 | 状态 | 覆盖率 |
|------|------|------|--------|
| 短期 | 输入验证层 (CrewValidator) | ✅ 完成 | 100% |
| 中期 | 统一清理流程 (CrewResourceManager) | ✅ 完成 | 87% |
| 长期 | 测试覆盖 90%+ | 🔄 进行中 | 35% (核心 66-87%) |

**核心模块覆盖率**:
- validation.py: 100%
- resource_manager.py: 87%
- context.py: 86%
- orchestrator.py: 78%
- state.py: 74%
- process.py: 66%

**测试数量**: 142 tests

**已修复的 Bug**:
1. ✅ 软超时无输出时卡死 → asyncio.shield() 模式
2. ✅ extended_count 未定义 → 变量初始化前移
3. ✅ timeout=0 被忽略 → `is not None` 检查
4. ✅ 未知 agent 崩溃 → CrewValidator 验证层
5. ✅ CancelledError 时 finalize 未调用 → CrewResourceManager

**新增测试文件**:
- tests/test_validation.py (32 tests)
- tests/test_resource_manager.py (20 tests)
- tests/test_process_flow.py (33 tests)
- tests/test_orchestrator_context.py (20 tests)
- tests/test_soft_timeout.py (补充边界测试)
- tests/test_cancelled_error_handling.py (补充清理测试)