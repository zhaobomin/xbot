"""File classification and path reference formatting for multimodal support.

Classifies files as IMAGE (for base64 inline), AUDIO (for base64 inline),
or FILE (for path reference), and formats file references for LLM consumption.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from xbot.utils.helpers import detect_audio_mime, detect_image_mime


class FileType(Enum):
    """File classification for multimodal processing."""

    IMAGE = "image"  # Send as base64 inline (Anthropic image content block)
    AUDIO = "audio"  # Send as base64 inline (Anthropic audio content block)
    FILE = "file"  # Send as path reference (model reads via tools)


_SUPPORTED_IMAGE_MIMES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)

_SUPPORTED_AUDIO_MIMES = frozenset(
    {"audio/mp3", "audio/wav", "audio/ogg", "audio/flac", "audio/mp4"}
)


def classify_file(path: str) -> FileType:
    """Classify a file as IMAGE, AUDIO, or FILE based on magic bytes.

    Images (PNG/JPEG/GIF/WebP) are sent as base64 content blocks.
    Audio files (MP3/WAV/OGG/FLAC/M4A) are sent as base64 content blocks.
    Everything else is sent as a path reference for the model to read via tools.
    """
    p = Path(path)
    if not p.is_file():
        return FileType.FILE
    try:
        # Read enough bytes for both image (16) and audio (12) detection
        with p.open("rb") as f:
            header = f.read(16)
    except OSError:
        return FileType.FILE

    # Check for image first (needs up to 12 bytes)
    mime = detect_image_mime(header)
    if mime and mime in _SUPPORTED_IMAGE_MIMES:
        return FileType.IMAGE

    # Check for audio (needs up to 12 bytes, use same header)
    audio_mime = detect_audio_mime(header[:12])
    if audio_mime and audio_mime in _SUPPORTED_AUDIO_MIMES:
        return FileType.AUDIO

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
