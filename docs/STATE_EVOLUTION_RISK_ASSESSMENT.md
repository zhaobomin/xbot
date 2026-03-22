# 状态演进风险评估与验证策略

> 评估日期: 2026-03-22
> 目标: 最小化演进风险，确保每一步可验证、可回滚

---

## 一、整体风险评估

### 风险矩阵

| 风险类别 | 当前严重度 | 演进后严重度 | 发生概率 | 影响范围 |
|---------|----------|------------|---------|---------|
| 状态不一致 | 🔴 高 | 🟢 低 | 中→低 | 会话中断 |
| 性能下降 | 🟢 低 | 🟡 中 | 低 | 响应延迟 |
| 内存泄漏 | 🟡 中 | 🟢 低 | 低→极低 | 长期运行 |
| 并发竞态 | 🟡 中 | 🟢 低 | 中→低 | 会话冲突 |
| 回归 Bug | 🟢 低 | 🟡 中 | 中 | 功能异常 |

### 核心关切

1. **xbot 是生产系统** - 任何改动都必须保证可用性
2. **演进周期长** - 4-6 周内系统处于"过渡态"
3. **多组件耦合** - 改动一个组件可能影响其他组件

---

## 二、风险最小化策略

### 策略 1: 影子模式 (Shadow Mode)

**原则**: 新代码先"只读运行"，不影响现有行为

```
┌─────────────────────────────────────────────────────────────────────┐
│  影子模式示意图                                                       │
│                                                                     │
│  用户请求 ──▶ 现有系统 (生产路径) ──▶ 响应                           │
│                 │                                                   │
│                 └──▶ 新系统 (影子路径) ──▶ 日志/指标                  │
│                          (不影响响应)                                │
└─────────────────────────────────────────────────────────────────────┘
```

**实现**:

```python
class AgentRuntime:
    def __init__(self, ...):
        # 现有系统 (保持不变)
        self._state_machine = SessionStateMachine(...)

        # 新系统 (影子模式)
        self._coordinator = SessionStateCoordinator()
        self._shadow_mode = True  # 配置控制

    async def _set_session_phase(self, session_key: str, phase: SessionPhase, ...):
        # 现有逻辑 (生产)
        self._state_machine.transition(session_key, phase, reason=reason, force=True)

        # 新逻辑 (影子 - 只记录，不生效)
        if self._shadow_mode:
            try:
                await self._coordinator.set_phase(session_key, phase, reason)
                # 比较差异
                await self._log_shadow_comparison(session_key)
            except Exception as e:
                # 影子模式异常不影响生产
                logger.debug(f"Shadow mode error (ignored): {e}")
```

### 策略 2: 功能开关 (Feature Flags)

**原则**: 每个新功能都有开关，可随时回退

```python
class StateManagementConfig(Base):
    """状态管理功能开关"""

    # Phase 0: 检查器
    enable_state_checker: bool = False
    state_checker_log_level: str = "DEBUG"  # DEBUG, INFO, WARNING

    # Phase 1: Coordinator
    use_coordinator: bool = False
    coordinator_shadow_mode: bool = True  # 影子模式
    coordinator_checkpoint_enabled: bool = False

    # Phase 2: SDK Adapter
    use_sdk_adapter: bool = False
    sdk_adapter_timeout: float = 300.0

    # Phase 3: Transaction
    use_transaction: bool = False
    transaction_auto_rollback: bool = True

    # Phase 4: 监控
    enable_state_metrics: bool = False
    state_metrics_endpoint: str = "/metrics/state"
```

### 策略 3: 增量验证

**原则**: 每一步都有明确的验证标准

