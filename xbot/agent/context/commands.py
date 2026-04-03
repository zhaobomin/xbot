"""Commands loader for slash commands."""

import re
from pathlib import Path
from typing import Any


class CommandsLoader:
    """
    Loader for workspace slash commands.

    Commands are markdown files in workspace/commands/ directory.
    When user types /command-name, the content is injected into the prompt.
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.commands_dir = workspace / "commands"

    def list_commands(self) -> list[dict[str, str]]:
        """
        List all available commands.

        Returns:
            List of command info dicts with 'name', 'path', 'description'.
        """
        commands = []

        if not self.commands_dir.exists():
            return commands

        for cmd_file in self.commands_dir.iterdir():
            if cmd_file.is_file() and cmd_file.suffix == ".md":
                name = cmd_file.stem  # filename without .md
                description = self._get_command_description(cmd_file)
                commands.append({
                    "name": name,
                    "path": str(cmd_file),
                    "description": description,
                })

        return sorted(commands, key=lambda c: c["name"])

    def load_command(self, name: str) -> str | None:
        """
        Load a command by name.

        Args:
            name: Command name (without / prefix).

        Returns:
            Command content (without frontmatter) or None if not found.
        """
        # Normalize name - remove / prefix if present
        if name.startswith("/"):
            name = name[1:]

        cmd_file = (self.commands_dir / f"{name}.md").resolve()
        commands_dir = self.commands_dir.resolve()
        if commands_dir not in cmd_file.parents:
            return None
        if not cmd_file.exists():
            return None

        content = cmd_file.read_text(encoding="utf-8")
        return self._strip_frontmatter(content)

    @staticmethod
    def _normalize_command_name(raw_name: str) -> str | None:
        name = raw_name.strip()
        if name in {"", ".", ".."}:
            return None
        if "/" in name or "\\" in name:
            return None
        return name

    def get_command_names(self) -> list[str]:
        """Get list of available command names (with / prefix)."""
        return [f"/{cmd['name']}" for cmd in self.list_commands()]

    def build_commands_summary(self) -> str:
        """
        Build a summary of available commands for help text.

        Returns:
            Formatted command list.
        """
        commands = self.list_commands()
        if not commands:
            return ""

        lines = []
        for cmd in commands:
            desc = cmd["description"]
            if desc:
                lines.append(f"/{cmd['name']} — {desc}")
            else:
                lines.append(f"/{cmd['name']}")

        return "\n".join(lines)

    def _get_command_description(self, cmd_file: Path) -> str:
        """Extract description from command frontmatter."""
        content = cmd_file.read_text(encoding="utf-8")

        if not content.startswith("---"):
            return ""

        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return ""

        # Parse frontmatter for description
        for line in match.group(1).split("\n"):
            if line.startswith("description:"):
                desc = line.split(":", 1)[1].strip().strip('"\'')
                return desc

        return ""

    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end():].strip()
        return content

    def is_command(self, text: str) -> bool:
        """Check if text starts with a valid command."""
        if not text.startswith("/"):
            return False

        cmd_name = self._normalize_command_name(text.split()[0][1:])  # Remove / and get command name
        if cmd_name is None:
            return False
        cmd_file = self.commands_dir / f"{cmd_name}.md"
        return cmd_file.exists()

    def get_command_from_text(self, text: str) -> str | None:
        """Extract command name from text if it's a command."""
        if not text.startswith("/"):
            return None

        parts = text.strip().split()
        if not parts:
            return None

        cmd_name = self._normalize_command_name(parts[0][1:])  # Remove / prefix
        if cmd_name is None:
            return None
        cmd_file = self.commands_dir / f"{cmd_name}.md"

        if cmd_file.exists():
            return cmd_name
        return None
