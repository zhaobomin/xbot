"""Skill to MCP converter.

This module converts xbot SKILL.md files to MCP tools,
enabling Claude SDK to use skills as structured tools.
"""

import logging
import re
from pathlib import Path
from typing import Any

from xbot.agent.capabilities import CapabilityCatalog

logger = logging.getLogger(__name__)

# Try to import SDK components
try:
    from claude_agent_sdk import tool, create_sdk_mcp_server

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    logger.warning("claude-agent-sdk not installed. Skill conversion will be limited.")


class SkillToMCPConverter:
    """Converts SKILL.md files to MCP tools.

    Skills are markdown files that teach the agent how to perform tasks.
    This converter transforms them into MCP tools for Claude SDK.
    """

    def __init__(self, workspace: str):
        """Initialize the converter.

        Args:
            workspace: Workspace path containing .xbot/skills directory
        """
        self.workspace = Path(workspace)
        self.catalog = CapabilityCatalog(self.workspace)

    def convert_all_skills(self) -> dict[str, Any]:
        """Convert all skills to MCP server.

        Returns:
            Dict mapping server name to MCP server config,
            or empty dict if no skills or SDK not available
        """
        if not SDK_AVAILABLE:
            logger.debug("SDK not available, skipping skill conversion")
            return {}

        tools = []

        for capability in self.catalog.list_skills(include_unavailable=True):
            skill_file = Path(capability.path)
            try:
                skill_tools = self._convert_skill(skill_file)
                tools.extend(skill_tools)
            except Exception as e:
                logger.warning(f"Error converting skill {skill_file}: {e}")

        if not tools:
            return {}

        logger.info(f"Converted {len(tools)} skill tools")
        return {
            "skills": create_sdk_mcp_server(
                name="skills",
                version="1.0.0",
                tools=tools,
            )
        }

    def _convert_skill(self, skill_path: Path) -> list:
        """Convert a single skill file to MCP tools.

        Args:
            skill_path: Path to SKILL.md file

        Returns:
            List of MCP tools
        """
        content = skill_path.read_text(encoding="utf-8")
        frontmatter, body = self._parse_frontmatter(content)

        skill_name = skill_path.parent.name
        description = frontmatter.get("description", skill_name)

        tools = []

        # 1. Extract action definitions (### action_name format)
        actions = self._extract_actions(body, skill_name)
        for action in actions:
            tools.append(self._create_action_tool(action))

        # 2. If no actions, create a consultation tool
        if not tools:
            tools.append(self._create_consultation_tool(skill_name, description, body))

        return tools

    def _extract_actions(self, body: str, skill_name: str) -> list[dict]:
        """Extract action definitions from skill body.

        Looks for ### action_name format.

        Args:
            body: Skill body content
            skill_name: Skill name for prefix

        Returns:
            List of action dicts
        """
        # Pattern: ### action_name followed by content until next ### or end
        pattern = r"###\s+(\w+)\s*\n([^#]+)"
        matches = re.findall(pattern, body)

        actions = []
        for name, content in matches:
            # Skip if name looks like a regular heading (not an action)
            if name.lower() in ["overview", "description", "usage", "example", "note", "notes"]:
                continue

            actions.append({
                "name": f"{skill_name}_{name.lower()}",
                "description": self._extract_description(content),
                "content": content.strip(),
            })

        return actions

    def _extract_description(self, content: str) -> str:
        """Extract a short description from content.

        Args:
            content: Content string

        Returns:
            First line or truncated content
        """
        lines = content.strip().split("\n")
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#"):
                # Truncate to 100 chars
                return line[:100] + "..." if len(line) > 100 else line
        return "Perform action"

    def _create_action_tool(self, action: dict) -> Any:
        """Create an MCP tool from an action definition.

        Args:
            action: Action dict with name, description, content

        Returns:
            MCP tool
        """
        # Capture action data in closure
        action_name = action["name"]
        action_desc = action["description"]
        action_content = action["content"]

        @tool(
            action_name,
            f"{action_desc}",
            {"query": str, "context": str},
        )
        async def action_tool(args: dict) -> dict:
            query = args.get("query", "")
            context = args.get("context", "")

            # Return the action guidance
            return {
                "content": [{
                    "type": "text",
                    "text": f"Action: {action_name}\n\n"
                    f"Guidance:\n{action_content}\n\n"
                    f"Query: {query}\n"
                    f"Context: {context}"
                }]
            }

        return action_tool

    def _create_consultation_tool(self, name: str, description: str, body: str) -> Any:
        """Create a consultation tool for a skill without actions.

        Args:
            name: Skill name
            description: Skill description
            body: Full skill body

        Returns:
            MCP tool
        """
        tool_name = f"skill_{name.replace('-', '_')}"

        @tool(
            tool_name,
            f"{description} - 提供指导和最佳实践",
            {"query": str},
        )
        async def consultation_tool(args: dict) -> dict:
            query = args.get("query", "")

            # Return relevant content
            relevant = body[:2000]  # Truncate to avoid huge responses

            return {
                "content": [{
                    "type": "text",
                    "text": f"根据 {name} 技能:\n\n{relevant}"
                }]
            }

        return consultation_tool

    def _parse_frontmatter(self, content: str) -> tuple[dict, str]:
        """Parse YAML frontmatter from markdown content.

        Args:
            content: Raw content

        Returns:
            Tuple of (frontmatter dict, body string)
        """
        if not content.startswith("---"):
            return {}, content

        match = re.match(r"^---\n(.*?)\n---\n(.*)$", content, re.DOTALL)
        if not match:
            return {}, content

        frontmatter_raw = match.group(1)
        body = match.group(2)

        # Simple YAML parsing
        frontmatter = {}
        for line in frontmatter_raw.split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                frontmatter[key.strip()] = value.strip().strip('"\'')

        return frontmatter, body
