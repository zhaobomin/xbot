# xbot Crew 动态规划 - 完整设计方案 v2

## 一、设计理念

```
核心理念: 分层解耦 + 动态扩展

┌─────────────────────────────────────────────────────────────┐
│  Layer 0: 角色池 (Role Pool)                                 │
│  - 预定义、可配置、可扩展                                     │
│  - 定义"能做什么"                                            │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 0.5: 角色创建器 (Role Creator) ← 新增                 │
│  - 发现角色缺口 → 创建新角色 → 验证 → 加入角色池             │
│  - 扩展"能做什么"                                            │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: 角色选择器 (Role Selector)                         │
│  - 分析目标 → 匹配所需能力 → 选择角色                         │
│  - 决定"用谁做"                                              │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 2: 任务规划器 (Task Planner)                          │
│  - 分解目标 → 规划任务 → 分配角色（仅限已选角色）             │
│  - 决定"怎么做"                                              │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 3: 配置生成器 (Config Generator)                      │
│  - 生成标准 crew_config.yaml                                 │
│  - 输出"执行蓝图"                                            │
└─────────────────────────────────────────────────────────────┘
```

---

## 二、目录结构

```
xbot/agent/crew/
├── __init__.py
├── orchestrator.py              # (现有) 执行引擎
├── models.py                    # (现有) 数据模型
├── process.py                   # (现有) 执行流程
├── ...
│
├── planner/                     # 新增: 动态规划模块
│   ├── __init__.py
│   ├── models.py                # 规划相关数据模型
│   ├── role_pool.py             # 角色池管理
│   ├── role_selector.py         # 角色选择器
│   ├── role_creator.py          # 角色创建器
│   ├── task_planner.py          # 任务规划器
│   ├── config_generator.py      # 配置生成器
│   ├── crew_planner.py          # 主入口
│   └── prompts.py               # LLM Prompt 模板
│
├── role_pool/                   # 新增: 预定义角色池
│   ├── core/                    # 核心角色 (始终可用)
│   │   ├── researcher.yaml
│   │   ├── coder.yaml
│   │   ├── reviewer.yaml
│   │   └── tester.yaml
│   ├── extended/                # 扩展角色 (按需启用)
│   │   ├── doc_writer.yaml
│   │   ├── data_analyst.yaml
│   │   └── devops.yaml
│   ├── specialist/              # 专业角色 (显式配置)
│   │   ├── security_auditor.yaml
│   │   └── ml_engineer.yaml
│   └── pool.yaml                # 角色池配置文件
│
├── cli/                         # 新增: CLI 命令模块
│   ├── __init__.py
│   ├── plan_cmd.py              # 规划相关命令
│   └── role_cmd.py              # 角色管理命令
│
└── templates/                   # (现有) crew 模板
    ├── code-review/
    ├── bug-hunter/
    └── ...
```

### 用户角色存储位置

```
# 项目级角色 (项目专用，优先级最高)
./project_roles/
├── my_analyst.yaml
└── my_custom_role.yaml

# 用户级角色 (跨项目共享)
~/.xbot/roles/
├── ml_engineer.yaml
└── security_expert.yaml
```

---

## 三、数据模型

### 3.1 角色定义模型

```python
# planner/models.py

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RoleTier(str, Enum):
    """角色层级"""
    CORE = "core"           # 核心角色，始终可用
    EXTENDED = "extended"   # 扩展角色，按需启用
    SPECIALIST = "specialist"  # 专业角色，显式配置


class Capability(str, Enum):
    """能力枚举 - 用于能力匹配"""
    # 信息处理
    SEARCH = "search"               # 搜索信息
    ANALYZE = "analyze"             # 分析数据/代码
    SUMMARIZE = "summarize"         # 总结归纳

    # 代码操作
    READ_CODE = "read_code"         # 阅读代码
    WRITE_CODE = "write_code"       # 编写代码
    REFACTOR = "refactor"           # 重构代码
    DEBUG = "debug"                 # 调试排错

    # 质量保障
    REVIEW = "review"               # 代码审查
    TEST = "test"                   # 测试编写
    VALIDATE = "validate"           # 验证校验

    # 文档
    DOCUMENT = "document"           # 编写文档

    # 数据
    DATA_ANALYSIS = "data_analysis" # 数据分析
    ML = "machine_learning"         # 机器学习

    # 运维
    DEPLOY = "deploy"               # 部署发布
    MONITOR = "monitor"             # 监控运维

    # 安全
    SECURITY_AUDIT = "security_audit"  # 安全审计


@dataclass
class RoleDefinition:
    """角色定义 - 从 YAML 加载"""
    name: str                               # 角色标识
    display_name: str                       # 显示名称
    description: str                        # 角色描述
    goal: str                               # 角色目标
    backstory: str                          # 背景故事
    tier: RoleTier                          # 角色层级

    # 能力标签 - 用于匹配
    capabilities: list[Capability]

    # 工具配置
    tools: list[str] | None = None          # 可用工具列表，None = 全部
    tool_restrictions: list[str] | None = None  # 禁用的工具

    # 执行配置
    max_iterations: int = 30
    timeout_multiplier: float = 1.0         # 超时倍数

    # 元数据
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)  # 适用场景示例

    def to_agent_role(self) -> "AgentRole":
        """转换为执行用的 AgentRole"""
        from xbot.agent.crew.models import AgentRole
        return AgentRole(
            name=self.name,
            description=self.description,
            goal=self.goal,
            backstory=self.backstory,
            tools=self.tools,
            max_iterations=self.max_iterations,
        )

    def matches_capabilities(self, required: list[Capability]) -> float:
        """计算能力匹配度 (0.0 - 1.0)"""
        if not required:
            return 0.0
        matched = len(set(self.capabilities) & set(required))
        return matched / len(required)
```

### 3.2 角色池模型

```python
@dataclass
class RolePoolConfig:
    """角色池配置"""
    enabled_tiers: list[RoleTier] = field(
        default_factory=lambda: [RoleTier.CORE]
    )
    custom_roles_dir: str | None = None     # 自定义角色目录
    role_overrides: dict[str, dict] = field(default_factory=dict)  # 角色覆盖配置


@dataclass
class RolePool:
    """角色池 - 管理所有可用角色"""
    roles: dict[str, RoleDefinition]
    config: RolePoolConfig

    def get_role(self, name: str) -> RoleDefinition | None:
        """获取角色"""
        return self.roles.get(name)

    def get_roles_by_tier(self, tier: RoleTier) -> list[RoleDefinition]:
        """按层级获取角色"""
        return [r for r in self.roles.values() if r.tier == tier]

    def get_available_roles(self) -> list[RoleDefinition]:
        """获取所有可用角色（按配置的层级过滤）"""
        return [
            r for r in self.roles.values()
            if r.tier in self.config.enabled_tiers
        ]

    def find_by_capabilities(
        self,
        required: list[Capability],
        min_score: float = 0.5,
    ) -> list[tuple[RoleDefinition, float]]:
        """按能力查找角色，返回 (角色, 匹配度) 列表"""
        results = []
        for role in self.get_available_roles():
            score = role.matches_capabilities(required)
            if score >= min_score:
                results.append((role, score))
        return sorted(results, key=lambda x: x[1], reverse=True)

    def to_description(self) -> str:
        """生成角色池描述（用于 LLM prompt）"""
        lines = ["可用角色列表:\n"]
        for role in self.get_available_roles():
            caps = ", ".join(c.value for c in role.capabilities)
            lines.append(f"- {role.name}: {role.description}")
            lines.append(f"  能力: {caps}")
            lines.append(f"  适用: {', '.join(role.examples[:3])}")
        return "\n".join(lines)
```

### 3.3 角色创建模型

