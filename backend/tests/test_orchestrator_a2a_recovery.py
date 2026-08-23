from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from execution.orchestrator.a2a_runtime.errors import (
    RecoverableCheckpointError,
    RecoverableTransportError,
)
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
from execution.orchestrator.a2a_runtime.recovery import (
    A2AArtifactRecoveryService,
    A2ACallRecoveryService,
    A2ACancellationRecoveryService,
    A2AContinuationRecoveryService,
    A2AInboxRecoveryService,
    A2ARecoveryCycle,
)

from ._orchestrator_a2a_helpers import ledger_record
from ._orchestrator_helpers import NOW


class Checkpoints:
    def __init__(self, accepted):
        self.accepted = accepted

    async def is_acceptance_checkpointed(self, *args):
        return self.accepted

    async def is_suspension_checkpointed(self, *args):
        return False


class Dispatch:
    def __init__(self, *, outcome="delivery_uncertain"):
        self.outcome = outcome

    async def inspect(self, command):
        if self.outcome == "terminal":
            return A2ADispatchReceipt(
                outcome="terminal",
                terminal_observation=NormalizedA2AObservation(
                    observation_id="recovery-terminal",
                    call_record_id=command.call_record_id,
                    source_kind="inspection",
                    source_identity="recovery:terminal",
                    binding_scope="endpoint",
                    event_kind="terminal",
                    observed_at=NOW,
                    status="completed",
                ),
            )
        return A2ADispatchReceipt(outcome=self.outcome)


