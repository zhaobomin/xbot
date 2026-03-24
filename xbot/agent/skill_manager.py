"""Skill Manager — unified skill lifecycle with hot-reload and Python plugin support.

Provides:
- Fingerprint-based change detection so the Claude SDK client can be
  invalidated when skills are added / modified / deleted on disk.
- Dynamic loading of Python skill plugins (``tool.py`` alongside ``SKILL.md``).
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xbot.agent.skills import BUILTIN_SKILLS_DIR, SkillsLoader
from xbot.agent.tools.base import Tool

if TYPE_CHECKING:
    from xbot.agent.tool_adapter import ToolAdapter

logger = logging.getLogger(__name__)


class SkillManager:
    """Manages skill discovery, change detection, and Python plugin loading.

    Usage::

        mgr = SkillManager(workspace)
        # On each request:
        if mgr.check_for_changes():
            mgr.sync_tools_to_adapter(tool_adapter)
        # Then check mgr.version when deciding whether to recreate the client.
    """

    def __init__(
        self,
        workspace: Path,
        builtin_skills_dir: Path | None = None,
    ) -> None:
        self._skills_loader = SkillsLoader(workspace, builtin_skills_dir)
        self._version: str = ""
        self._python_tools: list[Tool] = []
        self._python_tool_names: set[str] = set()
        self._loaded_module_names: set[str] = set()  # Track loaded module names for cleanup
        # Compute initial fingerprint and load Python skills
        self._version = self.compute_fingerprint()
        self._reload_python_skills()

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def skills_loader(self) -> SkillsLoader:
        """Expose the underlying :class:`SkillsLoader` for backward compat."""
        return self._skills_loader

    @property
    def version(self) -> str:
        """Current fingerprint version string."""
        return self._version

    # ------------------------------------------------------------------
    # Change detection
    # ------------------------------------------------------------------

    def compute_fingerprint(self) -> str:
        """Compute an md5 fingerprint of all skill directories.

        Collects ``(dir_name, SKILL.md mtime, tool.py exists, tool.py mtime)``
        tuples, deduplicates by skill name (higher-priority source wins),
        sorts them, and returns the md5 hex digest.  Only uses
        ``os.stat`` — typically < 1 ms.
        """
        # Use dict to deduplicate: first occurrence wins (higher priority)
        seen: dict[str, str] = {}
        loader = self._skills_loader

        for base in (
            loader.workspace_skills,
            loader.scoped_workspace_skills,
            loader.personal_skills,
            loader.builtin_skills,
        ):
            if base is None or not base.exists():
                continue
            try:
                for skill_dir in base.iterdir():
                    if not skill_dir.is_dir():
                        continue
                    skill_name = skill_dir.name
                    if skill_name in seen:
                        continue  # Higher-priority source already recorded
                    skill_file = skill_dir / "SKILL.md"
                    if not skill_file.exists():
                        continue
                    try:
                        skill_mtime = os.stat(skill_file).st_mtime_ns
                    except OSError:
                        skill_mtime = 0

                    tool_file = skill_dir / "tool.py"
                    if tool_file.exists():
                        try:
                            tool_mtime = os.stat(tool_file).st_mtime_ns
                        except OSError:
                            tool_mtime = 0
                        seen[skill_name] = f"{skill_name}:{skill_mtime}:py:{tool_mtime}"
                    else:
                        seen[skill_name] = f"{skill_name}:{skill_mtime}:md:0"
            except OSError:
                # Directory disappeared between check and iteration
                continue

        entries = sorted(seen.values())
        return hashlib.md5("|".join(entries).encode()).hexdigest()

    def check_for_changes(self) -> bool:
        """Compare current disk state with cached fingerprint.

        If the fingerprint changed, reloads Python skill plugins and updates
        :attr:`version`.

        Returns:
            ``True`` if skills changed since last check.
        """
        new_fp = self.compute_fingerprint()
        if new_fp == self._version:
            return False

        logger.info(
            "[SkillManager] Skills changed: %s -> %s, reloading Python skills",
            self._version,
            new_fp,
        )
        self._version = new_fp
        self._reload_python_skills()
        return True

    # ------------------------------------------------------------------
    # Python skill plugins
    # ------------------------------------------------------------------

    def get_python_tools(self) -> list[Tool]:
        """Return all currently loaded Python skill :class:`Tool` instances."""
        return list(self._python_tools)

    def sync_tools_to_adapter(self, adapter: ToolAdapter) -> None:
        """Push current Python skill tools into the :class:`ToolAdapter`.

        Old Python skill tools are removed first, then new ones are registered.
        """
        adapter.register_python_skill_tools(self._python_tools)

    def _reload_python_skills(self) -> None:
        """(Re-)load all Python skill plugins from disk.

        Each skill directory that contains ``tool.py`` (and whose ``SKILL.md``
        declares ``type: python``) is loaded via :mod:`importlib`.

        Stale modules from previously loaded skills that no longer exist on
        disk are removed from ``sys.modules`` to prevent memory leaks.
        """
        new_tools: list[Tool] = []
        new_module_names: set[str] = set()
        loader = self._skills_loader

        for skill_info in loader.list_skills(filter_unavailable=False):
            if skill_info.get("type") != "python":
                continue

            skill_name = skill_info["name"]
            skill_dir = Path(skill_info["path"]).parent
            tool_file = skill_dir / "tool.py"

            if not tool_file.exists():
                continue

            # Security: warn for workspace-sourced Python skills
            source = skill_info.get("source", "")
            if source == "workspace":
                logger.warning(
                    "[SkillManager] Loading Python skill '%s' from workspace. "
                    "Ensure you trust this code: %s",
                    skill_name,
                    tool_file,
                )

            module_name = f"xbot._skill_plugins.{source}.{skill_name}"

            try:
                tools = self._load_python_skill(skill_name, tool_file, module_name)
                new_tools.extend(tools)
                # Only track the module after successful load
                new_module_names.add(module_name)
                if tools:
                    logger.info(
                        "[SkillManager] Loaded %d tool(s) from Python skill '%s': %s",
                        len(tools),
                        skill_name,
                        [t.name for t in tools],
                    )
            except Exception:
                logger.exception(
                    "[SkillManager] Failed to load Python skill '%s' from %s, skipping",
                    skill_name,
                    tool_file,
                )

        # Clean up stale modules from deleted skills
        stale_modules = self._loaded_module_names - new_module_names
        for mod_name in stale_modules:
            sys.modules.pop(mod_name, None)
            logger.debug("[SkillManager] Removed stale module: %s", mod_name)

        self._python_tools = new_tools
        self._python_tool_names = {t.name for t in new_tools}
        self._loaded_module_names = new_module_names

    def _load_python_skill(
        self,
        skill_name: str,
        tool_file: Path,
        module_name: str,
    ) -> list[Tool]:
        """Load a single Python skill module and extract Tool instances.

        Args:
            skill_name: Skill directory name (for logging).
            tool_file: Path to the ``tool.py`` file.
            module_name: Fully-qualified module name for ``sys.modules``
                         isolation (e.g. ``xbot._skill_plugins.workspace.foo``).

        Discovery priority:
        1. ``create_tools(**kwargs)`` factory function
        2. All public classes that inherit from :class:`Tool`
        """

        # Remove stale module from sys.modules to force fresh import
        sys.modules.pop(module_name, None)

        spec = importlib.util.spec_from_file_location(module_name, tool_file)
        if spec is None or spec.loader is None:
            logger.warning(
                "[SkillManager] Cannot create import spec for %s", tool_file,
            )
            return []

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise

        # Strategy 1: factory function
        factory = getattr(module, "create_tools", None)
        if callable(factory):
            kwargs: dict[str, Any] = {
                "workspace": self._skills_loader.workspace,
                "skills_loader": self._skills_loader,
            }
            result = factory(**{
                k: v for k, v in kwargs.items()
                if k in inspect.signature(factory).parameters
            })
            if isinstance(result, list):
                return [t for t in result if isinstance(t, Tool)]
            return []

        # Strategy 2: auto-discover Tool subclasses defined in this module
        tools: list[Tool] = []
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if (
                isinstance(obj, type)
                and issubclass(obj, Tool)
                and obj is not Tool
                and not attr_name.startswith("_")
                and getattr(obj, "__module__", None) == module_name
            ):
                try:
                    tools.append(obj())
                except Exception:
                    logger.exception(
                        "[SkillManager] Failed to instantiate Tool class %s.%s",
                        skill_name,
                        attr_name,
                    )
        return tools