```python
@dataclass
class RoleCreationRequest:
    """角色创建请求"""
    suggested_name: str                       # 建议的角色名
    required_capabilities: list[Capability]   # 所需能力
    reason: str                               # 创建原因
    context: str                              # 上下文描述


@dataclass
class RoleCreationResult:
    """角色创建结果"""
    success: bool
    role: RoleDefinition | None
    errors: list[str]
    warnings: list[str]

    # 如果需要用户确认
    requires_confirmation: bool = False
    confirmation_message: str = ""


@dataclass
class RoleGap:
    """角色缺口 - 描述缺少的能力"""
    missing_capabilities: list[Capability]
    suggested_role_name: str
    suggested_role_description: str
    coverage_gap: float  # 缺失的能力占比


@dataclass
class GoalAnalysis:
    """目标分析结果"""
    summary: str                            # 目标摘要
    required_capabilities: list[Capability]  # 所需能力
    complexity: str                         # simple | medium | complex
    estimated_tasks: int                    # 预估任务数量
    suggested_process: str                  # sequential | hierarchical
    constraints: list[str] = field(default_factory=list)  # 约束条件
    role_gaps: list[RoleGap] = field(default_factory=list)  # 发现的角色缺口


@dataclass
class RoleSelection:
    """角色选择结果"""
    selected_roles: list[RoleDefinition]
    selection_reason: dict[str, str]  # role_name -> 选择原因
    skipped_roles: list[str]           # 考虑但未选择的角色
    coverage_score: float              # 能力覆盖度
    created_roles: list[RoleDefinition] = field(default_factory=list)  # 新创建的角色
    role_gaps: list[RoleGap] = field(default_factory=list)  # 未解决的缺口


@dataclass
class TaskPlan:
    """任务规划"""
    name: str
    description: str
    agent: str                          # 角色名称
    dependencies: list[str]             # 依赖的任务名
    expected_output: str
    timeout: int
    human_review: bool = False
    priority: int = 0                   # 优先级，数字越大越优先


@dataclass
class CrewPlan:
    """完整的 Crew 规划"""
    # 基本信息
    name: str
    description: str

    # 执行配置
    process: str  # sequential | hierarchical
    global_context: str

    # 规划结果
    roles: list[RoleDefinition]
    tasks: list[TaskPlan]

    # 元数据
    analysis: GoalAnalysis
    role_selection: RoleSelection
    planning_time: float
    confidence: float  # 规划置信度 0.0-1.0

    def validate(self) -> list[str]:
        """验证规划，返回错误列表"""
        errors = []
        role_names = {r.name for r in self.roles}

        for task in self.tasks:
            # 检查角色是否存在
            if task.agent not in role_names:
                errors.append(f"任务 '{task.name}' 引用了不存在的角色 '{task.agent}'")

            # 检查依赖是否存在
            task_names = {t.name for t in self.tasks}
            for dep in task.dependencies:
                if dep not in task_names:
                    errors.append(f"任务 '{task.name}' 引用了不存在的依赖 '{dep}'")

        return errors
```

---

## 四、核心组件设计

### 4.1 角色池管理器

```python
# planner/role_pool.py

import yaml
from pathlib import Path
from typing import Any

from xbot.agent.crew.planner.models import (
    Capability,
    RoleDefinition,
    RolePool,
    RolePoolConfig,
    RoleTier,
)


ROLE_POOL_DIR = Path(__file__).parent.parent / "role_pool"


class RolePoolManager:
    """角色池管理器 - 加载、管理、查询角色"""

    def __init__(self, config: RolePoolConfig | None = None):
        self.config = config or RolePoolConfig()
        self._roles: dict[str, RoleDefinition] = {}
        self._loaded = False

    def load(self) -> None:
        """加载角色池"""
        if self._loaded:
            return

        # 1. 加载核心角色
        self._load_from_dir(ROLE_POOL_DIR / "core", RoleTier.CORE)

        # 2. 加载扩展角色（如果启用）
        if RoleTier.EXTENDED in self.config.enabled_tiers:
            self._load_from_dir(ROLE_POOL_DIR / "extended", RoleTier.EXTENDED)

        # 3. 加载专业角色（如果启用）
        if RoleTier.SPECIALIST in self.config.enabled_tiers:
            self._load_from_dir(ROLE_POOL_DIR / "specialist", RoleTier.SPECIALIST)

        # 4. 加载自定义角色（如果配置）
        if self.config.custom_roles_dir:
            self._load_from_dir(
                Path(self.config.custom_roles_dir),
                RoleTier.EXTENDED
            )

        # 5. 应用覆盖配置
        self._apply_overrides()

        self._loaded = True

    def _load_from_dir(self, dir_path: Path, tier: RoleTier) -> None:
        """从目录加载角色"""
        if not dir_path.exists():
            return

        for yaml_file in dir_path.glob("*.yaml"):
            try:
                role = self._load_role(yaml_file, tier)
                if role:
                    self._roles[role.name] = role
            except Exception as e:
                # 加载失败但继续
                print(f"Warning: Failed to load role from {yaml_file}: {e}")

    def _load_role(self, path: Path, tier: RoleTier) -> RoleDefinition | None:
        """加载单个角色定义"""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data or not data.get("name"):
            return None

        # 解析能力
        capabilities = []
        for cap_str in data.get("capabilities", []):
            try:
                capabilities.append(Capability(cap_str))
            except ValueError:
                pass  # 忽略未知能力

        return RoleDefinition(
            name=data["name"],
            display_name=data.get("display_name", data["name"]),
            description=data.get("description", ""),
            goal=data.get("goal", ""),
            backstory=data.get("backstory", ""),
            tier=tier,
            capabilities=capabilities,
            tools=data.get("tools"),
            tool_restrictions=data.get("tool_restrictions"),
            max_iterations=data.get("max_iterations", 30),
            timeout_multiplier=data.get("timeout_multiplier", 1.0),
            tags=data.get("tags", []),
            examples=data.get("examples", []),
        )

    def _apply_overrides(self) -> None:
        """应用角色覆盖配置"""
        for role_name, overrides in self.config.role_overrides.items():
            if role_name not in self._roles:
                continue
            role = self._roles[role_name]
            for key, value in overrides.items():
                if hasattr(role, key):
                    setattr(role, key, value)

    def get_pool(self) -> RolePool:
        """获取角色池"""
        if not self._loaded:
            self.load()
        return RolePool(roles=self._roles, config=self.config)
```

### 4.2 角色创建器

