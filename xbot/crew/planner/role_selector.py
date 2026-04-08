"""Role selector for choosing appropriate roles based on goal analysis."""

from __future__ import annotations

from typing import TYPE_CHECKING

from xbot.crew.planner.models import (
    Capability,
    GoalAnalysis,
    RoleDefinition,
    RolePool,
    RoleSelection,
)
from xbot.crew.planner.prompts import ROLE_SELECTION_PROMPT
from xbot.crew.planner.utils import LLMResponseParser
from xbot.platform.logging.core import get_logger

if TYPE_CHECKING:
    from xbot.crew.planner.role_creator import RoleCreator

logger = get_logger(__name__)


class RoleSelector:
    """Selects appropriate roles based on goal analysis.

    This class handles:
    - Capability-based role matching
    - LLM-assisted role selection
    - Gap analysis for missing capabilities
    - Integration with role creator for dynamic role creation
    """

    def __init__(
        self,
        role_creator: RoleCreator | None = None,
        allow_create_roles: bool = False,
    ):
        """Initialize the role selector.

        Args:
            role_creator: Optional role creator for creating missing roles.
            allow_create_roles: Whether to create new roles for capability gaps.
        """
        self.role_creator = role_creator
        self.allow_create_roles = allow_create_roles

    def select(
        self,
        analysis: GoalAnalysis,
        role_pool: RolePool,
        llm_response: str | None = None,
    ) -> RoleSelection:
        """Select roles based on goal analysis.

        Args:
            analysis: Goal analysis results.
            role_pool: Available role pool.
            llm_response: Optional pre-computed LLM response for role selection.

        Returns:
            RoleSelection with selected roles and any gaps.
        """
        # 1. Capability-based candidate filtering
        candidates = role_pool.find_by_capabilities(
            analysis.required_capabilities,
            min_score=0.3,  # Loose filtering
        )

        if not candidates:
            # No matches, use all available roles
            candidates = [
                (r, 0.0)
                for r in role_pool.get_available_roles()
            ]

        # 2. Select roles (from LLM response or heuristic)
        selected = self._select_roles(analysis, candidates, llm_response)

        # 3. Analyze gaps
        created_roles = []
        role_gaps = []

        if self.allow_create_roles and self.role_creator:
            gaps = self.role_creator.analyze_gaps(
                analysis.required_capabilities,
                selected,
            )

            for gap in gaps:
                if gap.coverage_gap > 0.3:  # Only create for significant gaps
                    role_gaps.append(gap)
                # Note: Actual role creation should be done asynchronously
                # This just records the gaps

        # 4. Calculate coverage
        coverage = self._calculate_coverage(selected, analysis.required_capabilities)

        return RoleSelection(
            selected_roles=selected,
            selection_reason={r.name: "Capability match for goal" for r in selected},
            skipped_roles=[r.name for r, _ in candidates if r not in selected],
            coverage_score=coverage,
            created_roles=created_roles,
            role_gaps=role_gaps,
        )

    def _select_roles(
        self,
        analysis: GoalAnalysis,
        candidates: list[tuple[RoleDefinition, float]],
        llm_response: str | None = None,
    ) -> list[RoleDefinition]:
        """Select roles from candidates.

        Uses LLM response if provided, otherwise uses heuristic selection.
        """
        if llm_response:
            return self._parse_llm_selection(llm_response, candidates)

        # Heuristic selection: pick roles with highest capability coverage
        return self._heuristic_selection(analysis, candidates)

    def _parse_llm_selection(
        self,
        response: str,
        candidates: list[tuple[RoleDefinition, float]],
    ) -> list[RoleDefinition]:
        """Parse LLM response to extract selected role names."""
        # Use unified parser for string lists
        selected_names = LLMResponseParser.parse_string_list(response)

        # Map names to role definitions
        candidate_dict = {r.name: r for r, _ in candidates}
        return [
            candidate_dict[name]
            for name in selected_names
            if name in candidate_dict
        ]

    def _heuristic_selection(
        self,
        analysis: GoalAnalysis,
        candidates: list[tuple[RoleDefinition, float]],
    ) -> list[RoleDefinition]:
        """Heuristic-based role selection when LLM is not available."""
        selected = []
        covered_capabilities = set()

        # Sort by score descending
        sorted_candidates = sorted(candidates, key=lambda x: x[1], reverse=True)

        # Select roles that add new capabilities
        for role, score in sorted_candidates:
            role_caps = set(role.capabilities)
            new_caps = role_caps - covered_capabilities

            if new_caps or score >= 0.5:
                selected.append(role)
                covered_capabilities.update(role_caps)

            # Check if all required capabilities are covered
            if covered_capabilities >= set(analysis.required_capabilities):
                break

        # Ensure at least one role is selected
        if not selected and candidates:
            selected.append(candidates[0][0])

        # Limit based on complexity (use default for invalid complexity values)
        max_roles = {"simple": 2, "medium": 4, "complex": 6}
        max_count = max_roles.get(analysis.complexity, 4)
        if len(selected) > max_count:
            selected = selected[:max_count]

        return selected

    def _calculate_coverage(
        self,
        selected: list[RoleDefinition],
        required: list[Capability],
    ) -> float:
        """Calculate capability coverage score."""
        if not required:
            return 1.0

        covered = set()
        for role in selected:
            covered.update(role.capabilities)

        required_set = set(required)
        return len(covered & required_set) / len(required_set)

    def build_selection_prompt(
        self,
        analysis: GoalAnalysis,
        candidates: list[tuple[RoleDefinition, float]],
    ) -> str:
        """Build the prompt for LLM-based role selection.

        Args:
            analysis: Goal analysis results.
            candidates: Candidate roles with scores.

        Returns:
            Prompt string for LLM.
        """
        candidates_desc = "\n".join([
            f"- {role.name}: {role.description} (match: {score:.0%})"
            for role, score in candidates
        ])

        return ROLE_SELECTION_PROMPT.format(
            goal=analysis.summary,
            required_capabilities=", ".join(c.value for c in analysis.required_capabilities),
            complexity=analysis.complexity,
            candidates=candidates_desc,
        )
