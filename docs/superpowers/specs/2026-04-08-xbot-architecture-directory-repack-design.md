# xbot 架构与目录重组设计（业务域重分包，一次性迁移）

- **日期**: 2026-04-08
- **范围**: 仅 `xbot/`
- **目标**: 在保持对外行为基本不变的前提下，完成业务域重分包、清晰化模块边界，并合并模板体系（仅保留 `xbot/templates/`）
- **迁移策略**: 一次性迁移窗口（含冻结与回滚机制）

## 1. 背景与问题

当前代码在功能上可用，但工程结构存在以下问题：

1. 核心能力集中在少数大文件（例如 `agent/service.py`、`cli/commands.py`），模块职责边界不清。
2. 路径结构与真实职责耦合较强，`agent` 目录承载过多横切内容，不利于按业务域演进。
3. `init_templates` 与 `templates` 职责重叠，模板来源不唯一，维护成本高。
4. 若继续增量演进，跨模块依赖会进一步扩散，后续重构成本持续上升。

## 2. 设计目标

1. **按业务域重分包**，让目录结构直接表达系统职责。
2. **保持外部兼容**：CLI 入口与主要行为不变。
3. **一次性完成迁移**，避免长期“双轨目录”造成历史垃圾。
4. **模板唯一化**：合并 `init_templates` 到 `templates`，迁移后彻底移除 `init_templates` 引用。
5. **可回滚**：任何高优先级问题可快速回退到迁移前状态。

## 3. 目标架构与目录

### 3.1 目标目录树

```text
xbot/
  runtime/
    core/                  # 运行时核心（由原 agent/service 拆分）
    state/                 # 会话状态机
    system/                # cron + heartbeat
    session/               # 会话持久化/管理
  interaction/             # AskUserQuestion/权限/响应解析
  tools/                   # 内置工具与注册
  channels/                # 各平台通道
  memory/                  # 长短期记忆能力
  crew/                    # 多 agent 协作与 planner
  platform/
    config/                # 配置与 schema
    providers/             # 模型提供方封装
    security/              # 安全与边界策略
    bus/                   # 事件总线
    logging/               # 日志能力
    utils/                 # 纯通用工具函数
  interfaces/
    cli/                   # CLI 入口与命令
    webui/                 # WebUI 适配层
  templates/               # 唯一模板目录（合并后）
    config/
    memory/
    workspace/
    skills/
    packs/
```

### 3.2 关键映射（示例）

1. `xbot/agent/service.py` -> `xbot/runtime/core/service.py`
2. `xbot/agent/interaction/*` -> `xbot/interaction/*`
3. `xbot/agent/tools/*` -> `xbot/tools/*`
4. `xbot/cron/*` + `xbot/heartbeat/*` -> `xbot/runtime/system/*`
5. `xbot/cli/*` -> `xbot/interfaces/cli/*`
6. `xbot/webui/*` -> `xbot/interfaces/webui/*`
7. `xbot/init_templates/**` + `xbot/templates/**` -> `xbot/templates/**`

## 4. 依赖边界与数据流

### 4.1 允许依赖方向

`interfaces -> runtime/interaction/channels/crew/tools -> platform`

### 4.2 约束规则

1. `platform` 只作为底层能力，不反向依赖上层业务域。
2. `tools` 不依赖 `interfaces`。
3. `channels` 不直接依赖 `crew`。
4. `interaction` 可依赖 `runtime` 的受限接口，不直接操作 runtime 内部实现细节。
5. `runtime/system` 可依赖 `runtime/core` 与 `platform`，不依赖 `interfaces`。

### 4.3 核心执行流（迁移后）

1. `interfaces/cli` 或 `interfaces/webui` 接收请求。
2. 调用 `runtime/core` 处理会话与执行。
3. `runtime/core` 通过 `interaction` 处理权限与结构化响应。
4. `runtime/core` 通过 `tools` 触发工具调用；工具依赖 `platform` 能力。
5. 如涉及通道消息，交由 `channels` 下发。
6. 状态与调度由 `runtime/state`、`runtime/session`、`runtime/system` 协作完成。

## 5. 模板合并设计（init_templates -> templates）

### 5.1 原则

1. 迁移后仅保留 `xbot/templates/`。
2. 同名冲突默认保留 `templates` 现有版本。
3. `init_templates` 中新增但 `templates` 不存在的内容并入 `templates`。

### 5.2 代码策略

1. 全量替换代码路径引用中的 `init_templates` -> `templates`。
2. 同步更新测试、脚本、文档中的硬编码路径。
3. 增加阻断检查：CI/本地检查中若发现 `init_templates` 引用则失败。

## 6. 分阶段执行计划（一次性迁移窗口内）

### Phase 0: 冻结与基线

1. 冻结主干合并（半天到一天窗口）。
2. 打迁移前 tag：`pre-arch-repack-2026-04-08`。
3. 记录基线：全量测试、CLI 帮助命令、核心 smoke。

### Phase 1: 新结构落位

1. 创建目标域目录与 `__init__.py`。
2. 准备路径映射表（旧路径 -> 新路径）。

### Phase 2: 批量移动与 import 改写

1. 按域移动文件（一次窗口内完成）。
2. 批量改写 import 与动态导入路径。
3. 修复因拆分显式暴露的循环依赖。

### Phase 3: 模板体系合并

1. 合并 `init_templates` 内容到 `templates`。
2. 全仓替换 `init_templates` 引用。
3. 删除 `xbot/init_templates/`。

### Phase 4: 兼容收口

1. 保持 CLI entrypoint 和外部行为不变。
2. 必要时补最小兼容转发层（仅限对外高频路径）。

### Phase 5: 验证门禁

1. 全量测试通过。
2. CLI/WebUI/核心会话流 smoke 通过。
3. `init_templates` 引用计数为 0。

### Phase 6: 发布与文档

1. 更新架构文档与目录说明。
2. 产出迁移映射说明。

## 7. 风险与缓解

### 7.1 主要风险

1. 路径迁移导致 import 大面积失效。
2. 动态导入或字符串路径漏改。
3. 模板合并造成初始化行为差异。
4. 一次窗口改动量大，定位成本高。

### 7.2 缓解措施

1. 严格执行路径映射与自动化批改脚本。
2. 每阶段完成后立即跑回归（不拖到最后）。
3. 模板合并设阻断检查（`init_templates` 引用必须为 0）。
4. 单窗口单分支，失败立即回滚到迁移前 tag。

## 8. 验收标准

1. `pytest` 全量通过。
2. `xbot --help` 与核心子命令可用。
3. 会话处理、权限交互、工具调用、通道启停链路可用。
4. 仓库内 `init_templates` 引用为 0（代码/测试/文档/脚本）。
5. 无跨层违规依赖（尤其 `platform` 反向依赖上层）。

## 9. 回滚策略

1. 回滚点：`pre-arch-repack-2026-04-08`。
2. 回滚触发：
   - 核心命令不可用
   - 系统性测试失败
   - 模板初始化链路损坏
3. 回滚方式：整仓回退到 tag，并保留失败迁移分支用于后续拆解分析。

## 10. 里程碑

1. **M1**: 目录迁移与 import 改写完成，可启动。
2. **M2**: `templates` 合并完成，`init_templates` 清零。
3. **M3**: 全量测试与 smoke 通过。
4. **M4**: 文档更新与迁移说明完成，可发布。
