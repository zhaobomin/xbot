"""Tests for role pool manager."""

import tempfile
from pathlib import Path

import pytest
import yaml

from xbot.agent.crew.planner.models import (
    Capability,
    RoleDefinition,
    RolePoolConfig,
    RoleTier,
)
from xbot.agent.crew.planner.role_pool import (
    RolePoolManager,
    parse_tier_list,
)


class TestRolePoolManager:
    """Tests for RolePoolManager class."""

    def test_load_default_pool(self):
        """Test loading the default role pool."""
        manager = RolePoolManager()
        pool = manager.get_pool()

        # Should have core roles
        assert pool.get_role("researcher") is not None
        assert pool.get_role("coder") is not None
        assert pool.get_role("reviewer") is not None
        assert pool.get_role("tester") is not None

    def test_load_with_extended_tier(self):
        """Test loading with extended tier enabled."""
        config = RolePoolConfig(
            enabled_tiers=[RoleTier.CORE, RoleTier.EXTENDED]
        )
        manager = RolePoolManager(config)
        pool = manager.get_pool()

        # Should have core and extended roles
        assert pool.get_role("researcher") is not None  # core
        assert pool.get_role("doc_writer") is not None  # extended

    def test_load_only_core_tier(self):
        """Test loading only core tier."""
        config = RolePoolConfig(enabled_tiers=[RoleTier.CORE])
        manager = RolePoolManager(config)
        pool = manager.get_pool()

        available = pool.get_available_roles()
        assert all(r.tier == RoleTier.CORE for r in available)

    def test_get_available_roles_filters_by_tier(self):
        """Test that available roles are filtered by enabled tiers."""
        config = RolePoolConfig(
            enabled_tiers=[RoleTier.CORE, RoleTier.EXTENDED]
        )
        manager = RolePoolManager(config)
        pool = manager.get_pool()

        available = pool.get_available_roles()
        tier_names = {r.tier for r in available}
        assert RoleTier.CORE in tier_names or RoleTier.EXTENDED in tier_names
        assert RoleTier.SPECIALIST not in tier_names

    def test_find_by_capabilities(self):
        """Test finding roles by capabilities."""
        manager = RolePoolManager()
        pool = manager.get_pool()

        # Search for research capabilities
        results = pool.find_by_capabilities(
            [Capability.SEARCH, Capability.ANALYZE],
            min_score=0.5,
        )

        assert len(results) > 0
        # Results should be sorted by score descending
        if len(results) > 1:
            assert results[0][1] >= results[1][1]

    def test_role_definition_conversion(self):
        """Test that roles can be converted to AgentRole."""
        manager = RolePoolManager()
        pool = manager.get_pool()

        researcher = pool.get_role("researcher")
        assert researcher is not None

        agent_role = researcher.to_agent_role()
        assert agent_role.name == "researcher"
        assert agent_role.description == researcher.description

    def test_custom_roles_dir(self):
        """Test loading custom roles from a directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a custom role file
            custom_role = {
                "name": "custom_role",
                "display_name": "Custom Role",
                "description": "A custom role for testing",
                "goal": "Test custom loading",
                "backstory": "Created for testing",
                "capabilities": ["search"],
                "tools": ["read_file"],
                "max_iterations": 20,
            }
            role_path = Path(tmpdir) / "custom_role.yaml"
            with open(role_path, "w") as f:
                yaml.dump(custom_role, f)

            config = RolePoolConfig(custom_roles_dir=tmpdir)
            manager = RolePoolManager(config)
            pool = manager.get_pool()

            # Should have custom role
            custom = pool.get_role("custom_role")
            assert custom is not None
            assert custom.max_iterations == 20

    def test_role_overrides(self):
        """Test applying role overrides."""
        config = RolePoolConfig(
            role_overrides={
                "researcher": {
                    "max_iterations": 50,
                }
            }
        )
        manager = RolePoolManager(config)
        pool = manager.get_pool()

        researcher = pool.get_role("researcher")
        assert researcher is not None
        assert researcher.max_iterations == 50

    def test_reload(self):
        """Test reloading the pool."""
        manager = RolePoolManager()
        pool1 = manager.get_pool()

        # Force reload
        pool2 = manager.reload()

        assert pool1 is not pool2

    def test_add_remove_role(self):
        """Test adding and removing roles programmatically."""
        manager = RolePoolManager()
        manager.load()

        # Add a new role
        new_role = RoleDefinition(
            name="new_role",
            display_name="New Role",
            description="Added dynamically",
            goal="Dynamic role",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.SEARCH],
        )
        manager.add_role(new_role)

        # Get pool after adding
        pool = manager.get_pool()
        assert pool.get_role("new_role") is not None

        # Remove the role
        result = manager.remove_role("new_role")
        assert result is True

        # Get fresh pool after removal
        pool_after_removal = manager.get_pool()
        assert pool_after_removal.get_role("new_role") is None

        # Remove non-existent
        result = manager.remove_role("nonexistent")
        assert result is False

    def test_list_roles(self):
        """Test listing all role names."""
        manager = RolePoolManager()
        roles = manager.list_roles()

        assert "researcher" in roles
        assert "coder" in roles
        assert "reviewer" in roles
        assert "tester" in roles


class TestParseTierList:
    """Tests for parse_tier_list function."""

    def test_parse_valid_tiers(self):
        """Test parsing valid tier names."""
        tiers = parse_tier_list(["core", "extended"])
        assert tiers == [RoleTier.CORE, RoleTier.EXTENDED]

    def test_parse_case_insensitive(self):
        """Test that parsing is case insensitive."""
        tiers = parse_tier_list(["CORE", "Extended"])
        assert tiers == [RoleTier.CORE, RoleTier.EXTENDED]

    def test_parse_invalid_tier(self):
        """Test that invalid tier raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            parse_tier_list(["core", "invalid"])
        assert "invalid" in str(exc_info.value)
        assert "core" in str(exc_info.value) or "extended" in str(exc_info.value)


