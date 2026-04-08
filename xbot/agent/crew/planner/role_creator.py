"""Role creator for dynamic role creation.

This module provides functionality to:
- Validate role definitions
- Create new roles from requirements
- Save roles to YAML files
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from xbot.agent.crew.planner.models import (
    Capability,
    RoleCreationRequest,
    RoleCreationResult,
    RoleDefinition,
    RoleGap,
    RoleTier,
)
from xbot.agent.crew.planner.validators import LLMValidator
from xbot.logging import get_logger

logger = get_logger(__name__)


class RoleCreator:
    """Creator for dynamic role generation and management.

    This class handles:
    - Validating role definitions
    - Creating roles from capability gaps
    - Saving roles to YAML files
    """

    # Available tools for role configuration
    AVAILABLE_TOOLS = {
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "web_search",
        "web_fetch",
        "bash",
    }

    # Mapping from capabilities to recommended tools
    TOOL_CAPABILITY_MAP = {
        Capability.SEARCH: {"web_search", "web_fetch"},
        Capability.READ_CODE: {"read_file", "list_dir"},
        Capability.WRITE_CODE: {"write_file", "edit_file"},
        Capability.DEBUG: {"read_file", "bash"},
        Capability.TEST: {"read_file", "write_file", "edit_file", "bash"},
        Capability.ANALYZE: {"read_file", "list_dir"},
        Capability.DOCUMENT: {"read_file", "write_file"},
        Capability.DEPLOY: {"bash", "read_file", "write_file"},
        Capability.DATA_ANALYSIS: {"read_file", "bash"},
        Capability.ML: {"read_file", "write_file", "bash"},
        Capability.SECURITY_AUDIT: {"read_file", "list_dir", "bash"},
    }

    # Constraints for role creation
    CONSTRAINTS = {
        "name_pattern": r"^[a-z][a-z0-9_]*$",
        "name_max_length": 50,
        "max_capabilities": 5,
        "max_iterations_range": (10, 50),
        "timeout_multiplier_range": (0.5, 2.0),
    }

    def __init__(
        self,
        custom_roles_dir: Path | None = None,
        auto_save: bool = False,
        require_confirmation: bool = True,
    ):
        """Initialize the role creator.

        Args:
            custom_roles_dir: Directory to save created roles.
            auto_save: Whether to save roles automatically without confirmation.
            require_confirmation: Whether to require user confirmation before saving.
        """
        self.custom_roles_dir = Path(custom_roles_dir) if custom_roles_dir else None
        self.auto_save = auto_save
        self.require_confirmation = require_confirmation

    def analyze_gaps(
        self,
        required_capabilities: list[Capability],
        available_roles: list[RoleDefinition],
    ) -> list[RoleGap]:
        """Analyze capability gaps between requirements and available roles.

        Args:
            required_capabilities: Capabilities needed for a task.
            available_roles: Currently available roles.

        Returns:
            List of RoleGap describing missing capabilities.
        """
        # Get capabilities covered by available roles
        covered = set()
        for role in available_roles:
            covered.update(role.capabilities)

        # Find missing capabilities
        required_set = set(required_capabilities)
        missing = required_set - covered

        if not missing:
            return []

        # Calculate coverage gap
        coverage_gap = len(missing) / len(required_set) if required_set else 0

        # Generate suggestion
        gap = RoleGap(
            missing_capabilities=list(missing),
            suggested_role_name=self._suggest_role_name(missing),
            suggested_role_description=self._suggest_description(missing),
            coverage_gap=coverage_gap,
        )

        return [gap]

    async def create_role(
        self,
        request: RoleCreationRequest,
    ) -> RoleCreationResult:
        """Create a new role from a creation request.

        Args:
            request: The role creation request.

        Returns:
            RoleCreationResult with the created role or errors.
        """
        warnings = []

        # Generate role definition from request
        role = self._build_role_from_request(request)

        # Validate the role
        validation_errors = self.validate_role(role)
        if validation_errors:
            return RoleCreationResult(
                success=False,
                role=None,
                errors=validation_errors,
                warnings=[],
            )

        # Check for warnings
        warnings = self._check_warnings(role, request.required_capabilities)

        # Save if configured
        if self.auto_save and self.custom_roles_dir:
            try:
                self.save_role(role)
            except Exception as e:
                return RoleCreationResult(
                    success=False,
                    role=None,
                    errors=[f"Failed to save role: {e}"],
                    warnings=warnings,
                )

        return RoleCreationResult(
            success=True,
            role=role,
            errors=[],
            warnings=warnings,
            requires_confirmation=self.require_confirmation and not self.auto_save,
            confirmation_message=self._build_confirmation_message(role) if self.require_confirmation else "",
        )

    def create_role_from_definition(
        self,
        name: str,
        display_name: str,
        description: str,
        goal: str,
        backstory: str,
        capabilities: list[Capability],
        tools: list[str] | None = None,
        max_iterations: int = 30,
        timeout_multiplier: float = 1.0,
        tags: list[str] | None = None,
        examples: list[str] | None = None,
    ) -> RoleCreationResult:
        """Create a role from explicit parameters.

        Args:
            name: Role identifier.
            display_name: Human-readable name.
            description: Role description.
            goal: Role goal.
            backstory: Role backstory.
            capabilities: Role capabilities.
            tools: Available tools (None for all).
            max_iterations: Maximum iterations.
            timeout_multiplier: Timeout multiplier.
            tags: Tags for categorization.
            examples: Usage examples.

        Returns:
            RoleCreationResult with the created role or errors.
        """
        # Infer tools if not specified
        if tools is None:
            tools = self._infer_tools(capabilities)

        role = RoleDefinition(
            name=name,
            display_name=display_name,
            description=description,
            goal=goal,
            backstory=backstory,
            tier=RoleTier.EXTENDED,  # Created roles are extended by default
            capabilities=capabilities,
            tools=tools,
            max_iterations=max_iterations,
            timeout_multiplier=timeout_multiplier,
            tags=tags or ["custom"],
            examples=examples or [],
        )

        # Validate
        errors = self.validate_role(role)
        if errors:
            return RoleCreationResult(
                success=False,
                role=None,
                errors=errors,
                warnings=[],
            )

        # Check for warnings
        warnings = []
        if role.tools is None:
            warnings.append("Tools not specified, will use all available tools")

        return RoleCreationResult(
            success=True,
            role=role,
            errors=[],
            warnings=warnings,
            requires_confirmation=self.require_confirmation,
            confirmation_message=self._build_confirmation_message(role) if self.require_confirmation else "",
        )

    def validate_role(self, role: RoleDefinition) -> list[str]:
        """Validate a role definition.

        Args:
            role: The role to validate.

        Returns:
            List of validation errors (empty if valid).
        """
        errors = []

        # Validate name
        if not role.name:
            errors.append("Role name is required")
        elif not re.match(self.CONSTRAINTS["name_pattern"], role.name):
            errors.append(
                f"Invalid role name '{role.name}'. "
                f"Must match pattern: {self.CONSTRAINTS['name_pattern']}"
            )
        elif len(role.name) > self.CONSTRAINTS["name_max_length"]:
            errors.append(
                f"Role name too long: {len(role.name)} > {self.CONSTRAINTS['name_max_length']}"
            )

        # Validate required fields
        if not role.description:
            errors.append("Role description is required")
        if not role.goal:
            errors.append("Role goal is required")

        # Validate capabilities
        if not role.capabilities:
            errors.append("At least one capability is required")
        elif len(role.capabilities) > self.CONSTRAINTS["max_capabilities"]:
            errors.append(
                f"Too many capabilities: {len(role.capabilities)} > {self.CONSTRAINTS['max_capabilities']}"
            )

        # Validate tools
        if role.tools:
            unknown_tools = set(role.tools) - self.AVAILABLE_TOOLS
            if unknown_tools:
                errors.append(f"Unknown tools: {unknown_tools}")

        # Validate numeric ranges
        min_iter, max_iter = self.CONSTRAINTS["max_iterations_range"]
        if not (min_iter <= role.max_iterations <= max_iter):
            errors.append(
                f"max_iterations must be between {min_iter} and {max_iter}, "
                f"got {role.max_iterations}"
            )

        min_timeout, max_timeout = self.CONSTRAINTS["timeout_multiplier_range"]
        if not (min_timeout <= role.timeout_multiplier <= max_timeout):
            errors.append(
                f"timeout_multiplier must be between {min_timeout} and {max_timeout}, "
                f"got {role.timeout_multiplier}"
            )

        return errors

    def save_role(self, role: RoleDefinition, path: Path | None = None) -> Path:
        """Save a role definition to a YAML file.

        Args:
            role: The role to save.
            path: Optional path to save to. If not specified, uses custom_roles_dir.

        Returns:
            Path to the saved file.

        Raises:
            ValueError: If no save path is configured.
        """
        if path is None:
            if self.custom_roles_dir is None:
                raise ValueError("No save path configured. Set custom_roles_dir or provide path.")
            self.custom_roles_dir.mkdir(parents=True, exist_ok=True)
            path = self.custom_roles_dir / f"{role.name}.yaml"

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Build YAML data
        data = role.to_dict()

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

        logger.info(f"Saved role '{role.name}' to {path}")
        return path

    def load_role_from_file(self, path: Path) -> RoleDefinition | None:
        """Load a role definition from a YAML file.

        Args:
            path: Path to the YAML file.

        Returns:
            RoleDefinition or None if invalid.
        """
        path = Path(path)
        if not path.exists():
            return None

        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (yaml.YAMLError, OSError) as e:
            logger.warning(f"Failed to parse YAML from {path}: {e}")
            return None

        if not data or not data.get("name"):
            return None

        # Parse capabilities
        capabilities = []
        for cap_str in (data.get("capabilities") or []):
            try:
                capabilities.append(Capability(cap_str))
            except ValueError:
                pass

        # Parse tier with validation
        tier_str = data.get("tier") or "extended"
        try:
            tier = RoleTier(tier_str)
        except ValueError:
            logger.warning(f"Invalid tier '{tier_str}', defaulting to 'extended'")
            tier = RoleTier.EXTENDED

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

    def _build_role_from_request(self, request: RoleCreationRequest) -> RoleDefinition:
        """Build a role definition from a creation request."""
        # Validate required fields
        if not request.suggested_name:
            raise ValueError("suggested_name is required")
        if not request.required_capabilities:
            raise ValueError("required_capabilities is required")

        tools = self._infer_tools(request.required_capabilities)

        return RoleDefinition(
            name=request.suggested_name,
            display_name=request.suggested_name.replace("_", " ").title(),
            description=f"Custom role for: {request.reason or 'unknown reason'}",
            goal=f"Provide {', '.join(c.value for c in request.required_capabilities)} capabilities",
            backstory=f"Created to address: {request.reason or 'unknown reason'}",
            tier=RoleTier.EXTENDED,
            capabilities=request.required_capabilities,
            tools=tools,
            max_iterations=30,
            timeout_multiplier=1.0,
            tags=["auto-generated", "custom"],
            examples=[request.context] if request.context else [],
        )

    def _suggest_role_name(self, missing_capabilities: set[Capability]) -> str:
        """Suggest a role name based on missing capabilities."""
        name_map = {
            Capability.SECURITY_AUDIT: "security_auditor",
            Capability.ML: "ml_engineer",
            Capability.DATA_ANALYSIS: "data_analyst",
            Capability.DEPLOY: "devops",
            Capability.MONITOR: "sre",
        }

        for cap in missing_capabilities:
            if cap in name_map:
                return name_map[cap]

        return "custom_specialist"

    def _suggest_description(self, missing_capabilities: set[Capability]) -> str:
        """Suggest a role description based on missing capabilities."""
        desc_map = {
            Capability.SECURITY_AUDIT: "安全审计专家",
            Capability.ML: "机器学习工程师",
            Capability.DATA_ANALYSIS: "数据分析专家",
            Capability.DEPLOY: "DevOps 工程师",
            Capability.MONITOR: "SRE 工程师",
            Capability.SEARCH: "信息搜索专家",
            Capability.ANALYZE: "分析专家",
            Capability.DOCUMENT: "文档编写专家",
        }

        descriptions = []
        for cap in missing_capabilities:
            if cap in desc_map:
                descriptions.append(desc_map[cap])

        return "、".join(descriptions) if descriptions else "自定义专家"

    def _infer_tools(self, capabilities: list[Capability] | None) -> list[str] | None:
        """Infer recommended tools based on capabilities."""
        if not capabilities:
            return None
        tools = set()
        for cap in capabilities:
            if cap in self.TOOL_CAPABILITY_MAP:
                tools.update(self.TOOL_CAPABILITY_MAP[cap])
        return list(tools) if tools else None

    def _check_warnings(
        self,
        role: RoleDefinition,
        required_capabilities: list[Capability],
    ) -> list[str]:
        """Check for potential issues with a role."""
        warnings = []

        # Check capability coverage
        role_caps = set(role.capabilities)
        required_set = set(required_capabilities)
        missing = required_set - role_caps
        if missing:
            warnings.append(
                f"Role capabilities don't fully cover requirements. Missing: "
                f"{', '.join(c.value for c in missing)}"
            )

        # Check tool configuration
        if role.tools is None:
            warnings.append("Tools not specified, will use all available tools")

        return warnings

    def _build_confirmation_message(self, role: RoleDefinition) -> str:
        """Build a confirmation message for a role."""
        # Handle None, empty list, and non-empty list properly
        if role.tools is None:
            tools_str = "all available"
        elif len(role.tools) == 0:
            tools_str = "none specified"
        else:
            tools_str = ", ".join(role.tools)

        caps_str = ", ".join(c.value for c in role.capabilities) if role.capabilities else "none"
        examples_str = "\n  ".join(role.examples) if role.examples else "None"

        return f"""
Confirm role creation:

Name: {role.display_name} ({role.name})
Description: {role.description}
Goal: {role.goal}
Capabilities: {caps_str}
Tools: {tools_str}
Max iterations: {role.max_iterations}
Timeout multiplier: {role.timeout_multiplier}

Examples:
  {examples_str}

Save this role? (y/n): """


def validate_role_file(path: Path) -> tuple[bool, list[str], RoleDefinition | None]:
    """Validate a role YAML file.

    Args:
        path: Path to the YAML file.

    Returns:
        Tuple of (is_valid, errors, role_or_none).
    """
    creator = RoleCreator()
    role = creator.load_role_from_file(path)

    if role is None:
        return False, ["Failed to load role from file"], None

    errors = creator.validate_role(role)
    return len(errors) == 0, errors, role
