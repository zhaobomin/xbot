"""Goal analyzer for analyzing user goals and inferring requirements.

This module provides the GoalAnalyzer class that extracts planning
information from user goals, including:
- Required capabilities
- Complexity assessment
- Estimated task count
- Suggested process type
"""

from __future__ import annotations

from typing import Any, Callable

from xbot.crew.planner.models import Capability, GoalAnalysis
from xbot.crew.planner.prompts import GOAL_ANALYSIS_PROMPT
from xbot.crew.planner.utils import LLMResponseParser
from xbot.crew.planner.validators import LLMValidator
from xbot.platform.logging.core import get_logger

logger = get_logger(__name__)

# Type alias for LLM callable
LLMCallable = Callable[[str], str]


class GoalAnalyzer:
    """Analyzes user goals to determine planning requirements.

    This class handles:
    - Goal complexity inference
    - Capability inference from goal text
    - LLM-based goal analysis
    - Crew name generation

    Usage:
        >>> analyzer = GoalAnalyzer()
        >>> analysis = analyzer.analyze("Analyze code quality and fix bugs")
        >>> print(analysis.complexity)  # "medium"
        >>> print(analysis.required_capabilities)  # [Capability.ANALYZE, ...]
    """

    # Keyword mappings for capability inference
    CAPABILITY_KEYWORDS = {
        Capability.SEARCH: ["search", "find", "look for", "research", "调查", "搜索", "查找"],
        Capability.ANALYZE: ["analyze", "examine", "review", "audit", "分析", "审查"],
        Capability.READ_CODE: ["read", "understand", "代码", "code"],
        Capability.WRITE_CODE: ["write", "implement", "create", "fix", "编写", "实现", "修复"],
        Capability.REFACTOR: ["refactor", "restructure", "reorganize", "重构"],
        Capability.DEBUG: ["debug", "fix", "troubleshoot", "调试", "修复"],
        Capability.REVIEW: ["review", "check", "inspect", "审查", "检查"],
        Capability.TEST: ["test", "testing", "verify", "测试", "验证"],
        Capability.DOCUMENT: ["document", "write docs", "文档", "readme"],
        Capability.DEPLOY: ["deploy", "release", "publish", "部署", "发布"],
    }

    # Keywords for complexity inference
    COMPLEX_KEYWORDS = [
        "architecture", "system", "multiple", "integrate",
        "架构", "系统", "多个", "集成", "复杂",
    ]

    SIMPLE_KEYWORDS = [
        "quick", "simple", "single", "just", "only",
        "快速", "简单", "单个", "只",
    ]

    def __init__(self, llm_callable: LLMCallable | None = None):
        """Initialize the goal analyzer.

        Args:
            llm_callable: Optional callable for LLM-based analysis.
        """
        self.llm_callable = llm_callable

    def analyze(
        self,
        goal: str,
        context: dict[str, Any] | None = None,
    ) -> GoalAnalysis:
        """Analyze the goal to determine requirements.

        Args:
            goal: The user's goal description.
            context: Optional context (workspace, project_type, etc.).

        Returns:
            GoalAnalysis with inferred requirements.
        """
        # Validate goal
        if not goal or not goal.strip():
            # Return default analysis for empty goals
            return GoalAnalysis(
                summary="Empty goal",
                required_capabilities=[Capability.ANALYZE],
                complexity="simple",
                estimated_tasks=1,
                suggested_process="sequential",
            )

        # Build context string for LLM
        context_str = ""
        if context:
            context_str = "\n".join(f"- {k}: {v}" for k, v in context.items())

        # Start with heuristic analysis
        analysis = GoalAnalysis(
            summary=goal[:100] if len(goal) > 100 else goal,
            required_capabilities=self.infer_capabilities(goal),
            complexity=self.infer_complexity(goal),
            estimated_tasks=3,
            suggested_process="sequential",
        )

        # Try LLM-based analysis if available
        if self.llm_callable:
            prompt = GOAL_ANALYSIS_PROMPT.format(
                goal=goal,
                context=context_str or "None",
            )
            try:
                response = self.llm_callable(prompt)
                parsed = self.parse_llm_response(response)
                if parsed:
                    analysis = parsed
            except Exception as e:
                logger.warning(f"LLM analysis failed: {e}")

        return analysis

    def parse_llm_response(self, response: str) -> GoalAnalysis | None:
        """Parse LLM response into GoalAnalysis.

        Uses LLMResponseParser for robust JSON parsing.

        Args:
            response: Raw LLM response text.

        Returns:
            GoalAnalysis or None if parsing fails.
        """
        data = LLMResponseParser.parse_object(response)
        if not data:
            return None

        try:
            # Use LLMValidator for consistent validation
            capabilities = LLMValidator.validate_capabilities(
                data.get("required_capabilities")
            )

            estimated_tasks = LLMValidator.validate_estimated_tasks(
                data.get("estimated_tasks")
            )

            complexity = LLMValidator.validate_complexity(data.get("complexity"))

            suggested_process = LLMValidator.validate_process(
                data.get("suggested_process")
            )

            return GoalAnalysis(
                summary=data.get("summary") or "",
                required_capabilities=capabilities,
                complexity=complexity,
                estimated_tasks=estimated_tasks,
                suggested_process=suggested_process,
                constraints=LLMValidator.validate_string_list(data.get("constraints")),
            )
        except (ValueError, KeyError) as e:
            logger.warning(f"Failed to parse analysis: {e}")
            return None

    def infer_capabilities(self, goal: str) -> list[Capability]:
        """Infer required capabilities from goal text.

        Args:
            goal: The goal description.

        Returns:
            List of inferred capabilities.
        """
        goal_lower = goal.lower()
        capabilities = []

        for cap, keywords in self.CAPABILITY_KEYWORDS.items():
            if any(keyword in goal_lower for keyword in keywords):
                capabilities.append(cap)

        # Default to ANALYZE if no capabilities inferred
        if not capabilities:
            capabilities = [Capability.ANALYZE]

        return list(set(capabilities))

    def infer_complexity(self, goal: str) -> str:
        """Infer complexity from goal text.

        Args:
            goal: The goal description.

        Returns:
            Complexity level: "simple", "medium", or "complex".
        """
        goal_lower = goal.lower()

        # Check for complex indicators
        if any(word in goal_lower for word in self.COMPLEX_KEYWORDS):
            return "complex"

        # Check for simple indicators
        if any(word in goal_lower for word in self.SIMPLE_KEYWORDS):
            return "simple"

        return "medium"

    def generate_name(self, goal: str) -> str:
        """Generate a crew name from the goal.

        Args:
            goal: The goal description.

        Returns:
            A valid identifier name (max 30 chars).
        """
        # Extract first few words
        words = goal.split()[:4]
        name_parts = []

        for word in words:
            # Convert to lowercase and keep only alphanumeric
            clean = word.lower()
            clean = ''.join(c if c.isalnum() or c == '_' else '' for c in clean)
            # Remove leading digits
            clean = clean.lstrip('0123456789')
            if clean:
                name_parts.append(clean)

        name = "_".join(name_parts)

        # Ensure name starts with letter or underscore
        if name and name[0].isdigit():
            name = "_" + name

        return name[:30] or "dynamic_crew"
