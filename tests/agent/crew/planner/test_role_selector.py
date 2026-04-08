"""Tests for role selector module."""

import pytest

from xbot.agent.crew.planner.models import (
    Capability,
    GoalAnalysis,
    RoleDefinition,
    RolePool,
    RolePoolConfig,
    RoleSelection,
    RoleTier,
)
from xbot.agent.crew.planner.role_selector import RoleSelector


class TestRoleSelectorInit:
    """Tests for RoleSelector initialization."""

    def test_default_init(self):
        """Test default initialization."""
        selector = RoleSelector()
        assert selector.role_creator is None
        assert selector.allow_create_roles is False

    def test_custom_init(self):
        """Test custom initialization."""
        selector = RoleSelector(
            role_creator=None,
            allow_create_roles=True,
        )
        assert selector.allow_create_roles is True


class TestRoleSelectorSelect:
    """Tests for role selection."""

    @pytest.fixture
    def selector(self):
        return RoleSelector()

    @pytest.fixture
    def sample_pool(self):
        roles = {
            "researcher": RoleDefinition(
                name="researcher",
                display_name="Researcher",
                description="Research role",
                goal="Research",
                backstory="",
                tier=RoleTier.CORE,
                capabilities=[Capability.SEARCH, Capability.ANALYZE],
            ),
            "coder": RoleDefinition(
                name="coder",
                display_name="Coder",
                description="Coding role",
                goal="Code",
                backstory="",
                tier=RoleTier.CORE,
                capabilities=[Capability.WRITE_CODE, Capability.DEBUG],
            ),
        }
        config = RolePoolConfig(enabled_tiers=[RoleTier.CORE])
        return RolePool(roles=roles, config=config)

    def test_select_by_capability(self, selector, sample_pool):
        """Test selecting roles based on capabilities."""
        analysis = GoalAnalysis(
            summary="Search for information",
            required_capabilities=[Capability.SEARCH],
            complexity="simple",
            estimated_tasks=1,
            suggested_process="sequential",
        )

        selection = selector.select(analysis, sample_pool)

        assert len(selection.selected_roles) >= 1
        assert any(r.name == "researcher" for r in selection.selected_roles)

    def test_coverage_calculation(self, selector, sample_pool):
        """Test coverage score calculation."""
        analysis = GoalAnalysis(
            summary="Search and write code",
            required_capabilities=[Capability.SEARCH, Capability.WRITE_CODE],
            complexity="medium",
            estimated_tasks=2,
            suggested_process="sequential",
        )

        selection = selector.select(analysis, sample_pool)

        # Should cover both capabilities with both roles
        assert selection.coverage_score >= 0.5

    def test_select_without_candidates(self, selector):
        """Test selection when no candidates match."""
        # Empty pool
        config = RolePoolConfig(enabled_tiers=[RoleTier.CORE])
        pool = RolePool(roles={}, config=config)

        analysis = GoalAnalysis(
            summary="Test goal",
            required_capabilities=[Capability.SEARCH],
            complexity="simple",
            estimated_tasks=1,
            suggested_process="sequential",
        )

        selection = selector.select(analysis, pool)
        # Should handle gracefully
        assert isinstance(selection, RoleSelection)

    def test_llm_response_parsing(self, selector, sample_pool):
        """Test parsing LLM response for role selection."""
        analysis = GoalAnalysis(
            summary="Test",
            required_capabilities=[Capability.SEARCH, Capability.WRITE_CODE],
            complexity="medium",
            estimated_tasks=2,
            suggested_process="sequential",
        )

        # Simulate LLM response - both roles are candidates since they match required caps
        llm_response = '["researcher", "coder"]'

        selection = selector.select(analysis, sample_pool, llm_response)

        # Both roles should be selected as both match required capabilities
        assert len(selection.selected_roles) == 2
        assert any(r.name == "researcher" for r in selection.selected_roles)
        assert any(r.name == "coder" for r in selection.selected_roles)

    def test_build_selection_prompt(self, selector, sample_pool):
        """Test building the selection prompt."""
        analysis = GoalAnalysis(
            summary="Test goal",
            required_capabilities=[Capability.SEARCH],
            complexity="simple",
            estimated_tasks=1,
            suggested_process="sequential",
        )

        candidates = [
            (sample_pool.get_role("researcher"), 1.0),
            (sample_pool.get_role("coder"), 0.0),
        ]

        prompt = selector.build_selection_prompt(analysis, candidates)

        assert "Test goal" in prompt
        assert "researcher" in prompt
        assert "coder" in prompt


class TestHeuristicSelection:
    """Tests for heuristic-based role selection."""

    @pytest.fixture
    def selector(self):
        return RoleSelector()

    @pytest.fixture
    def multi_role_pool(self):
        roles = {}
        for i, (name, caps) in enumerate([
            ("researcher", [Capability.SEARCH, Capability.ANALYZE]),
            ("coder", [Capability.WRITE_CODE, Capability.DEBUG]),
            ("reviewer", [Capability.REVIEW, Capability.ANALYZE]),
            ("tester", [Capability.TEST, Capability.VALIDATE]),
        ]):
            roles[name] = RoleDefinition(
                name=name,
                display_name=name.title(),
                description=f"{name} role",
                goal=name,
                backstory="",
                tier=RoleTier.CORE,
                capabilities=caps,
            )
        config = RolePoolConfig(enabled_tiers=[RoleTier.CORE])
        return RolePool(roles=roles, config=config)

    def test_complex_goal_uses_more_roles(self, selector, multi_role_pool):
        """Test that complex goals select more roles."""
        simple_analysis = GoalAnalysis(
            summary="Simple task",
            required_capabilities=[Capability.SEARCH],
            complexity="simple",
            estimated_tasks=1,
            suggested_process="sequential",
        )

        complex_analysis = GoalAnalysis(
            summary="Complex task",
            required_capabilities=[Capability.SEARCH, Capability.WRITE_CODE, Capability.TEST],
            complexity="complex",
            estimated_tasks=5,
            suggested_process="sequential",
        )

        simple_selection = selector.select(simple_analysis, multi_role_pool)
        complex_selection = selector.select(complex_analysis, multi_role_pool)

        # Complex task should use more or equal roles
        assert len(complex_selection.selected_roles) >= len(simple_selection.selected_roles)

    def test_minimal_roles_for_simple_tasks(self, selector, multi_role_pool):
        """Test that simple tasks use minimal roles."""
        analysis = GoalAnalysis(
            summary="Quick search",
            required_capabilities=[Capability.SEARCH],
            complexity="simple",
            estimated_tasks=1,
            suggested_process="sequential",
        )

        selection = selector.select(analysis, multi_role_pool)

        # Simple task should use few roles
        assert len(selection.selected_roles) <= 2