```python
# planner/role_creator.py

from __future__ import annotations

import json
import re
import yaml
from pathlib import Path
from typing import TYPE_CHECKING

from xbot.agent.crew.planner.models import (
    Capability,
    RoleCreationRequest,
    RoleCreationResult,
    RoleDefinition,
    RoleGap,
    RoleTier,
)
from xbot.agent.crew.planner.prompts import ROLE_CREATION_PROMPT

if TYPE_CHECKING:
    from xbot.agent.crew.planner.llm_client import LLMClient


class RoleCreator:
    """角色创建器 - 动态创建新角色"""

    # 可用工具列表（用于验证）
    AVAILABLE_TOOLS = {
        "read_file", "write_file", "edit_file", "list_dir",
        "web_search", "web_fetch", "bash",
    }

    # 工具能力映射
    TOOL_CAPABILITY_MAP = {
        Capability.SEARCH: {"web_search", "web_fetch"},
        Capability.READ_CODE: {"read_file", "list_dir"},
        Capability.WRITE_CODE: {"write_file", "edit_file"},
        Capability.DEBUG: {"read_file", "bash"},
        Capability.TEST: {"read_file", "write_file", "edit_file", "bash"},
        Capability.ANALYZE: {"read_file", "list_dir"},
        Capability.DOCUMENT: {"read_file", "write_file"},
        Capability.DEPLOY: {"bash", "read_file", "write_file"},
    }

    def __init__(
        self,
        llm_client: LLMClient,
        custom_roles_dir: Path | None = None,
        auto_save: bool = False,
        require_confirmation: bool = True,
    ):
        self.llm = llm_client
        self.custom_roles_dir = custom_roles_dir
        self.auto_save = auto_save
        self.require_confirmation = require_confirmation

    async def analyze_gaps(
        self,
        required_capabilities: list[Capability],
        available_roles: list[RoleDefinition],
    ) -> list[RoleGap]:
        """分析能力缺口"""
        # 获取已有角色覆盖的能力
        covered = set()
        for role in available_roles:
            covered.update(role.capabilities)

        # 找出缺失的能力
        required_set = set(required_capabilities)
        missing = required_set - covered

        if not missing:
            return []

        # 计算缺口
        coverage_gap = len(missing) / len(required_set) if required_set else 0

        # 生成建议
        gap = RoleGap(
            missing_capabilities=list(missing),
            suggested_role_name=self._suggest_role_name(missing),
            suggested_role_description=self._suggest_description(missing),
            coverage_gap=coverage_gap,
        )

        return [gap]

    async def create_role(
        self,
        request: RoleCreationRequest,
    ) -> RoleCreationResult:
        """创建新角色"""
        errors = []
        warnings = []

        # 1. 使用 LLM 生成角色定义
        prompt = ROLE_CREATION_PROMPT.format(
            suggested_name=request.suggested_name,
            required_capabilities=", ".join(c.value for c in request.required_capabilities),
            reason=request.reason,
            context=request.context,
        )

        response = await self.llm.generate(prompt)

        # 2. 解析 LLM 响应
        role_data = self._parse_response(response)
        if not role_data:
            return RoleCreationResult(
                success=False,
                role=None,
                errors=["无法解析 LLM 生成的角色定义"],
                warnings=[],
            )

        # 3. 验证角色定义
        validation_errors = self._validate_role(role_data)
        if validation_errors:
            return RoleCreationResult(
                success=False,
                role=None,
                errors=validation_errors,
                warnings=[],
            )

        # 4. 构建角色对象
        role = self._build_role(role_data)

        # 5. 检查警告
        warnings = self._check_warnings(role, request.required_capabilities)

        # 6. 保存（如果启用）
        if self.auto_save and self.custom_roles_dir:
            self._save_role(role)

        return RoleCreationResult(
            success=True,
            role=role,
            errors=[],
            warnings=warnings,
            requires_confirmation=self.require_confirmation and not self.auto_save,
            confirmation_message=self._build_confirmation_message(role),
        )

    def _suggest_role_name(self, missing_capabilities: set[Capability]) -> str:
        """根据缺失能力建议角色名"""
        # 简单的命名规则
        cap_names = {
            Capability.SECURITY_AUDIT: "security_auditor",
            Capability.ML: "ml_engineer",
            Capability.DATA_ANALYSIS: "data_analyst",
            Capability.DEPLOY: "devops",
            Capability.MONITOR: "sre",
        }

        for cap in missing_capabilities:
            if cap in cap_names:
                return cap_names[cap]

        return "custom_specialist"

    def _suggest_description(self, missing_capabilities: set[Capability]) -> str:
        """生成角色描述建议"""
        cap_desc = {
            Capability.SECURITY_AUDIT: "安全审计专家",
            Capability.ML: "机器学习工程师",
            Capability.DATA_ANALYSIS: "数据分析专家",
            Capability.DEPLOY: "DevOps 工程师",
            Capability.MONITOR: "SRE 工程师",
            Capability.SEARCH: "信息搜索专家",
            Capability.ANALYZE: "分析专家",
        }

        descriptions = []
        for cap in missing_capabilities:
            if cap in cap_desc:
                descriptions.append(cap_desc[cap])

        return "、".join(descriptions) if descriptions else "自定义专家"

    def _parse_response(self, response: str) -> dict | None:
        """解析 LLM 响应"""
        try:
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                return json.loads(match.group())
        except json.JSONDecodeError:
            pass
        return None

    def _validate_role(self, data: dict) -> list[str]:
        """验证角色定义"""
        errors = []

        # 必填字段
        required_fields = ["name", "description", "goal", "backstory"]
        for field in required_fields:
            if not data.get(field):
                errors.append(f"缺少必填字段: {field}")

        # 名称验证
        name = data.get("name", "")
        if name:
            if not name.replace("_", "").isalnum():
                errors.append(f"角色名 '{name}' 格式无效，只能包含字母、数字和下划线")
            if len(name) > 50:
                errors.append(f"角色名过长: {len(name)} > 50")

        # 能力验证
        capabilities = data.get("capabilities", [])
        for cap_str in capabilities:
            try:
                Capability(cap_str)
            except ValueError:
                errors.append(f"未知的能力: {cap_str}")

        # 工具验证
        tools = data.get("tools", [])
        for tool in tools:
            if tool not in self.AVAILABLE_TOOLS:
                errors.append(f"未知的工具: {tool}")

        return errors

    def _build_role(self, data: dict) -> RoleDefinition:
        """构建角色对象"""
        capabilities = []
        for cap_str in data.get("capabilities", []):
            try:
                capabilities.append(Capability(cap_str))
            except ValueError:
                pass

        # 自动推断工具（如果未指定）
        tools = data.get("tools")
        if tools is None:
            tools = self._infer_tools(capabilities)

        return RoleDefinition(
            name=data["name"],
            display_name=data.get("display_name", data["name"]),
            description=data.get("description", ""),
            goal=data.get("goal", ""),
            backstory=data.get("backstory", ""),
            tier=RoleTier.EXTENDED,  # 新创建的角色默认为扩展层级
            capabilities=capabilities,
            tools=tools,
            tool_restrictions=data.get("tool_restrictions"),
            max_iterations=data.get("max_iterations", 30),
            timeout_multiplier=data.get("timeout_multiplier", 1.0),
            tags=data.get("tags", ["custom"]),
            examples=data.get("examples", []),
        )

    def _infer_tools(self, capabilities: list[Capability]) -> list[str]:
        """根据能力推断所需工具"""
        tools = set()
        for cap in capabilities:
            if cap in self.TOOL_CAPABILITY_MAP:
                tools.update(self.TOOL_CAPABILITY_MAP[cap])
        return list(tools) if tools else None

    def _check_warnings(
        self,
        role: RoleDefinition,
        required_capabilities: list[Capability],
    ) -> list[str]:
        """检查警告"""
        warnings = []

        # 检查能力覆盖
        role_caps = set(role.capabilities)
        required_set = set(required_capabilities)
        missing = required_set - role_caps
        if missing:
            warnings.append(
                f"角色能力未完全覆盖需求，缺少: {', '.join(c.value for c in missing)}"
            )

        # 检查工具配置
        if role.tools is None:
            warnings.append("未指定工具，将使用所有可用工具")

        return warnings

    def _save_role(self, role: RoleDefinition) -> Path:
        """保存角色到文件"""
        if not self.custom_roles_dir:
            raise ValueError("未配置 custom_roles_dir")

        self.custom_roles_dir.mkdir(parents=True, exist_ok=True)
        path = self.custom_roles_dir / f"{role.name}.yaml"

        # 构建保存数据
        data = {
            "name": role.name,
            "display_name": role.display_name,
            "description": role.description,
            "goal": role.goal,
            "backstory": role.backstory,
            "tier": role.tier.value,
            "capabilities": [c.value for c in role.capabilities],
            "tools": role.tools,
            "max_iterations": role.max_iterations,
            "timeout_multiplier": role.timeout_multiplier,
            "tags": role.tags,
            "examples": role.examples,
        }

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

        return path

    def _build_confirmation_message(self, role: RoleDefinition) -> str:
        """构建确认消息"""
        return f"""
是否创建新角色？

名称: {role.display_name} ({role.name})
描述: {role.description}
目标: {role.goal}
能力: {', '.join(c.value for c in role.capabilities)}
工具: {', '.join(role.tools) if role.tools else '全部'}

输入 'yes' 确认创建，'no' 取消:
"""

    async def create_from_gap(
        self,
        gap: RoleGap,
        context: str = "",
    ) -> RoleCreationResult:
        """从能力缺口创建角色"""
        request = RoleCreationRequest(
            suggested_name=gap.suggested_role_name,
            required_capabilities=gap.missing_capabilities,
            reason=f"缺少能力: {', '.join(c.value for c in gap.missing_capabilities)}",
            context=context,
        )
        return await self.create_role(request)
```

### 4.3 角色选择器

