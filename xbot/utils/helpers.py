"""Utility functions for xbot."""

import json
import logging
import re
import time
from datetime import datetime
from importlib.resources import files as pkg_files
from pathlib import Path
from typing import Any

import tiktoken

logger = logging.getLogger(__name__)


def detect_image_mime(data: bytes) -> str | None:
    """Detect image MIME type from magic bytes, ignoring file extension."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def detect_audio_mime(data: bytes) -> str | None:
    """Detect audio MIME type from magic bytes.

    Supports common audio formats:
    - MP3: MPEG Audio frame sync (0xFF 0xF*)
    - WAV: RIFF....WAVE
    - OGG: OggS
    - FLAC: fLaC
    - M4A/MP4: ftyp with audio brands

    Args:
        data: Audio file bytes (at least 12 bytes recommended)

    Returns:
        MIME type string or None if not recognized
    """
    if len(data) < 2:
        return None

    # MP3: MPEG Audio frame sync
    # Valid patterns: 0xFF followed by 0xF2-0xFF (varies by MPEG version/layer)
    # Common: 0xFF 0xFB (MPEG1 Layer III), 0xFF 0xFA (MPEG1 Layer III VBR)
    if data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        # Frame sync: 11 bits set (0xFF + upper 3 bits of second byte)
        return "audio/mp3"

    if len(data) < 4:
        return None

    # WAV: RIFF....WAVE (needs at least 12 bytes)
    if data[:4] == b"RIFF":
        if len(data) >= 12 and data[8:12] == b"WAVE":
            return "audio/wav"
        return None

    # OGG: OggS
    if data[:4] == b"OggS":
        return "audio/ogg"

    # FLAC: fLaC
    if data[:4] == b"fLaC":
        return "audio/flac"

    # M4A/MP4: ftyp box followed by brand
    # Format: 4-byte size + "ftyp" + brand (e.g., "M4A ", "mp42")
    if len(data) >= 8 and data[4:8] == b"ftyp":
        if len(data) < 11:
            return None
        # Get brand (4 bytes after "ftyp")
        brand = data[8:12] if len(data) >= 12 else data[8:11] + b" "

        # Audio-specific brands (M4A, M4B, F4A, F4B)
        if brand[:3] in (b"M4A", b"M4B", b"F4A", b"F4B"):
            return "audio/mp4"

        # Brands that could be audio-only MP4
        if brand in (b"mp41", b"mp42", b"isom", b"iso2", b"MSNV"):
            # These are generic MP4 - could be audio or video
            # Return audio/mp4 for compatibility (Claude will handle appropriately)
            return "audio/mp4"

        # Video-specific brands - don't classify as audio
        # avc1, mp4v, etc. are typically video containers

    return None


def ensure_dir(path: Path) -> Path:
    """Ensure directory exists, return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def timestamp() -> str:
    """Current ISO timestamp."""
    return datetime.now().isoformat()


