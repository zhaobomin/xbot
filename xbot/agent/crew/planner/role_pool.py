"""Role pool manager for loading and managing role definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from xbot.agent.crew.planner.models import (
    Capability,
    RoleDefinition,
    RolePool,
    RolePoolConfig,
    RoleTier,
)
from xbot.agent.crew.planner.validators import LLMValidator
from xbot.logging import get_logger

logger = get_logger(__name__)

# Default role pool directory
ROLE_POOL_DIR = Path(__file__).parent.parent / "role_pool"


class RolePoolManager:
    """Manager for loading and querying role definitions.

    Role definitions are stored in YAML files organized by tier:
    - core/: Always available basic roles
    - extended/: Optional roles enabled by configuration
    - specialist/: Roles requiring explicit enablement

    Example:
        >>> manager = RolePoolManager()
        >>> pool = manager.get_pool()
        >>> researcher = pool.get_role("researcher")
    """

    def __init__(self, config: RolePoolConfig | None = None):
        """Initialize the role pool manager.

        Args:
            config: Configuration for role pool loading.
        """
        self.config = config or RolePoolConfig()
        self._roles: dict[str, RoleDefinition] = {}
        self._loaded = False

    def load(self) -> None:
        """Load all role definitions from configured sources."""
        if self._loaded:
            return

        # 0. Load pool.yaml configuration if exists
        self._load_pool_config()

        # 1. Load core roles (always)
        self._load_from_dir(ROLE_POOL_DIR / "core", RoleTier.CORE)

        # 2. Load extended roles if enabled
        if RoleTier.EXTENDED in self.config.enabled_tiers:
            self._load_from_dir(ROLE_POOL_DIR / "extended", RoleTier.EXTENDED)

        # 3. Load specialist roles if enabled
        if RoleTier.SPECIALIST in self.config.enabled_tiers:
            self._load_from_dir(ROLE_POOL_DIR / "specialist", RoleTier.SPECIALIST)

        # 4. Load from global user directory (~/.xbot/roles/)
        global_roles_dir = Path.home() / ".xbot" / "roles"
        if global_roles_dir.exists():
            # Global roles are loaded as EXTENDED tier
            self._load_from_dir(global_roles_dir, RoleTier.EXTENDED)
            logger.debug(f"Loaded roles from global directory: {global_roles_dir}")

        # 5. Load custom roles if configured
        if self.config.custom_roles_dir:
            self._load_from_dir(
                Path(self.config.custom_roles_dir),
                RoleTier.EXTENDED
            )

        # 6. Remove disabled roles
        for disabled_name in self.config.disabled_roles:
            if disabled_name in self._roles:
                del self._roles[disabled_name]
                logger.debug(f"Disabled role: {disabled_name}")

        # 7. Apply overrides
        self._apply_overrides()

        self._loaded = True
        logger.info(
            f"Loaded {len(self._roles)} roles from pool "
            f"(tiers: {[t.value for t in self.config.enabled_tiers]})"
        )

    def _load_pool_config(self) -> None:
        """Load pool.yaml configuration file if it exists."""
        pool_config_path = ROLE_POOL_DIR / "pool.yaml"
        if not pool_config_path.exists():
            return

        try:
            with open(pool_config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if not data:
                return

            # Load overrides
            overrides = data.get("overrides") or {}
            self.config.role_overrides.update(overrides)

            # Load disabled roles
            disabled = data.get("disabled") or []
            self.config.disabled_roles.extend(disabled)

            # Load aliases
            aliases = data.get("aliases") or {}
            self.config.role_aliases.update(aliases)

            logger.debug(f"Loaded pool config from {pool_config_path}")

        except Exception as e:
            logger.warning(f"Failed to load pool config: {e}")

    def _load_from_dir(self, dir_path: Path, tier: RoleTier) -> None:
        """Load all role YAML files from a directory.

        Args:
            dir_path: Directory containing role YAML files.
            tier: Tier to assign to loaded roles.
        """
        if not dir_path.exists():
            logger.debug(f"Role pool directory not found: {dir_path}")
            return

        # Load both .yaml and .yml files
        for yaml_file in list(dir_path.glob("*.yaml")) + list(dir_path.glob("*.yml")):
            try:
                role = self._load_role(yaml_file, tier)
                if role:
                    if role.name in self._roles:
                        logger.warning(
                            f"Role '{role.name}' already exists, "
                            f"overwriting with {yaml_file}"
                        )
                    self._roles[role.name] = role
                    logger.debug(f"Loaded role: {role.name} from {yaml_file}")
            except Exception as e:
                logger.warning(f"Failed to load role from {yaml_file}: {e}")

    def _load_role(self, path: Path, tier: RoleTier) -> RoleDefinition | None:
        """Load a single role definition from a YAML file.

        Args:
            path: Path to the YAML file.
            tier: Tier to assign to the role (can be overridden by file).

        Returns:
            RoleDefinition or None if invalid.
        """
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data or not data.get("name"):
            logger.warning(f"Invalid role file (missing name): {path}")
            return None

        # Parse capabilities
        capabilities = []
        for cap_str in (data.get("capabilities") or []):
            try:
                capabilities.append(Capability(cap_str))
            except ValueError:
                logger.warning(
                    f"Unknown capability '{cap_str}' in role '{data['name']}'"
                )

        # Warn if no valid capabilities found
        if not capabilities:
            logger.warning(
                f"Role '{data['name']}' has no valid capabilities. "
                f"This may limit its usefulness in planning."
            )

        # Parse tier from file if present, with validation
        file_tier = data.get("tier")
        if file_tier:
            try:
                tier = RoleTier(file_tier.lower())
            except ValueError:
                logger.warning(
                    f"Invalid tier '{file_tier}' in role '{data['name']}', "
                    f"using directory tier '{tier.value}'"
                )

        return RoleDefinition(
            name=data["name"],
            display_name=data.get("display_name") or data["name"],
            description=data.get("description") or "",
            goal=data.get("goal") or "",
            backstory=data.get("backstory") or "",
            tier=tier,
            capabilities=capabilities,
            tools=data.get("tools"),
            tool_restrictions=data.get("tool_restrictions"),
            max_iterations=LLMValidator.validate_max_iterations(data.get("max_iterations")),
            timeout_multiplier=LLMValidator.validate_timeout_multiplier(data.get("timeout_multiplier")),
            tags=LLMValidator.validate_string_list(data.get("tags")),
            examples=LLMValidator.validate_string_list(data.get("examples")),
        )

    def _apply_overrides(self) -> None:
        """Apply configured overrides to loaded roles."""
        for role_name, overrides in self.config.role_overrides.items():
            if role_name not in self._roles:
                logger.warning(
                    f"Override configured for unknown role: {role_name}"
                )
                continue

            role = self._roles[role_name]
            role_data = {
                "name": role.name,
                "display_name": role.display_name,
                "description": role.description,
                "goal": role.goal,
                "backstory": role.backstory,
                "tier": role.tier.value if hasattr(role.tier, "value") else role.tier,
                "capabilities": [cap.value if hasattr(cap, "value") else cap for cap in role.capabilities],
                "tools": role.tools,
                "tool_restrictions": role.tool_restrictions,
                "max_iterations": role.max_iterations,
                "timeout_multiplier": role.timeout_multiplier,
                "tags": role.tags,
                "examples": role.examples,
            }

            for key, value in overrides.items():
                if key in role_data:
                    role_data[key] = value
                    logger.debug(f"Applied override to {role_name}.{key}")
                else:
                    logger.warning(
                        f"Unknown attribute '{key}' in override for role '{role_name}'"
                    )

            capabilities = []
            for cap_str in (role_data.get("capabilities") or []):
                try:
                    capabilities.append(Capability(cap_str))
                except ValueError:
                    logger.warning(
                        f"Unknown capability '{cap_str}' in override for role '{role_name}'"
                    )

            self._roles[role_name] = RoleDefinition(
                name=role_data["name"],
                display_name=role_data.get("display_name") or role_data["name"],
                description=role_data.get("description") or "",
                goal=role_data.get("goal") or "",
                backstory=role_data.get("backstory") or "",
                tier=RoleTier(role_data["tier"]),
                capabilities=capabilities,
                tools=role_data.get("tools"),
                tool_restrictions=role_data.get("tool_restrictions"),
                max_iterations=LLMValidator.validate_max_iterations(role_data.get("max_iterations")),
                timeout_multiplier=LLMValidator.validate_timeout_multiplier(role_data.get("timeout_multiplier")),
                tags=LLMValidator.validate_string_list(role_data.get("tags")),
                examples=LLMValidator.validate_string_list(role_data.get("examples")),
            )

    def get_pool(self) -> RolePool:
        """Get the loaded role pool.

        Returns:
            RolePool instance with all loaded roles.
        """
        if not self._loaded:
            self.load()
        return RolePool(roles=dict(self._roles), config=self.config)

    def reload(self) -> RolePool:
        """Force reload all roles and return updated pool.

        Returns:
            Freshly loaded RolePool.

        Raises:
            Exception: If loading fails, previous state is preserved.
        """
        # Backup current state
        old_roles = dict(self._roles)
        old_loaded = self._loaded
        # Backup and reset pool.yaml derived config
        old_overrides = dict(self.config.role_overrides)
        old_disabled = list(self.config.disabled_roles)
        old_aliases = dict(self.config.role_aliases)

        try:
            self._loaded = False
            self._roles.clear()
            # Clear pool.yaml derived config to prevent accumulation
            self.config.role_overrides.clear()
            self.config.disabled_roles.clear()
            self.config.role_aliases.clear()
            self.load()
        except Exception:
            # Restore previous state on failure
            self._roles = old_roles
            self._loaded = old_loaded
            self.config.role_overrides = old_overrides
            self.config.disabled_roles = old_disabled
            self.config.role_aliases = old_aliases
            raise

        return self.get_pool()

    def add_role(self, role: RoleDefinition) -> None:
        """Add a role to the pool programmatically.

        Args:
            role: Role definition to add.

        Note:
            If load() hasn't been called yet, this role will be included
            in the loaded roles. Call load() first if you want to ensure
            predefined roles are loaded before adding custom roles.
        """
        self._roles[role.name] = role

    def remove_role(self, name: str) -> bool:
        """Remove a role from the pool.

        Args:
            name: Name of the role to remove.

        Returns:
            True if role was removed, False if not found.
        """
        if name in self._roles:
            del self._roles[name]
            return True
        return False

    def list_roles(self) -> list[str]:
        """List all loaded role names.

        Returns:
            List of role names.
        """
        if not self._loaded:
            self.load()
        return list(self._roles.keys())


def parse_tier_list(tiers: list[str]) -> list[RoleTier]:
    """Parse a list of tier strings into RoleTier enums.

    Args:
        tiers: List of tier name strings.

    Returns:
        List of RoleTier enums.

    Raises:
        ValueError: If any tier name is invalid.
    """
    result = []
    for tier_str in tiers:
        try:
            result.append(RoleTier(tier_str.lower()))
        except ValueError:
            valid = [t.value for t in RoleTier]
            raise ValueError(
                f"Invalid tier '{tier_str}'. Valid tiers: {valid}"
            )
    return result
