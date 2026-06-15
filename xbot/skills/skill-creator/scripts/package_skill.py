#!/usr/bin/env python3
"""Package an xbot skill directory into a .skill zip archive."""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path


def package_skill(skill_dir: str | Path, dist_dir: str | Path) -> Path | None:
    skill_path = Path(skill_dir).resolve()
    if not skill_path.is_dir() or not (skill_path / "SKILL.md").is_file():
        return None

    for path in skill_path.rglob("*"):
        if path.is_symlink():
            return None

    output_dir = Path(dist_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / f"{skill_path.name}.skill"

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(skill_path.rglob("*")):
            if path.is_file():
                archive.write(path, f"{skill_path.name}/{path.relative_to(skill_path).as_posix()}")

    return archive_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Package an xbot skill directory.")
    parser.add_argument("skill_dir")
    parser.add_argument("--dist", default="dist")
    args = parser.parse_args()

    archive = package_skill(args.skill_dir, args.dist)
    if archive is None:
        print("Failed to package skill", file=sys.stderr)
        return 1
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
