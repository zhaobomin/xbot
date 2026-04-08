"""Tests for planner models."""


import pytest

from xbot.agent.crew.planner.models import (
    Capability,
    CrewPlan,
    GoalAnalysis,
    RoleCreationRequest,
    RoleCreationResult,
    RoleDefinition,
    RoleGap,
    RolePool,
    RolePoolConfig,
    RoleSelection,
    RoleTier,
    TaskPlan,
)


class TestCapability:
    """Tests for Capability enum."""

    def test_capability_values(self):
        """Test that all capabilities have string values."""
        assert Capability.SEARCH.value == "search"
        assert Capability.ANALYZE.value == "analyze"
        assert Capability.WRITE_CODE.value == "write_code"

    def test_capability_from_string(self):
        """Test creating capability from string."""
        cap = Capability("search")
        assert cap == Capability.SEARCH

    def test_invalid_capability_raises(self):
        """Test that invalid capability raises ValueError."""
        with pytest.raises(ValueError):
            Capability("invalid_capability")


class TestRoleTier:
    """Tests for RoleTier enum."""

    def test_tier_values(self):
        """Test tier values."""
        assert RoleTier.CORE.value == "core"
        assert RoleTier.EXTENDED.value == "extended"
        assert RoleTier.SPECIALIST.value == "specialist"

    def test_tier_from_string(self):
        """Test creating tier from string."""
        assert RoleTier("core") == RoleTier.CORE
        assert RoleTier("extended") == RoleTier.EXTENDED


class TestRoleDefinition:
    """Tests for RoleDefinition dataclass."""

    @pytest.fixture
    def sample_role(self):
        """Create a sample role for testing."""
        return RoleDefinition(
            name="test_role",
            display_name="Test Role",
            description="A test role",
            goal="Test things",
            backstory="Test backstory",
            tier=RoleTier.CORE,
            capabilities=[Capability.SEARCH, Capability.ANALYZE],
            tools=["read_file", "write_file"],
            max_iterations=25,
            tags=["test"],
            examples=["example 1"],
        )

    def test_role_creation(self, sample_role):
        """Test basic role creation."""
        assert sample_role.name == "test_role"
        assert sample_role.tier == RoleTier.CORE
        assert len(sample_role.capabilities) == 2

    def test_matches_capabilities_full_match(self, sample_role):
        """Test capability matching with full match."""
        required = [Capability.SEARCH, Capability.ANALYZE]
        score = sample_role.matches_capabilities(required)
        assert score == 1.0

    def test_matches_capabilities_partial_match(self, sample_role):
        """Test capability matching with partial match."""
        required = [Capability.SEARCH, Capability.ANALYZE, Capability.WRITE_CODE]
        score = sample_role.matches_capabilities(required)
        assert score == pytest.approx(2 / 3)

    def test_matches_capabilities_no_match(self, sample_role):
        """Test capability matching with no match."""
        required = [Capability.WRITE_CODE, Capability.DEBUG]
        score = sample_role.matches_capabilities(required)
        assert score == 0.0

    def test_matches_capabilities_empty_required(self, sample_role):
        """Test capability matching with empty required list.

        Empty requirements means any role is acceptable, so score should be 1.0.
        """
        score = sample_role.matches_capabilities([])
        assert score == 1.0

    def test_to_agent_role(self, sample_role):
        """Test conversion to AgentRole."""
        from xbot.agent.crew.models import AgentRole

        agent_role = sample_role.to_agent_role()
        assert isinstance(agent_role, AgentRole)
        assert agent_role.name == "test_role"
        assert agent_role.description == "A test role"
        assert agent_role.goal == "Test things"
        assert agent_role.tools == ["read_file", "write_file"]
        assert agent_role.max_iterations == 25

    def test_to_dict(self, sample_role):
        """Test serialization to dictionary."""
        data = sample_role.to_dict()
        assert data["name"] == "test_role"
        assert data["tier"] == "core"
        assert data["capabilities"] == ["search", "analyze"]

    def test_default_values(self):
        """Test default values for optional fields."""
        role = RoleDefinition(
            name="minimal",
            display_name="Minimal",
            description="Minimal role",
            goal="Do nothing",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[],
        )
        assert role.tools is None
        assert role.max_iterations == 30
        assert role.timeout_multiplier == 1.0
        assert role.tags == []
        assert role.examples == []


