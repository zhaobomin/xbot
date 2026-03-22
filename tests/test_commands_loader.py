"""Tests for CommandsLoader (workspace slash commands)."""

import tempfile
from pathlib import Path

import pytest

from xbot.agent.commands import CommandsLoader


class TestCommandsLoader:
    """Tests for CommandsLoader class."""

    def test_init_sets_workspace_and_commands_dir(self) -> None:
        """Test that init sets workspace and commands_dir correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            loader = CommandsLoader(workspace)

            assert loader.workspace == workspace
            assert loader.commands_dir == workspace / "commands"

    def test_list_commands_empty_when_no_commands_dir(self) -> None:
        """Test that list_commands returns empty list when commands dir doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            loader = CommandsLoader(workspace)

            commands = loader.list_commands()
            assert commands == []

    def test_list_commands_returns_markdown_files(self) -> None:
        """Test that list_commands returns .md files from commands directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            commands_dir = workspace / "commands"
            commands_dir.mkdir()

            # Create command files
            (commands_dir / "test1.md").write_text("# Test Command 1")
            (commands_dir / "test2.md").write_text("# Test Command 2")
            (commands_dir / "notcmd.txt").write_text("Not a command")

            loader = CommandsLoader(workspace)
            commands = loader.list_commands()

            assert len(commands) == 2
            names = [c["name"] for c in commands]
            assert "test1" in names
            assert "test2" in names
            assert "notcmd" not in names

    def test_list_commands_extracts_description_from_frontmatter(self) -> None:
        """Test that list_commands extracts description from YAML frontmatter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            commands_dir = workspace / "commands"
            commands_dir.mkdir()

            cmd_content = """---
name: mycommand
description: "A test command"
---

# My Command

This is the command content.
"""
            (commands_dir / "mycommand.md").write_text(cmd_content)

            loader = CommandsLoader(workspace)
            commands = loader.list_commands()

            assert len(commands) == 1
            assert commands[0]["name"] == "mycommand"
            assert commands[0]["description"] == "A test command"

    def test_list_commands_sorted_alphabetically(self) -> None:
        """Test that commands are sorted alphabetically by name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            commands_dir = workspace / "commands"
            commands_dir.mkdir()

            (commands_dir / "zebra.md").write_text("# Zebra")
            (commands_dir / "alpha.md").write_text("# Alpha")
            (commands_dir / "beta.md").write_text("# Beta")

            loader = CommandsLoader(workspace)
            commands = loader.list_commands()

            names = [c["name"] for c in commands]
            assert names == ["alpha", "beta", "zebra"]

    def test_load_command_returns_content(self) -> None:
        """Test that load_command returns file content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            commands_dir = workspace / "commands"
            commands_dir.mkdir()

            (commands_dir / "test.md").write_text("# Test Command\n\nContent here.")

            loader = CommandsLoader(workspace)
            content = loader.load_command("test")

            assert content == "# Test Command\n\nContent here."

    def test_load_command_strips_frontmatter(self) -> None:
        """Test that load_command strips YAML frontmatter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            commands_dir = workspace / "commands"
            commands_dir.mkdir()

            cmd_content = """---
name: test
description: Test
---

# Test Command