```python
# planner/role_selector.py

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from xbot.agent.crew.planner.models import (
    Capability,
    GoalAnalysis,
    RoleDefinition,
    RolePool,
    RoleSelection,
)
from xbot.agent.crew.planner.prompts import ROLE_SELECTION_PROMPT

if TYPE_CHECKING:
    from xbot.agent.crew.planner.llm_client import LLMClient


class RoleSelector:
    """角色选择器 - 根据目标分析选择合适的角色"""

    def __init__(
        self,
        llm_client: LLMClient,
        role_creator: RoleCreator | None = None,
        allow_create_roles: bool = False,
    ):
        self.llm = llm_client
        self.role_creator = role_creator
        self.allow_create_roles = allow_create_roles

    async def select(
        self,
        analysis: GoalAnalysis,
        role_pool: RolePool,
    ) -> RoleSelection:
        """根据分析结果选择角色"""

        # 1. 基于能力匹配进行初步筛选
        candidates = role_pool.find_by_capabilities(
            analysis.required_capabilities,
            min_score=0.3,  # 宽松筛选
        )

        if not candidates:
            # 如果没有匹配，使用所有可用角色
            candidates = [(r, 0.0) for r in role_pool.get_available_roles()]

        # 2. 使用 LLM 进行最终选择
        selected = await self._llm_select(analysis, candidates)

        # 3. 分析能力缺口
        created_roles = []
        role_gaps = []

        if self.allow_create_roles and self.role_creator:
            gaps = await self.role_creator.analyze_gaps(
                analysis.required_capabilities,
                selected,
            )

            for gap in gaps:
                if gap.coverage_gap > 0.3:  # 缺口超过 30% 才创建
                    result = await self.role_creator.create_from_gap(
                        gap,
                        context=analysis.summary,
                    )
                    if result.success and result.role:
                        created_roles.append(result.role)
                        selected.append(result.role)
                else:
                    role_gaps.append(gap)

        # 4. 计算覆盖度
        coverage = self._calculate_coverage(selected, analysis.required_capabilities)

        return RoleSelection(
            selected_roles=selected,
            selection_reason={},  # LLM 可提供原因
            skipped_roles=[r.name for r, _ in candidates if r not in selected],
            coverage_score=coverage,
            created_roles=created_roles,
            role_gaps=role_gaps,
        )

    async def _llm_select(
        self,
        analysis: GoalAnalysis,
        candidates: list[tuple[RoleDefinition, float]],
    ) -> list[RoleDefinition]:
        """使用 LLM 选择角色"""

        # 构建候选角色描述
        candidates_desc = "\n".join([
            f"- {role.name}: {role.description} "
            f"(能力匹配度: {score:.0%})"
            for role, score in candidates
        ])

        prompt = ROLE_SELECTION_PROMPT.format(
            goal=analysis.summary,
            required_capabilities=", ".join(c.value for c in analysis.required_capabilities),
            complexity=analysis.complexity,
            candidates=candidates_desc,
        )

        response = await self.llm.generate(prompt)

        # 解析 LLM 返回的角色列表
        selected_names = self._parse_response(response)

        # 验证并返回
        candidate_dict = {r.name: r for r, _ in candidates}
        return [
            candidate_dict[name]
            for name in selected_names
            if name in candidate_dict
        ]

    def _parse_response(self, response: str) -> list[str]:
        """解析 LLM 响应为角色名列表"""
        import json
        import re

        # 尝试解析 JSON
        match = re.search(r'\[.*?\]', response, re.DOTALL)
        if match:
            try:
                names = json.loads(match.group())
                if isinstance(names, list):
                    return [str(n).strip() for n in names]
            except json.JSONDecodeError:
                pass

        # 回退: 提取每行的角色名
        names = []
        for line in response.split("\n"):
            line = line.strip().strip("- ").strip()
            if line and not line.startswith("#"):
                names.append(line.split(":")[0].strip())

        return names

    def _calculate_coverage(
        self,
        selected: list[RoleDefinition],
        required: list[Capability],
    ) -> float:
        """计算能力覆盖度"""
        if not required:
            return 1.0

        covered = set()
        for role in selected:
            covered.update(role.capabilities)

        required_set = set(required)
        return len(covered & required_set) / len(required_set)
```

### 4.3 任务规划器

```python
# planner/task_planner.py

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from xbot.agent.crew.planner.models import (
    GoalAnalysis,
    RoleDefinition,
    RoleSelection,
    TaskPlan,
)
from xbot.agent.crew.planner.prompts import TASK_PLANNING_PROMPT

if TYPE_CHECKING:
    from xbot.agent.crew.planner.llm_client import LLMClient


class TaskPlanner:
    """任务规划器 - 根据目标和角色规划任务"""

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def plan(
        self,
        goal: str,
        analysis: GoalAnalysis,
        role_selection: RoleSelection,
    ) -> list[TaskPlan]:
        """规划任务"""

        # 构建可用角色描述
        roles_desc = "\n".join([
            f"- {role.name}: {role.description}\n"
            f"  目标: {role.goal}\n"
            f"  能力: {', '.join(c.value for c in role.capabilities)}"
            for role in role_selection.selected_roles
        ])

        prompt = TASK_PLANNING_PROMPT.format(
            goal=goal,
            complexity=analysis.complexity,
            estimated_tasks=analysis.estimated_tasks,
            roles=roles_desc,
            constraints="\n".join(f"- {c}" for c in analysis.constraints),
        )

        response = await self.llm.generate(prompt)

        # 解析任务规划
        tasks = self._parse_tasks(response, role_selection.selected_roles)

        # 验证并修复依赖
        tasks = self._validate_and_fix_dependencies(tasks)

        # 按依赖排序
        tasks = self._topological_sort(tasks)

        return tasks

    def _parse_tasks(
        self,
        response: str,
        available_roles: list[RoleDefinition],
    ) -> list[TaskPlan]:
        """解析 LLM 响应为任务列表"""

        role_names = {r.name for r in available_roles}
        tasks = []

        # 尝试解析 JSON
        try:
            match = re.search(r'\[.*?\]', response, re.DOTALL)
            if match:
                data = json.loads(match.group())
                if isinstance(data, list):
                    for item in data:
                        if not isinstance(item, dict):
                            continue

                        agent = item.get("agent", "")
                        if agent not in role_names:
                            # 尝试模糊匹配
                            agent = self._fuzzy_match_role(agent, role_names)

                        if agent:
                            tasks.append(TaskPlan(
                                name=item.get("name", f"task_{len(tasks)+1}"),
                                description=item.get("description", ""),
                                agent=agent,
                                dependencies=item.get("dependencies", []),
                                expected_output=item.get("expected_output", ""),
                                timeout=item.get("timeout", 300),
                                human_review=item.get("human_review", False),
                            ))
        except json.JSONDecodeError:
            pass

        return tasks

    def _fuzzy_match_role(self, name: str, role_names: set[str]) -> str | None:
        """模糊匹配角色名"""
        name_lower = name.lower().replace("-", "_").replace(" ", "_")
        for rn in role_names:
            if rn.lower().replace("-", "_") == name_lower:
                return rn
        return None

    def _validate_and_fix_dependencies(self, tasks: list[TaskPlan]) -> list[TaskPlan]:
        """验证并修复依赖关系"""
        task_names = {t.name for t in tasks}

        for task in tasks:
            # 移除无效依赖
            valid_deps = [d for d in task.dependencies if d in task_names]
            task.dependencies = valid_deps

        return tasks

    def _topological_sort(self, tasks: list[TaskPlan]) -> list[TaskPlan]:
        """按依赖关系拓扑排序"""
        from collections import defaultdict, deque

        # 构建依赖图
        in_degree = {t.name: 0 for t in tasks}
        graph = defaultdict(list)

        task_map = {t.name: t for t in tasks}

        for task in tasks:
            for dep in task.dependencies:
                graph[dep].append(task.name)
                in_degree[task.name] += 1

        # 拓扑排序
        queue = deque([name for name, deg in in_degree.items() if deg == 0])
        sorted_names = []

        while queue:
            name = queue.popleft()
            sorted_names.append(name)
            for neighbor in graph[name]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # 如果有环，使用原始顺序
        if len(sorted_names) != len(tasks):
            return tasks

        return [task_map[name] for name in sorted_names]
```