```
┌─────────────────────────────────────────────────────────────────────┐
│  增量验证流程                                                        │
│                                                                     │
│  Step 1: 单元测试 (本地)                                            │
│     ↓ 通过                                                          │
│  Step 2: 集成测试 (CI)                                              │
│     ↓ 通过                                                          │
│  Step 3: 影子模式 (开发环境)                                         │
│     ↓ 无差异                                                        │
│  Step 4: 影子模式 (生产环境)                                         │
│     ↓ 无差异 + 无异常                                               │
│  Step 5: 功能开关开启 (小流量)                                       │
│     ↓ 监控正常                                                      │
│  Step 6: 全量开启                                                   │
│     ↓ 稳定运行 1 周                                                 │
│  Step 7: 移除旧代码                                                 │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 三、各阶段风险与验证

### Phase 0: 准备工作

**风险**: 🟢 极低 (只添加检查和日志)

**改动范围**:
- 新增文件，不修改现有逻辑
- 只添加观测能力

**验证清单**:

| 验证项 | 方法 | 预期结果 |
|-------|------|---------|
| 单元测试通过 | `pytest tests/test_state_consistency.py` | 100% 通过 |
| 不影响现有测试 | `pytest tests/` | 929 passed |
| 日志输出正确 | 检查日志格式 | 包含状态快照信息 |
| 性能无影响 | 基准测试 | 延迟增加 < 1% |

**回滚方案**: 删除新增文件，配置 `enable_state_checker = False`

---

### Phase 1: 统一状态入口

**风险**: 🟡 中 (修改 Runtime 核心逻辑)

**主要风险**:

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 状态同步延迟 | 中 | 状态不一致 | 影子模式对比 |
| 锁竞争增加 | 低 | 性能下降 | 异步锁 + 无锁读取 |
| 内存增加 | 低 | 内存压力 | 状态清理 + TTL |

**验证清单**:

| 验证项 | 方法 | 预期结果 |
|-------|------|---------|
| Coordinator 单元测试 | `pytest tests/test_session_coordinator.py` | 100% 通过 |
| 状态一致性检查 | `StateConsistencyChecker` | 无不一致告警 |
| 影子模式对比 | 日志分析 | 新旧状态一致率 > 99.9% |
| 并发测试 | 100 并发会话 | 无竞态条件 |
| 性能基准 | 响应时间对比 | 延迟增加 < 5% |
| 内存基准 | 长时间运行 | 内存无增长趋势 |

**影子模式验证脚本**:

```python
# scripts/verify_shadow_mode.py

async def verify_shadow_consistency(runtime: AgentRuntime, duration: float = 3600):
    """验证影子模式下新旧系统一致性"""

    inconsistencies = []

    for _ in range(int(duration / 10)):
        await asyncio.sleep(10)

        for session_key in list(runtime._state_machine._states.keys()):
            old_phase = runtime._state_machine.get_phase(session_key)
            new_state = await runtime._coordinator.get(session_key)
            new_phase = new_state.runtime.phase if new_state else None

            if old_phase != new_phase:
                inconsistencies.append({
                    "session_key": session_key,
                    "old_phase": old_phase.value,
                    "new_phase": new_phase.value if new_phase else None,
                    "timestamp": time.time(),
                })

    return {
        "total_checks": len(runtime._state_machine._states) * int(duration / 10),
        "inconsistencies": inconsistencies,
        "consistency_rate": 1 - len(inconsistencies) / max(1, total_checks),
    }
```

**回滚方案**:
1. 配置 `use_coordinator = False`
2. 重启服务
3. 新系统停止工作，旧系统继续

---

### Phase 2: SDK 适配层

**风险**: 🟡 中 (修改 Backend 交互)

**主要风险**:

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| SDK 版本不兼容 | 低 | 功能异常 | SDK 版本锁定 + 测试 |
| 权限处理超时 | 中 | 会话卡住 | 超时机制 + 默认拒绝 |
| 异步回调丢失 | 低 | 权限不生效 | Future 超时 + 重试 |

**验证清单**:

| 验证项 | 方法 | 预期结果 |
|-------|------|---------|
| SDKAdapter 单元测试 | `pytest tests/test_sdk_adapter.py` | 100% 通过 |
| 权限流程测试 | 模拟权限请求/响应 | 正确处理 allow/deny/timeout |
| 超时处理测试 | 模拟超时场景 | 自动拒绝 + 清理状态 |
| 集成测试 | 端到端流程 | 完整对话流程正常 |
| 影子模式 | 生产环境影子运行 | 无异常 + 无差异 |

**权限处理测试用例**:

```python
class TestPermissionHandling:
    async def test_permission_allow(self, adapter, session_key):
        """测试权限允许流程"""
        # 1. 发送需要权限的工具调用
        # 2. 模拟用户允许
        # 3. 验证继续执行

    async def test_permission_deny(self, adapter, session_key):
        """测试权限拒绝流程"""
        # 1. 发送需要权限的工具调用
        # 2. 模拟用户拒绝
        # 3. 验证工具调用被跳过

    async def test_permission_timeout(self, adapter, session_key):
        """测试权限超时处理"""
        # 1. 发送需要权限的工具调用
        # 2. 不响应，等待超时
        # 3. 验证自动拒绝 + 状态清理

    async def test_permission_cancel(self, adapter, session_key):
        """测试权限取消处理"""
        # 1. 发送需要权限的工具调用
        # 2. 用户发送 !stop
        # 3. 验证状态正确清理
