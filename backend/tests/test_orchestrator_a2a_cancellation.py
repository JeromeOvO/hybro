from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from common.dto.hitl import A2AInteractionSpec
from execution.orchestrator.a2a_runtime.cancellation import A2ACancellationCoordinator
from execution.orchestrator.a2a_runtime.errors import (
    RecoverableAdapterError,
    RecoverableCheckpointError,
)
from execution.orchestrator.a2a_runtime.hitl import InMemoryHITLApplicationPort
from execution.orchestrator.a2a_runtime.in_memory import (
    InMemoryAgentCallLedgerStore,
    InMemoryObservationConflictStore,
    InMemoryObservationInboxStore,
    InMemoryRoomEpochStore,
)
from execution.orchestrator.a2a_runtime.ingress import (
    A2AObservationIngress,
    RejectExternalIngressAuthenticator,
)
from execution.orchestrator.a2a_runtime.ledger import (
    apply_observation,
    transition_call,
)
from execution.orchestrator.a2a_runtime.models import (
    A2ADispatchReceipt,
    A2ARuntimePolicy,
    NormalizedA2AObservation,
)

from ._orchestrator_a2a_helpers import ledger_record
from ._orchestrator_helpers import NOW


class FinalizerFaultHITL(InMemoryHITLApplicationPort):
    def __init__(self, mode):
        super().__init__()
        self.mode = mode
        self.failed = False

    async def abandon(self, interaction_id, **kwargs):
        if not self.failed:
            self.failed = True
            if self.mode == "conflict":
                return "conflict"
            if self.mode == "error":
                return "error"
            if self.mode == "outage":
                raise RecoverableAdapterError("HITL owner unavailable")
            outcome = await super().abandon(interaction_id, **kwargs)
            assert outcome == "accepted"
            raise RecoverableAdapterError("HITL close acknowledgement lost")
        return await super().abandon(interaction_id, **kwargs)


class Dispatch:
    def __init__(self, *, outcome="accepted"):
        self.commands = []
        self.outcome = outcome

    async def cancel(self, command):
        self.commands.append(command)
        return A2ADispatchReceipt(outcome=self.outcome)

    async def inspect_cancellation(self, command):
        return A2ADispatchReceipt(outcome="accepted")


class CancellationCASRaceLedger(InMemoryAgentCallLedgerStore):
    def __init__(
        self,
        *,
        site,
        terminal_status,
        reported_outcome,
        winner_visibility="visible",
    ):
        super().__init__()
        self.site = site
        self.terminal_status = terminal_status
        self.reported_outcome = reported_outcome
        self.winner_visibility = winner_visibility
        self.hide_next_winner = False
        self.remote_commands = None
        self.raced = False
        self.durable_winner = None

    def _matches(self, record):
        if self.site == "marker":
            return (
                record.state == "cancel_pending"
                and record.cancellation_state == "pending"
            )
        if self.site == "dispatching":
            return record.cancellation_state == "dispatching"
        if self.site == "terminal":
            return record.state == "canceled"
        if self.site == "uncertainty":
            return (
                record.state == "cancel_pending"
                and record.cancellation_state == "delivery_uncertain"
                and record.claim_owner is None
            )
        return record.state == "expired"

    async def cas(self, record, *, expected_state_version):
        if not self.raced and self._matches(record):
            if self.site in {"marker", "dispatching"}:
                assert (
                    await super().cas(
                        record, expected_state_version=expected_state_version
                    )
                    == "accepted"
                )
                current = record
                assert self.remote_commands is not None
                self.remote_commands.append(record.cancellation_command)
            else:
                current = await self.load_by_record_id(record.call_record_id)
                assert current is not None
            winner = apply_observation(
                current,
                NormalizedA2AObservation(
                    observation_id=f"cancel-race-{self.site}-{self.terminal_status}",
                    call_record_id=current.call_record_id,
                    source_kind="inspection",
                    source_identity=f"cancel-race:{self.site}:{self.terminal_status}",
                    binding_scope=current.endpoint_scope_digest,
                    event_kind="terminal",
                    observed_at=datetime.now(UTC),
                    task_id=current.a2a_task_id,
                    context_id=current.a2a_context_id,
                    status=self.terminal_status,
                ),
                recent_limit=current.runtime_policy.recent_observation_id_limit,
            )
            assert (
                await super().cas(winner, expected_state_version=current.state_version)
                == "accepted"
            )
            self.raced = True
            self.durable_winner = winner
            self.hide_next_winner = self.winner_visibility != "visible"
            if self.reported_outcome == "error":
                return "error"
        return await super().cas(record, expected_state_version=expected_state_version)

    async def load_by_record_id(self, call_record_id):
        if self.hide_next_winner:
            self.hide_next_winner = False
            if self.winner_visibility == "missing":
                return None
            raise RecoverableCheckpointError("winner temporarily unavailable")
        return await super().load_by_record_id(call_record_id)


