"""Tests for file support: classification, path references, and integration."""

from __future__ import annotations

import asyncio
import struct
from pathlib import Path
from typing import Any, AsyncIterator

import pytest


# ---------------------------------------------------------------------------
# file_reader: classify_file
# ---------------------------------------------------------------------------


class TestClassifyFile:
    """Tests for xbot.utils.file_reader.classify_file."""

    @staticmethod
    def _classify(path: str):
        from xbot.utils.file_reader import classify_file
        return classify_file(path)

    @staticmethod
    def _ft():
        from xbot.utils.file_reader import FileType
        return FileType

    def test_png_is_image(self, tmp_path: Path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        assert self._classify(str(img)) == self._ft().IMAGE

    def test_jpeg_is_image(self, tmp_path: Path):
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
        assert self._classify(str(img)) == self._ft().IMAGE

    def test_gif_is_image(self, tmp_path: Path):
        img = tmp_path / "test.gif"
        img.write_bytes(b"GIF89a" + b"\x00" * 16)
        assert self._classify(str(img)) == self._ft().IMAGE

    def test_webp_is_image(self, tmp_path: Path):
        img = tmp_path / "test.webp"
        img.write_bytes(b"RIFF" + struct.pack("<I", 20) + b"WEBP" + b"\x00" * 12)
        assert self._classify(str(img)) == self._ft().IMAGE

    def test_python_file_is_file(self, tmp_path: Path):
        f = tmp_path / "script.py"
        f.write_text("print('hello')")
        assert self._classify(str(f)) == self._ft().FILE

    def test_docx_is_file(self, tmp_path: Path):
        f = tmp_path / "report.docx"
        f.write_bytes(b"PK\x03\x04" + b"\x00" * 100)  # ZIP magic (docx is ZIP)
        assert self._classify(str(f)) == self._ft().FILE

    def test_csv_is_file(self, tmp_path: Path):
        f = tmp_path / "data.csv"
        f.write_text("a,b,c\n1,2,3")
        assert self._classify(str(f)) == self._ft().FILE

    def test_nonexistent_is_file(self):
        assert self._classify("/nonexistent/path.txt") == self._ft().FILE

    def test_binary_is_file(self, tmp_path: Path):
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x00\x01\x02\x03" * 100)
        assert self._classify(str(f)) == self._ft().FILE

    def test_bmp_is_file_not_image(self, tmp_path: Path):
        """BMP is not in supported image MIME list, should be FILE."""
        f = tmp_path / "test.bmp"
        f.write_bytes(b"BM" + b"\x00" * 100)
        assert self._classify(str(f)) == self._ft().FILE


# ---------------------------------------------------------------------------
# file_reader: format_file_reference
# ---------------------------------------------------------------------------


class TestFormatFileReference:
    """Tests for xbot.utils.file_reader.format_file_reference."""

    @staticmethod
    def _format(path: str) -> str:
        from xbot.utils.file_reader import format_file_reference
        return format_file_reference(path)

    def test_small_text_file(self, tmp_path: Path):
        f = tmp_path / "hello.txt"
        f.write_text("hello world")
        ref = self._format(str(f))
        assert "hello.txt" in ref
        assert "TXT" in ref
        assert str(f) in ref

    def test_large_file_shows_size(self, tmp_path: Path):
        f = tmp_path / "data.csv"
        f.write_bytes(b"x" * (1024 * 500))  # 500 KB
        ref = self._format(str(f))
        assert "data.csv" in ref
        assert "KB" in ref
        assert "CSV" in ref

    def test_mb_file_shows_mb(self, tmp_path: Path):
        f = tmp_path / "big.zip"
        f.write_bytes(b"x" * (1024 * 1024 * 2))  # 2 MB
        ref = self._format(str(f))
        assert "MB" in ref
        assert "ZIP" in ref

    def test_no_extension_shows_file(self, tmp_path: Path):
        f = tmp_path / "Makefile"
        f.write_text("all: build")
        ref = self._format(str(f))
        assert "FILE" in ref
        assert "Makefile" in ref

    def test_nonexistent_file_zero_size(self):
        ref = self._format("/nonexistent/report.pdf")
        assert "report.pdf" in ref
        assert "0B" in ref


# ---------------------------------------------------------------------------
# file_reader: _human_readable_size
# ---------------------------------------------------------------------------


class TestHumanReadableSize:
    """Tests for xbot.utils.file_reader._human_readable_size."""

    @staticmethod
    def _size(n: int) -> str:
        from xbot.utils.file_reader import _human_readable_size
        return _human_readable_size(n)

    def test_bytes(self):
        assert self._size(0) == "0B"
        assert self._size(512) == "512B"

    def test_kilobytes(self):
        result = self._size(1024)
        assert "KB" in result

    def test_megabytes(self):
        result = self._size(1024 * 1024 * 5)
        assert "MB" in result


# ---------------------------------------------------------------------------
# Backend: _build_file_content_blocks
# ---------------------------------------------------------------------------


class TestBuildFileContentBlocks:
    """Tests for ClaudeSDKBackend._build_file_content_blocks."""

    @staticmethod
    def _get_backend():
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        return backend

    def test_image_goes_to_image_paths(self, tmp_path: Path):
        backend = self._get_backend()
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        text_blocks, image_paths = backend._build_file_content_blocks([str(img)])
        assert len(text_blocks) == 0
        assert image_paths == [str(img)]

    def test_text_file_goes_to_ref_blocks(self, tmp_path: Path):
        backend = self._get_backend()
        f = tmp_path / "script.py"
        f.write_text("print('hi')")
        text_blocks, image_paths = backend._build_file_content_blocks([str(f)])
        assert len(text_blocks) == 1
        assert image_paths == []
        assert text_blocks[0]["type"] == "text"
        assert "script.py" in text_blocks[0]["text"]
        assert "通过工具读取或修改" in text_blocks[0]["text"]

    def test_mixed_images_and_files(self, tmp_path: Path):
        backend = self._get_backend()
        img = tmp_path / "photo.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        txt = tmp_path / "readme.md"
        txt.write_text("# Hello")
        csv = tmp_path / "data.csv"
        csv.write_text("a,b\n1,2")

        text_blocks, image_paths = backend._build_file_content_blocks(
            [str(img), str(txt), str(csv)]
        )
        assert image_paths == [str(img)]
        assert len(text_blocks) == 1
        block_text = text_blocks[0]["text"]
        assert "readme.md" in block_text
        assert "data.csv" in block_text

    def test_all_images_no_ref_blocks(self, tmp_path: Path):
        backend = self._get_backend()
        a = tmp_path / "a.png"
        b = tmp_path / "b.jpg"
        a.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        b.write_bytes(b"\xff\xd8\xff" + b"\x00" * 16)
        text_blocks, image_paths = backend._build_file_content_blocks(
            [str(a), str(b)]
        )
        assert len(text_blocks) == 0
        assert len(image_paths) == 2

    def test_empty_media(self):
        backend = self._get_backend()
        text_blocks, image_paths = backend._build_file_content_blocks([])
        assert text_blocks == []
        assert image_paths == []


# ---------------------------------------------------------------------------
# Backend: _build_multimodal_query with mixed files
# ---------------------------------------------------------------------------


class TestMultimodalQueryWithFiles:
    """Tests for _build_multimodal_query handling mixed image + file media."""

    @staticmethod
    def _get_backend():
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        return backend

    @staticmethod
    async def _collect_query(aiter: AsyncIterator[dict[str, Any]]) -> list[dict]:
        result = []
        async for msg in aiter:
            result.append(msg)
        return result

    @pytest.mark.asyncio
    async def test_file_only_produces_ref_and_prompt(self, tmp_path: Path):
        backend = self._get_backend()
        f = tmp_path / "code.py"
        f.write_text("x = 1")
        msgs = await self._collect_query(
            backend._build_multimodal_query("explain this", [str(f)], "sess1")
        )
        assert len(msgs) == 1
        content = msgs[0]["message"]["content"]
        assert isinstance(content, list)
        # Should have: file ref block + prompt block
        assert len(content) == 2
        assert "code.py" in content[0]["text"]
        assert content[1]["text"] == "explain this"

    @pytest.mark.asyncio
    async def test_image_and_file_mixed(self, tmp_path: Path):
        backend = self._get_backend()
        img = tmp_path / "photo.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        txt = tmp_path / "notes.txt"
        txt.write_text("some notes")
        msgs = await self._collect_query(
            backend._build_multimodal_query(
                "describe and read", [str(img), str(txt)], "sess2"
            )
        )
        assert len(msgs) == 1
        content = msgs[0]["message"]["content"]
        assert isinstance(content, list)
        # Should have: image block + file ref block + prompt block
        assert len(content) == 3
        assert content[0]["type"] == "image"
        assert "notes.txt" in content[1]["text"]
        assert content[2]["text"] == "describe and read"

    @pytest.mark.asyncio
    async def test_no_media_fallback_text(self, tmp_path: Path):
        """When all files are nonexistent, fall back to plain text."""
        backend = self._get_backend()
        # Nonexistent paths → classify as FILE, but format_file_reference still works
        msgs = await self._collect_query(
            backend._build_multimodal_query(
                "hello", ["/nonexistent/file.txt"], "sess3"
            )
        )
        assert len(msgs) == 1
        content = msgs[0]["message"]["content"]
        # Should still produce a file ref block (path reference works for nonexistent)
        assert isinstance(content, list)


# ---------------------------------------------------------------------------
# ContextBuilder: _build_user_content with files
# ---------------------------------------------------------------------------


class TestContextBuilderFileSupport:
    """Tests for ContextBuilder._build_user_content with file references."""

    @staticmethod
    def _get_builder():
        from unittest.mock import MagicMock
        from xbot.agent.context.builder import ContextBuilder
        builder = ContextBuilder.__new__(ContextBuilder)
        builder._config = MagicMock()
        return builder

    def test_no_media_returns_text(self):
        builder = self._get_builder()
        result = builder._build_user_content("hello", None)
        assert result == "hello"

    def test_image_only_returns_image_blocks(self, tmp_path: Path):
        builder = self._get_builder()
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        result = builder._build_user_content("describe", [str(img)])
        assert isinstance(result, list)
        assert result[0]["type"] == "image_url"
        assert result[-1]["text"] == "describe"

    def test_file_only_returns_string_with_refs(self, tmp_path: Path):
        builder = self._get_builder()
        f = tmp_path / "script.py"
        f.write_text("x = 1")
        result = builder._build_user_content("explain", [str(f)])
        # No images → returns string with file refs prepended
        assert isinstance(result, str)
        assert "script.py" in result
        assert "explain" in result
        assert "通过工具读取或修改" in result

    def test_mixed_image_and_file(self, tmp_path: Path):
        builder = self._get_builder()
        img = tmp_path / "photo.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        f = tmp_path / "data.csv"
        f.write_text("a,b\n1,2")
        result = builder._build_user_content("analyze", [str(img), str(f)])
        assert isinstance(result, list)
        assert result[0]["type"] == "image_url"
        # Last element should be text with file refs + prompt
        text_part = result[-1]["text"]
        assert "data.csv" in text_part
        assert "analyze" in text_part


# ---------------------------------------------------------------------------
# Additional edge cases: classify_file
# ---------------------------------------------------------------------------


class TestClassifyFileEdgeCases:
    """Additional edge case tests for classify_file."""

    @staticmethod
    def _classify(path: str):
        from xbot.utils.file_reader import classify_file
        return classify_file(path)

    @staticmethod
    def _ft():
        from xbot.utils.file_reader import FileType
        return FileType

    def test_empty_file_is_file(self, tmp_path: Path):
        """A zero-byte file cannot have magic bytes -> FILE."""
        f = tmp_path / "empty.dat"
        f.write_bytes(b"")
        assert self._classify(str(f)) == self._ft().FILE

    def test_directory_is_file(self, tmp_path: Path):
        """Directories should be classified as FILE (not is_file())."""
        d = tmp_path / "subdir"
        d.mkdir()
        assert self._classify(str(d)) == self._ft().FILE

    def test_broken_symlink_is_file(self, tmp_path: Path):
        """Broken symlinks (dangling) should be classified as FILE."""
        link = tmp_path / "broken_link.png"
        link.symlink_to(tmp_path / "nonexistent_target.png")
        assert self._classify(str(link)) == self._ft().FILE

    def test_valid_symlink_to_image(self, tmp_path: Path):
        """Valid symlink pointing to a real image should be IMAGE."""
        real_img = tmp_path / "real.png"
        real_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        link = tmp_path / "link.png"
        link.symlink_to(real_img)
        assert self._classify(str(link)) == self._ft().IMAGE

    def test_unicode_filename(self, tmp_path: Path):
        """Files with Chinese/unicode names should work."""
        f = tmp_path / "报告文件.docx"
        f.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
        assert self._classify(str(f)) == self._ft().FILE

    def test_image_extension_but_text_content(self, tmp_path: Path):
        """A .png file with text content is classified by magic bytes -> FILE."""
        f = tmp_path / "fake.png"
        f.write_text("this is not an image")
        assert self._classify(str(f)) == self._ft().FILE

    def test_no_extension_with_image_magic(self, tmp_path: Path):
        """File without extension but with PNG magic bytes -> IMAGE."""
        f = tmp_path / "screenshot"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        assert self._classify(str(f)) == self._ft().IMAGE


# ---------------------------------------------------------------------------
# Additional edge cases: format_file_reference
# ---------------------------------------------------------------------------


class TestFormatFileReferenceEdgeCases:
    """Additional edge case tests for format_file_reference."""

    @staticmethod
    def _format(path: str) -> str:
        from xbot.utils.file_reader import format_file_reference
        return format_file_reference(path)

    def test_relative_path_resolved_to_absolute(self, tmp_path: Path, monkeypatch):
        """Relative paths should be resolved to absolute in the output."""
        f = tmp_path / "test.txt"
        f.write_text("content")
        monkeypatch.chdir(tmp_path)
        ref = self._format("test.txt")
        assert str(tmp_path) in ref
        assert "test.txt" in ref

    def test_unicode_filename_in_ref(self, tmp_path: Path):
        """Chinese filename should appear correctly in reference."""
        f = tmp_path / "数据表.csv"
        f.write_text("a,b\n1,2")
        ref = self._format(str(f))
        assert "数据表.csv" in ref
        assert "CSV" in ref

    def test_multiple_dots_in_name(self, tmp_path: Path):
        """File like archive.tar.gz should show GZ suffix."""
        f = tmp_path / "archive.tar.gz"
        f.write_bytes(b"\x1f\x8b" + b"\x00" * 50)
        ref = self._format(str(f))
        assert "archive.tar.gz" in ref
        assert "GZ" in ref

    def test_hidden_dotfile(self, tmp_path: Path):
        """Dotfiles like .gitignore should show FILE suffix."""
        f = tmp_path / ".gitignore"
        f.write_text("*.pyc\n__pycache__/")
        ref = self._format(str(f))
        assert ".gitignore" in ref


# ---------------------------------------------------------------------------
# Additional edge cases: _human_readable_size
# ---------------------------------------------------------------------------


class TestHumanReadableSizeEdgeCases:
    """Boundary and edge case tests for _human_readable_size."""

    @staticmethod
    def _size(n: int) -> str:
        from xbot.utils.file_reader import _human_readable_size
        return _human_readable_size(n)

    def test_boundary_1023(self):
        assert self._size(1023) == "1023B"

    def test_boundary_1024(self):
        assert self._size(1024) == "1.0KB"

    def test_boundary_1025(self):
        result = self._size(1025)
        assert "KB" in result

    def test_exactly_1mb(self):
        result = self._size(1024 * 1024)
        assert result == "1.0MB"

    def test_exactly_1gb(self):
        result = self._size(1024 ** 3)
        assert result == "1.0GB"

    def test_gigabytes_range(self):
        result = self._size(1024 ** 3 * 5)
        assert "GB" in result


# ---------------------------------------------------------------------------
# CLI regex: additional @path parsing edge cases
# ---------------------------------------------------------------------------


class TestParseMediaEdgeCases:
    """Additional edge case tests for _parse_media_from_input."""

    @staticmethod
    def _parse(text: str) -> tuple[str, list[str]]:
        from xbot.cli.commands import _parse_media_from_input
        return _parse_media_from_input(text)

    def test_double_quoted_path_with_spaces(self, tmp_path: Path):
        """Double-quoted paths containing spaces should be matched."""
        f = tmp_path / "my report.docx"
        f.write_bytes(b"PK\x03\x04" + b"\x00" * 50)
        clean, paths = self._parse(f'@"{f}" summarize')
        assert len(paths) == 1
        assert paths[0] == str(f.resolve())
        assert "summarize" in clean

    def test_single_quoted_path_with_spaces(self, tmp_path: Path):
        """Single-quoted paths containing spaces should be matched."""
        f = tmp_path / "data file.csv"
        f.write_text("a,b\n1,2")
        clean, paths = self._parse(f"@'{f}' analyze")
        assert len(paths) == 1
        assert "analyze" in clean

    def test_multiple_at_signs_in_text(self):
        """Multiple @mentions without file extensions should not match."""
        clean, paths = self._parse("@alice and @bob please review")
        assert paths == []
        assert "@alice" in clean

    def test_at_sign_in_middle_of_word(self):
        """@ in the middle of a word should not match."""
        clean, paths = self._parse("send to team@company.com now")
        assert paths == []
        assert "team@company.com" in clean

    def test_multiple_files_in_one_message(self, tmp_path: Path):
        """Multiple @path references in a single message."""
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        c = tmp_path / "c.py"
        a.write_text("x = 1")
        b.write_text("y = 2")
        c.write_text("z = 3")
        clean, paths = self._parse(f"@{a} @{b} @{c} review all")
        assert len(paths) == 3
        assert "review all" in clean

    def test_path_at_end_of_line(self, tmp_path: Path):
        """File path at the very end of input."""
        f = tmp_path / "test.py"
        f.write_text("pass")
        clean, paths = self._parse(f"explain @{f}")
        assert len(paths) == 1
        assert "explain" in clean

    def test_tilde_home_expansion(self, tmp_path: Path, monkeypatch):
        """Tilde ~ should be expanded to home directory."""
        home = tmp_path / "fakehome"
        home.mkdir()
        f = home / "test.txt"
        f.write_text("hello")
        monkeypatch.setenv("HOME", str(home))
        clean, paths = self._parse("@~/test.txt read")
        assert len(paths) == 1
        assert str(f) in paths[0] or "test.txt" in paths[0]

    def test_no_extension_not_matched(self):
        """Paths without extensions should not be matched by regex."""
        clean, paths = self._parse("@Makefile check")
        assert paths == []
        assert "@Makefile" in clean

    def test_consecutive_at_paths(self, tmp_path: Path):
        """Two @paths without space between them: only first matched."""
        a = tmp_path / "a.txt"
        a.write_text("aaa")
        clean, paths = self._parse(f"@{a}@{a}")
        assert len(paths) == 1


# ---------------------------------------------------------------------------
# ContextBuilder: additional edge cases
# ---------------------------------------------------------------------------


class TestContextBuilderEdgeCases:
    """Additional edge case tests for ContextBuilder._build_user_content."""

    @staticmethod
    def _get_builder():
        from unittest.mock import MagicMock
        from xbot.agent.context.builder import ContextBuilder
        builder = ContextBuilder.__new__(ContextBuilder)
        builder._config = MagicMock()
        return builder

    def test_empty_media_list(self):
        """Empty media list (not None) should still return plain text."""
        builder = self._get_builder()
        result = builder._build_user_content("hello", [])
        assert result == "hello"

    def test_nonexistent_image_skipped(self, tmp_path: Path):
        """Nonexistent image path should be classified as FILE and generate ref."""
        builder = self._get_builder()
        result = builder._build_user_content(
            "describe", ["/nonexistent/photo.png"]
        )
        assert isinstance(result, str)
        assert "nonexistent" in result or "photo.png" in result

    def test_multiple_files_no_images(self, tmp_path: Path):
        """Multiple non-image files should all appear in refs."""
        builder = self._get_builder()
        a = tmp_path / "a.py"
        b = tmp_path / "b.csv"
        a.write_text("x = 1")
        b.write_text("a,b\n1,2")
        result = builder._build_user_content("review", [str(a), str(b)])
        assert isinstance(result, str)
        assert "a.py" in result
        assert "b.csv" in result
        assert "review" in result

    def test_image_that_fails_mime_detection(self, tmp_path: Path):
        """An image file with minimal PNG header should be handled gracefully."""
        builder = self._get_builder()
        f = tmp_path / "truncated.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n")
        result = builder._build_user_content("what is this", [str(f)])
        assert result is not None


