"""
Unit tests for CancellationToken (common/utils/cancellation.py).

Tests cover:
- CancellationError construction and message
- CancellationToken: cancel, is_cancelled, check, race, wait
"""

import asyncio
import pytest

from common.utils.cancellation import CancellationToken, CancellationError


# =============================================================================
# CancellationError Tests
# =============================================================================


class TestCancellationError:
    def test_includes_message_id(self):
        err = CancellationError("msg-123")
        assert err.message_id == "msg-123"
        assert "msg-123" in str(err)


# =============================================================================
# CancellationToken Tests
# =============================================================================


class TestCancellationTokenDirect:
    """Direct tests for CancellationToken (not via SSEManager)."""

    def test_starts_uncancelled(self):
        token = CancellationToken(message_id="msg-1")
        assert token.is_cancelled is False

    def test_cancel_sets_flag(self):
        token = CancellationToken(message_id="msg-1")
        token.cancel()
        assert token.is_cancelled is True

    def test_cancel_is_idempotent(self):
        token = CancellationToken(message_id="msg-1")
        token.cancel()
        token.cancel()
        assert token.is_cancelled is True

    def test_check_passes_when_not_cancelled(self):
        token = CancellationToken(message_id="msg-1")
        token.check()

    def test_check_raises_when_cancelled(self):
        token = CancellationToken(message_id="msg-1")
        token.cancel()
        with pytest.raises(CancellationError) as exc:
            token.check()
        assert exc.value.message_id == "msg-1"

    @pytest.mark.asyncio
    async def test_race_returns_result_when_not_cancelled(self):
        token = CancellationToken(message_id="msg-1")

        async def quick_work():
            return 42

        result = await token.race(quick_work())
        assert result == 42

    @pytest.mark.asyncio
    async def test_race_raises_when_cancelled_before_work(self):
        token = CancellationToken(message_id="msg-1")
        token.cancel()

        async def slow_work():
            await asyncio.sleep(10)
            return 42

        with pytest.raises(CancellationError):
            await token.race(slow_work())

    @pytest.mark.asyncio
    async def test_race_raises_when_cancelled_during_work(self):
        token = CancellationToken(message_id="msg-1")

        async def slow_work():
            await asyncio.sleep(10)

        async def cancel_soon():
            await asyncio.sleep(0.01)
            token.cancel()

        asyncio.create_task(cancel_soon())
        with pytest.raises(CancellationError):
            await token.race(slow_work())

    @pytest.mark.asyncio
    async def test_wait_returns_future(self):
        token = CancellationToken(message_id="msg-1")
        future = token.wait()
        assert not future.done()
        token.cancel()
        await asyncio.sleep(0.01)
        assert future.done()
