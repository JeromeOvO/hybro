from __future__ import annotations

from hashlib import sha256

import pytest

from common.dto.hitl import A2AInteractionSpec
from execution.orchestrator.a2a_runtime.errors import (
    AmbiguousRemoteEffectError,
    RecoverableAuthorizationError,
    RecoverableCheckpointError,
    RecoverableEpochError,
    RecoverableResourceError,
)
from execution.orchestrator.a2a_runtime.hitl import InMemoryHITLApplicationPort
from execution.orchestrator.a2a_runtime.in_memory import (
    InMemoryAgentCallLedgerStore,
    InMemoryObservationConflictStore,
    InMemoryObservationInboxStore,
    InMemoryPreparedInvocationSnapshotReader,
    InMemoryRoomEpochStore,
)
from execution.orchestrator.a2a_runtime.ingress import (
    A2AObservationIngress,
    RejectExternalIngressAuthenticator,
)
from execution.orchestrator.a2a_runtime.ledger import transition_call
from execution.orchestrator.a2a_runtime.models import (
    A2ADispatchReceipt,
    MaterializedResourcePart,
    NormalizedA2AObservation,
)
from execution.orchestrator.a2a_runtime.runtime import (
    A2AAcceptanceDenied,
    A2AAgentToolRuntime,
)
from execution.orchestrator.a2a_runtime.terminal_interactions import (
    TerminalInteractionFinalizer,
)
from execution.orchestrator.models import TextPart, ToolResult, ToolSuspension

from ._orchestrator_v3_a2a_helpers import invocation, prepared
from ._orchestrator_v3_helpers import NOW, NeverCancelled


class Authorization:
    def __init__(self, outcome="authorized"):
        self.outcome = outcome
        self.calls = 0

    async def authorize(self, **kwargs):
        self.calls += 1
        return self.outcome


class Checkpoints:
    def __init__(self, accepted=True):
        self.accepted = accepted

    async def is_acceptance_checkpointed(self, *args):
        return self.accepted

    async def is_suspension_checkpointed(self, *args):
        return False


class Resources:
    async def materialize(self, manifest, **kwargs):
        return [
            MaterializedResourcePart(
                ref_id=ref.ref_id,
                kind="text",
                content_digest=ref.content_digest,
                payload="content",
            )
            for ref in manifest.refs
        ]

    async def materialize_inbound_artifacts(self, **kwargs):
        return kwargs["artifact_refs"]


class Dispatch:
    def __init__(self, receipt=None, error=False):
        self.receipt = receipt or A2ADispatchReceipt(
            outcome="accepted", task_id="task-1", context_id="context-1"
        )
        self.error = error
        self.commands = []

    async def dispatch(self, command):
        self.commands.append(command)
        if self.error:
            raise AmbiguousRemoteEffectError("ambiguous")
        return self.receipt

    async def inspect(self, command):
        return self.receipt

    async def continue_task(self, command):
        return self.receipt

    async def inspect_continuation(self, command):
        return self.receipt

    async def cancel(self, command):
        return self.receipt

    async def inspect_cancellation(self, command):
        return self.receipt

    def is_command_retry_safe(self, transport_kind):
        return True


async def setup(
    *,
    checkpointed=True,
    auth="authorized",
    dispatch=None,
    direct_capabilities=None,
    ledger=None,
    hitl=None,
):
    ledger = ledger or InMemoryAgentCallLedgerStore()
    snapshots = InMemoryPreparedInvocationSnapshotReader()
    snapshot = prepared()
    if direct_capabilities is not None:
        snapshot = snapshot.model_copy(
            update={
                "binding": snapshot.binding.model_copy(
                    update={"direct_capabilities": direct_capabilities}
                )
            }
        )
    snapshots.put(snapshot)
    epochs = InMemoryRoomEpochStore()
    await epochs.activate("room-1", "create-1", activated_at=NOW)
    authorization = Authorization(auth)
    transport = dispatch or Dispatch()
    ingress = A2AObservationIngress(
        inbox=InMemoryObservationInboxStore(),
        conflicts=InMemoryObservationConflictStore(),
        ledger=ledger,
        authenticator=RejectExternalIngressAuthenticator(),
    )
    hitl = hitl or InMemoryHITLApplicationPort()
    runtime = A2AAgentToolRuntime(
        ledger=ledger,
        prepared_reader=snapshots,
        checkpoint_reader=Checkpoints(checkpointed),
        authorization=authorization,
        room_epochs=epochs,
        resources=Resources(),
        dispatch=transport,
        observations=ingress,
        terminal_finalizer=TerminalInteractionFinalizer(hitl),
    )
    return runtime, ledger, authorization, transport, ingress