### 4.4 配置生成器

```python
# planner/config_generator.py

import yaml
from pathlib import Path

from xbot.agent.crew.planner.models import CrewPlan, TaskPlan, RoleDefinition


class ConfigGenerator:
    """配置生成器 - 生成 crew_config.yaml"""

    def generate_yaml(self, plan: CrewPlan) -> str:
        """生成 YAML 配置字符串"""

        config = {
            "name": plan.name,
            "description": plan.description,
            "process": plan.process,
            "workspace": ".",
            "global_context": plan.global_context,
            "agents": self._build_agents(plan.roles),
            "tasks": self._build_tasks(plan.tasks),
        }

        return yaml.dump(config, default_flow_style=False, sort_keys=False, allow_unicode=True)

    def _build_agents(self, roles: list[RoleDefinition]) -> dict:
        """构建 agents 配置"""
        agents = {}
        for role in roles:
            agents[role.name] = {
                "description": role.description,
                "goal": role.goal,
                "backstory": role.backstory,
                "max_iterations": role.max_iterations,
            }
            if role.tools:
                agents[role.name]["tools"] = role.tools
        return agents

    def _build_tasks(self, tasks: list[TaskPlan]) -> list[dict]:
        """构建 tasks 配置"""
        result = []
        for task in tasks:
            item = {
                "name": task.name,
                "description": task.description,
                "agent": task.agent,
                "expected_output": task.expected_output,
                "timeout": task.timeout,
            }
            if task.dependencies:
                item["context_from"] = task.dependencies
            if task.human_review:
                item["human_review"] = True
            result.append(item)
        return result

    def save(self, plan: CrewPlan, path: Path) -> Path:
        """保存配置到文件"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        yaml_content = self.generate_yaml(plan)

        with open(path, "w", encoding="utf-8") as f:
            f.write("# 自动生成的 Crew 配置\n")
            f.write(f"# 生成时间: {plan.planning_time:.2f}s\n")
            f.write(f"# 置信度: {plan.confidence:.0%}\n\n")
            f.write(yaml_content)

        return path
```

### 4.5 主入口

```python
# planner/crew_planner.py

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xbot.agent.crew.planner.config_generator import ConfigGenerator
from xbot.agent.crew.planner.models import (
    CrewPlan,
    GoalAnalysis,
    RolePoolConfig,
    RoleSelection,
)
from xbot.agent.crew.planner.prompts import GOAL_ANALYSIS_PROMPT
from xbot.agent.crew.planner.role_pool import RolePoolManager
from xbot.agent.crew.planner.role_selector import RoleSelector
from xbot.agent.crew.planner.task_planner import TaskPlanner


@dataclass
class LLMClient:
    """LLM 客户端接口"""
    async def generate(self, prompt: str) -> str:
        """生成响应"""
        raise NotImplementedError


class CrewPlanner:
    """Crew 动态规划器 - 主入口"""

    def __init__(
        self,
        llm_client: LLMClient,
        role_pool_config: RolePoolConfig | None = None,
    ):
        self.llm = llm_client
        self.role_pool_config = role_pool_config or RolePoolConfig()

        # 初始化组件
        self.role_pool_manager = RolePoolManager(self.role_pool_config)
        self.role_selector = RoleSelector(llm_client)
        self.task_planner = TaskPlanner(llm_client)
        self.config_generator = ConfigGenerator()

    async def plan(
        self,
        goal: str,
        context: dict[str, Any] | None = None,
    ) -> CrewPlan:
        """
        根据目标生成 Crew 规划

        Args:
            goal: 目标描述
            context: 可选上下文 (workspace, project_type, tech_stack 等)

        Returns:
            完整的 CrewPlan
        """
        start_time = time.time()

        # Step 1: 加载角色池
        role_pool = self.role_pool_manager.get_pool()

        # Step 2: 分析目标
        analysis = await self._analyze_goal(goal, context)

        # Step 3: 选择角色
        role_selection = await self.role_selector.select(analysis, role_pool)

        # Step 4: 规划任务
        tasks = await self.task_planner.plan(goal, analysis, role_selection)

        # Step 5: 组装规划
        plan = CrewPlan(
            name=self._generate_name(goal),
            description=goal,
            process=analysis.suggested_process,
            global_context=self._build_global_context(goal, context),
            roles=role_selection.selected_roles,
            tasks=tasks,
            analysis=analysis,
            role_selection=role_selection,
            planning_time=time.time() - start_time,
            confidence=self._calculate_confidence(analysis, role_selection, tasks),
        )

        return plan

    async def generate_config(self, plan: CrewPlan) -> str:
        """生成 YAML 配置字符串"""
        return self.config_generator.generate_yaml(plan)

    async def plan_and_generate(
        self,
        goal: str,
        context: dict[str, Any] | None = None,
    ) -> tuple[CrewPlan, str]:
        """一站式: 规划并生成 YAML"""
        plan = await self.plan(goal, context)
        yaml_content = await self.generate_config(plan)
        return plan, yaml_content

    async def _analyze_goal(
        self,
        goal: str,
        context: dict[str, Any] | None,
    ) -> GoalAnalysis:
        """分析目标"""
        from xbot.agent.crew.planner.models import Capability

        context_str = ""
        if context:
            context_str = "\n".join(f"- {k}: {v}" for k, v in context.items())

        prompt = GOAL_ANALYSIS_PROMPT.format(
            goal=goal,
            context=context_str or "无",
        )

        response = await self.llm.generate(prompt)

        return self._parse_analysis(response)

    def _parse_analysis(self, response: str) -> GoalAnalysis:
        """解析目标分析结果"""
        import json
        import re

        from xbot.agent.crew.planner.models import Capability

        # 默认值
        analysis = GoalAnalysis(
            summary="",
            required_capabilities=[],
            complexity="medium",
            estimated_tasks=3,
            suggested_process="sequential",
        )

        # 尝试解析 JSON
        try:
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                data = json.loads(match.group())

                analysis.summary = data.get("summary", "")
                analysis.complexity = data.get("complexity", "medium")
                analysis.estimated_tasks = data.get("estimated_tasks", 3)
                analysis.suggested_process = data.get("suggested_process", "sequential")
                analysis.constraints = data.get("constraints", [])

                # 解析能力
                for cap_str in data.get("required_capabilities", []):
                    try:
                        analysis.required_capabilities.append(Capability(cap_str))
                    except ValueError:
                        pass
        except json.JSONDecodeError:
            pass

        return analysis

    def _generate_name(self, goal: str) -> str:
        """生成 crew 名称"""
        # 取前几个词作为名称
        words = goal.split()[:4]
        name = "_".join(w.lower() for w in words if w.isalnum())
        name = "".join(c if c.isalnum() or c == "_" else "" for c in name)
        return name or "dynamic_crew"

    def _build_global_context(self, goal: str, context: dict | None) -> str:
        """构建全局上下文"""
        lines = [f"目标: {goal}"]
        if context:
            lines.append("\n项目上下文:")
            for k, v in context.items():
                lines.append(f"- {k}: {v}")
        return "\n".join(lines)

    def _calculate_confidence(
        self,
        analysis: GoalAnalysis,
        role_selection: RoleSelection,
        tasks: list,
    ) -> float:
        """计算规划置信度"""
        # 基于能力覆盖度和任务数量匹配度
        coverage = role_selection.coverage_score
        task_match = 1.0 - abs(len(tasks) - analysis.estimated_tasks) / max(len(tasks), analysis.estimated_tasks, 1)

        return (coverage * 0.6 + task_match * 0.4)
```

---

## 五、LLM Prompt 模板