```

**回滚方案**:
1. 配置 `use_sdk_adapter = False`
2. 重启服务
3. 使用原有 Backend 逻辑

---

### Phase 3: 事务支持

**风险**: 🟡 中 (修改关键操作)

**主要风险**:

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 事务回滚失败 | 低 | 状态不一致 | 检查点恢复 + 强制清理 |
| 事务嵌套 | 低 | 死锁 | 禁止嵌套事务 |
| 性能下降 | 中 | 响应延迟 | 异步提交 + 批量操作 |

**验证清单**:

| 验证项 | 方法 | 预期结果 |
|-------|------|---------|
| 事务单元测试 | `pytest tests/test_state_transaction.py` | 100% 通过 |
| 回滚正确性 | 模拟异常场景 | 状态恢复到事务前 |
| 检查点恢复 | 模拟崩溃场景 | 恢复到最后检查点 |
| 并发事务测试 | 多会话并发 | 无死锁 + 无竞态 |
| 性能测试 | 事务 vs 非事务 | 延迟增加 < 10% |

**事务正确性测试**:

```python
class TestTransactionCorrectness:
    async def test_commit_success(self, coordinator, session_key):
        """测试事务成功提交"""
        async with coordinator.transaction(session_key) as tx:
            tx.update_runtime(phase=SessionPhase.RUNNING)
            tx.update_backend(has_client=True)

        state = await coordinator.get(session_key)
        assert state.runtime.phase == SessionPhase.RUNNING
        assert state.backend.has_client == True

    async def test_rollback_on_exception(self, coordinator, session_key):
        """测试异常时自动回滚"""
        await coordinator.set_phase(session_key, SessionPhase.IDLE)

        try:
            async with coordinator.transaction(session_key) as tx:
                tx.update_runtime(phase=SessionPhase.RUNNING)
                tx.update_backend(has_client=True)
                raise ValueError("Simulated error")
        except ValueError:
            pass

        state = await coordinator.get(session_key)
        assert state.runtime.phase == SessionPhase.IDLE  # 回滚
        assert state.backend.has_client == False

    async def test_checkpoint_restore(self, coordinator, session_key):
        """测试检查点恢复"""
        await coordinator.set_phase(session_key, SessionPhase.IDLE)
        checkpoint_id = await coordinator.save_checkpoint(session_key)

        await coordinator.set_phase(session_key, SessionPhase.RUNNING)

        restored = await coordinator.restore_checkpoint(session_key, checkpoint_id)
        assert restored == True

        state = await coordinator.get(session_key)
        assert state.runtime.phase == SessionPhase.IDLE
```

**回滚方案**:
1. 配置 `use_transaction = False`
2. 使用原有操作逻辑
3. 事务代码路径不执行

---

### Phase 4: 监控完善

**风险**: 🟢 低 (只添加监控)

**验证清单**:

| 验证项 | 方法 | 预期结果 |
|-------|------|---------|
| 指标格式正确 | Prometheus 抓取 | 格式正确，无错误 |
| 指标数据准确 | 对比实际状态 | 数据一致 |
| 健康检查正常 | 调用健康检查接口 | 返回正确状态 |
| 性能无影响 | 基准测试 | 延迟增加 < 1% |

---

## 四、验证基础设施

### 1. 自动化验证脚本

```bash
# scripts/verify_evolution.sh

#!/bin/bash
set -e

echo "=== Phase 0 Verification ==="
pytest tests/test_state_consistency.py -v
python -c "from xbot.agent.state_checker import StateConsistencyChecker; print('✓ Phase 0 OK')"

echo "=== Phase 1 Verification ==="
pytest tests/test_session_coordinator.py -v
python scripts/verify_shadow_mode.py --duration 60
python -c "from xbot.agent.session_coordinator import SessionStateCoordinator; print('✓ Phase 1 OK')"

echo "=== Phase 2 Verification ==="
pytest tests/test_sdk_adapter.py -v
python -c "from xbot.agent.backends.sdk_adapter import SDKAdapter; print('✓ Phase 2 OK')"

echo "=== Phase 3 Verification ==="
pytest tests/test_state_transaction.py -v
python -c "from xbot.agent.state_transaction import StateTransaction; print('✓ Phase 3 OK')"

echo "=== Integration Tests ==="
pytest tests/ -v -k "state or coordinator or transaction"
pytest tests/ -v --cov=xbot --cov-report=term-missing

echo "=== All Verifications Passed ==="
```

### 2. 性能基准测试

```python
# tests/benchmarks/test_state_performance.py

import asyncio
import time
from xbot.agent.session_coordinator import SessionStateCoordinator