class RecoveryCASRaceLedger(InMemoryAgentCallLedgerStore):
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
        self.raced = False
        self.durable_winner = None

    def _matches(self, record):
        if self.site == "dispatching":
            return (
                record.state == "delivery_uncertain" and record.inspection_attempts == 0
            )
        if self.site == "terminal":
            return record.state in {
                "completed",
                "failed",
                "canceled",
                "rejected",
                "expired",
            }
        if self.site == "working":
            return record.state == "working"
        if self.site == "retry":
            return record.inspection_attempts > 0
        return record.state == "expired"

    async def cas(self, record, *, expected_state_version):
        if not self.raced and self._matches(record):
            current = await self.load_by_record_id(record.call_record_id)
            assert current is not None
            winner = apply_observation(
                current,
                NormalizedA2AObservation(
                    observation_id=f"recovery-race-{self.site}-{self.terminal_status}",
                    call_record_id=current.call_record_id,
                    source_kind="inspection",
                    source_identity=f"recovery-race:{self.site}:{self.terminal_status}",
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


class PerRecordLoadOutageLedger(InMemoryAgentCallLedgerStore):
    def __init__(self, failing_call_record_id):
        super().__init__()
        self.failing_call_record_id = failing_call_record_id
        self.failed = False

    async def load_by_record_id(self, call_record_id):
        if call_record_id == self.failing_call_record_id and not self.failed:
            self.failed = True
            raise RecoverableCheckpointError("record authority unavailable")
        return await super().load_by_record_id(call_record_id)


async def service(
    record,
    *,
    checkpointed=False,
    dispatch=None,
    ledger=None,
    recover_dispatch_error=None,
):
    ledger = ledger or InMemoryAgentCallLedgerStore()
    await ledger.insert(record)
    epochs = InMemoryRoomEpochStore()
    await epochs.activate("room-1", "create-1", activated_at=NOW)
    recovered = []

    async def recover_dispatch(value):
        if recover_dispatch_error is not None:
            raise recover_dispatch_error
        recovered.append(value.call_record_id)

    observations = A2AObservationIngress(
        inbox=InMemoryObservationInboxStore(),
        conflicts=InMemoryObservationConflictStore(),
        ledger=ledger,
        authenticator=RejectExternalIngressAuthenticator(),
    )
    runtime = A2ACallRecoveryService(
        ledger=ledger,
        checkpoints=Checkpoints(checkpointed),
        room_epochs=epochs,
        dispatch=dispatch or Dispatch(),
        observations=observations,
        recover_dispatch=recover_dispatch,
    )
    return runtime, ledger, recovered


def recovery_record(site):
    record = transition_call(
        ledger_record(), to_state="ready_to_dispatch", updated_at=NOW
    )
    record = transition_call(record, to_state="dispatching", updated_at=NOW)
    if site != "dispatching":
        record = transition_call(record, to_state="delivery_uncertain", updated_at=NOW)
    if site == "expiry":
        record = record.model_copy(
            update={
                "runtime_policy": A2ARuntimePolicy(max_uncertain_inspection_attempts=1)
            }
        )
    return record


def recovery_dispatch(site):
    if site == "terminal":
        return Dispatch(outcome="terminal")
    if site == "working":
        return Dispatch(outcome="accepted")
    return Dispatch(outcome="delivery_uncertain")


@pytest.mark.parametrize(
    "site", ["dispatching", "terminal", "working", "retry", "expiry"]
)
@pytest.mark.parametrize("reported_outcome", ["conflict", "error"])
@pytest.mark.parametrize(
    "terminal_status", ["completed", "failed", "canceled", "rejected", "expired"]
)
async def test_call_recovery_cas_race_counts_only_durable_convergence(
    site, reported_outcome, terminal_status
):
    record = recovery_record(site)
    ledger = RecoveryCASRaceLedger(
        site=site,
        terminal_status=terminal_status,
        reported_outcome=reported_outcome,
    )
    runtime, ledger, _ = await service(
        record, dispatch=recovery_dispatch(site), ledger=ledger
    )

    assert await runtime.recover_call(record, now=NOW + timedelta(seconds=1))
    persisted = await ledger.load_by_record_id(record.call_record_id)
    assert ledger.raced is True
    assert persisted == ledger.durable_winner
    assert persisted.state == terminal_status


@pytest.mark.parametrize(
    "site", ["dispatching", "terminal", "working", "retry", "expiry"]
)
@pytest.mark.parametrize("winner_visibility", ["missing", "unloadable"])
async def test_call_recovery_unclassifiable_winner_is_not_counted(
    site, winner_visibility
):
    record = recovery_record(site)
    ledger = RecoveryCASRaceLedger(
        site=site,
        terminal_status="completed",
        reported_outcome="conflict",
        winner_visibility=winner_visibility,
    )
    runtime, ledger, _ = await service(
        record, dispatch=recovery_dispatch(site), ledger=ledger
    )

    assert not await runtime.recover_call(record, now=NOW + timedelta(seconds=1))
    persisted = await ledger.load_by_record_id(record.call_record_id)
    assert persisted.state == "completed"


async def test_recover_due_count_uses_classified_durable_winner():
    record = recovery_record("dispatching")
    ledger = RecoveryCASRaceLedger(
        site="dispatching",
        terminal_status="completed",
        reported_outcome="error",
    )
    runtime, _, _ = await service(record, ledger=ledger)
    assert await runtime.recover_due(due_at=NOW + timedelta(seconds=1)) == 1


async def test_uncheckpointed_acceptance_schedules_once_at_orphan_ttl():
    record = ledger_record()
    runtime, ledger, _ = await service(record, checkpointed=False)
    assert await runtime.recover_due(due_at=NOW) == 1
    persisted = await ledger.load_by_record_id(record.call_record_id)
    assert persisted.state == "accepted"
    assert persisted.next_attempt_at == NOW + timedelta(
        seconds=record.runtime_policy.orphan_acceptance_ttl_seconds
    )
    assert await runtime.recover_due(due_at=NOW) == 0


async def test_uncheckpointed_ready_and_waiting_schedule_bounded_retry():
    ready = transition_call(
        ledger_record(), to_state="ready_to_dispatch", updated_at=NOW
    )
    runtime, ledger, _ = await service(ready, checkpointed=False)
    assert await runtime.recover_due(due_at=NOW) == 1
    persisted = await ledger.load_by_record_id(ready.call_record_id)
    assert persisted.state == "ready_to_dispatch"
    assert persisted.next_attempt_at == NOW + timedelta(
        seconds=ready.runtime_policy.retry_backoff_initial_seconds
    )
    assert await runtime.recover_due(due_at=NOW) == 0

    waiting = transition_call(ready, to_state="dispatching", updated_at=NOW)
    waiting = transition_call(waiting, to_state="working", updated_at=NOW)
    waiting = transition_call(waiting, to_state="continuation_pending", updated_at=NOW)
    waiting = transition_call(
        waiting,
        to_state="input_required",
        updated_at=NOW,
        pending_interaction_id="interaction-1",
        interaction_revision=1,
        interaction_fingerprint="fingerprint",
    )
    runtime, ledger, _ = await service(waiting, checkpointed=True)
    assert await runtime.recover_due(due_at=NOW) == 1
    persisted = await ledger.load_by_record_id(waiting.call_record_id)
    assert persisted.state == "input_required"
    assert persisted.next_attempt_at > NOW
    assert await runtime.recover_due(due_at=NOW) == 0


async def test_failed_recover_dispatch_schedules_bounded_retry_once():
    record = ledger_record()
    runtime, ledger, _ = await service(
        record,
        checkpointed=True,
        recover_dispatch_error=RecoverableTransportError("dispatch unavailable"),
    )
    assert await runtime.recover_due(due_at=NOW) == 1
    persisted = await ledger.load_by_record_id(record.call_record_id)
    assert persisted.state == "accepted"
    assert persisted.next_attempt_at == NOW + timedelta(
        seconds=record.runtime_policy.retry_backoff_initial_seconds
    )
    assert await runtime.recover_due(due_at=NOW) == 0


async def test_per_record_load_outage_does_not_abort_later_recovery():
    first = recovery_record("dispatching")
    second = transition_call(
        ledger_record(run_id="run-2", call_id="call-2"),
        to_state="ready_to_dispatch",
        updated_at=NOW,
    )
    second = transition_call(second, to_state="dispatching", updated_at=NOW)
    ledger = PerRecordLoadOutageLedger(first.call_record_id)
    runtime, ledger, _ = await service(first, ledger=ledger)
    await ledger.insert(second)
    assert await runtime.recover_due(due_at=NOW + timedelta(seconds=1)) == 1
    assert (await ledger.load_by_record_id(second.call_record_id)).state == (
        "delivery_uncertain"
    )


async def test_lost_recovery_claim_counts_zero():
    class LostClaimLedger(InMemoryAgentCallLedgerStore):
        async def claim(self, *args, **kwargs):
            return None

    record = recovery_record("dispatching")
    runtime, _, _ = await service(record, ledger=LostClaimLedger())
    assert await runtime.recover_due(due_at=NOW + timedelta(seconds=1)) == 0


async def test_orphan_acceptance_expires_and_never_dispatches():
    record = ledger_record()
    runtime, ledger, recovered = await service(record, checkpointed=False)
    await runtime.recover_call(record, now=NOW + timedelta(seconds=301))
    assert (await ledger.load_by_record_id(record.call_record_id)).state == "expired"
    assert recovered == []


async def test_checkpointed_acceptance_is_recovered_for_dispatch():
    record = ledger_record()
    runtime, _, recovered = await service(record, checkpointed=True)
    await runtime.recover_call(record, now=NOW)
    assert recovered == [record.call_record_id]


async def test_expired_dispatch_claim_becomes_delivery_uncertain_not_ready():
    record = transition_call(
        ledger_record(), to_state="ready_to_dispatch", updated_at=NOW
    )
    record = transition_call(record, to_state="dispatching", updated_at=NOW)
    runtime, ledger, _ = await service(record, checkpointed=True)
    await runtime.recover_call(record, now=NOW + timedelta(minutes=1))
    assert (
        await ledger.load_by_record_id(record.call_record_id)
    ).state == "delivery_uncertain"


async def test_recovery_inspection_programming_error_surfaces():
    class BrokenInspection:
        async def inspect(self, command):
            raise AssertionError("inspection programming defect")

    record = transition_call(
        ledger_record(), to_state="ready_to_dispatch", updated_at=NOW
    )
    record = transition_call(record, to_state="dispatching", updated_at=NOW)
    record = transition_call(record, to_state="delivery_uncertain", updated_at=NOW)
    runtime, _, _ = await service(record, dispatch=BrokenInspection())
    with pytest.raises(AssertionError, match="inspection programming defect"):
        await runtime.recover_call(record, now=NOW + timedelta(seconds=1))


async def test_recovery_inspection_typed_outage_remains_recoverable():
    class UnavailableInspection:
        async def inspect(self, command):
            raise RecoverableTransportError("inspection unavailable")

    record = transition_call(
        ledger_record(), to_state="ready_to_dispatch", updated_at=NOW
    )
    record = transition_call(record, to_state="dispatching", updated_at=NOW)
    record = transition_call(record, to_state="delivery_uncertain", updated_at=NOW)
    runtime, ledger, _ = await service(record, dispatch=UnavailableInspection())
    assert await runtime.recover_call(record, now=NOW + timedelta(seconds=1))
    persisted = await ledger.load_by_record_id(record.call_record_id)
    assert persisted.state == "delivery_uncertain"
    assert persisted.inspection_attempts == 1


class NoProgressInbox:
    def __init__(self, record, *, load_outage=False):
        self.record = record
        self.load_outage = load_outage

    async def list_due(self, **kwargs):
        return [self.record]

    async def load(self, observation_id):
        if self.load_outage:
            raise RecoverableCheckpointError("inbox load unavailable")
        return self.record


class NoProgressProcessor:
    def __init__(self, outcome):
        self.outcome = outcome

    async def process(self, observation_id):
        return self.outcome

    async def defer_poison(self, *args, **kwargs):
        return "pending"


class NoProgressCallLedger:
    def __init__(self, record, *, load_outage=False):
        self.record = record
        self.load_outage = load_outage

    async def list_due(self, **kwargs):
        return [self.record]

    async def load_by_record_id(self, call_record_id):
        if self.load_outage:
            raise RecoverableCheckpointError("call load unavailable")
        return self.record


class NoProgressCoordinator:
    async def reconcile_answer(self, **kwargs):
        return "delivery_uncertain"

    async def recover_call(self, **kwargs):
        return "cancel_pending"


def no_progress_call(*, state, continuation=False, cancellation=False):
    return SimpleNamespace(
        call_record_id="record-1",
        state=state,
        state_version=3,
        answer_applied=None,
        continuation_command=object() if continuation else None,
        continuation_state="delivery_uncertain" if continuation else None,
        continuation_attempts=1,
        cancellation_command=object() if cancellation else None,
        cancellation_state="delivery_uncertain" if cancellation else None,
        cancellation_attempts=1,
        inspection_attempts=1,
        terminal_result_digest=None,
    )


@pytest.mark.parametrize("outcome", ["retryable", "conflict", "accepted"])
async def test_inbox_recovery_does_not_count_retryable_or_unchanged_rows(outcome):
    record = SimpleNamespace(
        observation_id="observation-1",
        state="pending",
        state_version=1,
        delivery_state="unresolved",
        outcome_digest=None,
    )
    inbox = NoProgressInbox(record)
    recovery = A2AInboxRecoveryService(
        processor=NoProgressProcessor(outcome), inbox=inbox
    )
    assert await recovery.recover_due(due_at=NOW) == 0


async def test_inbox_recovery_load_outage_is_not_counted():
    record = SimpleNamespace(
        observation_id="observation-1",
        state="pending",
        state_version=1,
        delivery_state="unresolved",
        outcome_digest=None,
    )
    recovery = A2AInboxRecoveryService(
        processor=NoProgressProcessor("accepted"),
        inbox=NoProgressInbox(record, load_outage=True),
    )
    assert await recovery.recover_due(due_at=NOW) == 0


@pytest.mark.parametrize(
    "state,continuation",
    [("input_required", False), ("resuming", True)],
)
async def test_continuation_recovery_does_not_count_unchanged_calls(
    state, continuation
):
    record = no_progress_call(state=state, continuation=continuation)
    recovery = A2AContinuationRecoveryService(
        NoProgressCoordinator(), NoProgressCallLedger(record)
    )
    assert await recovery.recover_due(due_at=NOW) == 0


async def test_continuation_recovery_load_outage_is_not_counted():
    record = no_progress_call(state="resuming", continuation=True)
    recovery = A2AContinuationRecoveryService(
        NoProgressCoordinator(),
        NoProgressCallLedger(record, load_outage=True),
    )
    assert await recovery.recover_due(due_at=NOW) == 0


async def test_cancellation_recovery_does_not_count_unchanged_cancel_pending():
    record = no_progress_call(state="cancel_pending", cancellation=True)
    recovery = A2ACancellationRecoveryService(
        NoProgressCoordinator(), NoProgressCallLedger(record)
    )
    assert await recovery.recover_due(due_at=NOW) == 0


async def test_cancellation_recovery_load_outage_is_not_counted():
    record = no_progress_call(state="cancel_pending", cancellation=True)
    recovery = A2ACancellationRecoveryService(
        NoProgressCoordinator(),
        NoProgressCallLedger(record, load_outage=True),
    )
    assert await recovery.recover_due(due_at=NOW) == 0


async def test_artifact_recovery_does_not_count_inbox_no_progress():
    record = SimpleNamespace(
        observation_id="artifact-1",
        state="pending",
        state_version=1,
        delivery_state="unresolved",
        outcome_digest=None,
    )
    inbox_recovery = A2AInboxRecoveryService(
        processor=NoProgressProcessor("retryable"),
        inbox=NoProgressInbox(record),
    )
    recovery = A2AArtifactRecoveryService(inbox_recovery)
    assert await recovery.recover_due(due_at=NOW) == 0


async def test_recovery_cycle_keeps_watchdog_last():
    order = []

    def phase(name):
        async def run():
            order.append(name)

        return run

    cycle = A2ARecoveryCycle(
        cancellation=phase("cancel"),
        continuation=phase("hitl"),
        observations=phase("inbox"),
        calls=phase("calls"),
        artifacts=phase("artifacts"),
        generic_runs=phase("runs"),
        projection=phase("projection"),
        watchdog=phase("watchdog"),
    )
    await cycle.run_once()
    assert order == [
        "cancel",
        "hitl",
        "inbox",
        "calls",
        "artifacts",
        "runs",
        "projection",
        "watchdog",
    ]


async def test_recovery_cycle_isolates_phase_failures_without_reordering():
    order = []

    def phase(name, *, fail=False):
        async def run():
            order.append(name)
            if fail:
                raise RuntimeError(f"{name} failed")

        return run

    cycle = A2ARecoveryCycle(
        cancellation=phase("cancel", fail=True),
        continuation=phase("hitl"),
        observations=phase("inbox", fail=True),
        calls=phase("calls"),
        artifacts=phase("artifacts"),
        generic_runs=phase("runs", fail=True),
        projection=phase("projection"),
        watchdog=phase("watchdog"),
    )
    await cycle.run_once()
    assert order == [
        "cancel",
        "hitl",
        "inbox",
        "calls",
        "artifacts",
        "runs",
        "projection",
        "watchdog",
    ]