```python
# planner/prompts.py

GOAL_ANALYSIS_PROMPT = """
你是一个任务分析专家。请分析以下目标，输出 JSON 格式的分析结果。

## 目标
{goal}

## 上下文
{context}

## 输出格式
请输出一个 JSON 对象，包含以下字段：
{{
  "summary": "目标摘要（一句话）",
  "required_capabilities": ["能力1", "能力2", ...],
  "complexity": "simple|medium|complex",
  "estimated_tasks": 预估任务数量（整数）,
  "suggested_process": "sequential|hierarchical",
  "constraints": ["约束条件1", "约束条件2", ...]
}}

## 可用能力列表
- search: 搜索信息
- analyze: 分析数据/代码
- summarize: 总结归纳
- read_code: 阅读代码
- write_code: 编写代码
- refactor: 重构代码
- debug: 调试排错
- review: 代码审查
- test: 测试编写
- validate: 验证校验
- document: 编写文档
- data_analysis: 数据分析
- deploy: 部署发布
- security_audit: 安全审计

请只输出 JSON，不要有其他内容。
"""

ROLE_SELECTION_PROMPT = """
你是一个团队组建专家。请根据目标分析结果，从候选角色中选择最合适的角色组合。

## 目标
{goal}

## 所需能力
{required_capabilities}

## 复杂度
{complexity}

## 候选角色
{candidates}

## 输出格式
请输出一个 JSON 数组，包含选中的角色名称，按重要性排序：
["role_name_1", "role_name_2", ...]

选择原则：
1. 优先选择能力匹配度高的角色
2. 避免冗余，一个角色能完成的不要选多个
3. 复杂任务可能需要多个角色协作
4. 简单任务尽量用最少的角色

请只输出 JSON 数组，不要有其他内容。
"""

TASK_PLANNING_PROMPT = """
你是一个任务规划专家。请根据目标和可用角色，规划具体的任务列表。

## 目标
{goal}

## 复杂度
{complexity}

## 预估任务数量
{estimated_tasks}

## 可用角色（只能从中选择）
{roles}

## 约束条件
{constraints}

## 输出格式
请输出一个 JSON 数组，每个任务是一个对象：
[
  {{
    "name": "task_name",
    "description": "任务描述",
    "agent": "角色名称",
    "dependencies": ["依赖的任务名"],
    "expected_output": "期望输出描述",
    "timeout": 300,
    "human_review": false
  }},
  ...
]

规划原则：
1. 任务要具体、可执行
2. 依赖关系要合理（不能循环依赖）
3. 每个任务只能分配给一个可用角色
4. 任务粒度适中，不要过大或过小
5. 按执行顺序排列

请只输出 JSON 数组，不要有其他内容。
"""

ROLE_CREATION_PROMPT = """
你是一个角色设计专家。请根据需求创建一个新的角色定义。

## 建议名称
{suggested_name}

## 所需能力
{required_capabilities}

## 创建原因
{reason}

## 上下文
{context}

## 输出格式
请输出一个 JSON 对象，定义新角色：
{{
  "name": "角色标识（英文，下划线分隔）",
  "display_name": "显示名称",
  "description": "角色描述（一句话）",
  "goal": "角色目标",
  "backstory": "背景故事（2-3句话）",
  "capabilities": ["能力1", "能力2", ...],
  "tools": ["工具1", "工具2", ...] 或 null（自动推断）,
  "max_iterations": 30,
  "timeout_multiplier": 1.0,
  "tags": ["标签1", "标签2"],
  "examples": ["适用场景1", "适用场景2"]
}}

## 可用能力列表
- search: 搜索信息
- analyze: 分析数据/代码
- summarize: 总结归纳
- read_code: 阅读代码
- write_code: 编写代码
- refactor: 重构代码
- debug: 调试排错
- review: 代码审查
- test: 测试编写
- validate: 验证校验
- document: 编写文档
- data_analysis: 数据分析
- deploy: 部署发布
- security_audit: 安全审计

## 可用工具列表
- read_file: 读取文件
- write_file: 写入文件
- edit_file: 编辑文件
- list_dir: 列出目录
- web_search: 网页搜索
- web_fetch: 网页抓取
- bash: 执行命令

设计原则：
1. 角色定义要清晰、专业
2. 能力要与创建原因匹配
3. 工具选择要合理，不要过多或过少
4. backstory 要体现角色的专业性

请只输出 JSON，不要有其他内容。
"""
```

---

## 六、角色定义示例

### 6.1 核心角色

```yaml
# role_pool/core/researcher.yaml
name: researcher
display_name: 研究员
description: 信息收集与分析专家
goal: 收集、整理、分析信息，为决策提供依据
backstory: |
  你是一个经验丰富的研究员，擅长快速收集和分析信息。
  你能够从多个来源提取关键信息，并组织成结构化的报告。
tier: core
capabilities:
  - search
  - analyze
  - summarize
tools:
  - web_search
  - web_fetch
  - read_file
  - list_dir
max_iterations: 25
timeout_multiplier: 1.0
tags:
  - 信息处理
  - 分析
examples:
  - 搜索相关资料
  - 分析代码库结构
  - 收集技术文档
```

```yaml
# role_pool/core/coder.yaml
name: coder
display_name: 开发工程师
description: 代码编写与修改专家
goal: 编写高质量、可维护的代码
backstory: |
  你是一个资深开发者，精通多种编程语言和框架。
  你编写的代码清晰、高效、易于维护。
tier: core
capabilities:
  - read_code
  - write_code
  - refactor
  - debug
tools:
  - read_file
  - write_file
  - edit_file
  - bash
max_iterations: 40
timeout_multiplier: 1.2
tags:
  - 编码
  - 重构
  - 调试
examples:
  - 实现新功能
  - 修复 bug
  - 重构代码
```

```yaml
# role_pool/core/reviewer.yaml
name: reviewer
display_name: 代码审查员
description: 代码质量审查专家
goal: 发现代码问题，提出改进建议
backstory: |
  你是一个严格的代码审查员，对代码质量有很高的标准。
  你能发现潜在的问题，并提出具体的改进建议。
tier: core
capabilities:
  - read_code
  - review
  - analyze
tools:
  - read_file
  - list_dir
max_iterations: 30
timeout_multiplier: 1.0
tags:
  - 审查
  - 质量保障
examples:
  - 代码质量审查
  - 安全漏洞检测
  - 最佳实践检查
```

```yaml
# role_pool/core/tester.yaml
name: tester
display_name: 测试工程师
description: 测试编写与验证专家
goal: 编写全面的测试，确保代码质量
backstory: |
  你是一个专业的测试工程师，擅长编写各种类型的测试。
  你能够发现边界情况和潜在的问题。
tier: core
capabilities:
  - read_code
  - write_code
  - test
  - validate
tools:
  - read_file
  - write_file
  - edit_file
  - bash
max_iterations: 35
timeout_multiplier: 1.0
tags:
  - 测试
  - 验证
examples:
  - 编写单元测试
  - 编写集成测试
  - 测试覆盖率分析
```

### 6.2 扩展角色

```yaml
# role_pool/extended/doc_writer.yaml
name: doc_writer
display_name: 文档工程师
description: 技术文档编写专家
goal: 编写清晰、完整的技术文档
backstory: |
  你是一个技术文档专家，能够将复杂的概念用简单的语言表达。
  你编写的文档结构清晰、易于理解。
tier: extended
capabilities:
  - analyze
  - summarize
  - document
tools:
  - read_file
  - write_file
  - list_dir
max_iterations: 25
timeout_multiplier: 1.0
tags:
  - 文档
  - 写作
examples:
  - API 文档编写
  - README 编写
  - 用户手册编写
```

### 6.3 专业角色

```yaml
# role_pool/specialist/security_auditor.yaml
name: security_auditor
display_name: 安全审计专家
description: 代码安全审计专家
goal: 发现安全漏洞，提出修复建议
backstory: |
  你是一个专业的安全审计员，熟悉各种安全漏洞和攻击模式。
  你能够发现潜在的安全风险并提供修复方案。
tier: specialist
capabilities:
  - read_code
  - analyze
  - security_audit
tools:
  - read_file
  - list_dir
  - bash
max_iterations: 40
timeout_multiplier: 1.5
tags:
  - 安全
  - 审计
examples:
  - 安全漏洞扫描
  - 代码安全审计
  - 渗透测试辅助
```