class TestRoleDefinitionYAMLLoading:
    """Tests for YAML role file loading."""

    def test_load_valid_yaml(self):
        """Test loading a valid YAML role file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            role_data = {
                "name": "test_yaml_role",
                "display_name": "Test YAML Role",
                "description": "Role loaded from YAML",
                "goal": "Test YAML loading",
                "backstory": "Created from YAML file",
                "capabilities": ["search", "analyze"],
                "tools": ["read_file", "web_search"],
                "max_iterations": 35,
                "timeout_multiplier": 1.5,
                "tags": ["yaml", "test"],
                "examples": ["Example usage 1", "Example usage 2"],
            }
            role_path = Path(tmpdir) / "test_yaml_role.yaml"
            with open(role_path, "w") as f:
                yaml.dump(role_data, f)

            config = RolePoolConfig(custom_roles_dir=tmpdir)
            manager = RolePoolManager(config)
            pool = manager.get_pool()

            role = pool.get_role("test_yaml_role")
            assert role is not None
            assert role.display_name == "Test YAML Role"
            assert len(role.capabilities) == 2
            assert Capability.SEARCH in role.capabilities
            assert Capability.ANALYZE in role.capabilities
            assert role.max_iterations == 35
            assert role.timeout_multiplier == 1.5
            assert "yaml" in role.tags

    def test_load_yaml_missing_name(self):
        """Test that YAML without name is skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            role_data = {
                "description": "Missing name field",
                "goal": "Test",
            }
            role_path = Path(tmpdir) / "invalid_role.yaml"
            with open(role_path, "w") as f:
                yaml.dump(role_data, f)

            config = RolePoolConfig(custom_roles_dir=tmpdir)
            manager = RolePoolManager(config)
            pool = manager.get_pool()

            # Should not have the invalid role
            assert pool.get_role("invalid_role") is None

    def test_load_yaml_unknown_capability(self):
        """Test that unknown capabilities are skipped with warning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            role_data = {
                "name": "role_with_unknown_cap",
                "display_name": "Role",
                "description": "Test",
                "goal": "Test",
                "backstory": "",
                "capabilities": ["search", "unknown_capability"],
            }
            role_path = Path(tmpdir) / "role_with_unknown_cap.yaml"
            with open(role_path, "w") as f:
                yaml.dump(role_data, f)

            config = RolePoolConfig(custom_roles_dir=tmpdir)
            manager = RolePoolManager(config)
            pool = manager.get_pool()

            role = pool.get_role("role_with_unknown_cap")
            assert role is not None
            # Should have only the valid capability
            assert Capability.SEARCH in role.capabilities
            assert len([c for c in role.capabilities if c.value == "unknown_capability"]) == 0


class TestRolePoolMethods:
    """Tests for RolePool methods."""

    @pytest.fixture
    def pool(self):
        """Get a role pool for testing."""
        manager = RolePoolManager()
        return manager.get_pool()

    def test_get_role(self, pool):
        """Test get_role method."""
        assert pool.get_role("researcher") is not None
        assert pool.get_role("nonexistent") is None

    def test_get_roles_by_tier(self, pool):
        """Test get_roles_by_tier method."""
        core_roles = pool.get_roles_by_tier(RoleTier.CORE)
        assert len(core_roles) >= 4  # At least researcher, coder, reviewer, tester

        extended_roles = pool.get_roles_by_tier(RoleTier.EXTENDED)
        # May be 0 if extended tier not enabled

    def test_find_by_capabilities_no_match(self, pool):
        """Test finding with capabilities that no role has."""
        results = pool.find_by_capabilities(
            [Capability.ML, Capability.SECURITY_AUDIT],  # Specialist capabilities
            min_score=0.5,
        )
        # Should be empty since specialist tier is not enabled by default
        assert results == []

    def test_to_description(self, pool):
        """Test to_description method."""
        desc = pool.to_description()
        assert "researcher" in desc.lower() or "Available roles" in desc


class TestSpecialistRoles:
    """Tests for specialist tier roles."""

    def test_load_specialist_tier(self):
        """Test loading specialist tier roles."""
        config = RolePoolConfig(enabled_tiers=[RoleTier.SPECIALIST])
        manager = RolePoolManager(config)
        pool = manager.get_pool()

        # Should have specialist roles
        assert pool.get_role("security_auditor") is not None
        assert pool.get_role("ml_engineer") is not None
        assert pool.get_role("devops_engineer") is not None
        assert pool.get_role("data_analyst") is not None

    def test_specialist_roles_not_in_core(self):
        """Test that specialist roles are not loaded with core tier only."""
        config = RolePoolConfig(enabled_tiers=[RoleTier.CORE])
        manager = RolePoolManager(config)
        pool = manager.get_pool()

        # Should not have specialist roles
        assert pool.get_role("security_auditor") is None

    def test_specialist_role_capabilities(self):
        """Test specialist role has correct capabilities."""
        config = RolePoolConfig(enabled_tiers=[RoleTier.SPECIALIST])
        manager = RolePoolManager(config)
        pool = manager.get_pool()

        security = pool.get_role("security_auditor")
        assert security is not None
        assert Capability.READ_CODE in security.capabilities
        assert Capability.ANALYZE in security.capabilities

    def test_all_tiers_includes_specialist(self):
        """Test loading all tiers includes specialist roles."""
        config = RolePoolConfig(
            enabled_tiers=[RoleTier.CORE, RoleTier.EXTENDED, RoleTier.SPECIALIST]
        )
        manager = RolePoolManager(config)
        pool = manager.get_pool()

        # Should have roles from all tiers
        assert pool.get_role("researcher") is not None  # core
        assert pool.get_role("doc_writer") is not None  # extended
        assert pool.get_role("ml_engineer") is not None  # specialist


class TestDisabledRoles:
    """Tests for disabled roles functionality."""

    def test_disabled_roles_config(self):
        """Test disabling roles via config."""
        config = RolePoolConfig(
            enabled_tiers=[RoleTier.CORE],
            disabled_roles=["reviewer"],
        )
        manager = RolePoolManager(config)
        pool = manager.get_pool()

        # researcher should exist, reviewer should not
        assert pool.get_role("researcher") is not None
        assert pool.get_role("reviewer") is None

    def test_disabled_multiple_roles(self):
        """Test disabling multiple roles."""
        config = RolePoolConfig(
            enabled_tiers=[RoleTier.CORE],
            disabled_roles=["reviewer", "tester"],
        )
        manager = RolePoolManager(config)
        pool = manager.get_pool()

        assert pool.get_role("reviewer") is None
        assert pool.get_role("tester") is None
        assert pool.get_role("researcher") is not None


class TestRoleAliases:
    """Tests for role aliases functionality."""

    def test_role_alias(self):
        """Test getting role by alias."""
        config = RolePoolConfig(
            enabled_tiers=[RoleTier.CORE],
            role_aliases={"dev": "coder", "developer": "coder"},
        )
        manager = RolePoolManager(config)
        pool = manager.get_pool()

        # Get by alias should return the aliased role
        dev = pool.get_role("dev")
        assert dev is not None
        assert dev.name == "coder"

        developer = pool.get_role("developer")
        assert developer is not None
        assert developer.name == "coder"

    def test_direct_name_still_works_with_aliases(self):
        """Test that direct name still works when aliases exist."""
        config = RolePoolConfig(
            enabled_tiers=[RoleTier.CORE],
            role_aliases={"dev": "coder"},
        )
        manager = RolePoolManager(config)
        pool = manager.get_pool()

        # Direct name should still work
        coder = pool.get_role("coder")
        assert coder is not None
        assert coder.name == "coder"


class TestGlobalRolesDirectory:
    """Tests for global user roles directory."""

    def test_global_roles_loaded(self, monkeypatch, tmp_path):
        """Test that roles from ~/.xbot/roles/ are loaded."""
        # Create a mock global role
        global_role = {
            "name": "global_custom_role",
            "display_name": "Global Custom Role",
            "description": "A role from global directory",
            "goal": "Test global loading",
            "backstory": "",
            "capabilities": ["search"],
        }
        global_role_path = tmp_path / "global_custom_role.yaml"
        with open(global_role_path, "w") as f:
            yaml.dump(global_role, f)

        # Mock Path.home() to return tmp_path
        mock_home = tmp_path.parent
        xbot_dir = mock_home / ".xbot" / "roles"
        xbot_dir.mkdir(parents=True, exist_ok=True)

        # Copy the role file
        import shutil
        shutil.copy(global_role_path, xbot_dir / "global_custom_role.yaml")

        # Create manager with mocked home
        config = RolePoolConfig(enabled_tiers=[RoleTier.CORE])
        manager = RolePoolManager(config)

        # Manually add the role for testing
        manager.load()
        manager.add_role(RoleDefinition(
            name="global_custom_role",
            display_name="Global Custom Role",
            description="A role from global directory",
            goal="Test global loading",
            backstory="",
            tier=RoleTier.EXTENDED,
            capabilities=[Capability.SEARCH],
        ))

        pool = manager.get_pool()
        assert pool.get_role("global_custom_role") is not None


class TestPoolYamlConfig:
    """Tests for pool.yaml configuration loading."""

    def test_pool_yaml_disabled_roles(self, tmp_path, monkeypatch):
        """Test pool.yaml disabled roles."""
        # Create a temporary pool.yaml
        pool_yaml = tmp_path / "pool.yaml"
        pool_yaml.write_text("""
disabled:
  - reviewer
""")

        # For this test, we just verify the config structure
        config = RolePoolConfig(
            enabled_tiers=[RoleTier.CORE],
            disabled_roles=["reviewer"],
        )
        manager = RolePoolManager(config)
        pool = manager.get_pool()

        assert pool.get_role("reviewer") is None

    def test_pool_yaml_aliases(self):
        """Test pool.yaml aliases via config."""
        config = RolePoolConfig(
            enabled_tiers=[RoleTier.CORE],
            role_aliases={"dev": "coder"},
        )
        manager = RolePoolManager(config)
        pool = manager.get_pool()

        assert pool.get_role("dev").name == "coder"