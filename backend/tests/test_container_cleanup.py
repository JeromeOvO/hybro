import asyncio

import pytest

from container import _run_cleanup_steps


@pytest.mark.asyncio
async def test_cleanup_steps_preserve_first_failure_and_continue_all_stages():
    calls = []
    first = RuntimeError("delivery close failed")

    async def delivery():
        calls.append("delivery")
        raise first

    async def cancellation():
        calls.append("cancellation")
        raise ValueError("cancellation close failed")

    async def redis():
        calls.append("redis")

    async def mongo():
        calls.append("mongo")

    error = await _run_cleanup_steps(
        [
            ("delivery", delivery),
            ("cancellation", cancellation),
            ("redis", redis),
            ("mongo", mongo),
        ]
    )

    assert error is first
    assert calls == ["delivery", "cancellation", "redis", "mongo"]


@pytest.mark.asyncio
async def test_cleanup_steps_continue_after_cancellation_error():
    calls = []

    async def cancelled():
        calls.append("cancelled")
        raise asyncio.CancelledError

    async def later():
        calls.append("later")

    error = await _run_cleanup_steps([("cancelled", cancelled), ("later", later)])

    assert isinstance(error, asyncio.CancelledError)
    assert calls == ["cancelled", "later"]
