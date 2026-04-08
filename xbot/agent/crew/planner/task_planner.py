"""Task planner for decomposing goals into executable tasks."""

from __future__ import annotations

from collections import defaultdict, deque

from xbot.agent.crew.planner.models import (
    Capability,
    GoalAnalysis,
    RoleDefinition,
    RoleSelection,
    TaskPlan,
)
from xbot.agent.crew.planner.prompts import TASK_PLANNING_PROMPT
from xbot.agent.crew.planner.utils import LLMResponseParser
from xbot.agent.crew.planner.validators import LLMValidator
from xbot.logging import get_logger

logger = get_logger(__name__)


class TaskPlanner:
    """Plans tasks based on goals and available roles.

    This class handles:
    - Goal decomposition into tasks
    - Task dependency management
    - Task ordering and prioritization
    """

    def plan(
        self,
        goal: str,
        analysis: GoalAnalysis,
        role_selection: RoleSelection,
        llm_response: str | None = None,
    ) -> list[TaskPlan]:
        """Plan tasks based on goal and role selection.

        Args:
            goal: The user's goal description.
            analysis: Goal analysis results.
            role_selection: Selected roles.
            llm_response: Optional pre-computed LLM response for task planning.

        Returns:
            List of TaskPlan objects in execution order.
        """
        if llm_response:
            tasks = self._parse_llm_tasks(llm_response, role_selection.selected_roles)
        else:
            tasks = self._heuristic_planning(goal, analysis, role_selection)

        # Validate and fix dependencies
        tasks = self._validate_dependencies(tasks)

        # Topological sort for execution order
        tasks = self._topological_sort(tasks)

        return tasks

    def _parse_llm_tasks(
        self,
        response: str,
        available_roles: list[RoleDefinition],
    ) -> list[TaskPlan]:
        """Parse LLM response into task plans."""
        tasks = []
        role_names = {r.name for r in available_roles}

        # Use unified parser
        data = LLMResponseParser.parse_array(response)
        if not data:
            logger.debug("No JSON array found in LLM response")
            return tasks

        skipped_tasks = 0
        for item in data:
            if not isinstance(item, dict):
                continue

            agent = item.get("agent", "")
            if agent not in role_names:
                agent = self._fuzzy_match_role(agent, role_names)

            if agent:
                # Use LLMValidator for consistent validation
                timeout = LLMValidator.validate_timeout(item.get("timeout"))

                # Validate dependencies - ensure it's a list of strings
                raw_deps = item.get("dependencies")
                if raw_deps is not None and not isinstance(raw_deps, list):
                    # If it's a string, convert to single-item list
                    if isinstance(raw_deps, str):
                        raw_deps = [raw_deps] if raw_deps else []
                    else:
                        raw_deps = []
                dependencies = raw_deps if raw_deps else []

                tasks.append(TaskPlan(
                    name=item.get("name") or f"task_{len(tasks) + 1}",
                    description=item.get("description") or "",
                    agent=agent,
                    dependencies=dependencies,
                    expected_output=item.get("expected_output") or "",
                    timeout=timeout,
                    human_review=self._parse_human_review(item.get("human_review")),
                ))
            else:
                skipped_tasks += 1
                logger.warning(
                    f"Skipped task '{item.get('name', 'unnamed')}': "
                    f"agent '{item.get('agent')}' not found in available roles"
                )

        if skipped_tasks > 0:
            logger.warning(f"Total {skipped_tasks} task(s) skipped due to missing agents")

        return tasks

    def _parse_human_review(self, value: object) -> bool:
        """Parse legacy human_review values using the unified validator."""
        return LLMValidator.validate_boolean(value)

    def _heuristic_planning(
        self,
        goal: str,
        analysis: GoalAnalysis,
        role_selection: RoleSelection,
    ) -> list[TaskPlan]:
        """Heuristic-based task planning when LLM is not available."""
        tasks = []
        roles = role_selection.selected_roles

        if not roles:
            return tasks

        # Create tasks based on complexity
        if analysis.complexity == "simple":
            tasks = self._create_simple_tasks(goal, roles)
        elif analysis.complexity == "medium":
            tasks = self._create_medium_tasks(goal, roles, analysis)
        else:
            tasks = self._create_complex_tasks(goal, roles, analysis)

        return tasks

    def _create_simple_tasks(
        self,
        goal: str,
        roles: list[RoleDefinition],
    ) -> list[TaskPlan]:
        """Create tasks for simple goals."""
        # Use first role for a single task
        primary_role = roles[0]
        return [
            TaskPlan(
                name="execute_task",
                description=f"Execute the goal: {goal}",
                agent=primary_role.name,
                expected_output="Complete the requested task",
                timeout=300,
            )
        ]

    def _create_medium_tasks(
        self,
        goal: str,
        roles: list[RoleDefinition],
        analysis: GoalAnalysis,
    ) -> list[TaskPlan]:
        """Create tasks for medium complexity goals."""
        tasks = []

        # First task: analysis/research
        researcher = self._find_role_with_capability(
            roles, [Capability.SEARCH, Capability.ANALYZE]
        ) or roles[0]

        tasks.append(TaskPlan(
            name="analyze_goal",
            description=f"Analyze and plan for: {goal}",
            agent=researcher.name,
            expected_output="Analysis report and action plan",
            timeout=300,
        ))

        # Second task: execution
        if len(roles) > 1:
            executor = self._find_role_with_capability(
                roles, [Capability.WRITE_CODE, Capability.DEBUG]
            ) or roles[min(1, len(roles) - 1)]

            tasks.append(TaskPlan(
                name="execute_plan",
                description="Execute the planned actions",
                agent=executor.name,
                dependencies=["analyze_goal"],
                expected_output="Execution results",
                timeout=400,
            ))

        return tasks

    def _create_complex_tasks(
        self,
        goal: str,
        roles: list[RoleDefinition],
        analysis: GoalAnalysis,
    ) -> list[TaskPlan]:
        """Create tasks for complex goals."""
        tasks = []

        # Phase 1: Research and Analysis
        researcher = self._find_role_with_capability(
            roles, [Capability.SEARCH, Capability.ANALYZE]
        ) or roles[0]

        tasks.append(TaskPlan(
            name="research",
            description=f"Research and gather information for: {goal}",
            agent=researcher.name,
            expected_output="Research findings and context",
            timeout=300,
        ))

        # Phase 2: Planning
        tasks.append(TaskPlan(
            name="plan",
            description="Create detailed execution plan",
            agent=researcher.name,
            dependencies=["research"],
            expected_output="Detailed execution plan",
            timeout=200,
        ))

        # Track the last task for dependency chain
        last_task_name = "plan"

        # Phase 3: Implementation
        coder = self._find_role_with_capability(
            roles, [Capability.WRITE_CODE]
        )

        if coder:
            tasks.append(TaskPlan(
                name="implement",
                description="Implement the planned solution",
                agent=coder.name,
                dependencies=["plan"],
                expected_output="Implementation artifacts",
                timeout=500,
            ))
            last_task_name = "implement"

        # Phase 4: Review
        reviewer = self._find_role_with_capability(
            roles, [Capability.REVIEW]
        )

        if reviewer:
            tasks.append(TaskPlan(
                name="review",
                description="Review and validate the implementation",
                agent=reviewer.name,
                dependencies=[last_task_name],
                expected_output="Review report and recommendations",
                timeout=200,
            ))
            last_task_name = "review"

        # Phase 5: Test (if applicable)
        tester = self._find_role_with_capability(
            roles, [Capability.TEST]
        )

        if tester:
            tasks.append(TaskPlan(
                name="test",
                description="Create and run tests",
                agent=tester.name,
                dependencies=[last_task_name],
                expected_output="Test results",
                timeout=300,
            ))

        return tasks

    def _find_role_with_capability(
        self,
        roles: list[RoleDefinition],
        capabilities: list[Capability],
    ) -> RoleDefinition | None:
        """Find first role with any of the specified capabilities."""
        cap_set = set(capabilities)
        for role in roles:
            if cap_set & set(role.capabilities):
                return role
        return None

    def _fuzzy_match_role(self, name: str | None, role_names: set[str]) -> str | None:
        """Fuzzy match a role name.

        Args:
            name: Role name to match (can be None).
            role_names: Set of valid role names.

        Returns:
            Matched role name or None if no match.
        """
        if not name:
            return None

        name_lower = name.lower().replace("-", "_").replace(" ", "_")
        for rn in role_names:
            if rn.lower().replace("-", "_") == name_lower:
                return rn
        return None

    def _validate_dependencies(self, tasks: list[TaskPlan]) -> list[TaskPlan]:
        """Validate and fix task dependencies."""
        task_names = {t.name for t in tasks}

        for task in tasks:
            # Remove invalid dependencies
            valid_deps = [d for d in task.dependencies if d in task_names]
            task.dependencies = valid_deps

        return tasks

    def _topological_sort(self, tasks: list[TaskPlan]) -> list[TaskPlan]:
        """Sort tasks by dependencies (topological sort).

        If circular dependencies are detected, they are removed before returning.
        """
        if not tasks:
            return tasks

        # Build dependency graph
        in_degree = {t.name: 0 for t in tasks}
        graph = defaultdict(list)
        task_map = {t.name: t for t in tasks}

        for task in tasks:
            for dep in task.dependencies:
                graph[dep].append(task.name)
                in_degree[task.name] += 1

        # Kahn's algorithm
        queue = deque([name for name, deg in in_degree.items() if deg == 0])
        sorted_names = []

        while queue:
            name = queue.popleft()
            sorted_names.append(name)
            for neighbor in graph[name]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # If cycle detected, remove cyclic dependencies
        if len(sorted_names) != len(tasks):
            logger.warning("Circular dependency detected in tasks, removing invalid dependencies")
            # Tasks not in sorted_names are part of cycles
            valid_names = set(sorted_names)
            for task in tasks:
                if task.name not in valid_names:
                    # Clear all dependencies for tasks in cycles
                    task.dependencies = []
                    sorted_names.append(task.name)
                else:
                    # Remove dependencies pointing to tasks in cycles
                    task.dependencies = [d for d in task.dependencies if d in valid_names]

        return [task_map[name] for name in sorted_names]

    def build_planning_prompt(
        self,
        goal: str,
        analysis: GoalAnalysis,
        role_selection: RoleSelection,
    ) -> str:
        """Build the prompt for LLM-based task planning."""
        roles_desc = "\n".join([
            f"- {role.name}: {role.description}\n"
            f"  Goal: {role.goal}\n"
            f"  Capabilities: {', '.join(c.value for c in role.capabilities)}"
            for role in role_selection.selected_roles
        ])

        constraints = "\n".join(f"- {c}" for c in analysis.constraints) if analysis.constraints else "None"

        return TASK_PLANNING_PROMPT.format(
            goal=goal,
            complexity=analysis.complexity,
            estimated_tasks=analysis.estimated_tasks,
            roles=roles_desc,
            constraints=constraints,
        )
