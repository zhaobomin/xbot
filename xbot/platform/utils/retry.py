"""Shared retry helpers for transient async operations."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

from xbot.platform.logging.core import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    base_delay: float
    max_delay: float
    retryable_exceptions: tuple[type[BaseException], ...] = ()
    jitter: bool = True

    def delay_for_attempt(self, attempt: int) -> float:
        delay = min(self.base_delay * (2 ** max(attempt - 1, 0)), self.max_delay)
        if self.jitter and delay > 0:
            return random.uniform(delay * 0.5, delay)
        return delay


async def run_with_retry(
    policy: RetryPolicy,
    op_name: str,
    func: Callable[[], Awaitable[T]],
    *,
    sleep_func: Callable[[float], Awaitable[None]] | None = None,
) -> T:
    """Run an async operation with exponential backoff for retryable exceptions."""
    if policy.max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    sleep = sleep_func or asyncio.sleep

    last_error: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await func()
        except asyncio.CancelledError:
            raise
        except policy.retryable_exceptions as exc:
            last_error = exc
            if attempt >= policy.max_attempts:
                raise
            delay = policy.delay_for_attempt(attempt)
            logger.warning(
                "%s failed with retryable error on attempt %s/%s, retrying in %.2fs: %s",
                op_name,
                attempt,
                policy.max_attempts,
                delay,
                exc,
            )
            await sleep(delay)
        except Exception:
            raise

    assert last_error is not None
    raise last_error
