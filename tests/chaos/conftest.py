"""Shared chaos test infrastructure.

Provides:
- deterministic RNG per test (`chaos_rng` fixture, seeded)
- injectable delay/exception decorators for async fault injection
- a `Chaos` object that records what it did so tests can log the actual
  fault sequence when they fail (essential for reproduction).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import pytest

pytestmark = pytest.mark.chaos


@dataclass
class Chaos:
    """Deterministic chaos injector.

    Every random decision goes through this object's `rng`, and every
    decision is logged.  On a failing test, `chaos.trace` is the whole
    story: which calls were delayed, which raised, which were cancelled.
    """

    seed: int
    rng: random.Random = field(init=False)
    trace: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)

    # ---- primitive fault decisions ------------------------------------

    def maybe_delay_ms(self, prob: float, min_ms: int, max_ms: int, *, tag: str = "") -> int:
        """Return a delay in ms with probability `prob`, else 0."""
        if self.rng.random() < prob:
            ms = self.rng.randint(min_ms, max_ms)
            self.trace.append(f"delay:{tag}:{ms}ms")
            return ms
        return 0

    def maybe_raise(self, prob: float, exc: type[BaseException], *, tag: str = "") -> None:
        if self.rng.random() < prob:
            self.trace.append(f"raise:{tag}:{exc.__name__}")
            raise exc(f"chaos-injected:{tag}")

    def choose(self, options: list[str]) -> str:
        return self.rng.choice(options)

    def coin(self, prob: float) -> bool:
        return self.rng.random() < prob


@pytest.fixture
def chaos_rng() -> Iterator[Chaos]:
    """Seeded Chaos instance.  Override CHAOS_SEED env for repro."""
    import os

    seed = int(os.environ.get("CHAOS_SEED", "20260730"))
    yield Chaos(seed=seed)


# --------------------------------------------------------------------- #
# Adversarial task helpers
# --------------------------------------------------------------------- #


async def cancel_after(task: asyncio.Task, delay_s: float) -> None:
    """Cancel `task` after `delay_s`.  Swallow CancelledError from own scope."""
    try:
        await asyncio.sleep(delay_s)
    except asyncio.CancelledError:
        return
    if not task.done():
        task.cancel()


async def gather_swallow(*coros: Awaitable[Any]) -> list[Any]:
    """asyncio.gather with return_exceptions=True — for chaos scenarios
    where we expect some tasks to raise/cancel and want to inspect all
    outcomes.
    """
    return await asyncio.gather(*coros, return_exceptions=True)


def race_seq(chaos: Chaos, actions: list[Callable[[], Awaitable[Any]]]) -> list[Awaitable[Any]]:
    """Shuffle `actions` deterministically and return awaitables ready
    for gather().  Records order in chaos.trace."""
    idx = list(range(len(actions)))
    chaos.rng.shuffle(idx)
    chaos.trace.append(f"race_seq:order={idx}")
    return [actions[i]() for i in idx]
