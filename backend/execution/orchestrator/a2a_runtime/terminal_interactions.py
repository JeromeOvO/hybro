"""Shared durable terminal-to-HITL convergence."""

from __future__ import annotations

from .errors import RecoverableCheckpointError
from .models import AgentCallLedgerRecord
from .ports import HITLApplicationPort


class TerminalInteractionFinalizer:
    """Close exact HITL ownership before exposing terminal call authority."""

    def __init__(self, owner: HITLApplicationPort) -> None:
        self.owner = owner

    async def finalize(self, record: AgentCallLedgerRecord) -> None:
        if record.pending_interaction_id is None:
            return
        await self.finalize_interaction(
            interaction_id=record.pending_interaction_id,
            call_record_id=record.call_record_id,
            terminal_state=record.state,
        )

    async def finalize_interaction(
        self,
        *,
        interaction_id: str,
        call_record_id: str,
        terminal_state: str,
    ) -> None:
        outcome = await self.owner.abandon(
            interaction_id,
            call_record_id=call_record_id,
            reason=f"terminal_winner:{terminal_state}",
        )
        if outcome not in {"accepted", "replayed", "absent"}:
            raise RecoverableCheckpointError(
                "terminal HITL aggregate could not be finalized"
            )


__all__ = ["TerminalInteractionFinalizer"]
