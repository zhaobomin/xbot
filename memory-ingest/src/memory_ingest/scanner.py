from __future__ import annotations

from datetime import datetime, timedelta, timezone
import fnmatch
import hashlib
from pathlib import Path

from .config import SourcesConfig
from .models import ScannedFile


SUPPORTED_SUFFIXES = {
    ".md": "md",
    ".txt": "txt",
    ".docx": "docx",
    ".pptx": "pptx",
    ".pdf": "pdf",
}


def _match_any(patterns: list[str], candidate: str) -> bool:
    if not patterns:
        return True
    candidates = {candidate, candidate.lstrip("./"), Path(candidate).name, f"**/{candidate}"}
    return any(fnmatch.fnmatch(item, pattern) for pattern in patterns for item in candidates)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_sources(
    config: SourcesConfig,
    *,
    since: timedelta | None = None,
    limit: int | None = None,
) -> list[ScannedFile]:
    results: list[ScannedFile] = []
    cutoff = datetime.now(timezone.utc) - since if since else None

    for directory in config.directories:
        root = Path(directory)
        if not root.exists():
            continue
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            doc_type = SUPPORTED_SUFFIXES.get(path.suffix.lower())
            if not doc_type:
                continue
            rel = path.relative_to(root).as_posix()
            if not _match_any(config.include_globs, rel):
                continue
            if config.exclude_globs and any(fnmatch.fnmatch(rel, p) for p in config.exclude_globs):
                continue

            modified_time = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if cutoff and modified_time < cutoff:
                continue

            results.append(
                ScannedFile(
                    path=str(path.resolve()),
                    doc_type=doc_type,
                    modified_time=modified_time,
                    content_hash=_hash_file(path),
                )
            )
            if limit is not None and len(results) >= limit:
                return results
    return results
