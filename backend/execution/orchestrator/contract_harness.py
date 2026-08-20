"""Unbound in-memory harness for executable persistence ordering contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from .models import (
    AcceptedAgentCall,
    OrchestratorEvent,
    OrchestratorRunState,
    ProjectionIntent,
    RecoveryClaim,
    TextPart,
    ToolResultMessage,
)
from .persistence import NON_TERMINAL_RUN_STATUSES
from .settlement import transition_projection_intent, transition_projection_settlement
from .transitions import TERMINAL_AGENT_CALL_STATES

HarnessOutcome = Literal["accepted", "replayed", "conflict", "gone"]


class InMemoryOrchestratorContractHarness:
    """A deterministic store/command harness; never used by production wiring."""

    def __init__(self) -> None:
        self.runs: dict[str, OrchestratorRunState] = {}
        self.room_generations: dict[str, int] = {}
        self.event_store: dict[str, OrchestratorEvent] = {}
        self.delivered_dedupe_keys: set[str] = set()
        self.dispatch_log: list[str] = []
        self._room_locks: dict[str, str] = {}
        self._projection_claims: dict[tuple[str, str], tuple[str, int]] = {}

    def create(self, run: OrchestratorRunState) -> HarnessOutcome:
        generation = self.room_generations.setdefault(
            run.room_id, run.request.room_generation
        )
        if generation != run.request.room_generation:
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
        """Return non-terminal runs whose retry is due and lease is absent/stale."""

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
        """Claim a due/stale Run at its current aggregate version."""

        run = self.runs.get(run_id)
        if run is None or not self._has_active_generation(run):
            return "gone"
        if (
            run.state_version != expected_state_version
            or lease_expires_at <= claimed_at
            or not self._is_recovery_due(run, at=claimed_at)
        ):
            return "conflict"
        previous_owner = run.recovery_claim.owner_id
        lock_owner = self._room_locks.get(run.room_id)
        if lock_owner not in {None, previous_owner}:
            return "conflict"
        self._room_locks[run.room_id] = owner_id
        self.runs[run_id] = run.model_copy(
            update={
                "recovery_claim": RecoveryClaim(
                    owner_id=owner_id,
                    lease_expires_at=lease_expires_at,
                    next_attempt_at=None,
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
        """Extend a live lease only for its owner and current Run version."""

        run = self.runs.get(run_id)
        if run is None or not self._has_active_generation(run):
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
        """Release only the owner's current lease and schedule its next attempt."""

        run = self.runs.get(run_id)
        if run is None or not self._has_active_generation(run):
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

    def _has_active_generation(self, run: OrchestratorRunState) -> bool:
        return self.room_generations.get(run.room_id) == run.request.room_generation

    def _is_recovery_due(self, run: OrchestratorRunState, *, at: datetime) -> bool:
        claim = run.recovery_claim
        return (
            run.status in NON_TERMINAL_RUN_STATUSES
            and self._has_active_generation(run)
            and (claim.next_attempt_at is None or claim.next_attempt_at <= at)
            and (claim.lease_expires_at is None or claim.lease_expires_at <= at)
        )

    def persist_call_before_dispatch(
        self,
        run_id: str,
        *,
        call: AcceptedAgentCall,
        dispatch_intent: ProjectionIntent,
        expected_state_version: int,
    ) -> HarnessOutcome:
        run = self.runs[run_id]
        if run.state_version != expected_state_version:
            return "conflict"
        if call.run_id != run_id or dispatch_intent.kind != "dispatch_agent_call":
            return "conflict"
        if any(item.call_id == call.call_id for item in run.calls):
            return "replayed"
        self.runs[run_id] = run.model_copy(
            update={
                "calls": [*run.calls, call],
                "projection_outbox": [*run.projection_outbox, dispatch_intent],
                "state_version": run.state_version + 1,
                "updated_at": call.updated_at,
            }
        )
        return "accepted"

    def dispatch(self, run_id: str, call_id: str) -> HarnessOutcome:
        run = self.runs[run_id]
        call = next((item for item in run.calls if item.call_id == call_id), None)
        intent = next(
            (
                item
                for item in run.projection_outbox
                if item.kind == "dispatch_agent_call"
                and item.payload.get("call_id") == call_id
            ),
            None,
        )
        if call is None or intent is None:
            return "conflict"
        self.dispatch_log.append(call_id)
        return "accepted"

    def ingest_callback(
        self,
        run_id: str,
        *,
        call_id: str,
        observation_id: str,
        room_generation: int,
        message_id: str,
        artifact_refs: list[str],
        observed_at: datetime,
    ) -> HarnessOutcome:
        run = self.runs.get(run_id)
        if (
            run is None
            or self.room_generations.get(run.room_id) != room_generation
            or run.request.room_generation != room_generation
        ):
            return "gone"
        call_index = next(
            (index for index, item in enumerate(run.calls) if item.call_id == call_id),
            None,
        )
        if call_index is None:
            return "conflict"
        call = run.calls[call_index]
        if observation_id in call.processed_observation_ids:
            return "replayed"
        if (
            run.status not in NON_TERMINAL_RUN_STATUSES
            or call.state in TERMINAL_AGENT_CALL_STATES
        ):
            calls = list(run.calls)
            calls[call_index] = call.model_copy(
                update={
                    "processed_observation_ids": [
                        *call.processed_observation_ids,
                        observation_id,
                    ],
                    "state_version": call.state_version + 1,
                }
            )
            self.runs[run_id] = run.model_copy(
                update={
                    "calls": calls,
                    "state_version": run.state_version + 1,
                    "updated_at": observed_at,
                }
            )
            return "accepted"

        updated_call = call.model_copy(
            update={
                "state": "completed",
                "processed_observation_ids": [
                    *call.processed_observation_ids,
                    observation_id,
                ],
                "artifact_refs": list(
                    dict.fromkeys([*call.artifact_refs, *artifact_refs])
                ),
                "terminal_at": observed_at,
                "updated_at": observed_at,
                "state_version": call.state_version + 1,
            }
        )
        calls = list(run.calls)
        calls[call_index] = updated_call
        result = ToolResultMessage(
            message_id=message_id,
            call_id=call.call_id,
            tool_name=call.tool_name,
            status="completed",
            content=[TextPart(text="callback result")],
            artifact_refs=artifact_refs,
            is_error=False,
            created_at=observed_at,
        )
        projection = ProjectionIntent(
            intent_id=f"callback:{observation_id}",
            kind="project_call_observation",
            target=run.room_id,
            dedupe_key=f"callback:{observation_id}",
            required=True,
            event_id=f"callback-event:{observation_id}",
            event_sequence=max(
                (item.event_sequence for item in run.projection_outbox), default=0
            )
            + 1,
            causation_id=observation_id,
            payload={"call_id": call_id, "artifact_refs": artifact_refs},
            status="pending",
        )
        self.runs[run_id] = run.model_copy(
            update={
                "calls": calls,
                "transcript": [*run.transcript, result],
                "artifact_refs": list(
                    dict.fromkeys([*run.artifact_refs, *artifact_refs])
                ),
                "projection_outbox": [*run.projection_outbox, projection],
                "state_version": run.state_version + 1,
                "updated_at": observed_at,
            }
        )
        return "accepted"

    def save_authoritative(self, run: OrchestratorRunState) -> None:
        """Simulate a successful aggregate CAS before any outbox side effect."""

        self.runs[run.run_id] = run

    def repair_outbox(self, run_id: str, *, repaired_at: datetime) -> int:
        """Idempotently repair events and delivery, then derive settlement."""

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
        self, run_id: str, intent_id: str, *, owner_id: str, room_generation: int
    ) -> HarnessOutcome:
        run = self.runs.get(run_id)
        if (
            run is None
            or run.request.room_generation != room_generation
            or self.room_generations.get(run.room_id) != room_generation
        ):
            return "gone"
        lock_owner = self._room_locks.get(run.room_id)
        if lock_owner not in {None, owner_id}:
            return "conflict"
        self._room_locks[run.room_id] = owner_id
        self._projection_claims[(run_id, intent_id)] = (owner_id, room_generation)
        return "accepted"

    def release_projection(self, run_id: str, intent_id: str, *, owner_id: str) -> None:
        run = self.runs[run_id]
        self._projection_claims.pop((run_id, intent_id), None)
        if self._room_locks.get(run.room_id) == owner_id:
            self._room_locks.pop(run.room_id)

    def confirm_projection(
        self, run_id: str, intent_id: str, *, owner_id: str, room_generation: int
    ) -> HarnessOutcome:
        run = self.runs.get(run_id)
        if (
            run is None
            or self.room_generations.get(run.room_id) != room_generation
            or self._projection_claims.get((run_id, intent_id))
            != (owner_id, room_generation)
        ):
            return "gone"
        return "accepted"

    def delete_room(self, room_id: str, *, owner_id: str) -> HarnessOutcome:
        lock_owner = self._room_locks.get(room_id)
        if lock_owner not in {None, owner_id}:
            return "conflict"
        self._room_locks[room_id] = owner_id
        self.room_generations[room_id] = self.room_generations.get(room_id, 0) + 1
        deleted_run_ids = {
            run_id for run_id, run in self.runs.items() if run.room_id == room_id
        }
        for run_id in deleted_run_ids:
            self.runs.pop(run_id)
        for key in list(self._projection_claims):
            if key[0] in deleted_run_ids:
                self._projection_claims.pop(key)
        self._room_locks.pop(room_id)
        return "accepted"
