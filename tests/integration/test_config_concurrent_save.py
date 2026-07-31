"""Integration tests: config save_config atomicity under concurrency.

These tests verify that the atomic write pattern in ``save_config``
(NamedTemporaryFile → fsync → os.replace) behaves correctly when multiple
concurrent calls attempt to save different config states simultaneously.

Focus areas:
- Concurrent saves produce a valid JSON config (no partial writes / corruption).
- Last writer wins semantics via os.replace atomicity.
- Temp file cleanup on failure (no leaked .tmp files on disk).
- Concurrent reads during writes always see a complete config (never partial).
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from xbot.platform.config.loader import save_config
from xbot.platform.config.schema import Config

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> Config:
    """Create a minimal valid Config for testing."""
    return Config(**overrides)


def _read_config_json(path: Path) -> dict:
    """Read and parse the config file. Raises on invalid JSON."""
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Test: concurrent saves don't corrupt the file
# ---------------------------------------------------------------------------


class TestConcurrentSaveAtomicity:
    """Verify that rapid concurrent save_config calls never produce a
    corrupted (non-parseable or truncated) config file."""

    def test_parallel_saves_always_produce_valid_json(self, tmp_path: Path):
        """Launch many threads saving different config variants concurrently.
        After all complete, the file must be valid JSON."""
        config_path = tmp_path / "config.json"
        num_writers = 20

        configs = []
        for i in range(num_writers):
            cfg = _make_config()
            cfg.gateway.port = 9000 + i  # Each writer saves a different port
            configs.append(cfg)

        def writer(cfg: Config) -> None:
            save_config(cfg, config_path)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(writer, cfg) for cfg in configs]
            for f in futures:
                f.result()  # Propagate exceptions

        # File MUST be valid JSON
        data = _read_config_json(config_path)
        assert "gateway" in data
        # Port should be one of the values we wrote (last-writer-wins)
        port = data["gateway"]["port"]
        assert 9000 <= port < 9000 + num_writers

    def test_no_temp_files_left_after_concurrent_saves(self, tmp_path: Path):
        """After concurrent saves complete, no .tmp files should remain."""
        config_path = tmp_path / "config.json"
        num_writers = 15

        def writer(i: int) -> None:
            cfg = _make_config()
            cfg.gateway.port = 8000 + i
            save_config(cfg, config_path)

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(writer, i) for i in range(num_writers)]
            for f in futures:
                f.result()

        # No temp files should remain
        tmp_files = list(tmp_path.glob(".config.json.*.tmp"))
        assert tmp_files == [], f"Leaked temp files: {tmp_files}"

    def test_readers_never_see_partial_content(self, tmp_path: Path):
        """While writers are saving, concurrent readers should always
        see a complete, parseable JSON file (or FileNotFoundError before
        the first write)."""
        config_path = tmp_path / "config.json"
        num_writers = 20
        num_readers = 5
        read_errors: list[str] = []
        stop_event = threading.Event()

        def writer(i: int) -> None:
            cfg = _make_config()
            cfg.gateway.port = 7000 + i
            save_config(cfg, config_path)

        def reader() -> None:
            while not stop_event.is_set():
                try:
                    content = config_path.read_text(encoding="utf-8")
                    parsed = json.loads(content)
                    # Basic structural check
                    if "gateway" not in parsed:
                        read_errors.append(f"Missing 'gateway' key: {content[:100]}")
                except FileNotFoundError:
                    pass  # File not yet created — acceptable
                except json.JSONDecodeError as e:
                    read_errors.append(f"JSON decode error: {e}")
                except Exception as e:
                    read_errors.append(f"Unexpected error: {e}")

        # Start reader threads (separate from writer pool to avoid deadlock)
        reader_threads = []
        for _ in range(num_readers):
            t = threading.Thread(target=reader, daemon=True)
            t.start()
            reader_threads.append(t)

        # Run writers in a pool
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(writer, i) for i in range(num_writers)]
            for f in futures:
                f.result()

        # Stop readers
        stop_event.set()
        for t in reader_threads:
            t.join(timeout=2.0)

        assert read_errors == [], f"Readers saw corrupt state: {read_errors[:5]}"


# ---------------------------------------------------------------------------
# Test: temp file cleanup on write failure
# ---------------------------------------------------------------------------


class TestTempFileCleanupOnFailure:
    """Verify that if the write process fails mid-way, the temp file is
    cleaned up and the original config (if any) remains intact."""

    def test_failed_write_preserves_original(self, tmp_path: Path, monkeypatch):
        """If os.replace fails, the original file should remain untouched."""
        config_path = tmp_path / "config.json"

        # Write an initial config
        initial_cfg = _make_config()
        initial_cfg.gateway.port = 5555
        save_config(initial_cfg, config_path)
        original_content = config_path.read_text(encoding="utf-8")

        # Make os.replace raise to simulate a failure after writing the temp file
        original_replace = os.replace

        def failing_replace(src, dst):
            raise OSError("Simulated disk failure on replace")

        monkeypatch.setattr(os, "replace", failing_replace)

        new_cfg = _make_config()
        new_cfg.gateway.port = 6666

        with pytest.raises(OSError, match="Simulated disk failure"):
            save_config(new_cfg, config_path)

        # Original file should be unchanged
        assert config_path.read_text(encoding="utf-8") == original_content

        # No temp files should remain
        tmp_files = list(tmp_path.glob(".config.json.*.tmp"))
        assert tmp_files == [], f"Leaked temp files: {tmp_files}"

    def test_failed_write_no_original_leaves_no_file(self, tmp_path: Path, monkeypatch):
        """If the first-ever save fails, no config file should exist at all."""
        config_path = tmp_path / "config.json"

        original_replace = os.replace

        def failing_replace(src, dst):
            raise OSError("Simulated disk failure")

        monkeypatch.setattr(os, "replace", failing_replace)

        cfg = _make_config()
        with pytest.raises(OSError, match="Simulated disk failure"):
            save_config(cfg, config_path)

        assert not config_path.exists()
        tmp_files = list(tmp_path.glob(".config.json.*.tmp"))
        assert tmp_files == [], f"Leaked temp files: {tmp_files}"


# ---------------------------------------------------------------------------
# Test: save_config idempotency
# ---------------------------------------------------------------------------


class TestSaveIdempotency:
    """Saving the same config twice should produce identical file content."""

    def test_double_save_produces_identical_content(self, tmp_path: Path):
        config_path = tmp_path / "config.json"

        cfg = _make_config()
        cfg.gateway.port = 4444

        save_config(cfg, config_path)
        content_a = config_path.read_text(encoding="utf-8")

        save_config(cfg, config_path)
        content_b = config_path.read_text(encoding="utf-8")

        assert content_a == content_b

    def test_file_permissions_set_to_600(self, tmp_path: Path):
        """Config file should have restrictive permissions (0o600)."""
        config_path = tmp_path / "config.json"
        cfg = _make_config()
        save_config(cfg, config_path)

        mode = config_path.stat().st_mode & 0o777
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"