---

## 七、CLI 命令设计

### 7.1 规划命令

```bash
# 1. 规划并生成配置
xbot crew plan "分析这个项目的代码质量，找出潜在bug并修复" \
  --workspace ./myproject \
  --output ./crew_config.yaml

# 2. 规划并直接运行
xbot crew run-dynamic "生成这个项目的API文档" \
  --workspace ./myproject \
  --tier extended

# 3. 规划预览（不生成文件）
xbot crew plan "..." --preview

# 4. 保存生成的配置为模板
xbot crew plan "..." --save-template my_workflow

# 5. 规划时允许创建角色
xbot crew plan "分析机器学习模型性能" \
  --allow-create-roles \
  --custom-roles-dir ./my_roles
```

### 7.2 角色管理命令

```bash
# === 角色查看 ===

# 查看所有可用角色
xbot crew roles list

# 按层级查看
xbot crew roles list --tier core
xbot crew roles list --tier extended
xbot crew roles list --tier specialist

# 查看自定义角色
xbot crew roles list --custom-dir ./my_roles

# 查看角色详情
xbot crew roles show researcher
xbot crew roles show my_custom_role --custom-dir ./my_roles

# === 角色创建 ===

# 交互式创建（推荐）
xbot crew roles create

# 快速创建（指定参数）
xbot crew roles create \
  --name my_analyst \
  --display-name "数据分析专家" \
  --description "数据分析与可视化专家" \
  --capabilities "analyze,data_analysis" \
  --tools "read_file,bash" \
  --output ./my_roles

# 从模板创建（复制现有角色并修改）
xbot crew roles create --from-template researcher \
  --name my_researcher \
  --display-name "我的研究员"

# 从 YAML 文件创建
xbot crew roles create --from-file ./role_draft.yaml

# === 角色管理 ===

# 验证角色定义
xbot crew roles validate ./my_roles/custom_role.yaml

# 编辑角色（打开编辑器）
xbot crew roles edit my_custom_role --custom-dir ./my_roles

# 复制角色
xbot crew roles copy researcher my_researcher --custom-dir ./my_roles

# 删除自定义角色
xbot crew roles delete my_custom_role --custom-dir ./my_roles

# 导出角色
xbot crew roles export my_custom_role --output ./exported_roles

# 导入角色
xbot crew roles import ./downloaded_role.yaml --custom-dir ./my_roles
```

### 7.3 角色创建交互示例

```
$ xbot crew roles create

🚀 角色创建向导

? 角色名称 (英文标识): ml_engineer
? 显示名称: 机器学习工程师
? 角色描述: 机器学习模型训练与优化专家
? 角色目标: 构建和优化机器学习模型
? 背景故事: 你是一个经验丰富的机器学习工程师，精通各种模型架构和训练技巧

? 选择能力 (空格选择，回车确认):
  ◯ search        - 搜索信息
  ◉ analyze       - 分析数据/代码
  ◯ summarize     - 总结归纳
  ◯ read_code     - 阅读代码
  ◉ write_code    - 编写代码
  ◯ refactor      - 重构代码
  ◯ debug         - 调试排错
  ◯ review        - 代码审查
  ◉ test          - 测试编写
  ◯ validate      - 验证校验
  ◯ document      - 编写文档
  ◉ data_analysis - 数据分析
  ◯ deploy        - 部署发布
  ◯ security_audit - 安全审计

? 选择工具 (空格选择，回车确认):
  ◉ read_file  - 读取文件
  ◉ write_file - 写入文件
  ◉ edit_file  - 编辑文件
  ◯ list_dir   - 列出目录
  ◯ web_search - 网页搜索
  ◯ web_fetch  - 网页抓取
  ◉ bash       - 执行命令

? 最大迭代次数 (默认 30): 35
? 超时倍数 (默认 1.0): 1.2

? 适用场景示例 (可选，回车跳过):
  场景 1: 训练机器学习模型
  场景 2: 优化模型性能
  场景 3: (回车结束)

? 保存位置:
  ❯ ./my_roles (自定义角色目录)
    ~/.xbot/roles (全局角色目录)
    ./project_roles (项目角色目录)

✓ 角色创建成功！

角色定义预览:
┌────────────────────────────────────────────┐
│ 名称: ml_engineer                           │
│ 显示名: 机器学习工程师                       │
│ 描述: 机器学习模型训练与优化专家              │
│ 能力: analyze, write_code, test, data_analysis │
│ 工具: read_file, write_file, edit_file, bash │
│ 迭代: 35 | 超时: 1.2x                       │
└────────────────────────────────────────────┘

? 确认保存? (Y/n) Y

✓ 已保存到: ./my_roles/ml_engineer.yaml

你可以通过以下方式使用:
  xbot crew roles show ml_engineer --custom-dir ./my_roles
  xbot crew plan "..." --custom-roles-dir ./my_roles
```

---

## 八、配置示例

### 输入

```
目标: 分析这个 Python 项目的代码质量，找出潜在的 bug，并生成修复建议

上下文:
- workspace: /home/user/myproject
- project_type: python
- tech_stack: fastapi, sqlalchemy
```

### 输出

```yaml
# 自动生成的 Crew 配置
# 生成时间: 2.35s
# 置信度: 85%

name: analyze_code_quality_find
description: 分析这个 Python 项目的代码质量，找出潜在的 bug，并生成修复建议
process: sequential
workspace: .

global_context: |
  目标: 分析这个 Python 项目的代码质量，找出潜在的 bug，并生成修复建议

  项目上下文:
  - project_type: python
  - tech_stack: fastapi, sqlalchemy

agents:
  reviewer:
    description: 代码质量审查专家
    goal: 发现代码问题，提出改进建议
    backstory: |
      你是一个严格的代码审查员，对代码质量有很高的标准。
      你能发现潜在的问题，并提出具体的改进建议。
    max_iterations: 30

  coder:
    description: 代码编写与修改专家
    goal: 编写高质量、可维护的代码
    backstory: |
      你是一个资深开发者，精通多种编程语言和框架。
      你编写的代码清晰、高效、易于维护。
    max_iterations: 40

  tester:
    description: 测试工程师
    goal: 编写全面的测试，确保代码质量
    backstory: |
      你是一个专业的测试工程师，擅长编写各种类型的测试。
    max_iterations: 35

tasks:
  - name: review_codebase
    description: |
      审查项目代码库，分析代码质量。

      检查项:
      1. 代码结构和组织
      2. 命名规范和可读性
      3. 潜在 bug 和错误处理
      4. 性能问题
      5. 安全隐患
    agent: reviewer
    expected_output: 代码质量报告，包含问题列表和优先级
    timeout: 300

  - name: analyze_bugs
    description: |
      深入分析代码审查中发现的潜在 bug。

      对于每个 bug:
      1. 解释问题原因
      2. 分析影响范围
      3. 建议修复方案
    agent: reviewer
    context_from:
      - review_codebase
    expected_output: Bug 分析报告，包含修复建议
    timeout: 240

  - name: generate_fixes
    description: |
      根据分析结果，生成具体的代码修复方案。

      对于每个需要修复的问题:
      1. 展示当前代码
      2. 展示修复后的代码
      3. 解释修复逻辑
    agent: coder
    context_from:
      - review_codebase
      - analyze_bugs
    expected_output: 代码修复建议和示例
    timeout: 300

  - name: write_tests
    description: |
      为修复的代码编写回归测试。
    agent: tester
    context_from:
      - generate_fixes
    expected_output: 测试代码文件
    timeout: 240
```

---

## 九、实现优先级

