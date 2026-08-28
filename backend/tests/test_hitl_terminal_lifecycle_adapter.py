from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from execution.hitl.adapters import HITLTerminalLifecycleAdapter


@pytest.mark.asyncio
async def test_canonical_hitl_owner_routes_only_to_canonical_run_store():
    legacy_store = MagicMock()
    legacy_store.get_run = AsyncMock()
    legacy_projection = MagicMock()
    legacy_projection.project_run_state = AsyncMock()
    canonical = AsyncMock(return_value=True)
    adapter = HITLTerminalLifecycleAdapter(
        legacy_store,
        legacy_projection,
        canonical_terminalizer=canonical,
    )
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
    legacy_store.get_run.assert_not_awaited()
    legacy_projection.project_run_state.assert_not_awaited()
