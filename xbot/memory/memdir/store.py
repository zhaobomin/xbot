from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
import re

from xbot.logging import get_logger
from xbot.memory.memdir.frontmatter import parse_frontmatter
from xbot.memory.memdir.index import render_index
from xbot.memory.memdir.paths import get_memory_dir
from xbot.memory.memdir.scan import scan_memory_files
from xbot.memory.memdir.types import VALID_MEMORY_TYPES
from xbot.memory.models import MemoryDocument, MemoryHeader, MemoryType

logger = get_logger(__name__)


class MemoryDirStore:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.memory_dir = get_memory_dir(self.workspace)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.memory_dir / "MEMORY.md"

    def create_memory(
        self,
        memory_type: MemoryType,
        title: str,
        description: str,
        body: str,
    ) -> Path:
        self._validate_type(memory_type)
        folder = self.memory_dir / memory_type
        folder.mkdir(parents=True, exist_ok=True)
        filename = self._slugify(title) + ".md"
        path = folder / filename
        self._atomic_write(path, self._render_document(memory_type, title, description, body))
        self.rebuild_index()
        return path

    def update_memory(self, path: Path, body: str, description: str | None = None) -> None:
        managed_path = self.resolve_managed_path(path)
        doc = self.read_memory(managed_path)
        desc = description or doc.description
        self._atomic_write(managed_path, self._render_document(doc.memory_type, doc.name, desc, body))
        self.rebuild_index()

    def delete_memory(self, path: Path) -> None:
        managed_path = self.resolve_managed_path(path)
        managed_path.unlink(missing_ok=True)
        self.rebuild_index()

    def scan_headers(self) -> list[MemoryHeader]:
        return scan_memory_files(self.memory_dir)

    def rebuild_index(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self.index_path, render_index(scan_memory_files(self.memory_dir, limit=None)))

    def load_index_for_prompt(self) -> str:
        if not self.index_path.exists():
            self.rebuild_index()
            return self.index_path.read_text(encoding="utf-8")
        content = self.index_path.read_text(encoding="utf-8")
        if self._is_legacy_memory_content(content):
            self._migrate_legacy_memory(content)
            return self.index_path.read_text(encoding="utf-8")
        return content

    # ------------------------------------------------------------------
    # Legacy MEMORY.md migration
    # ------------------------------------------------------------------

    def _is_legacy_memory_content(self, content: str) -> bool:
        """Return True if MEMORY.md looks like old-style inline content, not an index.

        The new index format only contains lines like ``- [name](path)`` or
        ``> WARNING:`` overflow notices.  Legacy files typically have
        frontmatter (``---``), numbered lists, or plain prose.
        """
        stripped = content.strip()
        if not stripped:
            return False
        if stripped.startswith("---"):
            return True
        lines = [line for line in stripped.splitlines() if line.strip()]
        if not lines:
            return False
        non_index = sum(
            1 for line in lines
            if not line.strip().startswith("- [") and not line.strip().startswith("> WARNING")
        )
        return non_index > len(lines) * 0.5

    def _migrate_legacy_memory(self, content: str) -> None:
        """Save legacy inline MEMORY.md as a memory file, then rebuild index."""
        logger.info("[MemoryMigration] Detected legacy MEMORY.md with inline content, migrating…")
        backup_dir = self.memory_dir / "project"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / "legacy-memory.md"
        if not backup_path.exists():
            fm, body = parse_frontmatter(content)
            if not body.strip():
                body = content
            self._atomic_write(
                backup_path,
                self._render_document("project", "Legacy Memory", "Migrated from old MEMORY.md", body),
            )
            logger.info("[MemoryMigration] Saved legacy content → %s", backup_path)
        self.rebuild_index()
        logger.info("[MemoryMigration] Index rebuilt. Migration complete.")

    def read_memory(self, path: Path) -> MemoryDocument:
        managed_path = self.resolve_managed_path(path)
        raw = managed_path.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(raw)
        if not frontmatter:
            raise ValueError(f"Memory document missing or malformed frontmatter: {managed_path}")
        return MemoryDocument(
            path=managed_path,
            memory_type=frontmatter.get("type", "project"),  # type: ignore[arg-type]
            name=frontmatter.get("name", managed_path.stem),
            description=frontmatter.get("description", ""),
            updated_at=frontmatter.get("updated_at", ""),
            body=body,
        )

    def list_memories(self) -> list[MemoryHeader]:
        return self.scan_headers()

    def _atomic_write(self, path: Path, content: str) -> None:
        """Write content atomically via temp+rename (mirrors SessionManager.save)."""
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp), str(path))
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def _render_document(self, memory_type: MemoryType, title: str, description: str, body: str) -> str:
        updated_at = datetime.now().isoformat(timespec="seconds")
        return (
            "---\n"
            f"name: {title}\n"
            f"description: {description}\n"
            f"type: {memory_type}\n"
            f"updated_at: {updated_at}\n"
            "---\n\n"
            f"{body.strip()}\n"
        )

    def _validate_type(self, memory_type: MemoryType) -> None:
        if memory_type not in VALID_MEMORY_TYPES:
            raise ValueError(f"Unsupported memory type: {memory_type}")

    def resolve_managed_path(self, path: Path | str) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.memory_dir / candidate
        if candidate.is_symlink():
            raise ValueError(f"Symlinks not allowed in memory directory: {path}")
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.memory_dir.resolve())
        except ValueError as exc:
            raise ValueError(f"Path is outside memory dir: {path}") from exc
        return resolved

    def _slugify(self, title: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        return slug or "memory"
