"""Tests for image support: CLI parsing and Backend multimodal query."""

from __future__ import annotations

import asyncio
import base64
import struct
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# CLI: _parse_media_from_input
# ---------------------------------------------------------------------------


class TestParseMediaFromInput:
    """Tests for xbot.cli.commands._parse_media_from_input."""

    @staticmethod
    def _parse(text: str) -> tuple[str, list[str]]:
        from xbot.cli.commands import _parse_media_from_input
        return _parse_media_from_input(text)

    def test_no_media_reference(self):
        clean, paths = self._parse("hello world")
        assert clean == "hello world"
        assert paths == []

    def test_single_image_existing_file(self, tmp_path: Path):
        img = tmp_path / "photo.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        clean, paths = self._parse(f"@{img} describe this")
        assert "describe this" in clean
        assert len(paths) == 1
        assert paths[0] == str(img)

    def test_multiple_images(self, tmp_path: Path):
        a = tmp_path / "a.png"
        b = tmp_path / "b.jpg"
        a.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        b.write_bytes(b"\xff\xd8\xff" + b"\x00" * 16)
        clean, paths = self._parse(f"@{a} @{b} compare")
        assert "compare" in clean
        assert len(paths) == 2

    def test_nonexistent_file_kept_in_text(self):
        clean, paths = self._parse("@/nonexistent/photo.png test")
        assert "@/nonexistent/photo.png" in clean
        assert paths == []

    def test_image_only_default_prompt(self, tmp_path: Path):
        img = tmp_path / "x.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 16)
        clean, paths = self._parse(f"@{img}")
        assert clean  # Should have default text
        assert len(paths) == 1

    def test_quoted_path(self, tmp_path: Path):
        img = tmp_path / "photo.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        clean, paths = self._parse(f"@'{img}' describe")
        assert len(paths) == 1
        assert paths[0] == str(img)

    def test_non_image_extension_now_matched(self, tmp_path: Path):
        """With universal @path, text files are now matched too."""
        txt = tmp_path / "notes.txt"
        txt.write_text("hello")
        clean, paths = self._parse(f"@{txt} read this")
        assert len(paths) == 1
        assert paths[0] == str(txt)

    def test_email_not_matched(self):
        """Email addresses should not be matched by @path regex."""
        clean, paths = self._parse("contact user@example.com for help")
        assert paths == []
        assert "user@example.com" in clean

    def test_python_file_matched(self, tmp_path: Path):
        f = tmp_path / "script.py"
        f.write_text("x = 1")
        clean, paths = self._parse(f"@{f} explain")
        assert len(paths) == 1
        assert paths[0] == str(f)

    def test_case_insensitive_extension(self, tmp_path: Path):
        img = tmp_path / "PHOTO.PNG"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        clean, paths = self._parse(f"@{img} look")
        assert len(paths) == 1

    def test_webp_extension(self, tmp_path: Path):
        img = tmp_path / "pic.webp"
        img.write_bytes(b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 16)
        clean, paths = self._parse(f"@{img} what is this")
        assert len(paths) == 1

    def test_gif_extension(self, tmp_path: Path):
        img = tmp_path / "anim.gif"
        img.write_bytes(b"GIF89a" + b"\x00" * 16)
        clean, paths = self._parse(f"@{img} explain")
        assert len(paths) == 1


# ---------------------------------------------------------------------------
# Backend: _build_image_content_blocks
# ---------------------------------------------------------------------------


def _make_png(width: int = 1, height: int = 1) -> bytes:
    """Create a minimal valid PNG file."""
    # PNG magic + IHDR chunk (simplified but enough for MIME detection)
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


def _make_jpeg() -> bytes:
    """Create minimal JPEG header."""
    return b"\xff\xd8\xff\xe0" + b"\x00" * 100


def _make_webp() -> bytes:
    """Create minimal WebP header."""
    return b"RIFF" + struct.pack("<I", 20) + b"WEBP" + b"\x00" * 12


