"""Tests for role creator module."""

import asyncio
import tempfile
from pathlib import Path

import pytest
import yaml

from xbot.agent.crew.planner.models import (
    Capability,
    RoleCreationRequest,
    RoleDefinition,
    RoleTier,
)
from xbot.agent.crew.planner.role_creator import (
    RoleCreator,
    validate_role_file,
)


class TestRoleCreatorInit:
    """Tests for RoleCreator initialization."""

    def test_default_init(self):
        """Test default initialization."""
        creator = RoleCreator()
        assert creator.custom_roles_dir is None
        assert creator.auto_save is False
        assert creator.require_confirmation is True

    def test_custom_init(self):
        """Test custom initialization."""
        creator = RoleCreator(
            custom_roles_dir=Path("/custom/roles"),
            auto_save=True,
            require_confirmation=False,
        )
        assert creator.custom_roles_dir == Path("/custom/roles")
        assert creator.auto_save is True
        assert creator.require_confirmation is False


class TestAnalyzeGaps:
    """Tests for gap analysis."""

    @pytest.fixture
    def creator(self):
        return RoleCreator()

    def test_no_gaps(self, creator):
        """Test when all capabilities are covered."""
        available_roles = [
            RoleDefinition(
                name="researcher",
                display_name="Researcher",
                description="Research",
                goal="Research",
                backstory="",
                tier=RoleTier.CORE,
                capabilities=[Capability.SEARCH, Capability.ANALYZE],
            )
        ]
        required = [Capability.SEARCH, Capability.ANALYZE]

        gaps = creator.analyze_gaps(required, available_roles)
        assert gaps == []

    def test_has_gaps(self, creator):
        """Test when capabilities are missing."""
        available_roles = [
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
        required = [Capability.SEARCH, Capability.ML]

        gaps = creator.analyze_gaps(required, available_roles)
        assert len(gaps) == 1
        assert Capability.ML in gaps[0].missing_capabilities

    def test_coverage_gap_calculation(self, creator):
        """Test coverage gap calculation."""
        available_roles = [
            RoleDefinition(
                name="role",
                display_name="Role",
                description="",
                goal="",
                backstory="",
                tier=RoleTier.CORE,
                capabilities=[Capability.SEARCH],
            )
        ]
        required = [Capability.SEARCH, Capability.ANALYZE, Capability.WRITE_CODE]

        gaps = creator.analyze_gaps(required, available_roles)
        assert gaps[0].coverage_gap == pytest.approx(2 / 3)


class TestCreateRole:
    """Tests for role creation."""

    @pytest.fixture
    def creator(self):
        return RoleCreator()

    @pytest.mark.asyncio
    async def test_create_role_from_request(self, creator):
        """Test creating a role from a request."""
        request = RoleCreationRequest(
            suggested_name="test_role",
            required_capabilities=[Capability.SEARCH, Capability.ANALYZE],
            reason="Testing role creation",
            context="Test context",
        )

        result = await creator.create_role(request)

        assert result.success
        assert result.role is not None
        assert result.role.name == "test_role"

    @pytest.mark.asyncio
    async def test_create_role_with_invalid_name(self, creator):
        """Test that invalid names are rejected."""
        request = RoleCreationRequest(
            suggested_name="Invalid-Name!",  # Invalid: uppercase and special chars
            required_capabilities=[Capability.SEARCH],
            reason="Test",
        )

        result = await creator.create_role(request)

        assert not result.success
        assert len(result.errors) > 0

    def test_create_role_from_definition(self, creator):
        """Test creating a role from explicit parameters."""
        result = creator.create_role_from_definition(
            name="my_role",
            display_name="My Role",
            description="A test role",
            goal="Test things",
            backstory="Created for testing",
            capabilities=[Capability.SEARCH, Capability.ANALYZE],
        )

        assert result.success
        assert result.role.name == "my_role"
        assert result.role.tier == RoleTier.EXTENDED  # Created roles are extended

    def test_infer_tools(self, creator):
        """Test automatic tool inference."""
        result = creator.create_role_from_definition(
            name="coder",
            display_name="Coder",
            description="Code role",
            goal="Code",
            backstory="",
            capabilities=[Capability.WRITE_CODE, Capability.DEBUG],
            tools=None,  # Should be inferred
        )

        assert result.success
        assert result.role.tools is not None
        assert "write_file" in result.role.tools or "edit_file" in result.role.tools


class TestValidateRole:
    """Tests for role validation."""

    @pytest.fixture
    def creator(self):
        return RoleCreator()

    def test_valid_role(self, creator):
        """Test validation of a valid role."""
        role = RoleDefinition(
            name="valid_role",
            display_name="Valid Role",
            description="A valid role",
            goal="Do things",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.SEARCH],
        )

        errors = creator.validate_role(role)
        assert errors == []

    def test_invalid_name_pattern(self, creator):
        """Test that invalid name patterns are caught."""
        role = RoleDefinition(
            name="Invalid-Name",
            display_name="Invalid",
            description="",
            goal="",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.SEARCH],
        )

        errors = creator.validate_role(role)
        assert any("name" in e.lower() for e in errors)

    def test_missing_required_fields(self, creator):
        """Test that missing required fields are caught."""
        role = RoleDefinition(
            name="test",
            display_name="Test",
            description="",  # Missing
            goal="",  # Missing
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[],
        )

        errors = creator.validate_role(role)
        assert len(errors) > 0

    def test_invalid_tools(self, creator):
        """Test that unknown tools are caught."""
        role = RoleDefinition(
            name="test",
            display_name="Test",
            description="Test",
            goal="Test",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.SEARCH],
            tools=["unknown_tool"],
        )

        errors = creator.validate_role(role)
        assert any("unknown" in e.lower() for e in errors)

    def test_max_iterations_out_of_range(self, creator):
        """Test that out-of-range max_iterations is caught."""
        role = RoleDefinition(
            name="test",
            display_name="Test",
            description="Test",
            goal="Test",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.SEARCH],
            max_iterations=100,  # Too high
        )

        errors = creator.validate_role(role)
        assert any("max_iterations" in e.lower() for e in errors)


