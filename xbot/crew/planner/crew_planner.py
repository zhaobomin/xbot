"""Main entry point for dynamic crew planning.

This module provides the CrewPlanner class that orchestrates the entire
planning process: goal analysis -> role selection -> task planning -> config generation.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from xbot.crew.planner.config_generator import ConfigGenerator
from xbot.crew.planner.goal_analyzer import GoalAnalyzer
from xbot.crew.planner.models import (
    CrewPlan,
    GoalAnalysis,
    RolePoolConfig,
    RoleSelection,
)
from xbot.crew.planner.role_pool import RolePoolManager
from xbot.crew.planner.role_selector import RoleSelector
from xbot.crew.planner.task_planner import TaskPlanner
from xbot.platform.logging.core import get_logger

logger = get_logger(__name__)


# Type alias for LLM callable
LLMCallable = Callable[[str], str]


class CrewPlanner:
    """Orchestrates dynamic crew planning.

    This is the main entry point for the planning system. It coordinates:
    1. Role pool loading
    2. Goal analysis
    3. Role selection
    4. Task planning
    5. Configuration generation

    Usage:
        >>> planner = CrewPlanner(llm_callable=my_llm)
        >>> plan = await planner.plan("Analyze code quality and fix bugs")
        >>> yaml_content = planner.generate_config(plan)
    """

    def __init__(
        self,
        llm_callable: LLMCallable | None = None,
        role_pool_config: RolePoolConfig | None = None,
    ):
        """Initialize the crew planner.

        Args:
            llm_callable: Optional callable that takes a prompt and returns LLM response.
                         If None, heuristic-based planning is used.
            role_pool_config: Configuration for the role pool.
        """
        self.llm_callable = llm_callable
        self.role_pool_config = role_pool_config or RolePoolConfig()

        # Initialize components
        self.role_pool_manager = RolePoolManager(self.role_pool_config)
        self.goal_analyzer = GoalAnalyzer(llm_callable)
        self.role_selector = RoleSelector()
        self.task_planner = TaskPlanner()
        self.config_generator = ConfigGenerator()

    def plan(
        self,
        goal: str,
        context: dict[str, Any] | None = None,
    ) -> CrewPlan:
        """Generate a crew plan based on a goal.

        Args:
            goal: The user's goal description.
            context: Optional context (workspace, project_type, etc.).

        Returns:
            A complete CrewPlan.
        """
        start_time = time.time()

        # Step 1: Load role pool
        role_pool = self.role_pool_manager.get_pool()

        # Step 2: Analyze goal (using GoalAnalyzer)
        analysis = self.goal_analyzer.analyze(goal, context)

        # Step 3: Select roles
        # Get initial candidates for LLM prompt
        candidates = role_pool.find_by_capabilities(
            analysis.required_capabilities,
            min_score=0.3,
        )

        llm_response = None
        if self.llm_callable and candidates:
            prompt = self.role_selector.build_selection_prompt(analysis, candidates)
            try:
                llm_response = self.llm_callable(prompt)
            except Exception as e:
                logger.warning(f"LLM call failed: {e}")

        role_selection = self.role_selector.select(analysis, role_pool, llm_response)

        # Step 4: Plan tasks
        if self.llm_callable:
            prompt = self.task_planner.build_planning_prompt(goal, analysis, role_selection)
            try:
                llm_response = self.llm_callable(prompt)
            except Exception as e:
                logger.warning(f"LLM call failed: {e}")
                llm_response = None

        tasks = self.task_planner.plan(goal, analysis, role_selection, llm_response)

        # Step 5: Assemble plan
        plan = CrewPlan(
            name=self.goal_analyzer.generate_name(goal),
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

    def generate_config(self, plan: CrewPlan) -> str:
        """Generate YAML configuration from a crew plan.

        Args:
            plan: The crew plan to convert.

        Returns:
            YAML string.
        """
        return self.config_generator.generate_yaml(plan)

    def plan_and_generate(
        self,
        goal: str,
        context: dict[str, Any] | None = None,
    ) -> tuple[CrewPlan, str]:
        """Plan and generate configuration in one call.

        Args:
            goal: The user's goal description.
            context: Optional context.

        Returns:
            Tuple of (CrewPlan, YAML string).
        """
        plan = self.plan(goal, context)
        yaml_content = self.generate_config(plan)
        return plan, yaml_content

    def save_config(
        self,
        plan: CrewPlan,
        path: str | None = None,
    ) -> str:
        """Save the crew configuration to a file.

        Args:
            plan: The crew plan.
            path: Optional file path. If None, uses a temp location.

        Returns:
            Path to the saved file.
        """
        from pathlib import Path

        if path is None:
            import tempfile
            path = Path(tempfile.gettempdir()) / f"crew_{plan.name}.yaml"

        return str(self.config_generator.save(plan, Path(path)))

    def preview(self, plan: CrewPlan) -> str:
        """Generate a human-readable preview of the plan.

        Args:
            plan: The crew plan.

        Returns:
            Formatted preview string.
        """
        return self.config_generator.generate_preview(plan)

    # Proxy methods for backward compatibility with tests
    # These delegate to GoalAnalyzer

    def _analyze_goal(self, goal: str, context: dict | None):
        """Proxy method for backward compatibility.

        Delegates to GoalAnalyzer.analyze.
        """
        return self.goal_analyzer.analyze(goal, context)

    def _infer_capabilities(self, goal: str) -> list:
        """Proxy method for backward compatibility.

        Delegates to GoalAnalyzer.infer_capabilities.
        """
        return self.goal_analyzer.infer_capabilities(goal)

    def _infer_complexity(self, goal: str) -> str:
        """Proxy method for backward compatibility.

        Delegates to GoalAnalyzer.infer_complexity.
        """
        return self.goal_analyzer.infer_complexity(goal)

    def _generate_name(self, goal: str) -> str:
        """Proxy method for backward compatibility.

        Delegates to GoalAnalyzer.generate_name.
        """
        return self.goal_analyzer.generate_name(goal)

    def _parse_analysis(self, response: str):
        """Proxy method for backward compatibility.

        Delegates to GoalAnalyzer.parse_llm_response.
        """
        return self.goal_analyzer.parse_llm_response(response)

    def _build_global_context(self, goal: str, context: dict | None) -> str:
        """Build the global context string."""
        lines = [f"Goal: {goal}"]
        if context:
            lines.append("\nProject Context:")
            for k, v in context.items():
                lines.append(f"- {k}: {v}")
        return "\n".join(lines)

    def _calculate_confidence(
        self,
        analysis: GoalAnalysis,
        role_selection: RoleSelection,
        tasks: list,
    ) -> float:
        """Calculate planning confidence score."""
        # Based on coverage and task count match
        coverage = role_selection.coverage_score
        task_match = 1.0 - abs(len(tasks) - analysis.estimated_tasks) / max(
            len(tasks), analysis.estimated_tasks, 1
        )

        # Clamp result to valid range [0.0, 1.0]
        return max(0.0, min(1.0, coverage * 0.6 + task_match * 0.4))