def _make_gif() -> bytes:
    """Create minimal GIF header."""
    return b"GIF89a" + b"\x00" * 100


class TestBuildImageContentBlocks:
    """Tests for ClaudeSDKBackend._build_image_content_blocks."""

    @staticmethod
    def _get_backend():
        """Create a minimal ClaudeSDKBackend instance for testing."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        return backend

    def test_single_png(self, tmp_path: Path):
        backend = self._get_backend()
        img = tmp_path / "test.png"
        img.write_bytes(_make_png())
        blocks = backend._build_image_content_blocks([str(img)])
        assert len(blocks) == 1
        assert blocks[0]["type"] == "image"
        assert blocks[0]["source"]["type"] == "base64"
        assert blocks[0]["source"]["media_type"] == "image/png"
        # Verify base64 decodes correctly
        decoded = base64.b64decode(blocks[0]["source"]["data"])
        assert decoded == _make_png()

    def test_jpeg(self, tmp_path: Path):
        backend = self._get_backend()
        img = tmp_path / "test.jpg"
        img.write_bytes(_make_jpeg())
        blocks = backend._build_image_content_blocks([str(img)])
        assert len(blocks) == 1
        assert blocks[0]["source"]["media_type"] == "image/jpeg"

    def test_webp(self, tmp_path: Path):
        backend = self._get_backend()
        img = tmp_path / "test.webp"
        img.write_bytes(_make_webp())
        blocks = backend._build_image_content_blocks([str(img)])
        assert len(blocks) == 1
        assert blocks[0]["source"]["media_type"] == "image/webp"

    def test_gif(self, tmp_path: Path):
        backend = self._get_backend()
        img = tmp_path / "test.gif"
        img.write_bytes(_make_gif())
        blocks = backend._build_image_content_blocks([str(img)])
        assert len(blocks) == 1
        assert blocks[0]["source"]["media_type"] == "image/gif"

    def test_nonexistent_file_skipped(self):
        backend = self._get_backend()
        blocks = backend._build_image_content_blocks(["/nonexistent/file.png"])
        assert blocks == []

    def test_oversized_file_skipped(self, tmp_path: Path):
        backend = self._get_backend()
        img = tmp_path / "huge.png"
        # Write a file just over 20 MB
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (20 * 1024 * 1024 + 1))
        blocks = backend._build_image_content_blocks([str(img)])
        assert blocks == []

    def test_unsupported_format_skipped(self, tmp_path: Path):
        backend = self._get_backend()
        txt = tmp_path / "readme.txt"
        txt.write_text("not an image")
        blocks = backend._build_image_content_blocks([str(txt)])
        assert blocks == []

    def test_multiple_images(self, tmp_path: Path):
        backend = self._get_backend()
        png = tmp_path / "a.png"
        jpg = tmp_path / "b.jpg"
        png.write_bytes(_make_png())
        jpg.write_bytes(_make_jpeg())
        blocks = backend._build_image_content_blocks([str(png), str(jpg)])
        assert len(blocks) == 2
        assert blocks[0]["source"]["media_type"] == "image/png"
        assert blocks[1]["source"]["media_type"] == "image/jpeg"

    def test_mixed_valid_and_invalid(self, tmp_path: Path):
        backend = self._get_backend()
        good = tmp_path / "good.png"
        good.write_bytes(_make_png())
        blocks = backend._build_image_content_blocks([
            str(good),
            "/nonexistent/bad.png",
        ])
        assert len(blocks) == 1

    def test_empty_list(self):
        backend = self._get_backend()
        blocks = backend._build_image_content_blocks([])
        assert blocks == []


# ---------------------------------------------------------------------------
# Backend: _build_multimodal_query
# ---------------------------------------------------------------------------


class TestBuildMultimodalQuery:
    """Tests for ClaudeSDKBackend._build_multimodal_query."""

    @staticmethod
    def _get_backend():
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        return backend

    @staticmethod
    async def _collect(aiter: AsyncIterator) -> list:
        result = []
        async for item in aiter:
            result.append(item)
        return result

    @pytest.mark.asyncio
    async def test_with_valid_images(self, tmp_path: Path):
        backend = self._get_backend()
        img = tmp_path / "test.png"
        img.write_bytes(_make_png())

        messages = await self._collect(
            backend._build_multimodal_query("describe", [str(img)], "sess-1")
        )
        assert len(messages) == 1
        msg = messages[0]
        assert msg["type"] == "user"
        assert msg["session_id"] == "sess-1"
        assert msg["parent_tool_use_id"] is None

        content = msg["message"]["content"]
        assert isinstance(content, list)
        # image block + text block
        assert len(content) == 2
        assert content[0]["type"] == "image"
        assert content[1]["type"] == "text"
        assert content[1]["text"] == "describe"

    @pytest.mark.asyncio
    async def test_nonexistent_image_becomes_file_reference(self):
        """Nonexistent .png is classified as FILE (can't check magic bytes),
        so it produces a file reference instead of falling back to text."""
        backend = self._get_backend()
        messages = await self._collect(
            backend._build_multimodal_query("hello", ["/nonexistent.png"], "sess-2")
        )
        assert len(messages) == 1
        content = messages[0]["message"]["content"]
        assert isinstance(content, list)
        # Should have file ref block + prompt block
        assert any("nonexistent.png" in b.get("text", "") for b in content)
        assert content[-1]["text"] == "hello"

    @pytest.mark.asyncio
    async def test_multiple_images(self, tmp_path: Path):
        backend = self._get_backend()
        a = tmp_path / "a.png"
        b = tmp_path / "b.jpg"
        a.write_bytes(_make_png())
        b.write_bytes(_make_jpeg())

        messages = await self._collect(
            backend._build_multimodal_query("compare", [str(a), str(b)], "sess-3")
        )
        content = messages[0]["message"]["content"]
        assert isinstance(content, list)
        assert len(content) == 3  # 2 images + 1 text
        assert content[0]["type"] == "image"
        assert content[1]["type"] == "image"
        assert content[2]["type"] == "text"


# ---------------------------------------------------------------------------
# Integration: process_direct with media parameter
# ---------------------------------------------------------------------------


class TestProcessDirectMedia:
    """Verify process_direct passes media to InboundMessage."""

    @pytest.mark.asyncio
    async def test_process_direct_passes_media(self):
        """process_direct should include media in InboundMessage."""
        from xbot.agent.runtime import AgentRuntime
        from xbot.bus.events import InboundMessage

        captured_msg = None

        async def mock_handle(msg, on_progress=None):
            nonlocal captured_msg
            captured_msg = msg
            # Return a minimal OutboundMessage-like object
            result = MagicMock()
            result.content = "ok"
            return result

        runtime = AgentRuntime.__new__(AgentRuntime)
        runtime._is_local_runtime_command = lambda c: False
        runtime.initialize = AsyncMock()
        runtime._handle_message = mock_handle

        result = await runtime.process_direct(
            content="describe",
            media=["/path/to/img.png"],
        )

        assert captured_msg is not None
        assert captured_msg.media == ["/path/to/img.png"]
        assert captured_msg.content == "describe"
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_process_direct_no_media_default(self):
        """process_direct without media should default to empty list."""
        from xbot.agent.runtime import AgentRuntime
        from xbot.bus.events import InboundMessage

        captured_msg = None

        async def mock_handle(msg, on_progress=None):
            nonlocal captured_msg
            captured_msg = msg
            result = MagicMock()
            result.content = "ok"
            return result

        runtime = AgentRuntime.__new__(AgentRuntime)
        runtime._is_local_runtime_command = lambda c: False
        runtime.initialize = AsyncMock()
        runtime._handle_message = mock_handle

        await runtime.process_direct(content="hello")
        assert captured_msg is not None
        assert captured_msg.media == []
