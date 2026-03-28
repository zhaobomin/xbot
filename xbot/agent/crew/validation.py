"""Input validation for crew execution.

This module provides validation utilities to catch errors before execution,
enabling fail-fast behavior with clear error messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from xbot.agent.crew.models import CrewConfig, TaskDefinition


@dataclass
class ValidationError:
    """Represents a single validation error."""

    task_name: str
    field: str
    message: str

    def __str__(self) -> str:
        return f"Task '{self.task_name}': {self.field} - {self.message}"

    def to_result_message(self) -> str:
        """Convert to a user-friendly error message."""
        return f"Validation failed: {self.message}"


@dataclass
class ValidationWarning:
    """Represents a validation warning (non-fatal)."""

    message: str


class CrewValidator:
    """Validator for crew configuration and tasks.

    This class provides validation methods to check inputs before execution.
    All validation is performed upfront to enable fail-fast behavior.

    Design principles:
    1. Validate early, fail fast
    2. Clear error messages
    3. Separate errors (blocking) from warnings (non-blocking)
    """

    @classmethod
    def validate_crew_config(cls, config: CrewConfig) -> list[ValidationWarning]:
        """Validate crew configuration.

        Returns a list of warnings (non-fatal issues).
        Fatal issues raise exceptions during config loading.

        Args:
            config: Crew configuration to validate

        Returns:
            List of validation warnings
        """
        warnings = []

        # Check for empty agents
        if not config.agents:
            warnings.append(ValidationWarning("No agents defined in crew configuration"))

        # Check for empty tasks
        if not config.tasks:
            warnings.append(ValidationWarning("No tasks defined in crew configuration"))

        # Check for unused agents
        used_agents = {t.agent for t in config.tasks}
        unused_agents = set(config.agents.keys()) - used_agents
        if unused_agents:
            warnings.append(ValidationWarning(
                f"Agents defined but not used: {', '.join(unused_agents)}"
            ))

        # Check manager agent for hierarchical process
        if config.process.value == "hierarchical":
            if not config.manager_agent:
                warnings.append(ValidationWarning(
                    "Hierarchical process requires manager_agent, will use sequential fallback"
                ))
            elif config.manager_agent not in config.agents:
                warnings.append(ValidationWarning(
                    f"Manager agent '{config.manager_agent}' not found in agents"
                ))

        return warnings

    @classmethod
    def validate_task(
        cls,
        task: TaskDefinition,
        available_agents: set[str],
        task_names: set[str],
    ) -> ValidationError | None:
        """Validate a single task definition.

        Args:
            task: Task to validate
            available_agents: Set of valid agent names
            task_names: Set of all task names (for context_from validation)

        Returns:
            ValidationError if invalid, None if valid
        """
        # Validate agent exists
        if task.agent not in available_agents:
            available = ", ".join(sorted(available_agents)) if available_agents else "none"
            return ValidationError(
                task_name=task.name,
                field="agent",
                message=f"Agent '{task.agent}' not found. Available agents: {available}",
            )

        # Validate timeout is non-negative
        if task.timeout is not None and task.timeout < 0:
            return ValidationError(
                task_name=task.name,
                field="timeout",
                message=f"Timeout must be non-negative, got {task.timeout}",
            )

        # Validate context_from references exist
        for dep in task.context_from:
            if dep not in task_names:
                return ValidationError(
                    task_name=task.name,
                    field="context_from",
                    message=f"Dependency '{dep}' references unknown task",
                )

        # Validate description is not empty
        if not task.description or not task.description.strip():
            return ValidationError(
                task_name=task.name,
                field="description",
                message="Task description cannot be empty",
            )

        return None

    @classmethod
    def validate_all_tasks(
        cls,
        tasks: list[TaskDefinition],
        available_agents: set[str],
    ) -> list[ValidationError]:
        """Validate all tasks and return list of errors.

        Args:
            tasks: List of tasks to validate
            available_agents: Set of valid agent names

        Returns:
            List of validation errors (empty if all valid)
        """
        errors = []
        task_names = {t.name for t in tasks}

        for task in tasks:
            error = cls.validate_task(task, available_agents, task_names)
            if error:
                errors.append(error)

        return errors

    @classmethod
    def log_warnings(cls, warnings: list[ValidationWarning]) -> None:
        """Log validation warnings.

        Args:
            warnings: List of warnings to log
        """
        for warning in warnings:
            logger.warning(f"[crew-validation] {warning.message}")


class ExecutionPreconditions:
    """Runtime precondition checks for execution.

    These are checked during execution to provide graceful degradation
    when preconditions are not met.
    """

    @staticmethod
    def check_agent_available(
        agent_name: str,
        available_agents: set[str],
        failed_agents: dict[str, str],
    ) -> ValidationError | None:
        """Check if an agent is available for execution.

        Args:
            agent_name: Agent to check
            available_agents: Successfully initialized agents
            failed_agents: Agents that failed to initialize with error messages

        Returns:
            ValidationError if not available, None if available
        """
        if agent_name in available_agents:
            return None

        if agent_name in failed_agents:
            return ValidationError(
                task_name="",  # Will be filled by caller
                field="agent",
                message=f"Agent '{agent_name}' failed to initialize: {failed_agents[agent_name]}",
            )

        return ValidationError(
            task_name="",  # Will be filled by caller
            field="agent",
            message=f"Agent '{agent_name}' not initialized",
        )

    @staticmethod
    def check_upstream_completed(
        task: TaskDefinition,
        completed_tasks: set[str],
    ) -> bool:
        """Check if all upstream dependencies are completed.

        Args:
            task: Task to check
            completed_tasks: Set of completed task names

        Returns:
            True if all dependencies are completed
        """
        return all(dep in completed_tasks for dep in task.context_from)