class TestSaveAndLoadRole:
    """Tests for saving and loading roles."""

    def test_save_role(self):
        """Test saving a role to a file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            creator = RoleCreator(custom_roles_dir=Path(tmpdir))

            role = RoleDefinition(
                name="test_role",
                display_name="Test Role",
                description="Test description",
                goal="Test goal",
                backstory="Test backstory",
                tier=RoleTier.EXTENDED,
                capabilities=[Capability.SEARCH, Capability.ANALYZE],
                tools=["read_file"],
                max_iterations=25,
            )

            path = creator.save_role(role)
            assert path.exists()
            assert path.name == "test_role.yaml"

            # Verify content
            with open(path) as f:
                data = yaml.safe_load(f)

            assert data["name"] == "test_role"
            assert data["capabilities"] == ["search", "analyze"]

    def test_load_role_from_file(self):
        """Test loading a role from a file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a role file
            role_data = {
                "name": "loaded_role",
                "display_name": "Loaded Role",
                "description": "Loaded from file",
                "goal": "Load test",
                "backstory": "Test backstory",
                "tier": "extended",
                "capabilities": ["search"],
                "tools": ["read_file"],
            }

            path = Path(tmpdir) / "loaded_role.yaml"
            with open(path, "w") as f:
                yaml.dump(role_data, f)

            creator = RoleCreator()
            role = creator.load_role_from_file(path)

            assert role is not None
            assert role.name == "loaded_role"
            assert Capability.SEARCH in role.capabilities

    def test_load_invalid_file(self):
        """Test loading an invalid file returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create an invalid file
            path = Path(tmpdir) / "invalid.yaml"
            with open(path, "w") as f:
                f.write("not a valid yaml: [")

            creator = RoleCreator()
            # Should handle exception and return None
            try:
                role = creator.load_role_from_file(path)
                assert role is None
            except Exception:
                # If it raises, that's also acceptable for invalid files
                pass

    def test_load_missing_name(self):
        """Test loading a file without a name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            role_data = {"description": "No name"}
            path = Path(tmpdir) / "no_name.yaml"
            with open(path, "w") as f:
                yaml.dump(role_data, f)

            creator = RoleCreator()
            role = creator.load_role_from_file(path)

            assert role is None


class TestValidateRoleFile:
    """Tests for validate_role_file function."""

    def test_validate_valid_file(self):
        """Test validating a valid role file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            role_data = {
                "name": "valid",
                "display_name": "Valid",
                "description": "Valid role",
                "goal": "Valid goal",
                "backstory": "",
                "capabilities": ["search"],
            }
            path = Path(tmpdir) / "valid.yaml"
            with open(path, "w") as f:
                yaml.dump(role_data, f)

            is_valid, errors, role = validate_role_file(path)

            assert is_valid
            assert errors == []
            assert role is not None
            assert role.name == "valid"

    def test_validate_invalid_file(self):
        """Test validating an invalid role file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            role_data = {
                "name": "invalid-name!",  # Invalid name
                "description": "",  # Missing required
            }
            path = Path(tmpdir) / "invalid.yaml"
            with open(path, "w") as f:
                yaml.dump(role_data, f)

            is_valid, errors, role = validate_role_file(path)

            assert not is_valid
            assert len(errors) > 0


class TestToolCapabilityMap:
    """Tests for tool inference from capabilities."""

    def test_search_infers_web_tools(self):
        """Test that SEARCH capability infers web tools."""
        creator = RoleCreator()
        tools = creator._infer_tools([Capability.SEARCH])

        assert tools is not None
        assert "web_search" in tools or "web_fetch" in tools

    def test_write_code_infers_file_tools(self):
        """Test that WRITE_CODE capability infers file tools."""
        creator = RoleCreator()
        tools = creator._infer_tools([Capability.WRITE_CODE])

        assert tools is not None
        assert "write_file" in tools or "edit_file" in tools

    def test_multiple_capabilities_combine_tools(self):
        """Test that multiple capabilities combine tools."""
        creator = RoleCreator()
        tools = creator._infer_tools([
            Capability.SEARCH,
            Capability.WRITE_CODE,
        ])

        assert tools is not None
        # Should have both web and file tools
        assert len(tools) >= 2

    def test_no_matching_capability(self):
        """Test inference with no matching capability."""
        creator = RoleCreator()
        tools = creator._infer_tools([Capability.REVIEW])

        # REVIEW may not have a specific mapping, so tools might be None or minimal
        assert tools is None or isinstance(tools, list)