```
Phase 1 - 核心功能 (P0)
├── 数据模型定义 (models.py)
├── 角色池管理 (role_pool.py)
├── 角色选择器 (role_selector.py)
├── 任务规划器 (task_planner.py)
├── 配置生成器 (config_generator.py)
├── 主入口 (crew_planner.py)
└── Prompt 模板

Phase 2 - 角色创建与管理 (P0.5)
├── 角色创建器 (role_creator.py)
├── 角色验证逻辑
├── 工具能力映射
├── 自动工具推断
├── 角色保存机制
├── 与角色选择器集成
│
├── 角色管理 CLI (新增) ← 独立角色管理能力
│   ├── xbot crew roles list      # 列出角色
│   ├── xbot crew roles show      # 查看详情
│   ├── xbot crew roles create    # 交互式创建
│   ├── xbot crew roles validate  # 验证角色
│   ├── xbot crew roles edit      # 编辑角色
│   ├── xbot crew roles copy      # 复制角色
│   ├── xbot crew roles delete    # 删除角色
│   ├── xbot crew roles export    # 导出角色
│   └── xbot crew roles import    # 导入角色
│
└── 交互式创建向导
    ├── 能力选择界面
    ├── 工具选择界面
    ├── 参数配置
    └── 预览与确认

Phase 3 - 集成与规划 CLI (P1)
├── 规划命令实现
│   ├── xbot crew plan
│   ├── xbot crew run-dynamic
│   └── --allow-create-roles 支持
├── 与现有 Orchestrator 集成
├── 错误处理和验证
└── 单元测试

Phase 4 - 角色池扩展 (P2)
├── 更多预定义角色
├── 角色覆盖机制
├── 角色模板库
├── 全局角色目录 (~/.xbot/roles)
└── 项目角色目录 (./.xbot/roles)

Phase 5 - 优化与增强 (P3)
├── 角色创建用户确认流程
├── 规划缓存
├── 用户反馈学习
├── 规划质量评估
└── 规划结果可视化
```

---

## 十、设计决策说明

### 10.1 为什么分离角色定义和任务规划？

| 维度 | 分离架构 | 一体化架构 |
|------|---------|-----------|
| **可控性** | ✅ 角色池可审核、可约束 | ❌ LLM 可能创建奇怪角色 |
| **一致性** | ✅ 角色定义标准化 | ❌ 每次生成的角色不一致 |
| **复用性** | ✅ 角色池可跨任务复用 | ❌ 每次重新生成 |
| **调试性** | ✅ 角色问题易定位 | ❌ 难以复现问题 |
| **灵活性** | ⚠️ 受限于预定义角色 | ✅ 可应对新场景 |

**结论**: 分离架构更符合工程实践，保证可控性和可维护性。

### 10.2 角色池分层设计的考虑

- **Core (核心)**: 最常用的角色，始终可用，减少配置负担
- **Extended (扩展)**: 特定场景需要的角色，按需启用
- **Specialist (专业)**: 高级或敏感操作的角色，需要显式配置

这种分层设计平衡了易用性和灵活性。

### 10.3 能力匹配的作用

能力匹配是角色选择的辅助手段：

1. **快速筛选**: 在 LLM 选择前，先过滤掉明显不匹配的角色
2. **量化评估**: 匹配度分数可以作为 LLM 决策的参考
3. **覆盖度计算**: 评估所选角色组合是否覆盖所有所需能力

---

## 十一、角色创建设计决策

### 11.1 角色创建触发条件

角色创建不是随意触发的，需要满足一定条件：

| 条件 | 阈值 | 说明 |
|------|------|------|
| 能力缺口 | > 30% | 所需能力中有超过 30% 没有角色覆盖 |
| 显式请求 | 用户指定 | 用户通过 `--allow-create-roles` 启用 |
| 无可用角色 | 匹配度为 0 | 所有候选角色的能力匹配度都为 0 |

### 11.2 角色创建安全约束

```python
# 角色创建的安全约束
ROLE_CREATION_CONSTRAINTS = {
    # 名称约束
    "name_pattern": r"^[a-z][a-z0-9_]*$",  # 小写字母开头
    "name_max_length": 50,

    # 工具约束
    "allowed_tools": {
        "read_file", "write_file", "edit_file", "list_dir",
        "web_search", "web_fetch", "bash",
    },
    "dangerous_tools": {
        "bash",  # 需要额外审核
    },

    # 能力约束
    "max_capabilities": 5,  # 单个角色最多 5 个能力

    # 迭代约束
    "max_iterations_range": (10, 50),
    "timeout_multiplier_range": (0.5, 2.0),
}
```

### 11.3 角色创建流程

```
┌─────────────────────────────────────────────────────────────┐
│  Step 1: 发现缺口                                            │
│  - 分析所需能力 vs 现有角色能力                               │
│  - 计算覆盖缺口                                              │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼ 缺口 > 阈值
┌─────────────────────────────────────────────────────────────┐
│  Step 2: 生成角色定义                                        │
│  - LLM 生成角色定义                                          │
│  - 包含 name, description, capabilities, tools 等            │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 3: 验证                                                │
│  - 名称格式验证                                              │
│  - 能力有效性验证                                            │
│  - 工具白名单验证                                            │
│  - 安全检查（如 bash 工具）                                   │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼ 验证通过
┌─────────────────────────────────────────────────────────────┐
│  Step 4: 确认（可选）                                        │
│  - 展示角色定义给用户                                         │
│  - 用户确认或修改                                            │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼ 用户确认
┌─────────────────────────────────────────────────────────────┐
│  Step 5: 保存                                                │
│  - 保存到 custom_roles_dir                                   │
│  - 加入当前角色池                                            │
└─────────────────────────────────────────────────────────────┘
```

### 11.4 角色创建与预定义角色的平衡

| 场景 | 处理方式 |
|------|---------|
| 需求匹配已有角色 | 直接使用，不创建 |
| 需求与已有角色相似 | 优先使用已有角色 + 提示用户可自定义 |
| 需求完全无匹配 | 允许创建新角色 |
| 创建的角色与已有角色重复 | 提示用户，建议使用已有角色 |

### 11.5 独立角色管理的定位

独立角色管理是系统的一等公民，与动态规划同等重要：

```
┌─────────────────────────────────────────────────────────────┐
│                    角色管理能力架构                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────┐      ┌─────────────────┐              │
│  │  预定义角色池    │      │  自定义角色池    │              │
│  │  (内置/系统)    │      │  (用户创建)      │              │
│  └────────┬────────┘      └────────┬────────┘              │
│           │                        │                        │
│           └────────────┬───────────┘                        │
│                        │                                    │
│                        ▼                                    │
│           ┌─────────────────────────┐                      │
│           │     统一角色池管理       │                      │
│           │  - 加载  - 查询  - 验证  │                      │
│           └────────────┬────────────┘                      │
│                        │                                    │
│          ┌─────────────┼─────────────┐                      │
│          ▼             ▼             ▼                      │
│   ┌────────────┐ ┌────────────┐ ┌────────────┐             │
│   │ 规划时选择 │ │ 独立创建   │ │ 角色管理   │             │
│   │ (自动)     │ │ (手动)     │ │ (CLI)      │             │
│   └────────────┘ └────────────┘ └────────────┘             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**使用场景对比**:

| 方式 | 命令 | 适用场景 |
|------|------|---------|
| 规划时创建 | `xbot crew plan --allow-create-roles` | 临时需求、快速响应 |
| 独立创建 | `xbot crew roles create` | 预先准备、精细配置 |
| 从模板创建 | `xbot crew roles create --from-template` | 基于现有角色定制 |
| 从文件导入 | `xbot crew roles import` | 团队共享、角色分发 |

**角色存储位置优先级**:

```
1. 项目级: ./project_roles/        (优先级最高，项目专用)
2. 用户级: ~/.xbot/roles/          (用户自定义，跨项目)
3. 系统级: xbot/agent/crew/role_pool/  (内置角色)
```

---

## 十二、后续扩展方向

1. **规划缓存**: 相似目标复用已有规划
2. **用户反馈学习**: 根据执行结果优化规划
3. **多目标规划**: 支持同时处理多个相关目标
4. **动态调整**: 执行过程中根据反馈调整规划
5. **角色能力扩展**: 支持角色动态获取新能力
6. **角色市场**: 共享和下载社区创建的角色
7. **角色性能追踪**: 追踪角色执行效率，优化角色定义
8. **角色组合推荐**: 基于历史数据推荐最佳角色组合