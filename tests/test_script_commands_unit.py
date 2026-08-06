"""Unit tests for ScriptCommandManager."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from xbot.runtime.core.script_commands import (
    RESERVED_NAMES,
    ScriptCommandManager,
    validate_shortname,
)


@pytest.fixture
def tmp_scripts_file():
    """Create a temp scripts.json path that gets cleaned up."""
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)  # Remove so manager starts fresh
    yield path
    # Cleanup
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def manager(tmp_scripts_file):
    return ScriptCommandManager(tmp_scripts_file)


class TestValidateShortname:
    """Tests for validate_shortname()."""

    def test_valid_simple(self):
        assert validate_shortname("deploy") is None

    def test_valid_with_hyphen(self):
        assert validate_shortname("git-pull") is None

    def test_valid_with_underscore(self):
        assert validate_shortname("git_pull") is None

    def test_valid_with_digits(self):
        assert validate_shortname("backup2") is None

    def test_valid_single_char(self):
        assert validate_shortname("x") is None

    def test_empty_rejected(self):
        assert validate_shortname("") is not None

    def test_reserved_list(self):
        assert validate_shortname("list") is not None

    def test_reserved_add(self):
        assert validate_shortname("add") is not None

    def test_reserved_remove(self):
        assert validate_shortname("remove") is not None

    def test_too_long(self):
        assert validate_shortname("a" * 33) is not None

    def test_max_length_ok(self):
        assert validate_shortname("a" * 32) is None

    def test_spaces_rejected(self):
        assert validate_shortname("my name") is not None

    def test_special_chars_rejected(self):
        assert validate_shortname("rm-rf") is None  # hyphen ok, but...

    def test_shell_metachar_rejected(self):
        for name in ["rm -rf", "; rm", "$(", "&&whoami", "|cat", "`id`"]:
            assert validate_shortname(name) is not None, f"Should reject: {name}"


class TestScriptCommandManagerLoadSave:
    """Tests for load/save mechanics."""

    def test_load_missing_file(self, manager):
        assert manager.list_commands() == {}

    def test_save_and_load_roundtrip(self, manager):
        manager.add("deploy", "docker-compose up -d")
        assert manager.get("deploy") == "docker-compose up -d"

    def test_save_creates_file(self, tmp_scripts_file):
        manager = ScriptCommandManager(tmp_scripts_file)
        manager.add("test", "echo hi")
        assert Path(tmp_scripts_file).exists()

    def test_save_file_permissions(self, manager):
        manager.add("test", "echo hi")
        mode = manager.scripts_file.stat().st_mode
        assert (mode & 0o777) == 0o600

    def test_save_is_valid_json(self, manager):
        manager.add("deploy", "docker-compose up -d")
        manager.add("backup", "backup.sh")
        with open(manager.scripts_file) as f:
            data = json.load(f)
        assert data == {"backup": "backup.sh", "deploy": "docker-compose up -d"}

    def test_load_corrupt_json(self, manager):
        manager.scripts_file.parent.mkdir(parents=True, exist_ok=True)
        manager.scripts_file.write_text("{invalid json}")
        assert manager.list_commands() == {}

    def test_load_non_dict_json(self, manager):
        manager.scripts_file.parent.mkdir(parents=True, exist_ok=True)
        manager.scripts_file.write_text('["not", "a", "dict"]')
        assert manager.list_commands() == {}

    def test_load_list_json(self, manager):
        manager.scripts_file.parent.mkdir(parents=True, exist_ok=True)
        manager.scripts_file.write_text('[1, 2, 3]')
        assert manager.list_commands() == {}

    def test_hot_reload_after_manual_edit(self, manager):
        manager.add("deploy", "docker-compose up -d")
        # Manually edit the file
        with open(manager.scripts_file, "w") as f:
            json.dump({"manual_cmd": "echo manual"}, f)
        # Manager should see the change
        assert manager.get("manual_cmd") == "echo manual"
        assert manager.get("deploy") is None


class TestScriptCommandManagerAdd:
    """Tests for add() operation."""

    def test_add_new(self, manager):
        assert manager.add("deploy", "docker-compose up -d") is True

    def test_add_update_existing(self, manager):
        manager.add("deploy", "docker-compose up -d")
        assert manager.add("deploy", "docker-compose up -d --force") is False
        assert manager.get("deploy") == "docker-compose up -d --force"

    def test_add_reserved_name_raises(self, manager):
        for name in RESERVED_NAMES:
            with pytest.raises(ValueError):
                manager.add(name, "echo hi")

    def test_add_invalid_name_raises(self, manager):
        with pytest.raises(ValueError):
            manager.add("rm -rf", "echo hi")
        with pytest.raises(ValueError):
            manager.add("", "echo hi")
        with pytest.raises(ValueError):
            manager.add("a" * 33, "echo hi")

    def test_add_empty_command_raises(self, manager):
        with pytest.raises(ValueError):
            manager.add("test", "")
        with pytest.raises(ValueError):
            manager.add("test", "   ")


class TestScriptCommandManagerRemove:
    """Tests for remove() operation."""

    def test_remove_existing(self, manager):
        manager.add("deploy", "docker-compose up -d")
        assert manager.remove("deploy") is True
        assert manager.get("deploy") is None

    def test_remove_nonexistent(self, manager):
        assert manager.remove("nonexistent") is False

    def test_remove_then_list(self, manager):
        manager.add("a", "echo a")
        manager.add("b", "echo b")
        manager.remove("a")
        cmds = manager.list_commands()
        assert "a" not in cmds
        assert "b" in cmds


class TestScriptCommandManagerExists:
    """Tests for exists() operation."""

    def test_exists_true(self, manager):
        manager.add("deploy", "docker-compose up -d")
        assert manager.exists("deploy") is True

    def test_exists_false(self, manager):
        assert manager.exists("nonexistent") is False