class TestRolePoolConfig:
    """Tests for RolePoolConfig dataclass."""

    def test_default_config(self):
        """Test default configuration."""
        config = RolePoolConfig()
        assert config.enabled_tiers == [RoleTier.CORE]
        assert config.custom_roles_dir is None
        assert config.role_overrides == {}

    def test_custom_config(self):
        """Test custom configuration."""
        config = RolePoolConfig(
            enabled_tiers=[RoleTier.CORE, RoleTier.EXTENDED],
            custom_roles_dir="/custom/roles",
            role_overrides={"researcher": {"max_iterations": 50}},
        )
        assert RoleTier.EXTENDED in config.enabled_tiers
        assert config.custom_roles_dir == "/custom/roles"
        assert config.role_overrides["researcher"]["max_iterations"] == 50


class TestRolePool:
    """Tests for RolePool dataclass."""

    @pytest.fixture
    def sample_pool(self):
        """Create a sample role pool for testing."""
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
            "specialist": RoleDefinition(
                name="specialist",
                display_name="Specialist",
                description="Special role",
                goal="Specialize",
                backstory="",
                tier=RoleTier.SPECIALIST,
                capabilities=[Capability.SECURITY_AUDIT],
            ),
        }
        config = RolePoolConfig(enabled_tiers=[RoleTier.CORE])
        return RolePool(roles=roles, config=config)

    def test_get_role(self, sample_pool):
        """Test getting a role by name."""
        role = sample_pool.get_role("researcher")
        assert role is not None
        assert role.name == "researcher"

    def test_get_role_not_found(self, sample_pool):
        """Test getting a non-existent role."""
        role = sample_pool.get_role("nonexistent")
        assert role is None

    def test_get_roles_by_tier(self, sample_pool):
        """Test filtering roles by tier."""
        core_roles = sample_pool.get_roles_by_tier(RoleTier.CORE)
        assert len(core_roles) == 2
        assert all(r.tier == RoleTier.CORE for r in core_roles)

    def test_get_available_roles(self, sample_pool):
        """Test getting available roles based on config."""
        available = sample_pool.get_available_roles()
        # Only CORE tier is enabled
        assert len(available) == 2
        assert all(r.tier == RoleTier.CORE for r in available)

    def test_find_by_capabilities(self, sample_pool):
        """Test finding roles by capabilities."""
        results = sample_pool.find_by_capabilities(
            [Capability.SEARCH, Capability.ANALYZE],
            min_score=0.5,
        )
        assert len(results) == 1
        role, score = results[0]
        assert role.name == "researcher"
        assert score == 1.0

    def test_to_description(self, sample_pool):
        """Test generating description string."""
        desc = sample_pool.to_description()
        assert "researcher" in desc
        assert "search" in desc


