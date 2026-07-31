"""Property-based tests for config save_config atomicity.

Tests that save_config always produces valid JSON regardless of the
config content, and that concurrent saves never corrupt the file.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from xbot.platform.config.loader import save_config, load_config
from xbot.platform.config.schema import Config


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_PORTS = st.integers(min_value=1024, max_value=65535)


# ---------------------------------------------------------------------------
# Property: save_config always produces valid JSON
# ---------------------------------------------------------------------------


class TestSaveConfigAlwaysValid:
    """save_config must always produce valid JSON, never partial content."""

    @given(port=_PORTS)
    @settings(max_examples=50, deadline=None)
    def test_single_save_always_valid(self, port: int):
        """A single save always produces parseable JSON."""
        tmp_dir = Path(tempfile.mkdtemp())
        try:
            config_path = tmp_dir / "config.json"
            config = Config()
            config.gateway.port = port
            save_config(config, config_path)

            content = config_path.read_text(encoding="utf-8")
            data = json.loads(content)
            assert isinstance(data, dict)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @given(num_writers=st.integers(min_value=2, max_value=20))
    @settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_concurrent_saves_never_corrupt(self, num_writers: int):
        """Multiple concurrent saves must leave valid JSON (last-writer-wins)."""
        tmp_dir = Path(tempfile.mkdtemp())
        try:
            config_path = tmp_dir / "config.json"
            barrier = threading.Barrier(num_writers)

            def writer(idx: int):
                cfg = Config()
                cfg.gateway.port = 10000 + idx
                barrier.wait(timeout=5)
                save_config(cfg, config_path)

            with ThreadPoolExecutor(max_workers=num_writers) as pool:
                futures = [pool.submit(writer, i) for i in range(num_writers)]
                for f in as_completed(futures):
                    f.result()

            # Final file must be valid JSON with a valid port
            content = config_path.read_text(encoding="utf-8")
            data = json.loads(content)
            assert isinstance(data, dict)
            gateway = data.get("gateway", {})
            assert isinstance(gateway.get("port"), int)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @given(num_writers=st.integers(min_value=5, max_value=15))
    @settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_no_temp_file_leaks(self, num_writers: int):
        """After concurrent saves complete, no .tmp files remain."""
        tmp_dir = Path(tempfile.mkdtemp())
        try:
            config_path = tmp_dir / "config.json"
            barrier = threading.Barrier(num_writers)

            def writer(idx: int):
                cfg = Config()
                cfg.gateway.port = 10000 + idx
                barrier.wait(timeout=5)
                save_config(cfg, config_path)

            with ThreadPoolExecutor(max_workers=num_writers) as pool:
                futures = [pool.submit(writer, i) for i in range(num_writers)]
                for f in as_completed(futures):
                    f.result()

            tmp_files = list(tmp_dir.glob("*.tmp"))
            assert tmp_files == [], f"Leaked temp files: {tmp_files}"
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Property: permissions always 0o600
# ---------------------------------------------------------------------------


class TestSaveConfigPermissions:
    """File permissions must always be 0o600 after save."""

    @given(port=_PORTS)
    @settings(max_examples=20, deadline=None)
    def test_permissions_always_restrictive(self, port: int):
        tmp_dir = Path(tempfile.mkdtemp())
        try:
            config_path = tmp_dir / "config.json"
            cfg = Config()
            cfg.gateway.port = port
            save_config(cfg, config_path)

            mode = os.stat(config_path).st_mode & 0o777
            assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Property: load(save(x)) round-trips key fields
# ---------------------------------------------------------------------------


class TestSaveLoadRoundTrip:
    """Saving and loading config must preserve key fields."""

    @given(port=_PORTS)
    @settings(max_examples=30, deadline=None)
    def test_port_round_trips(self, port: int):
        tmp_dir = Path(tempfile.mkdtemp())
        try:
            config_path = tmp_dir / "config.json"
            cfg = Config()
            cfg.gateway.port = port
            save_config(cfg, config_path)

            loaded = load_config(config_path)
            assert loaded.gateway.port == port
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