async def benchmark_state_operations():
    """基准测试状态操作性能"""
    coordinator = SessionStateCoordinator()

    # 测试 10000 次状态更新
    start = time.perf_counter()
    for i in range(10000):
        await coordinator.set_phase(f"test:{i}", SessionPhase.RUNNING)
    duration = time.perf_counter() - start

    ops_per_sec = 10000 / duration
    print(f"State updates: {ops_per_sec:.0f} ops/sec")

    # 阈值: 应该 > 1000 ops/sec
    assert ops_per_sec > 1000, f"Performance too low: {ops_per_sec} ops/sec"

async def benchmark_concurrent_access():
    """测试并发访问性能"""
    coordinator = SessionStateCoordinator()

    async def update_session(session_key: str, count: int):
        for _ in range(count):
            await coordinator.set_phase(session_key, SessionPhase.RUNNING)
            await coordinator.set_phase(session_key, SessionPhase.IDLE)

    start = time.perf_counter()
    await asyncio.gather(*[
        update_session(f"test:{i}", 100) for i in range(100)
    ])
    duration = time.perf_counter() - start

    print(f"Concurrent updates (100 sessions x 200 ops): {duration:.2f}s")

    # 阈值: 100 sessions x 200 ops 应该 < 5s
    assert duration < 5, f"Concurrent performance too slow: {duration}s"
```

### 3. 状态一致性监控

```python
# xbot/agent/state_monitor.py

class StateConsistencyMonitor:
    """状态一致性监控服务"""

    def __init__(self, coordinator: SessionStateCoordinator):
        self._coordinator = coordinator
        self._alert_threshold = 3  # 连续 3 次不一致告警
        self._consecutive_issues = 0

    async def check_and_alert(self) -> dict:
        """检查并告警"""
        issues = []

        for session_key, state in self._coordinator._states.items():
            inconsistencies = state.check_inconsistencies()
            if inconsistencies:
                issues.append({
                    "session_key": session_key,
                    "phase": state.runtime.phase.value,
                    "issues": inconsistencies,
                })

        if issues:
            self._consecutive_issues += 1
            if self._consecutive_issues >= self._alert_threshold:
                logger.error(
                    f"State inconsistency alert: {len(issues)} sessions affected, "
                    f"consecutive checks: {self._consecutive_issues}"
                )
                # 发送告警 (可接入 PagerDuty、Slack 等)
        else:
            self._consecutive_issues = 0

        return {
            "has_issues": len(issues) > 0,
            "issue_count": len(issues),
            "consecutive_issues": self._consecutive_issues,
            "details": issues[:10],  # 只返回前 10 个
        }
```

---

## 五、渐进式迁移路线图

### 迁移阶段图

```
┌─────────────────────────────────────────────────────────────────────┐
│  迁移阶段与验证检查点                                                 │
│                                                                     │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐         │
│  │ Stage 0 │───▶│ Stage 1 │───▶│ Stage 2 │───▶│ Stage 3 │         │
│  │ 影子模式 │    │ 部分开启 │    │ 全量开启 │    │ 旧码移除 │         │
│  └─────────┘    └─────────┘    └─────────┘    └─────────┘         │
│       │              │              │              │               │
│       ▼              ▼              ▼              ▼               │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐         │
│  │验证 24h │    │验证 48h │    │验证 7天 │    │验证 14天 │         │
│  │无差异   │    │无异常   │    │稳定     │    │稳定     │         │
│  └─────────┘    └─────────┘    └─────────┘    └─────────┘         │
│                                                                     │
│  每个阶段失败时 ←──────────────────────── 回退到上一阶段             │
└─────────────────────────────────────────────────────────────────────┘
```

### 各阶段详细验证

#### Stage 0: 影子模式 (24 小时)

```yaml
配置:
  enable_state_checker: true
  use_coordinator: true
  coordinator_shadow_mode: true  # 关键: 影子模式

验证:
  - 日志中无异常
  - 新旧状态对比一致率 > 99.9%
  - 性能无影响 (延迟 < 基线 + 2%)

回退条件:
  - 出现任何异常
  - 一致率 < 99%
  - 延迟增加 > 5%
```

#### Stage 1: 部分开启 (48 小时)

```yaml
配置:
  coordinator_shadow_mode: false  # 关闭影子模式
  use_coordinator: true
  use_transaction: false  # 事务暂不开启

验证:
  - 所有功能正常
  - 无状态不一致告警
  - 性能下降 < 5%

回退条件:
  - 功能异常
  - 状态不一致
  - 性能下降 > 10%
```

#### Stage 2: 全量开启 (7 天)

```yaml
配置:
  use_transaction: true
  use_sdk_adapter: true
  enable_state_metrics: true

