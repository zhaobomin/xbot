"""File classification and path reference formatting for multimodal support.

Classifies files as IMAGE (for base64 inline) or FILE (for path reference),
and formats file references for LLM consumption.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from xbot.utils.helpers import detect_image_mime


class FileType(Enum):
    """File classification for multimodal processing."""

    IMAGE = "image"  # Send as base64 inline (Anthropic image content block)
    FILE = "file"  # Send as path reference (model reads via tools)


_SUPPORTED_IMAGE_MIMES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)


def classify_file(path: str) -> FileType:
    """Classify a file as IMAGE or FILE based on magic bytes.

    Images (PNG/JPEG/GIF/WebP) are sent as base64 content blocks.
    Everything else is sent as a path reference for the model to read via tools.
    """
    p = Path(path)
    if not p.is_file():
        return FileType.FILE
    try:
        with p.open("rb") as f:
            header = f.read(16)
    except OSError:
        return FileType.FILE
    mime = detect_image_mime(header)
    if mime and mime in _SUPPORTED_IMAGE_MIMES:
        return FileType.IMAGE
    return FileType.FILE


def format_file_reference(path: str) -> str:
    """Format a file path into a reference string for the LLM.

    Includes filename, human-readable size, and file type suffix so the
    model knows what it's dealing with and where to find it.
    Always outputs an absolute path so the model can reliably access it.
    """
    p = Path(path).resolve()
    try:
        size = p.stat().st_size if p.is_file() else 0
    except OSError:
        size = 0
    human_size = _human_readable_size(size)
    suffix = p.suffix.lstrip(".").upper() or "FILE"
    return f"[附件: {p.name} ({human_size}, {suffix}), 路径: {p}]"


def _human_readable_size(size: int) -> str:
    """Convert bytes to human-readable size string."""
    if size < 1024:
        return f"{size}B"
    for unit in ("KB", "MB", "GB", "TB"):
        size_f = size / 1024
        if size_f < 1024 or unit == "TB":
            return f"{size_f:.1f}{unit}"
        size = int(size_f)
    return f"{size_f:.1f}TB"  # pragma: no cover