async def test_accept_is_durable_idempotent_and_has_no_transport_effect():
    runtime, ledger, authorization, dispatch, _ = await setup()
    accepted = await runtime.accept(invocation())
    runtime.prepared_reader = InMemoryPreparedInvocationSnapshotReader()
    replay = await runtime.accept(invocation())
    assert accepted == replay
    assert authorization.calls == 1
    assert dispatch.commands == []
    assert (await ledger.load("run-1", "call-1")) is not None


async def test_accept_denial_happens_before_any_ledger_record():
    runtime, ledger, _, _, _ = await setup(auth="denied")
    try:
        await runtime.accept(invocation())
    except A2AAcceptanceDenied:
        pass
    else:
        raise AssertionError("authorization denial was accepted")
    assert await ledger.load("run-1", "call-1") is None


async def test_execute_never_dispatches_without_generic_acceptance_receipt():
    runtime, ledger, _, dispatch, _ = await setup(checkpointed=False)
    accepted = await runtime.accept(invocation())
    outcome = await runtime.execute(invocation(), accepted, signal=NeverCancelled())
    assert isinstance(outcome, ToolSuspension)
    assert dispatch.commands == []
    assert (await ledger.load("run-1", "call-1")).state == "accepted"


async def test_execute_dispatches_stable_command_and_suspends_for_remote_work():
    runtime, ledger, _, dispatch, _ = await setup()
    accepted = await runtime.accept(invocation())
    outcome = await runtime.execute(invocation(), accepted, signal=NeverCancelled())
    assert isinstance(outcome, ToolSuspension)
    assert len(dispatch.commands) == 1
    persisted = await ledger.load("run-1", "call-1")
    assert persisted.state == "working"
    assert dispatch.commands[0].command_id == persisted.dispatch_command_id


async def test_frozen_binding_selects_stream_then_sync_then_poll_capability():
    for capabilities, expected in (
        (["sync", "stream", "poll"], "stream"),
        (["sync", "poll"], "sync"),
        (["poll"], "poll"),
    ):
        runtime, ledger, _, _, _ = await setup(direct_capabilities=capabilities)
        await runtime.accept(invocation())
        persisted = await ledger.load("run-1", "call-1")
        assert persisted.dispatch_snapshot.direct_mode == expected


class BoundaryFailure:
    def __init__(self, error):
        self.error = error

    async def is_acceptance_checkpointed(self, *args):
        raise self.error

    async def authorize(self, **kwargs):
        raise self.error

    async def verify_active(self, *args):
        raise self.error

    async def materialize(self, *args, **kwargs):
        raise self.error

    async def dispatch(self, command):
        raise self.error


@pytest.mark.parametrize(
    "boundary,error",
    [
        ("checkpoint", ValueError("checkpoint contract defect")),
        ("authorization", TypeError("authorization programming defect")),
        ("epoch", AssertionError("epoch invariant defect")),
        ("resource", ValueError("resource contract defect")),
        ("dispatch", RuntimeError("dispatch programming defect")),
    ],
)
async def test_programming_defects_surface_from_execute_boundaries(boundary, error):
    runtime, ledger, _, _, _ = await setup()
    accepted = await runtime.accept(invocation())
    failure = BoundaryFailure(error)
    if boundary == "checkpoint":
        runtime.checkpoint_reader = failure
    elif boundary == "authorization":
        runtime.authorization = failure
    elif boundary == "epoch":
        runtime.room_epochs = failure
    elif boundary == "resource":
        runtime.resources = failure
    else:
        runtime.dispatch = failure
    with pytest.raises(type(error), match=str(error)):
        await runtime.execute(invocation(), accepted, signal=NeverCancelled())
    if boundary == "dispatch":
        assert (await ledger.load("run-1", "call-1")).state == "dispatching"


