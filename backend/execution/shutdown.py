from __future__ import annotations

import asyncio

GRACEFUL_SHUTDOWN_CANCEL_REASON = "hybro-graceful-shutdown"


def is_graceful_shutdown_cancellation(error: asyncio.CancelledError) -> bool:
    """Return whether task cancellation is an infrastructure shutdown signal."""
    return bool(error.args) and error.args[0] == GRACEFUL_SHUTDOWN_CANCEL_REASON
