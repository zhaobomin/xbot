"""Skill loading tool for on-demand skill content retrieval."""

from typing import Any

from xbot.agent.tools.base import Tool
from xbot.logging import get_logger

logger = get_logger(__name__)


class LoadSkillContentTool(Tool):
    """
    Tool for loading full skill content on demand.

    This implements Claude Code's Level 2 lazy loading pattern:
    - Level 1: Skills Catalog (description only) - always in context
    - Level 2: Full SKILL.md content - loaded via this tool when needed
    - Level 3: Supporting files - loaded via read_file tool
    """

    def __init__(self, skills_loader: Any, progress_callback: Any = None):
        """Initialize the skill loading tool.

        Args:
            skills_loader: SkillsLoader instance for loading skill content
            progress_callback: Optional callback for progress notifications
                Signature: async (skill_name: str, status: str) -> None
        """
        self._skills_loader = skills_loader
        self._progress_callback = progress_callback

    @property
    def name(self) -> str:
        return "load_skill_content"

    @property
    def description(self) -> str:
        return (
            "Load the full content of a skill. Use this when you've decided to use a skill "
            "and need its detailed instructions. The skill content includes guidance, "
            "best practices, and examples for using the skill effectively."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "The name of the skill to load (e.g., 'weather', 'cron', 'memory')",
                },
            },
            "required": ["skill_name"],
        }

    async def execute(self, skill_name: str, **kwargs: Any) -> str:
        """Load and return the full skill content.

        Args:
            skill_name: Name of the skill to load

        Returns:
            Full skill content (SKILL.md with frontmatter stripped)
        """
        # Send progress notification if callback is set
        if self._progress_callback:
            try:
                await self._progress_callback(skill_name, "loading")
            except Exception as e:
                logger.debug(f"Progress callback failed: {e}")

        # Load the skill content
        content = self._skills_loader.load_skill(skill_name)

        if content is None:
            error_msg = f"Skill '{skill_name}' not found. Use the Skills Catalog to see available skills."
            if self._progress_callback:
                try:
                    await self._progress_callback(skill_name, "not_found")
                except Exception:
                    pass
            return error_msg

        # Strip frontmatter for cleaner output
        content = self._skills_loader._strip_frontmatter(content)

        # Send success notification
        if self._progress_callback:
            try:
                await self._progress_callback(skill_name, "loaded")
            except Exception as e:
                logger.debug(f"Progress callback failed: {e}")

        # Return the skill content with a header
        return f"# Skill: {skill_name}\n\n{content}"

    def set_progress_callback(self, callback: Any) -> None:
        """Set or update the progress callback.

        Args:
            callback: Async callback function(skill_name: str, status: str)
        """
        self._progress_callback = callback