@pytest.mark.parametrize(
    "boundary,error",
    [
        ("checkpoint", RecoverableCheckpointError("checkpoint unavailable")),
        ("authorization", RecoverableAuthorizationError("auth unavailable")),
        ("epoch", RecoverableEpochError("epoch unavailable")),
        ("resource", RecoverableResourceError("resource unavailable")),
        ("dispatch", AmbiguousRemoteEffectError("dispatch ambiguous")),
    ],
)
async def test_typed_recoverable_boundary_failures_suspend(boundary, error):
    runtime, ledger, _, _, _ = await setup()
    accepted = await runtime.accept(invocation())
    failure = BoundaryFailure(error)
    if boundary == "checkpoint":
        runtime.checkpoint_reader = failure
    elif boundary == "authorization":
        runtime.authorization = failure
    elif boundary == "epoch":
        runtime.room_epochs = failure
    elif boundary == "resource":
        runtime.resources = failure
    else:
        runtime.dispatch = failure
    assert isinstance(
        await runtime.execute(invocation(), accepted, signal=NeverCancelled()),
        ToolSuspension,
    )
    if boundary == "dispatch":
        assert (await ledger.load("run-1", "call-1")).state == ("delivery_uncertain")


async def test_transport_exception_becomes_delivery_uncertain_suspension():
    runtime, ledger, _, _, _ = await setup(dispatch=Dispatch(error=True))
    accepted = await runtime.accept(invocation())
    outcome = await runtime.execute(invocation(), accepted, signal=NeverCancelled())
    assert isinstance(outcome, ToolSuspension)
    assert (await ledger.load("run-1", "call-1")).state == "delivery_uncertain"


class RuntimeFinalizerFaultHITL(InMemoryHITLApplicationPort):
    def __init__(self, mode):
        super().__init__()
        self.mode = mode
        self.abandon_calls = 0
        self.effects = 0
        self.failed = False

    async def abandon(self, interaction_id, *, call_record_id, reason):
        self.abandon_calls += 1
        if self.mode == "absent" and not self.failed:
            self.failed = True
            self._interactions.pop(interaction_id, None)
            self._eligible_interactions.discard(interaction_id)
            return "absent"
        if self.mode == "replayed" and not self.failed:
            self.failed = True
            assert (
                await super().abandon(
                    interaction_id,
                    call_record_id=call_record_id,
                    reason=reason,
                )
                == "accepted"
            )
            self.effects += 1
            return await super().abandon(
                interaction_id,
                call_record_id=call_record_id,
                reason=reason,
            )
        if not self.failed and self.mode in {
            "conflict",
            "error",
            "outage",
            "ack_loss",
        }:
            self.failed = True
            if self.mode == "conflict":
                return "conflict"
            if self.mode == "error":
                return "error"
            if self.mode == "outage":
                raise RecoverableCheckpointError("HITL owner unavailable")
            outcome = await super().abandon(
                interaction_id,
                call_record_id=call_record_id,
                reason=reason,
            )
            assert outcome == "accepted"
            self.effects += 1
            raise RecoverableCheckpointError("HITL acknowledgement lost")
        outcome = await super().abandon(
            interaction_id,
            call_record_id=call_record_id,
            reason=reason,
        )
        if outcome == "accepted":
            self.effects += 1
        return outcome


def _interaction(event_kind):
    return A2AInteractionSpec.model_validate(
        {
            "schema_version": 1,
            "interaction_id": "interaction-1",
            "questions": [
                {
                    "question_id": "question-1",
                    "interaction_kind": (
                        "questionnaire"
                        if event_kind == "input_required"
                        else "auth_challenge"
                    ),
                    "prompt": "Continue?",
                    "answer_kind": (
                        "confirmation"
                        if event_kind == "input_required"
                        else "authorization_result"
                    ),
                }
            ],
        }
    )