async def setup(*, ledger=None, dispatch=None, policy=None, hitl=None):
    ledger = ledger or InMemoryAgentCallLedgerStore()
    accepted = ledger_record()
    if policy is not None:
        accepted = accepted.model_copy(update={"runtime_policy": policy})
    record = transition_call(accepted, to_state="ready_to_dispatch", updated_at=NOW)
    record = transition_call(record, to_state="dispatching", updated_at=NOW)
    record = transition_call(record, to_state="working", updated_at=NOW)
    await ledger.insert(record)
    epochs = InMemoryRoomEpochStore()
    await epochs.activate("room-1", "create-1", activated_at=NOW)
    dispatch = dispatch or Dispatch()
    if isinstance(ledger, CancellationCASRaceLedger):
        ledger.remote_commands = dispatch.commands
    inbox = InMemoryObservationInboxStore()
    observations = A2AObservationIngress(
        inbox=inbox,
        conflicts=InMemoryObservationConflictStore(),
        ledger=ledger,
        authenticator=RejectExternalIngressAuthenticator(),
    )
    hitl = hitl or InMemoryHITLApplicationPort()
    coordinator = A2ACancellationCoordinator(
        ledger=ledger,
        room_epochs=epochs,
        dispatch=dispatch,
        observations=observations,
        hitl=hitl,
        policy=policy,
    )
    return coordinator, ledger, epochs, dispatch, inbox, record


def cancellation_interaction():
    return A2AInteractionSpec.model_validate(
        {
            "schema_version": 1,
            "interaction_id": "cancel-interaction",
            "questions": [
                {
                    "question_id": "q1",
                    "interaction_kind": "questionnaire",
                    "prompt": "Choose",
                    "answer_kind": "single_choice",
                    "choices": ["a", "b"],
                }
            ],
        }
    )


async def attach_cancellation_interaction(ledger, hitl, record, *, create=True):
    pending = transition_call(record, to_state="continuation_pending", updated_at=NOW)
    assert (
        await ledger.cas(pending, expected_state_version=record.state_version)
        == "accepted"
    )
    attached = transition_call(
        pending,
        to_state="input_required",
        updated_at=NOW,
        pending_interaction_id="cancel-interaction",
        interaction_revision=1,
        interaction_fingerprint="cancel-fingerprint",
    )
    assert (
        await ledger.cas(attached, expected_state_version=pending.state_version)
        == "accepted"
    )
    if create:
        await hitl.create_or_replay(
            call=attached,
            interaction=cancellation_interaction(),
            interaction_fingerprint="cancel-fingerprint",
        )
    return attached


@pytest.mark.parametrize(
    "terminal_status", ["completed", "failed", "canceled", "expired"]
)
@pytest.mark.parametrize(
    "close_mode",
    ["accepted", "replayed", "absent", "conflict", "error", "outage", "ack_loss"],
)
async def test_cancellation_terminal_return_requires_hitl_finalization(
    terminal_status, close_mode
):
    ledger = CancellationCASRaceLedger(
        site="terminal",
        terminal_status=terminal_status,
        reported_outcome="conflict",
    )
    hitl = (
        FinalizerFaultHITL(close_mode)
        if close_mode in {"conflict", "error", "outage", "ack_loss"}
        else InMemoryHITLApplicationPort()
    )
    coordinator, ledger, _, dispatch, inbox, record = await setup(
        ledger=ledger, hitl=hitl
    )
    attached = await attach_cancellation_interaction(
        ledger, hitl, record, create=close_mode != "absent"
    )
    if close_mode == "replayed":
        assert (
            await hitl.abandon(
                "cancel-interaction",
                call_record_id=attached.call_record_id,
                reason="preclosed",
            )
            == "accepted"
        )

    first = await coordinator.cancel_call(
        call_record_id=record.call_record_id, reason="user canceled"
    )
    persisted = await ledger.load_by_record_id(record.call_record_id)
    if close_mode in {"conflict", "error", "outage", "ack_loss"}:
        assert first == "cancel_pending"
    else:
        assert first == terminal_status
    retry = await coordinator.cancel_call(
        call_record_id=record.call_record_id, reason="user canceled"
    )

    assert persisted.state == terminal_status
    assert retry == terminal_status
    assert hitl.read_interaction_for_test("cancel-interaction") is None
    with pytest.raises(KeyError):
        await hitl.answer(
            interaction_id="cancel-interaction",
            interaction_revision=1,
            route_fingerprint="unused",
            answers=[],
            authenticated_answerer_id="user-1",
            verified_auth_reference_digests=[],
            verified_auth_references=[],
        )
    assert len(dispatch.commands) == 1
    assert await inbox.load(f"cancel-observation-{persisted.cancellation_command_id}")