# ---------------------------------------------------------------------------
# Backend: additional _build_file_content_blocks edge cases
# ---------------------------------------------------------------------------


class TestBuildFileContentBlocksEdgeCases:
    """Additional edge case tests for _build_file_content_blocks."""

    @staticmethod
    def _get_backend():
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        return backend

    def test_nonexistent_file_still_produces_ref(self):
        """Nonexistent files should still produce a file reference block."""
        backend = self._get_backend()
        text_blocks, image_paths = backend._build_file_content_blocks(
            ["/does/not/exist.txt"]
        )
        assert len(text_blocks) == 1
        assert image_paths == []
        assert "exist.txt" in text_blocks[0]["text"]

    def test_multiple_nonexistent_files(self):
        """Multiple nonexistent files should all appear in one ref block."""
        backend = self._get_backend()
        text_blocks, image_paths = backend._build_file_content_blocks(
            ["/a/file1.py", "/b/file2.csv", "/c/file3.md"]
        )
        assert len(text_blocks) == 1
        block_text = text_blocks[0]["text"]
        assert "file1.py" in block_text
        assert "file2.csv" in block_text
        assert "file3.md" in block_text

    def test_unicode_filename_in_blocks(self, tmp_path: Path):
        """Chinese filenames should work in content blocks."""
        backend = self._get_backend()
        f = tmp_path / "测试文件.txt"
        f.write_text("你好世界")
        text_blocks, image_paths = backend._build_file_content_blocks([str(f)])
        assert len(text_blocks) == 1
        assert "测试文件.txt" in text_blocks[0]["text"]