def _terminal_result(record, status):
    return ToolResult(
        call_id=record.invocation_id,
        tool_name=record.tool_name,
        status=status,
        content=[TextPart(text="terminal winner")],
        artifact_refs=[],
        error_code=status,
        error_message=status,
    )


async def _persist_attached_terminal(
    ledger, owner, record, *, event_kind, terminal_status
):
    while record.state != "working":
        next_state = {
            "accepted": "ready_to_dispatch",
            "ready_to_dispatch": "dispatching",
            "dispatching": "working",
        }[record.state]
        candidate = transition_call(record, to_state=next_state, updated_at=NOW)
        assert (
            await ledger.cas(candidate, expected_state_version=record.state_version)
            == "accepted"
        )
        record = candidate
    pending = transition_call(record, to_state="continuation_pending", updated_at=NOW)
    assert (
        await ledger.cas(pending, expected_state_version=record.state_version)
        == "accepted"
    )
    attached = transition_call(
        pending,
        to_state=event_kind,
        updated_at=NOW,
        pending_interaction_id="interaction-1",
        interaction_revision=1,
        interaction_fingerprint="fingerprint-1",
    )
    assert (
        await ledger.cas(attached, expected_state_version=pending.state_version)
        == "accepted"
    )
    await owner.create_or_replay(
        call=attached,
        interaction=_interaction(event_kind),
        interaction_fingerprint="fingerprint-1",
    )
    result = _terminal_result(attached, terminal_status)
    terminal = transition_call(
        attached,
        to_state=terminal_status,
        updated_at=NOW,
        terminal_result=result,
        terminal_result_digest=sha256(result.model_dump_json().encode()).hexdigest(),
    )
    assert (
        await ledger.cas(terminal, expected_state_version=attached.state_version)
        == "accepted"
    )
    return terminal


class RuntimeTerminalWinnerLedger(InMemoryAgentCallLedgerStore):
    def __init__(self, owner, *, event_kind, terminal_status):
        super().__init__()
        self.owner = owner
        self.event_kind = event_kind
        self.terminal_status = terminal_status
        self.raced = False
        self.durable_winner = None

    async def cas(self, record, *, expected_state_version):
        if not self.raced and record.state == "working":
            self.raced = True
            assert (
                await super().cas(record, expected_state_version=expected_state_version)
                == "accepted"
            )
            self.durable_winner = await _persist_attached_terminal(
                self,
                self.owner,
                record,
                event_kind=self.event_kind,
                terminal_status=self.terminal_status,
            )
            return "conflict"
        return await super().cas(record, expected_state_version=expected_state_version)


LEGAL_ATTACHED_TERMINALS = [
    ("input_required", "canceled"),
    ("input_required", "expired"),
    ("auth_required", "canceled"),
    ("auth_required", "rejected"),
    ("auth_required", "expired"),
]
FINALIZER_OUTCOMES = [
    "accepted",
    "replayed",
    "absent",
    "conflict",
    "error",
    "outage",
    "ack_loss",
]


async def _assert_closed_and_unanswerable(owner):
    assert owner.read_interaction_for_test("interaction-1") is None
    with pytest.raises(KeyError):
        await owner.answer(
            interaction_id="interaction-1",
            interaction_revision=1,
            route_fingerprint="fingerprint-1",
            answers=[],
            authenticated_answerer_id="user-1",
            verified_auth_reference_digests=[],
            verified_auth_references=[],
        )


