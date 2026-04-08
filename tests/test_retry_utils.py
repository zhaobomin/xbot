from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_run_with_retry_retries_retryable_exceptions(monkeypatch) -> None:
    from xbot.utils.retry import RetryPolicy, run_with_retry

    calls = 0
    delays: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        delays.append(delay)

    async def op() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("retry me")
        return "ok"

    monkeypatch.setattr("xbot.utils.retry.asyncio.sleep", _fake_sleep)

    result = await run_with_retry(
        RetryPolicy(
            max_attempts=3,
            base_delay=0.5,
            max_delay=2.0,
            retryable_exceptions=(TimeoutError,),
            jitter=False,
        ),
        "test-op",
        op,
    )

    assert result == "ok"
    assert calls == 3
    assert delays == [0.5, 1.0]


@pytest.mark.asyncio
async def test_run_with_retry_stops_on_non_retryable_exception(monkeypatch) -> None:
    from xbot.utils.retry import RetryPolicy, run_with_retry

    delays: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        delays.append(delay)

    async def op() -> str:
        raise ValueError("fatal")

    monkeypatch.setattr("xbot.utils.retry.asyncio.sleep", _fake_sleep)

    with pytest.raises(ValueError, match="fatal"):
        await run_with_retry(
            RetryPolicy(
                max_attempts=3,
                base_delay=0.5,
                max_delay=2.0,
                retryable_exceptions=(TimeoutError,),
                jitter=False,
            ),
            "test-op",
            op,
        )

    assert delays == []