class TestPlanningModels:
    """Tests for planning-related models."""

    def test_goal_analysis(self):
        """Test GoalAnalysis dataclass."""
        analysis = GoalAnalysis(
            summary="Test goal",
            required_capabilities=[Capability.SEARCH],
            complexity="medium",
            estimated_tasks=3,
            suggested_process="sequential",
            constraints=["Must be fast"],
        )
        assert analysis.summary == "Test goal"
        assert Capability.SEARCH in analysis.required_capabilities

    def test_role_gap(self):
        """Test RoleGap dataclass."""
        gap = RoleGap(
            missing_capabilities=[Capability.ML],
            suggested_role_name="ml_engineer",
            suggested_role_description="ML expert",
            coverage_gap=0.3,
        )
        assert gap.suggested_role_name == "ml_engineer"
        assert Capability.ML in gap.missing_capabilities

    def test_task_plan(self):
        """Test TaskPlan dataclass."""
        task = TaskPlan(
            name="test_task",
            description="Test task description",
            agent="researcher",
            dependencies=["previous_task"],
            expected_output="A report",
            timeout=300,
        )
        assert task.name == "test_task"
        assert "previous_task" in task.dependencies

    def test_crew_plan_validation(self):
        """Test CrewPlan validation."""
        roles = [
            RoleDefinition(
                name="researcher",
                display_name="Researcher",
                description="Research",
                goal="Research",
                backstory="",
                tier=RoleTier.CORE,
                capabilities=[Capability.SEARCH],
            )
        ]
        tasks = [
            TaskPlan(
                name="task1",
                description="Task 1",
                agent="researcher",
            ),
            TaskPlan(
                name="task2",
                description="Task 2",
                agent="coder",  # Invalid - coder not in roles
            ),
        ]
        analysis = GoalAnalysis(
            summary="Test",
            required_capabilities=[],
            complexity="simple",
            estimated_tasks=2,
            suggested_process="sequential",
        )
        selection = RoleSelection(
            selected_roles=roles,
            selection_reason={},
            skipped_roles=[],
            coverage_score=1.0,
        )

        plan = CrewPlan(
            name="test_plan",
            description="Test",
            process="sequential",
            global_context="",
            roles=roles,
            tasks=tasks,
            analysis=analysis,
            role_selection=selection,
            planning_time=1.0,
            confidence=0.9,
        )

        errors = plan.validate()
        assert len(errors) == 1
        assert "coder" in errors[0]

    def test_crew_plan_to_dict(self):
        """Test CrewPlan conversion to CrewConfig dict."""
        roles = [
            RoleDefinition(
                name="researcher",
                display_name="Researcher",
                description="Research",
                goal="Research",
                backstory="Test backstory",
                tier=RoleTier.CORE,
                capabilities=[Capability.SEARCH],
            )
        ]
        tasks = [
            TaskPlan(
                name="task1",
                description="Task 1",
                agent="researcher",
                expected_output="Output",
            )
        ]
        analysis = GoalAnalysis(
            summary="Test",
            required_capabilities=[],
            complexity="simple",
            estimated_tasks=1,
            suggested_process="sequential",
        )
        selection = RoleSelection(
            selected_roles=roles,
            selection_reason={},
            skipped_roles=[],
            coverage_score=1.0,
        )

        plan = CrewPlan(
            name="test_plan",
            description="Test description",
            process="sequential",
            global_context="Test context",
            roles=roles,
            tasks=tasks,
            analysis=analysis,
            role_selection=selection,
            planning_time=1.0,
            confidence=0.9,
        )

        config_dict = plan.to_crew_config_dict()
        assert config_dict["name"] == "test_plan"
        assert "researcher" in config_dict["agents"]
        assert len(config_dict["tasks"]) == 1


class TestRoleCreationModels:
    """Tests for role creation models."""

    def test_role_creation_request(self):
        """Test RoleCreationRequest."""
        request = RoleCreationRequest(
            suggested_name="ml_engineer",
            required_capabilities=[Capability.ML, Capability.DATA_ANALYSIS],
            reason="Need ML capabilities",
            context="ML project",
        )
        assert request.suggested_name == "ml_engineer"
        assert len(request.required_capabilities) == 2

    def test_role_creation_result_success(self):
        """Test successful RoleCreationResult."""
        role = RoleDefinition(
            name="test",
            display_name="Test",
            description="Test",
            goal="Test",
            backstory="",
            tier=RoleTier.EXTENDED,
            capabilities=[Capability.SEARCH],
        )
        result = RoleCreationResult(
            success=True,
            role=role,
            errors=[],
            warnings=[],
        )
        assert result.success
        assert result.role is not None

    def test_role_creation_result_failure(self):
        """Test failed RoleCreationResult."""
        result = RoleCreationResult(
            success=False,
            role=None,
            errors=["Invalid name"],
            warnings=[],
        )
        assert not result.success
        assert "Invalid name" in result.errors