def current_time_str() -> str:
    """Human-readable current time with weekday and timezone, e.g. '2026-03-15 22:30 (Saturday) (CST)'."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
    tz = time.strftime("%Z") or "UTC"
    return f"{now} ({tz})"


_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')

def safe_filename(name: str) -> str:
    """Replace unsafe path characters with underscores."""
    return _UNSAFE_CHARS.sub("_", name).strip()


def split_message(content: str, max_len: int = 2000) -> list[str]:
    """
    Split content into chunks within max_len, preferring line breaks.

    Args:
        content: The text content to split.
        max_len: Maximum length per chunk (default 2000 for Discord compatibility).

    Returns:
        List of message chunks, each within max_len.
    """
    if not content:
        return []
    if len(content) <= max_len:
        return [content]
    chunks: list[str] = []
    while content:
        if len(content) <= max_len:
            chunks.append(content)
            break
        cut = content[:max_len]
        # Try to break at newline first, then space, then hard break
        pos = cut.rfind('\n')
        if pos < 1:
            pos = cut.rfind(' ')
        if pos < 1:
            pos = max_len
        chunk = content[:pos]
        if chunk:  # Guard against empty chunks
            chunks.append(chunk)
        content = content[pos:].lstrip()
    return chunks


def build_assistant_message(
    content: str | None,
    tool_calls: list[dict[str, Any]] | None = None,
    reasoning_content: str | None = None,
    thinking_blocks: list[dict] | None = None,
) -> dict[str, Any]:
    """Build a provider-safe assistant message with optional reasoning fields."""
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if reasoning_content is not None:
        msg["reasoning_content"] = reasoning_content
    if thinking_blocks:
        msg["thinking_blocks"] = thinking_blocks
    return msg


def estimate_prompt_tokens(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> int:
    """Estimate prompt tokens with tiktoken."""
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        parts: list[str] = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        txt = part.get("text", "")
                        if txt:
                            parts.append(txt)
        if tools:
            parts.append(json.dumps(tools, ensure_ascii=False))
        return len(enc.encode("\n".join(parts)))
    except Exception as e:
        logger.debug("Failed to estimate prompt tokens: %s", e)
        return 0


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """Estimate prompt tokens contributed by one persisted message."""
    content = message.get("content")
    parts: list[str] = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text", "")
                if text:
                    parts.append(text)
            else:
                parts.append(json.dumps(part, ensure_ascii=False))
    elif content is not None:
        parts.append(json.dumps(content, ensure_ascii=False))

    for key in ("name", "tool_call_id"):
        value = message.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    if message.get("tool_calls"):
        parts.append(json.dumps(message["tool_calls"], ensure_ascii=False))

    payload = "\n".join(parts)
    if not payload:
        return 1
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return max(1, len(enc.encode(payload)))
    except Exception as e:
        logger.debug("Failed to estimate message tokens: %s", e)
        return max(1, len(payload) // 4)


def estimate_prompt_tokens_chain(
    provider: Any,
    model: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> tuple[int, str]:
    """Estimate prompt tokens via provider counter first, then tiktoken fallback."""
    provider_counter = getattr(provider, "estimate_prompt_tokens", None)
    if callable(provider_counter):
        try:
            tokens, source = provider_counter(messages, tools, model)
            if isinstance(tokens, (int, float)) and tokens > 0:
                return int(tokens), str(source or "provider_counter")
        except Exception as e:
            logger.debug("Provider token counter failed: %s", e)

    estimated = estimate_prompt_tokens(messages, tools)
    if estimated > 0:
        return int(estimated), "tiktoken"
    return 0, "none"


def sync_workspace_templates(workspace: Path, silent: bool = False) -> list[str]:
    """Sync bundled templates to workspace. Only creates missing files."""
    try:
        init_root = pkg_files("xbot") / "init_templates" / "workspace"
    except Exception:
        init_root = None
    try:
        legacy_root = pkg_files("xbot") / "templates"
    except Exception:
        legacy_root = None

    source_root = init_root if init_root and init_root.is_dir() else legacy_root
    if not source_root or not source_root.is_dir():
        return []

    added: list[str] = []

    def _copy_tree(src_root, dst_root: Path) -> None:
        for item in src_root.iterdir():
            if item.name.startswith("."):
                continue
            dst = dst_root / item.name
            if item.is_dir():
                _copy_tree(item, dst)
                continue
            if dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(item.read_bytes())
            added.append(str(dst.relative_to(workspace)))

    _copy_tree(source_root, workspace)
    (workspace / "skills").mkdir(exist_ok=True)
    (workspace / "commands").mkdir(exist_ok=True)

    if added and not silent:
        from rich.console import Console
        for name in added:
            Console().print(f"  [dim]Created {name}[/dim]")
    return added


def _copy_traversable_dir(src_dir, dest_dir: Path) -> None:
    """Copy a traversable directory tree to dest_dir."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    for item in src_dir.iterdir():
        target = dest_dir / item.name
        if item.is_dir():
            _copy_traversable_dir(item, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item.read_bytes())


def load_init_pack(pack_name: str = "default") -> dict[str, Any]:
    """Load init pack manifest from bundled init templates."""
    manifest = pkg_files("xbot") / "init_templates" / "packs" / f"{pack_name}.json"
    if not manifest.is_file():
        raise FileNotFoundError(f"Init pack not found: {pack_name}")
    return json.loads(manifest.read_text(encoding="utf-8"))


def sync_workspace_skill_pack(workspace: Path, pack_name: str = "default") -> list[str]:
    """Install skills from pack into workspace/skills (create-if-missing only)."""
    pack = load_init_pack(pack_name)
    names = pack.get("skills", [])
    if not isinstance(names, list):
        return []

    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    init_skills = pkg_files("xbot") / "init_templates" / "skills"
    builtin_skills = pkg_files("xbot") / "skills"

    added: list[str] = []
    for name in names:
        if not isinstance(name, str) or not name:
            continue
        src = init_skills / name
        if not src.is_dir():
            src = builtin_skills / name
        if not src.is_dir():
            continue
        dest = skills_dir / name
        if dest.exists():
            continue
        _copy_traversable_dir(src, dest)
        added.append(str(dest.relative_to(workspace)))
    return added


def sync_workspace_command_pack(workspace: Path, pack_name: str = "default") -> list[str]:
    """Install command templates from pack into workspace/commands."""
    pack = load_init_pack(pack_name)
    names = pack.get("commands", [])
    if not isinstance(names, list):
        return []

    commands_dir = workspace / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
    init_commands = pkg_files("xbot") / "init_templates" / "commands"

    added: list[str] = []
    for name in names:
        if not isinstance(name, str) or not name:
            continue
        src = init_commands / name
        if not src.is_dir() and not src.is_file():
            md_src = init_commands / f"{name}.md"
            src = md_src if md_src.is_file() else src
        if not src.is_dir() and not src.is_file():
            continue
        if src.is_dir():
            dest = commands_dir / name
            if dest.exists():
                continue
            _copy_traversable_dir(src, dest)
            added.append(str(dest.relative_to(workspace)))
            continue
        dest = commands_dir / src.name
        if dest.exists():
            continue
        dest.write_bytes(src.read_bytes())
        added.append(str(dest.relative_to(workspace)))
    return added
