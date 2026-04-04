from __future__ import annotations

from pathlib import Path
from pathlib import PurePosixPath

from xbot.memory.instructions.parser import parse_instruction_file
from xbot.memory.models import InstructionFile

MAX_INCLUDE_DEPTH = 5


class InstructionLoader:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)

    def get_instruction_files(self) -> list[InstructionFile]:
        return self._load(None)

    def get_instruction_files_for_path(self, target_path: Path) -> list[InstructionFile]:
        rel = target_path.relative_to(self.workspace).as_posix()
        return [
            item
            for item in self._load(rel)
            if not item.globs or any(self._matches_glob(rel, pattern) for pattern in item.globs)
        ]

    def _load(self, target_relative_path: str | None) -> list[InstructionFile]:
        ordered: list[tuple[Path, str]] = []
        root = self.workspace / "CLAUDE.md"
        dot = self.workspace / ".claude" / "CLAUDE.md"
        rules_dir = self.workspace / ".claude" / "rules"
        local = self.workspace / "CLAUDE.local.md"
        if root.exists():
            ordered.append((root, "project"))
        if dot.exists():
            ordered.append((dot, "project"))
        if rules_dir.exists():
            ordered.extend((path, "rule") for path in sorted(rules_dir.rglob("*.md")))
        if local.exists():
            ordered.append((local, "local"))

        result: list[InstructionFile] = []
        seen: set[Path] = set()
        for path, kind in ordered:
            result.extend(self._process(path.resolve(), kind, seen, 0, target_relative_path))
        return result

    def _process(
        self,
        path: Path,
        kind: str,
        seen: set[Path],
        depth: int,
        target_relative_path: str | None,
        parent: Path | None = None,
    ) -> list[InstructionFile]:
        if depth >= MAX_INCLUDE_DEPTH or path in seen or not path.exists():
            return []
        seen.add(path)
        parsed = parse_instruction_file(path, path.read_text(encoding="utf-8"))
        if target_relative_path and parsed.globs and not any(
            self._matches_glob(target_relative_path, pattern) for pattern in parsed.globs
        ):
            return []
        current = InstructionFile(
            path=path,
            content=parsed.content,
            kind=kind,  # type: ignore[arg-type]
            globs=parsed.globs,
            parent=parent,
        )
        items = [current]
        for include in parsed.include_paths:
            if include.suffix.lower() not in {"", ".md", ".txt", ".py", ".ts", ".tsx", ".js", ".json", ".yaml", ".yml", ".toml"}:
                continue
            if include.exists():
                items.extend(self._process(include, kind, seen, depth + 1, target_relative_path, parent=path))
        return items

    def _matches_glob(self, relative_path: str, pattern: str) -> bool:
        path = PurePosixPath(relative_path)
        return path.match(pattern) or path.match(pattern.replace("**/", ""))
