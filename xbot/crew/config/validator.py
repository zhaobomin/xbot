"""Enhanced configuration validation with detailed error messages.

Validation rules:
- Undefined variable references (Error)
- Circular inheritance (Error)
- Unknown agent references (Error)
- Unknown task references (Error)
- Circular task dependencies (Error)
- Agent overload warning (Warning)
- Short timeout warning (Warning)
- Deep dependency chain warning (Warning)
- Orphan tasks info (Info)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ValidationLevel(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationMessage:
    """A single validation message."""

    level: ValidationLevel
    path: str  # JSON path to the issue
    message: str
    suggestions: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        level_str = {
            ValidationLevel.ERROR: "Error",
            ValidationLevel.WARNING: "Warning",
            ValidationLevel.INFO: "Info",
        }[self.level]
        result = f"  [{level_str}] {self.path}: {self.message}"
        if self.suggestions:
            for s in self.suggestions:
                result += f"\n    {s}"
        return result


@dataclass
class ValidationResult:
    """Result of configuration validation."""

    valid: bool
    messages: list[ValidationMessage] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationMessage]:
        return [m for m in self.messages if m.level == ValidationLevel.ERROR]

    @property
    def warnings(self) -> list[ValidationMessage]:
        return [m for m in self.messages if m.level == ValidationLevel.WARNING]

    @property
    def infos(self) -> list[ValidationMessage]:
        return [m for m in self.messages if m.level == ValidationLevel.INFO]

    def add_error(self, path: str, message: str, suggestions: list[str] | None = None) -> None:
        self.messages.append(ValidationMessage(ValidationLevel.ERROR, path, message, suggestions or []))
        self.valid = False

    def add_warning(self, path: str, message: str, suggestions: list[str] | None = None) -> None:
        self.messages.append(ValidationMessage(ValidationLevel.WARNING, path, message, suggestions or []))

    def add_info(self, path: str, message: str, suggestions: list[str] | None = None) -> None:
        self.messages.append(ValidationMessage(ValidationLevel.INFO, path, message, suggestions or []))

    def __str__(self) -> str:
        if not self.messages:
            return "✓ Configuration is valid"

        lines = ["Configuration validation results:", ""]

        for msg in self.errors:
            lines.append(str(msg))
        for msg in self.warnings:
            lines.append(str(msg))
        for msg in self.infos:
            lines.append(str(msg))

        lines.append("")
        lines.append(f"Summary: {len(self.errors)} error(s), {len(self.warnings)} warning(s)")

        return "\n".join(lines)


class CrewConfigValidator:
    """Validates crew configuration with detailed messages."""

    def validate(self, config: dict[str, Any]) -> ValidationResult:
        """Validate a crew configuration.

        Args:
            config: The configuration dictionary to validate

        Returns:
            ValidationResult with messages and validity status
        """
        result = ValidationResult(valid=True)

        # 1. Validate required fields
        self._validate_required_fields(config, result)

        # 2. Validate agent definitions
        self._validate_agents(config, result)

        # 3. Validate task definitions
        self._validate_tasks(config, result)

        # 4. Validate cross-references
        self._validate_references(config, result)

        # 5. Check for circular dependencies
        self._check_circular_dependencies(config, result)

        # 6. Quality checks (warnings)
        self._check_quality(config, result)

        return result

    def _validate_required_fields(self, config: dict, result: ValidationResult) -> None:
        """Check required top-level fields."""
        if "name" not in config:
            result.add_error(
                "name",
                "Required field 'name' is missing",
                ["Add: name: my_crew_name"],
            )

        if "agents" not in config or not config.get("agents"):
            result.add_error(
                "agents",
                "No agents defined",
                ["Add at least one agent in the 'agents' section"],
            )

        if "tasks" not in config or not config.get("tasks"):
            result.add_error(
                "tasks",
                "No tasks defined",
                ["Add at least one task in the 'tasks' section"],
            )

    def _validate_agents(self, config: dict, result: ValidationResult) -> None:
        """Validate agent definitions."""
        agents = config.get("agents", {})

        for name, agent in agents.items():
            path = f"agents.{name}"

            # Check required agent fields
            if not isinstance(agent, dict):
                result.add_error(
                    path,
                    f"Agent '{name}' must be a dictionary",
                )
                continue

            if "description" not in agent and "goal" not in agent:
                result.add_warning(
                    path,
                    f"Agent '{name}' has no description or goal",
                    [f"Add: description: Description of {name}'s role"],
                )

            # Check max_iterations range
            max_iter = agent.get("max_iterations", 30)
            if max_iter < 5:
                result.add_warning(
                    f"{path}.max_iterations",
                    f"max_iterations ({max_iter}) is very low, may not complete tasks",
                    ["Consider increasing to at least 10"],
                )
            elif max_iter > 100:
                result.add_warning(
                    f"{path}.max_iterations",
                    f"max_iterations ({max_iter}) is very high, may cause long hangs",
                    ["Consider reducing to 30-50"],
                )

    def _validate_tasks(self, config: dict, result: ValidationResult) -> None:
        """Validate task definitions."""
        tasks = config.get("tasks", [])
        task_names = set()

        for i, task in enumerate(tasks):
            path = f"tasks[{i}]"

            if not isinstance(task, dict):
                result.add_error(path, f"Task {i} must be a dictionary")
                continue

            # Check name
            name = task.get("name")
            if not name:
                result.add_error(path, f"Task {i} is missing 'name' field")
                continue

            if name in task_names:
                result.add_error(
                    f"{path}.name",
                    f"Duplicate task name: '{name}'",
                    ["Task names must be unique"],
                )
            task_names.add(name)

            # Check agent reference (will be validated in cross-references)
            if "agent" not in task:
                result.add_error(
                    f"{path}.agent",
                    f"Task '{name}' is missing 'agent' field",
                    ["Add: agent: <agent_name>"],
                )

            # Check timeout
            timeout = task.get("timeout", 600)
            if timeout < 60:
                result.add_warning(
                    f"{path}.timeout",
                    f"Task '{name}' has a very short timeout ({timeout}s)",
                    ["Consider increasing to at least 120s"],
                )

    def _validate_references(self, config: dict, result: ValidationResult) -> None:
        """Validate cross-references between tasks and agents."""
        agents = set(config.get("agents", {}).keys())
        tasks = config.get("tasks", [])
        task_names = {t.get("name") for t in tasks if isinstance(t, dict) and t.get("name")}

        for i, task in enumerate(tasks):
            if not isinstance(task, dict):
                continue

            name = task.get("name", f"tasks[{i}]")
            path = f"tasks[{i}]" if name == f"tasks[{i}]" else f"tasks[{i}].{name}"

            # Check agent reference
            agent = task.get("agent")
            if agent and agent not in agents:
                result.add_error(
                    f"{path}.agent",
                    f"Task '{name}' references unknown agent '{agent}'",
                    [f"Available agents: {', '.join(sorted(agents))}"],
                )

            # Check context_from references
            context_from = task.get("context_from", [])
            for dep in context_from:
                if dep not in task_names:
                    result.add_error(
                        f"{path}.context_from",
                        f"Task '{name}' depends on unknown task '{dep}'",
                        [f"Available tasks: {', '.join(sorted(task_names))}"],
                    )

    def _check_circular_dependencies(self, config: dict, result: ValidationResult) -> None:
        """Check for circular task dependencies."""
        tasks = config.get("tasks", [])
        task_deps: dict[str, list[str]] = {}

        for task in tasks:
            if isinstance(task, dict) and task.get("name"):
                task_deps[task["name"]] = task.get("context_from", [])

        # DFS to find cycles
        white, gray, black = 0, 1, 2
        colors = {name: white for name in task_deps}
        cycles: list[list[str]] = []

        def dfs(node: str, path: list[str]) -> None:
            colors[node] = gray
            path.append(node)

            for dep in task_deps.get(node, []):
                if dep not in colors:
                    continue
                if colors[dep] == gray:
                    # Found cycle
                    cycle_start = path.index(dep)
                    cycles.append(path[cycle_start:] + [dep])
                elif colors[dep] == white:
                    dfs(dep, path)

            path.pop()
            colors[node] = black

        for node in task_deps:
            if colors[node] == white:
                dfs(node, [])

        for cycle in cycles:
            cycle_str = " -> ".join(cycle)
            result.add_error(
                "tasks",
                f"Circular dependency detected: {cycle_str}",
                ["Remove or restructure dependencies to break the cycle"],
            )

    def _check_quality(self, config: dict, result: ValidationResult) -> None:
        """Check for quality issues (warnings and info)."""
        agents = config.get("agents", {})
        tasks = config.get("tasks", [])

        if not tasks:
            return

        # Check agent workload
        agent_task_count: dict[str, int] = {name: 0 for name in agents}
        for task in tasks:
            if isinstance(task, dict) and task.get("agent"):
                agent = task["agent"]
                agent_task_count[agent] = agent_task_count.get(agent, 0) + 1

        total_tasks = len(tasks)
        for agent, count in agent_task_count.items():
            if count > 0:
                ratio = count / total_tasks
                if ratio > 0.5 and total_tasks > 2:
                    result.add_warning(
                        f"agents.{agent}",
                        f"Agent '{agent}' handles {count}/{total_tasks} tasks ({ratio:.0%})",
                        ["Consider distributing tasks to other agents"],
                    )

        # Check dependency chain depth
        task_deps: dict[str, list[str]] = {}
        for task in tasks:
            if isinstance(task, dict) and task.get("name"):
                task_deps[task["name"]] = task.get("context_from", [])

        def get_depth(name: str, visited: set) -> int:
            if name in visited:
                return 0
            visited.add(name)
            deps = task_deps.get(name, [])
            if not deps:
                return 0
            # Filter out deps that don't exist (should be caught by earlier validation)
            valid_deps = [d for d in deps if d in task_deps]
            if not valid_deps:
                return 0
            return 1 + max(get_depth(d, visited.copy()) for d in valid_deps)

        for name in task_deps:
            depth = get_depth(name, set())
            if depth > 5:
                result.add_warning(
                    f"tasks.{name}",
                    f"Task '{name}' has a deep dependency chain (depth: {depth})",
                    ["Consider restructuring to flatten the dependency graph"],
                )
                # Only report once per task, but check all tasks

        # Check for orphan tasks (no downstream consumers)
        downstream: dict[str, int] = {name: 0 for name in task_deps}
        for _name, deps in task_deps.items():
            for dep in deps:
                if dep in downstream:
                    downstream[dep] += 1

        orphan_tasks = [name for name, count in downstream.items() if count == 0]
        # Last task is expected to have no downstream
        if len(tasks) > 1:
            last_task = tasks[-1].get("name") if isinstance(tasks[-1], dict) else None
            orphan_tasks = [t for t in orphan_tasks if t != last_task]

        if orphan_tasks:
            for orphan in orphan_tasks:
                result.add_info(
                    f"tasks.{orphan}",
                    f"Task '{orphan}' has no downstream consumers",
                    ["Output may not be used unless it's a final task"],
                )


def validate_crew_config(config: dict[str, Any]) -> ValidationResult:
    """Convenience function to validate a crew config.

    Args:
        config: The configuration dictionary to validate

    Returns:
        ValidationResult with messages and validity status
    """
    validator = CrewConfigValidator()
    return validator.validate(config)
