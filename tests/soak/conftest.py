"""Shared fixtures & helpers for soak tests."""

from __future__ import annotations

import gc
import tracemalloc
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest


pytestmark = pytest.mark.soak


@dataclass
class ResourceBaseline:
    """Snapshot of tracked resource sizes."""

    label: str
    sizes: dict[str, int]

    def diff(self, other: "ResourceBaseline") -> dict[str, int]:
        return {
            k: other.sizes.get(k, 0) - v
            for k, v in self.sizes.items()
        }


def capture_sizes(obj: Any, attrs: list[str], *, label: str = "") -> ResourceBaseline:
    """Capture the len() of the given attribute names on obj.

    Missing attributes report 0 (rather than error) so tests remain robust
    to refactors that rename internal state — the diff still tells the
    story.
    """
    sizes: dict[str, int] = {}
    for name in attrs:
        value = getattr(obj, name, None)
        if value is None:
            sizes[name] = 0
            continue
        try:
            sizes[name] = len(value)
        except TypeError:
            sizes[name] = 0
    return ResourceBaseline(label=label, sizes=sizes)


def assert_bounded_growth(
    before: ResourceBaseline,
    after: ResourceBaseline,
    *,
    tolerance: dict[str, int] | None = None,
    default_tolerance: int = 0,
) -> None:
    """Assert every attribute grew by <= tolerance.

    Tolerance defaults to 0 (must return to baseline). Override per-attr
    when the attribute is expected to grow (e.g. tombstone buffer, LRU
    caches).
    """
    tolerance = tolerance or {}
    growth = before.diff(after)
    failures: list[str] = []
    for name, delta in growth.items():
        cap = tolerance.get(name, default_tolerance)
        if delta > cap:
            failures.append(
                f"  {name}: grew by {delta} (baseline={before.sizes[name]}, "
                f"final={after.sizes.get(name, 0)}, allowed=+{cap})"
            )
    if failures:
        raise AssertionError(
            "Resource growth exceeded tolerance:\n" + "\n".join(failures)
        )


class MemTracker:
    """Lightweight tracemalloc snapshot helper.

    Usage:
        with MemTracker() as tracker:
            ...work...
            tracker.mark("phase1")
            ...more work...
        tracker.assert_bounded_growth(kb=500)
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, tracemalloc.Snapshot] = {}
        self._own_start = False

    def __enter__(self) -> "MemTracker":
        if not tracemalloc.is_tracing():
            tracemalloc.start()
            self._own_start = True
        gc.collect()
        self._snapshots["start"] = tracemalloc.take_snapshot()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        gc.collect()
        self._snapshots["end"] = tracemalloc.take_snapshot()
        if self._own_start:
            tracemalloc.stop()

    def mark(self, label: str) -> None:
        gc.collect()
        self._snapshots[label] = tracemalloc.take_snapshot()

    def growth_kb(self, start: str = "start", end: str = "end") -> float:
        """Total allocated-bytes growth between two snapshots, in KB."""
        s0 = self._snapshots[start]
        s1 = self._snapshots[end]
        diff = s1.compare_to(s0, "filename")
        total = sum(stat.size_diff for stat in diff)
        return total / 1024.0

    def top_growers(self, limit: int = 5, start: str = "start", end: str = "end") -> str:
        s0 = self._snapshots[start]
        s1 = self._snapshots[end]
        diff = s1.compare_to(s0, "lineno")
        lines = [f"top {limit} growers (start->{end}):"]
        for stat in diff[:limit]:
            lines.append(f"  +{stat.size_diff/1024:.1f}KB  {stat}")
        return "\n".join(lines)


@pytest.fixture
def mem_tracker() -> Iterator[MemTracker]:
    with MemTracker() as t:
        yield t