@pytest.mark.parametrize(
    "site", ["marker", "dispatching", "terminal", "uncertainty", "expiry"]
)
@pytest.mark.parametrize("reported_outcome", ["conflict", "error"])
@pytest.mark.parametrize(
    "terminal_status", ["completed", "failed", "canceled", "expired"]
)
async def test_cancellation_cas_race_returns_durable_terminal_winner(
    site, reported_outcome, terminal_status
):
    ledger = CancellationCASRaceLedger(
        site=site,
        terminal_status=terminal_status,
        reported_outcome=reported_outcome,
    )
    dispatch = Dispatch(
        outcome=(
            "delivery_uncertain" if site in {"uncertainty", "expiry"} else "accepted"
        )
    )
    policy = A2ARuntimePolicy(max_uncertain_inspection_attempts=1)
    coordinator, ledger, _, dispatch, _, record = await setup(
        ledger=ledger, dispatch=dispatch, policy=policy
    )
    cancel_kwargs = {
        "call_record_id": record.call_record_id,
        "reason": "user canceled",
    }

    if site == "expiry":
        assert await coordinator.cancel_call(**cancel_kwargs) == "cancel_pending"
        current = await ledger.load_by_record_id(record.call_record_id)
        due = current.model_copy(
            update={
                "next_attempt_at": datetime.now(UTC) - timedelta(seconds=1),
                "state_version": current.state_version + 1,
            }
        )
        assert (
            await ledger.cas(due, expected_state_version=current.state_version)
            == "accepted"
        )
        first = await coordinator.recover_call(call_record_id=record.call_record_id)
        retry = await coordinator.recover_call(call_record_id=record.call_record_id)
    else:
        first = await coordinator.cancel_call(**cancel_kwargs)
        retry = await coordinator.cancel_call(**cancel_kwargs)

    persisted = await ledger.load_by_record_id(record.call_record_id)
    assert ledger.raced is True
    assert persisted is not None
    assert persisted == ledger.durable_winner
    assert first == retry == persisted.state == terminal_status
    assert len(dispatch.commands) == 1
    assert persisted.cancellation_command == dispatch.commands[0]


@pytest.mark.parametrize("winner_visibility", ["missing", "unloadable"])
async def test_cancellation_unclassifiable_winner_is_typed_recoverable(
    winner_visibility,
):
    ledger = CancellationCASRaceLedger(
        site="terminal",
        terminal_status="completed",
        reported_outcome="conflict",
        winner_visibility=winner_visibility,
    )
    coordinator, ledger, _, dispatch, _, record = await setup(ledger=ledger)
    cancel_kwargs = {
        "call_record_id": record.call_record_id,
        "reason": "user canceled",
    }

    assert await coordinator.cancel_call(**cancel_kwargs) == "cancel_pending"
    persisted = await ledger.load_by_record_id(record.call_record_id)
    assert persisted is not None
    assert persisted.state == "completed"
    assert await coordinator.cancel_call(**cancel_kwargs) == "completed"
    assert len(dispatch.commands) == 1


async def test_cancellation_marker_precedes_remote_effect_and_is_idempotent():
    coordinator, ledger, _, dispatch, inbox, record = await setup()
    assert (
        await coordinator.cancel_call(
            call_record_id=record.call_record_id, reason="user canceled"
        )
        == "canceled"
    )
    persisted = await ledger.load_by_record_id(record.call_record_id)
    assert persisted.state == "canceled"
    assert persisted.cancellation_command_id == dispatch.commands[0].command_id
    observation_id = f"cancel-observation-{persisted.cancellation_command_id}"
    assert await inbox.load(observation_id) is not None
    assert (
        await coordinator.cancel_call(
            call_record_id=record.call_record_id, reason="user canceled"
        )
        == "canceled"
    )
    assert len(dispatch.commands) == 1


async def test_deletion_cleanup_can_cancel_tombstoned_epoch_without_model_resume():
    coordinator, ledger, epochs, dispatch, inbox, record = await setup()
    assert (await epochs.deactivate("room-1", 1, "delete-1", deactivated_at=NOW))[
        0
    ] == "accepted"
    assert (
        await coordinator.cancel_call(
            call_record_id=record.call_record_id,
            reason="Room deleted",
            deletion_id="delete-1",
        )
        == "canceled"
    )
    assert len(dispatch.commands) == 1
    persisted = await ledger.load_by_record_id(record.call_record_id)
    assert persisted.state == "canceled"
    assert (
        await inbox.load(f"cancel-observation-{persisted.cancellation_command_id}")
        is not None
    )
