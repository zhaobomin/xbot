"""Script command manager for !cmd system.

Manages a JSON config table that maps command shortnames to shell command strings.
Supports add/remove/list/execute operations, persisted to a JSON file.
"""

import json
import logging
import os
import re
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Reserved names that cannot be used as command shortnames
RESERVED_NAMES = {"list", "add", "remove"}

# Valid shortname pattern: alphanumeric, hyphens, underscores, 1-32 chars
_SHORTNAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


def validate_shortname(name: str) -> str | None:
    """Validate a command shortname. Returns error message or None if valid."""
    if not name:
        return "Shortname cannot be empty"
    if name in RESERVED_NAMES:
        return f"'{name}' is a reserved name"
    if not _SHORTNAME_RE.match(name):
        return "Shortname must be 1-32 chars, only letters, digits, hyphens, underscores"
    return None


class ScriptCommandManager:
    """Manages script command configuration table.

    Loads/saves a JSON file mapping shortname → command string.
    The file is read on every operation to support hot-reload
    (manual edits to the file are reflected immediately).
    """

    def __init__(self, scripts_file: str | Path):
        """Initialize with path to scripts.json.

        Args:
            scripts_file: Path to the JSON config file.
        """
        self._scripts_file = Path(scripts_file)

    @property
    def scripts_file(self) -> Path:
        return self._scripts_file

    def _load(self) -> dict[str, str]:
        """Load commands from JSON file. Returns empty dict if file missing."""
        if not self._scripts_file.exists():
            return {}
        try:
            with open(self._scripts_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
            logger.warning("scripts.json is not a dict, ignoring: %s", type(data))
            return {}
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to load scripts.json: %s", e)
            return {}

    def _save(self, commands: dict[str, str]) -> None:
        """Atomically save commands to JSON file."""
        self._scripts_file.parent.mkdir(parents=True, exist_ok=True)
        # Use tempfile for unique tmp path (avoids concurrent collision)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._scripts_file.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(commands, f, ensure_ascii=False, indent=2, sort_keys=True)
                f.write("\n")
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, str(self._scripts_file))
        except Exception:
            # Clean up tmp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def list_commands(self) -> dict[str, str]:
        """Return all configured commands."""
        return self._load()

    def get(self, name: str) -> str | None:
        """Get command string by shortname. Returns None if not found."""
        return self._load().get(name)

    def exists(self, name: str) -> bool:
        """Check if a command shortname exists."""
        return name in self._load()

    def add(self, name: str, command: str) -> bool:
        """Add or update a command. Returns True if created, False if updated.

        Raises ValueError if name is reserved or invalid.
        """
        error = validate_shortname(name)
        if error:
            raise ValueError(error)
        if not command or not command.strip():
            raise ValueError("Command cannot be empty")
        commands = self._load()
        is_new = name not in commands
        commands[name] = command
        self._save(commands)
        return is_new

    def remove(self, name: str) -> bool:
        """Remove a command. Returns True if removed, False if not found."""
        commands = self._load()
        if name not in commands:
            return False
        del commands[name]
        self._save(commands)
        return True
