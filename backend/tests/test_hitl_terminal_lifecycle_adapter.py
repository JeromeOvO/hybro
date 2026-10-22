from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from approval.adapters import HITLTerminalLifecycleAdapter


@pytest.mark.asyncio
async def test_canonical_hitl_owner_terminalizes_through_the_canonical_port():
    canonical = AsyncMock(return_value=True)
    adapter = HITLTerminalLifecycleAdapter(canonical_terminalizer=canonical)
    request = SimpleNamespace(
        orchestration_run_id="run-canonical",
        request_id="request-1",
        room_id="room-1",
        user_message_id="user-1",
        client_request_id="client-1",
    )

    await adapter.terminalize_owning_run(
        request,
        terminal_status="failed",
        reason="expired",
    )

    canonical.assert_awaited_once_with(
        request,
        terminal_status="failed",
        reason="expired",
    )