验证:
  - 7 天无严重异常
  - 状态一致性 > 99.9%
  - 事务回滚率 < 0.1%

回退条件:
  - 严重异常
  - 状态一致性问题
  - 性能问题
```

#### Stage 3: 旧码移除 (14 天)

```yaml
配置:
  移除:
    - SessionStateMachine 类
    - _state_machine 属性
    - 旧的状态转换逻辑

验证:
  - 14 天稳定运行
  - 无回退请求
  - 资源使用正常

回退条件:
  - 任何严重问题 (需要代码回滚)
```

---

## 六、应急响应计划

### 异常检测

```python
# 自动检测异常的规则

ANOMALY_DETECTION_RULES = [
    {
        "name": "state_inconsistency_spike",
        "condition": "inconsistent_sessions > 5 in 5min",
        "severity": "warning",
        "action": "alert",
    },
    {
        "name": "transaction_rollback_spike",
        "condition": "rollback_count > 10 in 5min",
        "severity": "warning",
        "action": "alert",
    },
    {
        "name": "performance_degradation",
        "condition": "p99_latency > baseline * 1.5",
        "severity": "warning",
        "action": "alert",
    },
    {
        "name": "memory_growth",
        "condition": "memory_growth > 100MB/hour",
        "severity": "critical",
        "action": "alert + auto_rollback",
    },
]
```

### 自动回滚触发器

```python
class AutoRollbackManager:
    """自动回滚管理器"""

    def __init__(self, config: StateManagementConfig):
        self._config = config
        self._metrics = deque(maxlen=100)  # 最近 100 个指标

    def should_rollback(self, metric: dict) -> tuple[bool, str]:
        """判断是否需要回滚"""

        # 规则 1: 错误率过高
        if metric.get("error_rate", 0) > 0.05:  # 5% 错误率
            return True, "error_rate_exceeded"

        # 规则 2: 响应时间过高
        if metric.get("p99_latency", 0) > self._baseline_p99 * 2:
            return True, "latency_exceeded"

        # 规则 3: 内存增长过快
        if metric.get("memory_growth_rate", 0) > 100 * 1024 * 1024:  # 100MB/hour
            return True, "memory_growth_exceeded"

        return False, ""

    async def perform_rollback(self, reason: str):
        """执行自动回滚"""
        logger.error(f"Auto-rollback triggered: {reason}")

        # 1. 设置功能开关为安全状态
        self._config.use_coordinator = False
        self._config.use_transaction = False
        self._config.use_sdk_adapter = False

        # 2. 发送告警
        await self._send_alert(reason)

        # 3. 记录事件
        await self._record_rollback_event(reason)
```

---

## 七、最终检查清单

### 开始演进前的准备

- [ ] 完整的代码备份 (git tag)
- [ ] 回滚脚本准备好
- [ ] 监控大盘配置完成
- [ ] 告警渠道配置完成
- [ ] 团队成员知晓演进计划
- [ ] 测试环境验证通过

### 每次发布前的检查

- [ ] 所有测试通过 (`pytest tests/ -v`)
- [ ] 覆盖率未下降 (`pytest --cov`)
- [ ] 性能基准通过
- [ ] 影子模式对比通过
- [ ] 功能开关配置正确
- [ ] 回滚步骤已测试

### 每个阶段结束时的检查

- [ ] 验证期间无严重异常
- [ ] 一致性指标达标
- [ ] 性能指标达标
- [ ] 团队评审通过
- [ ] 文档已更新

---

## 八、总结

### 风险控制核心原则

1. **影子模式先行** - 新代码先只读运行
2. **功能开关控制** - 随时可以回退
3. **增量验证** - 每一步都有明确标准
4. **自动告警** - 问题及时发现
5. **自动回滚** - 严重问题自动恢复

### 关键成功因素

| 因素 | 重要性 | 说明 |
|------|-------|------|
| 测试覆盖 | ⭐⭐⭐⭐⭐ | 确保功能正确性 |
| 影子模式 | ⭐⭐⭐⭐⭐ | 无风险验证 |
| 监控告警 | ⭐⭐⭐⭐ | 及时发现问题 |
| 功能开关 | ⭐⭐⭐⭐ | 快速回滚 |
| 团队协作 | ⭐⭐⭐ | 知识共享 |
| 文档完善 | ⭐⭐⭐ | 维护性 |

### 推荐演进顺序

```
最安全 → Phase 0 (检查器)
       → Phase 4 (监控)
       → Phase 1 (Coordinator, 影子模式)
       → Phase 3 (事务)
       → Phase 2 (SDK 适配)
← 最高风险
```

建议按照上述顺序执行，可以在最小风险下逐步完成演进。