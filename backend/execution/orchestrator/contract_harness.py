"""In-memory harness for generic Run persistence ordering contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from .models import (
    OrchestratorEvent,
    OrchestratorRunState,
    ProjectionIntent,
    RecoveryClaim,
)
from .persistence import NON_TERMINAL_RUN_STATUSES
from .settlement import transition_projection_intent, transition_projection_settlement

HarnessOutcome = Literal["accepted", "replayed", "conflict", "gone"]


class InMemoryOrchestratorContractHarness:
    def __init__(self) -> None:
        self.runs: dict[str, OrchestratorRunState] = {}
        self.room_epochs: dict[str, int] = {}
        self.event_store: dict[str, OrchestratorEvent] = {}
        self.delivered_dedupe_keys: set[str] = set()
        self._room_locks: dict[str, str] = {}
        self._projection_claims: dict[tuple[str, str], tuple[str, int]] = {}

    def create(self, run: OrchestratorRunState) -> HarnessOutcome:
        epoch = self.room_epochs.setdefault(run.room_id, run.request.room_epoch)
        if epoch != run.request.room_epoch:
            return "gone"
        if run.run_id in self.runs:
            return "replayed" if self.runs[run.run_id] == run else "conflict"
        if any(
            existing.room_id == run.room_id
            and existing.status in NON_TERMINAL_RUN_STATUSES
            for existing in self.runs.values()
        ):
            return "conflict"
        self.runs[run.run_id] = run
        return "accepted"

    def list_due_runs(
        self, *, due_at: datetime, limit: int
    ) -> list[OrchestratorRunState]:
        eligible = [
            run for run in self.runs.values() if self._is_recovery_due(run, at=due_at)
        ]
        eligible.sort(
            key=lambda run: (
                run.recovery_claim.next_attempt_at or run.updated_at,
                run.run_id,
            )
        )
        return eligible[: max(limit, 0)]

    def claim_recovery(
        self,
        run_id: str,
        *,
        expected_state_version: int,
        owner_id: str,
        lease_expires_at: datetime,
        claimed_at: datetime,
    ) -> HarnessOutcome:
        run = self.runs.get(run_id)
        if run is None or not self._has_active_epoch(run):
            return "gone"
        if (
            run.state_version != expected_state_version
            or lease_expires_at <= claimed_at
            or not self._is_recovery_due(run, at=claimed_at)
        ):
            return "conflict"
        previous_owner = run.recovery_claim.owner_id
        if self._room_locks.get(run.room_id) not in {None, previous_owner}:
            return "conflict"
        self._room_locks[run.room_id] = owner_id
        self.runs[run_id] = run.model_copy(
            update={
                "recovery_claim": RecoveryClaim(
                    owner_id=owner_id, lease_expires_at=lease_expires_at
                ),
                "state_version": run.state_version + 1,
                "updated_at": claimed_at,
            }
        )
        return "accepted"

    def renew_recovery(
        self,
        run_id: str,
        *,
        expected_state_version: int,
        owner_id: str,
        lease_expires_at: datetime,
        renewed_at: datetime,
    ) -> HarnessOutcome:
        run = self.runs.get(run_id)
        if run is None or not self._has_active_epoch(run):
            return "gone"
        claim = run.recovery_claim
        if (
            run.state_version != expected_state_version
            or claim.owner_id != owner_id
            or claim.lease_expires_at is None
            or claim.lease_expires_at <= renewed_at
            or lease_expires_at <= claim.lease_expires_at
            or self._room_locks.get(run.room_id) != owner_id
        ):
            return "conflict"
        self.runs[run_id] = run.model_copy(
            update={
                "recovery_claim": claim.model_copy(
                    update={"lease_expires_at": lease_expires_at}
                ),
                "state_version": run.state_version + 1,
                "updated_at": renewed_at,
            }
        )
        return "accepted"

    def release_recovery(
        self,
        run_id: str,
        *,
        expected_state_version: int,
        owner_id: str,
        next_attempt_at: datetime | None,
        released_at: datetime,
    ) -> HarnessOutcome:
        run = self.runs.get(run_id)
        if run is None or not self._has_active_epoch(run):
            return "gone"
        if (
            run.state_version != expected_state_version
            or run.recovery_claim.owner_id != owner_id
            or self._room_locks.get(run.room_id) != owner_id
        ):
            return "conflict"
        self.runs[run_id] = run.model_copy(
            update={
                "recovery_claim": RecoveryClaim(next_attempt_at=next_attempt_at),
                "state_version": run.state_version + 1,
                "updated_at": released_at,
            }
        )
        self._room_locks.pop(run.room_id, None)
        return "accepted"

    def save_authoritative(self, run: OrchestratorRunState) -> None:
        self.runs[run.run_id] = run

    def repair_outbox(self, run_id: str, *, repaired_at: datetime) -> int:
        run = self.runs[run_id]
        repaired = 0
        intents: list[ProjectionIntent] = []
        for intent in run.projection_outbox:
            if intent.status == "completed":
                intents.append(intent)
                continue
            if intent.kind == "append_orchestrator_event":
                event = OrchestratorEvent.model_validate(intent.payload)
                self.event_store.setdefault(event.event_id, event)
            else:
                self.delivered_dedupe_keys.add(intent.dedupe_key)
            claimed = transition_projection_intent(
                intent,
                to_status="claimed",
                claim_owner="repair-worker",
                claim_expires_at=repaired_at,
            )
            intents.append(transition_projection_intent(claimed, to_status="completed"))
            repaired += 1
        run = run.model_copy(update={"projection_outbox": intents})
        settlement = transition_projection_settlement(
            run,
            expected_state_version=run.state_version,
            updated_at=repaired_at,
        )
        self.runs[run_id] = settlement.run
        return repaired

    def claim_projection(
        self, run_id: str, intent_id: str, *, owner_id: str, room_epoch: int
    ) -> HarnessOutcome:
        run = self.runs.get(run_id)
        if (
            run is None
            or run.request.room_epoch != room_epoch
            or self.room_epochs.get(run.room_id) != room_epoch
        ):
            return "gone"
        if self._room_locks.get(run.room_id) not in {None, owner_id}:
            return "conflict"
        self._room_locks[run.room_id] = owner_id
        self._projection_claims[(run_id, intent_id)] = (owner_id, room_epoch)
        return "accepted"

    def release_projection(self, run_id: str, intent_id: str, *, owner_id: str) -> None:
        run = self.runs[run_id]
        self._projection_claims.pop((run_id, intent_id), None)
        if self._room_locks.get(run.room_id) == owner_id:
            self._room_locks.pop(run.room_id)

    def confirm_projection(
        self, run_id: str, intent_id: str, *, owner_id: str, room_epoch: int
    ) -> HarnessOutcome:
        run = self.runs.get(run_id)
        if (
            run is None
            or self.room_epochs.get(run.room_id) != room_epoch
            or self._projection_claims.get((run_id, intent_id))
            != (owner_id, room_epoch)
        ):
            return "gone"
        return "accepted"

    def delete_room(self, room_id: str, *, owner_id: str) -> HarnessOutcome:
        if self._room_locks.get(room_id) not in {None, owner_id}:
            return "conflict"
        self._room_locks[room_id] = owner_id
        self.room_epochs[room_id] = self.room_epochs.get(room_id, 0) + 1
        deleted = {
            run_id for run_id, run in self.runs.items() if run.room_id == room_id
        }
        for run_id in deleted:
            self.runs.pop(run_id)
        for key in list(self._projection_claims):
            if key[0] in deleted:
                self._projection_claims.pop(key)
        self._room_locks.pop(room_id)
        return "accepted"

    def _has_active_epoch(self, run: OrchestratorRunState) -> bool:
        return self.room_epochs.get(run.room_id) == run.request.room_epoch

    def _is_recovery_due(self, run: OrchestratorRunState, *, at: datetime) -> bool:
        claim = run.recovery_claim
        return (
            run.status in NON_TERMINAL_RUN_STATUSES
            and self._has_active_epoch(run)
            and (claim.next_attempt_at is None or claim.next_attempt_at <= at)
            and (claim.lease_expires_at is None or claim.lease_expires_at <= at)
        )
