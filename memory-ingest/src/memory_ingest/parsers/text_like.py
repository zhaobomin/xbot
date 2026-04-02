from __future__ import annotations

from pathlib import Path

from ..models import DocumentSection, ParsedDocument, ScannedFile


def parse_text_like(path: Path, scanned_file: ScannedFile) -> ParsedDocument:
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = [line.rstrip() for line in text.splitlines()]
    title = next((line.lstrip("# ").strip() for line in lines if line.strip()), path.stem)
    sections = [DocumentSection(text=text.strip())] if text.strip() else []
    return ParsedDocument(
        source_path=scanned_file.path,
        doc_type=scanned_file.doc_type,
        title=title or path.stem,
        sections=sections,
        modified_time=scanned_file.modified_time,
        content_hash=scanned_file.content_hash,
    )