@pytest.mark.parametrize("event_kind,terminal_status", LEGAL_ATTACHED_TERMINALS)
@pytest.mark.parametrize("close_mode", FINALIZER_OUTCOMES)
async def test_runtime_terminal_replay_requires_exact_hitl_finalization(
    event_kind, terminal_status, close_mode
):
    owner = RuntimeFinalizerFaultHITL(close_mode)
    runtime, ledger, _, dispatch, _ = await setup(hitl=owner)
    acceptance = await runtime.accept(invocation())
    accepted = await ledger.load("run-1", "call-1")
    winner = await _persist_attached_terminal(
        ledger,
        owner,
        accepted,
        event_kind=event_kind,
        terminal_status=terminal_status,
    )
    assert owner.read_interaction_for_test("interaction-1") is not None

    first = await runtime.execute(invocation(), acceptance, signal=NeverCancelled())
    if close_mode in {"conflict", "error", "outage", "ack_loss"}:
        assert isinstance(first, ToolSuspension)
        if close_mode != "ack_loss":
            assert owner.read_interaction_for_test("interaction-1") is not None
    else:
        assert isinstance(first, ToolResult)
        assert first.status == terminal_status
        await _assert_closed_and_unanswerable(owner)
    assert await ledger.load("run-1", "call-1") == winner

    retry = await runtime.execute(invocation(), acceptance, signal=NeverCancelled())
    assert isinstance(retry, ToolResult)
    assert retry.status == terminal_status
    assert await ledger.load("run-1", "call-1") == winner
    await _assert_closed_and_unanswerable(owner)
    assert owner.effects <= 1
    assert dispatch.commands == []


@pytest.mark.parametrize("event_kind,terminal_status", LEGAL_ATTACHED_TERMINALS)
@pytest.mark.parametrize("close_mode", FINALIZER_OUTCOMES)
async def test_runtime_competing_terminal_cas_winner_requires_hitl_finalization(
    event_kind, terminal_status, close_mode
):
    owner = RuntimeFinalizerFaultHITL(close_mode)
    ledger = RuntimeTerminalWinnerLedger(
        owner, event_kind=event_kind, terminal_status=terminal_status
    )
    runtime, ledger, _, dispatch, _ = await setup(ledger=ledger, hitl=owner)
    acceptance = await runtime.accept(invocation())

    first = await runtime.execute(invocation(), acceptance, signal=NeverCancelled())
    if close_mode in {"conflict", "error", "outage", "ack_loss"}:
        assert isinstance(first, ToolSuspension)
        if close_mode != "ack_loss":
            assert owner.read_interaction_for_test("interaction-1") is not None
    else:
        assert isinstance(first, ToolResult)
        assert first.status == terminal_status
        await _assert_closed_and_unanswerable(owner)
    winner = ledger.durable_winner
    assert winner is not None
    assert await ledger.load("run-1", "call-1") == winner

    retry = await runtime.execute(invocation(), acceptance, signal=NeverCancelled())
    assert isinstance(retry, ToolResult)
    assert retry.status == terminal_status
    assert await ledger.load("run-1", "call-1") == winner
    await _assert_closed_and_unanswerable(owner)
    assert owner.effects <= 1
    assert len(dispatch.commands) == 1


async def test_runtime_terminal_finalizer_programming_defect_surfaces():
    class ProgrammingDefectHITL(InMemoryHITLApplicationPort):
        async def abandon(self, *args, **kwargs):
            raise ValueError("HITL owner contract defect")

    owner = ProgrammingDefectHITL()
    runtime, ledger, _, _, _ = await setup(hitl=owner)
    acceptance = await runtime.accept(invocation())
    accepted = await ledger.load("run-1", "call-1")
    await _persist_attached_terminal(
        ledger,
        owner,
        accepted,
        event_kind="input_required",
        terminal_status="canceled",
    )

    with pytest.raises(ValueError, match="HITL owner contract defect"):
        await runtime.execute(invocation(), acceptance, signal=NeverCancelled())


async def test_inline_terminal_evidence_is_inboxed_before_result():
    observation = NormalizedA2AObservation(
        observation_id="observation-1",
        source_kind="direct",
        source_identity="direct:event-1",
        binding_scope="endpoint",
        event_kind="terminal",
        status="completed",
        observed_at=NOW,
        task_id="task-1",
        context_id="context-1",
    )
    runtime, ledger, _, _, ingress = await setup(
        dispatch=Dispatch(
            A2ADispatchReceipt(outcome="terminal", terminal_observation=observation)
        )
    )
    accepted = await runtime.accept(invocation())
    outcome = await runtime.execute(invocation(), accepted, signal=NeverCancelled())
    assert isinstance(outcome, ToolResult)
    assert outcome.status == "completed"
    inbox = await ingress.inbox.load("observation-1")
    assert inbox is not None
    assert inbox.delivery_route == "executor"
    assert (await ledger.load("run-1", "call-1")).state == "completed"