Body content.
"""
            (commands_dir / "test.md").write_text(cmd_content)

            loader = CommandsLoader(workspace)
            content = loader.load_command("test")

            assert "---" not in content
            assert "# Test Command" in content
            assert "Body content." in content

    def test_load_command_normalizes_name(self) -> None:
        """Test that load_command handles / prefix in name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            commands_dir = workspace / "commands"
            commands_dir.mkdir()

            (commands_dir / "test.md").write_text("Content")

            loader = CommandsLoader(workspace)

            # Both should work
            assert loader.load_command("test") == "Content"
            assert loader.load_command("/test") == "Content"

    def test_load_command_returns_none_for_missing(self) -> None:
        """Test that load_command returns None for non-existent command."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            loader = CommandsLoader(workspace)

            assert loader.load_command("nonexistent") is None

    def test_get_command_names_returns_slash_prefixed(self) -> None:
        """Test that get_command_names returns names with / prefix."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            commands_dir = workspace / "commands"
            commands_dir.mkdir()

            (commands_dir / "cmd1.md").write_text("Content 1")
            (commands_dir / "cmd2.md").write_text("Content 2")

            loader = CommandsLoader(workspace)
            names = loader.get_command_names()

            assert set(names) == {"/cmd1", "/cmd2"}

    def test_build_commands_summary_formats_correctly(self) -> None:
        """Test that build_commands_summary formats commands nicely."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            commands_dir = workspace / "commands"
            commands_dir.mkdir()

            (commands_dir / "foo.md").write_text("---\ndescription: Foo command\n---\nContent")
            (commands_dir / "bar.md").write_text("# Bar")  # No description

            loader = CommandsLoader(workspace)
            summary = loader.build_commands_summary()

            assert "/bar" in summary
            assert "/foo" in summary
            assert "Foo command" in summary

    def test_build_commands_summary_empty_when_no_commands(self) -> None:
        """Test that build_commands_summary returns empty string when no commands."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            loader = CommandsLoader(workspace)

            assert loader.build_commands_summary() == ""

    def test_is_command_detects_valid_command(self) -> None:
        """Test that is_command returns True for valid commands."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            commands_dir = workspace / "commands"
            commands_dir.mkdir()

            (commands_dir / "test.md").write_text("Content")

            loader = CommandsLoader(workspace)

            assert loader.is_command("/test") is True
            assert loader.is_command("/test args") is True
            assert loader.is_command("test") is False
            assert loader.is_command("/nonexistent") is False

    def test_get_command_from_text_extracts_name(self) -> None:
        """Test that get_command_from_text extracts command name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            commands_dir = workspace / "commands"
            commands_dir.mkdir()

            (commands_dir / "hello.md").write_text("Hello content")

            loader = CommandsLoader(workspace)

            assert loader.get_command_from_text("/hello") == "hello"
            assert loader.get_command_from_text("/hello world") == "hello"
            assert loader.get_command_from_text("hello") is None
            assert loader.get_command_from_text("/nonexistent") is None

    def test_get_command_from_text_requires_slash_at_start(self) -> None:
        """Test that get_command_from_text requires / at the very start."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            commands_dir = workspace / "commands"
            commands_dir.mkdir()

            (commands_dir / "test.md").write_text("Content")

            loader = CommandsLoader(workspace)

            # Valid command starts with /
            assert loader.get_command_from_text("/test") == "test"
            # Space before / means not a command
            assert loader.get_command_from_text(" /test") is None
            assert loader.get_command_from_text("  /test") is None


class TestStripFrontmatter:
    """Tests for _strip_frontmatter method."""

    def test_strips_yaml_frontmatter(self) -> None:
        """Test that YAML frontmatter is stripped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            loader = CommandsLoader(workspace)

            content = "---\nname: test\n---\n# Content\nBody"
            result = loader._strip_frontmatter(content)

            assert "---" not in result
            assert "# Content" in result
            assert "Body" in result

    def test_returns_original_when_no_frontmatter(self) -> None:
        """Test that content without frontmatter is returned unchanged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            loader = CommandsLoader(workspace)

            content = "# No Frontmatter\n\nJust content."
            result = loader._strip_frontmatter(content)

            assert result == content

    def test_handles_multiline_frontmatter(self) -> None:
        """Test that multiline frontmatter is handled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            loader = CommandsLoader(workspace)

            content = "---\nname: test\ndescription: |\n  Multi\n  line\n---\nBody"
            result = loader._strip_frontmatter(content)

            assert "name:" not in result
            assert "description:" not in result
            assert "Body" in result


class TestGetCommandDescription:
    """Tests for _get_command_description method."""

    def test_extracts_description_with_quotes(self) -> None:
        """Test extraction of quoted description."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            loader = CommandsLoader(workspace)

            cmd_file = workspace / "test.md"
            cmd_file.write_text('---\ndescription: "My command"\n---\nContent')

            desc = loader._get_command_description(cmd_file)
            assert desc == "My command"

    def test_extracts_description_without_quotes(self) -> None:
        """Test extraction of unquoted description."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            loader = CommandsLoader(workspace)

            cmd_file = workspace / "test.md"
            cmd_file.write_text("---\ndescription: Simple desc\n---\nContent")

            desc = loader._get_command_description(cmd_file)
            assert desc == "Simple desc"

    def test_returns_empty_when_no_description(self) -> None:
        """Test that empty string is returned when no description in frontmatter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            loader = CommandsLoader(workspace)

            cmd_file = workspace / "test.md"
            cmd_file.write_text("---\nname: test\n---\nContent")

            desc = loader._get_command_description(cmd_file)
            assert desc == ""

    def test_returns_empty_when_no_frontmatter(self) -> None:
        """Test that empty string is returned when no frontmatter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            loader = CommandsLoader(workspace)

            cmd_file = workspace / "test.md"
            cmd_file.write_text("# Just content\n\nNo frontmatter here.")

            desc = loader._get_command_description(cmd_file)
            assert desc == ""
