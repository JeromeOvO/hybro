from __future__ import annotations

import json
from hashlib import sha256

import pytest

from common.dto.hitl import A2AInteractionSpec
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
    A2AObservationProcessor,
    ObservationIngressError,
    RejectExternalIngressAuthenticator,
)
from execution.orchestrator.a2a_runtime.ledger import (
    apply_observation,
    ownership_alias_keys,
    transition_call,
)
from execution.orchestrator.a2a_runtime.models import (
    A2AObservationInboxRecord,
    A2AOwnershipAlias,
    NormalizedA2AObservation,
)
from execution.orchestrator.models import TextPart, ToolResult

from ._orchestrator_a2a_helpers import ledger_record
from ._orchestrator_helpers import NOW


class Authenticator:
    def __init__(self):
        self.called = False

    async def authenticate(self, **kwargs):
        self.called = True
        assert kwargs["body"]
        return "source"


class Artifacts:
    async def materialize(self, *args, **kwargs):
        return []

    async def materialize_inbound_artifacts(self, **kwargs):
        return list(kwargs["artifact_refs"])


class Checkpoints:
    async def is_suspension_checkpointed(self, *args):
        return True

    async def is_acceptance_checkpointed(self, *args):
        return True


class SuspendedAtInputRequiredOnly:
    async def is_suspension_checkpointed(self, _run_id, _invocation_id, status):
        return status == "input_required"

    async def is_acceptance_checkpointed(self, *args):
        return True


class Outcomes:
    async def has_processed_observation(self, *args):
        return True

    async def is_outcome_checkpointed(self, *args):
        return True


class HITLAttachRaceLedger(InMemoryAgentCallLedgerStore):
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
        if self.site == "initial":
            return (
                record.state == "continuation_pending"
                and record.pending_interaction_id is None
            )
        return record.state in {"input_required", "auth_required"}

    async def cas(self, record, *, expected_state_version):
        if not self.raced and self._matches(record):
            if self.site == "initial":
                assert (
                    await super().cas(
                        record, expected_state_version=expected_state_version
                    )
                    == "accepted"
                )
                current = record
            else:
                current = await self.load_by_record_id(record.call_record_id)
                assert current is not None
            winner = apply_observation(
                current,
                NormalizedA2AObservation(
                    observation_id=f"hitl-race-{self.site}-{self.terminal_status}",
                    call_record_id=current.call_record_id,
                    source_kind="inspection",
                    source_identity=f"hitl-race:{self.site}:{self.terminal_status}",
                    binding_scope=current.endpoint_scope_digest,
                    event_kind="terminal",
                    observed_at=NOW,
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


class ActivationTerminalHITL(InMemoryHITLApplicationPort):
    def __init__(self, *, ledger, terminal_status):
        super().__init__()
        self.ledger = ledger
        self.terminal_status = terminal_status
        self.injected = False

    async def activate(self, interaction_id, **kwargs):
        outcome = await super().activate(interaction_id, **kwargs)
        if not self.injected:
            current = await self.ledger.load_by_record_id(kwargs["call_record_id"])
            winner = apply_observation(
                current,
                NormalizedA2AObservation(
                    observation_id=f"activation-race-{self.terminal_status}",
                    call_record_id=current.call_record_id,
                    source_kind="inspection",
                    source_identity=f"activation-race:{self.terminal_status}",
                    binding_scope=current.endpoint_scope_digest,
                    event_kind="terminal",
                    observed_at=NOW,
                    task_id=current.a2a_task_id,
                    context_id=current.a2a_context_id,
                    status=self.terminal_status,
                ),
                recent_limit=current.runtime_policy.recent_observation_id_limit,
            )
            assert (
                await self.ledger.cas(
                    winner, expected_state_version=current.state_version
                )
                == "accepted"
            )
            self.injected = True
        return outcome


class ActivationEpochStore(InMemoryRoomEpochStore):
    def __init__(self):
        super().__init__()
        self.deactivate_on_verify = False

    async def verify_active(self, room_id, epoch):
        if self.deactivate_on_verify:
            self.deactivate_on_verify = False
            assert (
                await self.deactivate(
                    room_id, epoch, "delete-activation", deactivated_at=NOW
                )
            )[0] == "accepted"
        return await super().verify_active(room_id, epoch)


class ActivationEpochHITL(InMemoryHITLApplicationPort):
    def __init__(self, *, epochs, close_mode, loss_site):
        super().__init__()
        self.epochs = epochs
        self.close_mode = close_mode
        self.loss_site = loss_site
        self.deactivated = False
        self.close_failed = False

    async def _deactivate(self):
        assert (
            await self.epochs.deactivate(
                "room-1", 1, "delete-activation", deactivated_at=NOW
            )
        )[0] == "accepted"
        self.deactivated = True

    async def activate(self, interaction_id, **kwargs):
        if not self.deactivated and self.loss_site == "immediately_before":
            await self._deactivate()
        outcome = await super().activate(interaction_id, **kwargs)
        if not self.deactivated and self.loss_site == "during":
            await self._deactivate()
        if not self.deactivated and self.loss_site == "immediately_after":
            self.epochs.deactivate_on_verify = True
            self.deactivated = True
        return outcome

    async def abandon(self, interaction_id, **kwargs):
        if not self.close_failed:
            self.close_failed = True
            if self.close_mode == "absent":
                self._interactions.pop(interaction_id, None)
                self._eligible_interactions.discard(interaction_id)
                return "absent"
            if self.close_mode == "conflict":
                return "conflict"
            if self.close_mode == "error":
                return "error"
            if self.close_mode == "outage":
                raise RecoverableAdapterError("epoch cleanup unavailable")
            if self.close_mode in {"replayed", "ack_loss"}:
                outcome = await super().abandon(interaction_id, **kwargs)
                assert outcome == "accepted"
                if self.close_mode == "replayed":
                    return await super().abandon(interaction_id, **kwargs)
                raise RecoverableAdapterError("epoch cleanup acknowledgement lost")
        return await super().abandon(interaction_id, **kwargs)


class AbandonFaultHITL(InMemoryHITLApplicationPort):
    def __init__(self, mode):
        super().__init__()
        self.mode = mode
        self.failed = False

    async def abandon(self, interaction_id, **kwargs):
        if not self.failed:
            self.failed = True
            if self.mode == "absent":
                self._interactions.pop(interaction_id, None)
                self._eligible_interactions.discard(interaction_id)
                return "absent"
            if self.mode == "conflict":
                return "conflict"
            if self.mode == "error":
                return "error"
            if self.mode == "outage":
                raise RecoverableAdapterError("abandon owner unavailable")
            if self.mode in {"replayed", "ack_loss"}:
                outcome = await super().abandon(interaction_id, **kwargs)
                assert outcome == "accepted"
                if self.mode == "replayed":
                    return await super().abandon(interaction_id, **kwargs)
                raise RecoverableAdapterError("abandon acknowledgement lost")
        return await super().abandon(interaction_id, **kwargs)


class Sink:
    def __init__(self):
        self.values = []

    async def deliver(self, run_id, observation):
        self.values.append((run_id, observation))


def observation(**updates):
    values = {
        "observation_id": "observation-1",
        "source_kind": "webhook",
        "source_identity": "webhook:event-1",
        "binding_scope": "endpoint",
        "event_kind": "terminal",
        "status": "completed",
        "observed_at": NOW,
        "task_id": "task-1",
        "context_id": "context-1",
    }
    values.update(updates)
    return NormalizedA2AObservation(**values)


async def lineage_ledger(
    ledger=None,
) -> InMemoryAgentCallLedgerStore:
    ledger = ledger or InMemoryAgentCallLedgerStore()
    record = transition_call(
        ledger_record(), to_state="ready_to_dispatch", updated_at=NOW
    )
    record = transition_call(record, to_state="dispatching", updated_at=NOW)
    aliases = [A2AOwnershipAlias(kind="task", value="task-1", binding_scope="endpoint")]
    record = transition_call(
        record,
        to_state="working",
        updated_at=NOW,
        ownership_aliases=aliases,
        ownership_alias_keys=ownership_alias_keys(aliases),
        a2a_task_id="task-1",
        a2a_context_id="context-1",
    )
    await ledger.insert(record)
    return ledger


async def test_ingress_authenticates_bounds_and_inserts_before_ack():
    authenticator = Authenticator()
    ledger = await lineage_ledger()
    inbox = InMemoryObservationInboxStore()
    ingress = A2AObservationIngress(
        inbox=inbox,
        conflicts=InMemoryObservationConflictStore(),
        ledger=ledger,
        authenticator=authenticator,
    )
    item = observation()
    body = item.model_dump_json().encode()
    outcome, persisted = await ingress.ingest(
        source_kind="webhook",
        headers={"authorization": "signed"},
        body=body,
        normalize=lambda value: NormalizedA2AObservation.model_validate_json(value),
    )
    assert outcome == "accepted"
    assert authenticator.called
    assert await inbox.load(persisted.observation_id) == persisted


@pytest.mark.parametrize(
    "source_kind", ["direct", "webhook", "relay", "poll", "inspection"]
)
async def test_ingress_rows_are_deletable_before_processing_by_room_epoch(source_kind):
    ledger = await lineage_ledger()
    inbox = InMemoryObservationInboxStore()
    conflicts = InMemoryObservationConflictStore()
    ingress = A2AObservationIngress(
        inbox=inbox,
        conflicts=conflicts,
        ledger=ledger,
        authenticator=RejectExternalIngressAuthenticator(),
    )
    item = observation(
        source_kind=source_kind,
        source_identity=f"{source_kind}:retention",
        call_record_id=ledger_record().call_record_id,
    )
    _, persisted = await ingress.record(item)
    assert persisted.room_id == "room-1" and persisted.room_epoch == 1
    assert await inbox.delete_by_epoch("room-1", 1) == 1
    assert await inbox.load(item.observation_id) is None


async def test_conflict_rows_are_deletable_before_processing_by_room_epoch():
    ledger = await lineage_ledger()
    inbox = InMemoryObservationInboxStore()
    conflicts = InMemoryObservationConflictStore()
    ingress = A2AObservationIngress(
        inbox=inbox,
        conflicts=conflicts,
        ledger=ledger,
        authenticator=RejectExternalIngressAuthenticator(),
    )
    first = observation(call_record_id=ledger_record().call_record_id)
    await ingress.record(first)
    assert (await ingress.record(first.model_copy(update={"status": "failed"})))[0] == (
        "conflict"
    )
    row = (await conflicts.list_for_source(first.source_identity))[0]
    assert row.room_id == "room-1" and row.room_epoch == 1
    assert await conflicts.delete_by_epoch("room-1", 1) == 1
    assert await inbox.delete_by_epoch("room-1", 1) == 1


async def test_unresolved_evidence_is_rejected_before_private_persistence():
    ledger = InMemoryAgentCallLedgerStore()
    inbox = InMemoryObservationInboxStore()
    ingress = A2AObservationIngress(
        inbox=inbox,
        conflicts=InMemoryObservationConflictStore(),
        ledger=ledger,
        authenticator=RejectExternalIngressAuthenticator(),
    )
    item = observation(task_id="unknown", context_id=None)
    with pytest.raises(ObservationIngressError, match="lineage is unresolved"):
        await ingress.record(item)
    assert await inbox.load(item.observation_id) is None


async def test_same_identity_different_digest_creates_separate_conflict_record():
    ledger = await lineage_ledger()
    inbox = InMemoryObservationInboxStore()
    conflicts = InMemoryObservationConflictStore()
    ingress = A2AObservationIngress(
        inbox=inbox,
        conflicts=conflicts,
        ledger=ledger,
        authenticator=RejectExternalIngressAuthenticator(),
    )
    first = observation()
    assert (await ingress.record(first))[0] == "accepted"
    assert (await ingress.record(first))[0] == "replayed"
    changed = first.model_copy(update={"status": "failed"})
    outcome, accepted = await ingress.record(changed)
    assert outcome == "conflict"
    assert accepted.observation == first
    rows = await conflicts.list_for_source(first.source_identity)
    assert len(rows) == 1
    assert (await inbox.load(first.observation_id)).observation == first


async def setup_processor(item, *, ledger=None, hitl=None, epochs=None):
    inbox = InMemoryObservationInboxStore()
    conflicts = InMemoryObservationConflictStore()
    ledger = ledger or await lineage_ledger()
    ingress = A2AObservationIngress(
        inbox=inbox,
        conflicts=conflicts,
        ledger=ledger,
        authenticator=RejectExternalIngressAuthenticator(),
    )
    await ingress.record(item)
    epochs = epochs or InMemoryRoomEpochStore()
    if await epochs.read_active("room-1") is None:
        await epochs.activate("room-1", "create-1", activated_at=NOW)
    sink = Sink()
    hitl = hitl or InMemoryHITLApplicationPort()
    processor = A2AObservationProcessor(
        inbox=inbox,
        conflicts=conflicts,
        ledger=ledger,
        room_epochs=epochs,
        artifacts=Artifacts(),
        hitl=hitl,
        sink=sink,
        checkpoint_reader=Checkpoints(),
        outcome_reader=Outcomes(),
    )
    return processor, inbox, ledger, sink, hitl, ingress


async def test_processor_applies_terminal_once_and_uses_run_addressed_sink():
    processor, inbox, ledger, sink, _, _ = await setup_processor(observation())
    assert await processor.process("observation-1") == "accepted"
    assert (await inbox.load("observation-1")).state == "completed"
    assert (await ledger.load("run-1", "call-1")).state == "completed"
    assert sink.values[0][0] == "run-1"
    assert await processor.process("observation-1") == "replayed"
    assert len(sink.values) == 1


class _LateSuspensionOutcomes:
    def __init__(self):
        self.checkpointed = False

    async def is_outcome_checkpointed(self, *args):
        return self.checkpointed

    async def has_processed_observation(self, *args):
        return False


class _ApplyingSink(Sink):
    def __init__(self, outcomes):
        super().__init__()
        self._outcomes = outcomes

    async def deliver(self, run_id, observation):
        self.values.append((run_id, observation))
        # The kernel applies the delivered observation idempotently.
        self._outcomes.checkpointed = True


async def test_executor_observation_reroutes_to_sink_after_late_suspension():
    """A terminal observation first processed before the Run's suspension was
    checkpointed is routed to the live executor. If the executor has finished
    the call and the Run is suspended now, the processor must re-deliver via
    the sink or the row (and the Run) deadlocks in outcome_pending forever.
    """
    ledger = await lineage_ledger()
    record = await ledger.load("run-1", "call-1")
    result = ToolResult(
        call_id="call-1",
        tool_name=record.tool_name,
        status="completed",
        content=[TextPart(text="done")],
        artifact_refs=[],
        error_code=None,
        error_message=None,
    )
    outcome_digest = sha256(result.model_dump_json().encode()).hexdigest()
    terminal = transition_call(
        record,
        to_state="completed",
        updated_at=NOW,
        terminal_result=result,
        terminal_result_digest=outcome_digest,
    )
    assert (
        await ledger.cas(terminal, expected_state_version=record.state_version)
        == "accepted"
    )

    inbox = InMemoryObservationInboxStore()
    obs = observation(status="completed")
    stuck = A2AObservationInboxRecord(
        observation_id=obs.observation_id,
        source_kind=obs.source_kind,
        source_identity=obs.source_identity,
        payload_digest="digest",
        received_at=NOW,
        binding_scope="endpoint",
        room_id="room-1",
        room_epoch=1,
        event_kind=obs.event_kind,
        observation=obs,
        call_record_id=record.call_record_id,
        task_id=obs.task_id,
        context_id=obs.context_id,
        state="outcome_pending",
        delivery_route="executor",
        delivery_state="checkpointed",
        outcome_digest=outcome_digest,
    )
    assert await inbox.insert(stuck) == "accepted"
    epochs = InMemoryRoomEpochStore()
    await epochs.activate("room-1", "create-1", activated_at=NOW)
    outcomes = _LateSuspensionOutcomes()
    sink = _ApplyingSink(outcomes)
    processor = A2AObservationProcessor(
        inbox=inbox,
        conflicts=InMemoryObservationConflictStore(),
        ledger=ledger,
        room_epochs=epochs,
        artifacts=Artifacts(),
        hitl=InMemoryHITLApplicationPort(),
        sink=sink,
        checkpoint_reader=Checkpoints(),
        outcome_reader=outcomes,
    )

    assert await processor.process(obs.observation_id) == "accepted"
    stored = await inbox.load(obs.observation_id)
    assert stored.state == "completed"
    assert stored.delivery_route == "observation_sink"
    assert len(sink.values) == 1
    assert sink.values[0][0] == "run-1"


async def test_executor_observation_reroutes_to_sink_after_late_hitl_suspension():
    """A terminal continuation result must re-enter the kernel when the durable
    suspension checkpoint is HITL-owned input_required rather than
    waiting_external.
    """
    ledger = await lineage_ledger()
    record = await ledger.load("run-1", "call-1")
    result = ToolResult(
        call_id="call-1",
        tool_name=record.tool_name,
        status="completed",
        content=[TextPart(text="done after hitl")],
        artifact_refs=[],
        error_code=None,
        error_message=None,
    )
    outcome_digest = sha256(result.model_dump_json().encode()).hexdigest()
    terminal = transition_call(
        record,
        to_state="completed",
        updated_at=NOW,
        terminal_result=result,
        terminal_result_digest=outcome_digest,
    )
    assert await ledger.cas(terminal, expected_state_version=record.state_version) == (
        "accepted"
    )

    inbox = InMemoryObservationInboxStore()
    obs = observation(status="completed")
    stuck = A2AObservationInboxRecord(
        observation_id=obs.observation_id,
        source_kind=obs.source_kind,
        source_identity=obs.source_identity,
        payload_digest="digest",
        received_at=NOW,
        binding_scope="endpoint",
        room_id="room-1",
        room_epoch=1,
        event_kind=obs.event_kind,
        observation=obs,
        call_record_id=record.call_record_id,
        task_id=obs.task_id,
        context_id=obs.context_id,
        state="outcome_pending",
        delivery_route="executor",
        delivery_state="checkpointed",
        outcome_digest=outcome_digest,
    )
    assert await inbox.insert(stuck) == "accepted"
    epochs = InMemoryRoomEpochStore()
    await epochs.activate("room-1", "create-1", activated_at=NOW)
    outcomes = _LateSuspensionOutcomes()
    sink = _ApplyingSink(outcomes)
    processor = A2AObservationProcessor(
        inbox=inbox,
        conflicts=InMemoryObservationConflictStore(),
        ledger=ledger,
        room_epochs=epochs,
        artifacts=Artifacts(),
        hitl=InMemoryHITLApplicationPort(),
        sink=sink,
        checkpoint_reader=SuspendedAtInputRequiredOnly(),
        outcome_reader=outcomes,
    )

    assert await processor.process(obs.observation_id) == "accepted"
    stored = await inbox.load(obs.observation_id)
    assert stored.state == "completed"
    assert stored.delivery_route == "observation_sink"
    assert len(sink.values) == 1
    assert sink.values[0][0] == "run-1"


def interaction_spec(event_kind="input_required"):
    if event_kind == "auth_required":
        question = {
            "question_id": "q1",
            "interaction_kind": "auth_challenge",
            "prompt": "Authenticate",
            "answer_kind": "authorization_result",
        }
    else:
        question = {
            "question_id": "q1",
            "interaction_kind": "questionnaire",
            "prompt": "Which option?",
            "answer_kind": "single_choice",
            "choices": ["a", "b"],
        }
    return {
        "schema_version": 1,
        "interaction_id": "interaction-1",
        "questions": [question],
    }


def interaction_fingerprint(event_kind):
    spec = A2AInteractionSpec.model_validate(interaction_spec(event_kind))
    return sha256(
        json.dumps(
            spec.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


async def test_typed_input_state_is_durable_before_suspension_delivery():
    spec = interaction_spec()
    item = observation(
        event_kind="input_required",
        status=None,
        interaction_spec=spec,
    )
    processor, inbox, ledger, sink, hitl, _ = await setup_processor(item)
    assert await processor.process("observation-1") == "accepted"
    persisted = await ledger.load("run-1", "call-1")
    assert persisted.state == "input_required"
    assert persisted.pending_interaction_id == "interaction-1"
    assert hitl.read_interaction_for_test("interaction-1") is not None
    assert sink.values[0][1].outcome.status == "input_required"


@pytest.mark.parametrize("event_kind", ["input_required", "auth_required"])
@pytest.mark.parametrize("site", ["initial", "attach"])
@pytest.mark.parametrize("reported_outcome", ["conflict", "error"])
@pytest.mark.parametrize("terminal_status", ["failed", "canceled", "expired"])
async def test_hitl_attach_cas_race_converges_to_terminal_without_suspension(
    event_kind, site, reported_outcome, terminal_status
):
    ledger = HITLAttachRaceLedger(
        site=site,
        terminal_status=terminal_status,
        reported_outcome=reported_outcome,
    )
    await lineage_ledger(ledger)
    item = observation(
        event_kind=event_kind,
        status=None,
        interaction_spec=interaction_spec(event_kind),
    )
    processor, inbox, ledger, sink, hitl, _ = await setup_processor(item, ledger=ledger)

    first = await processor.process(item.observation_id)
    persisted = await ledger.load_by_record_id(ledger_record().call_record_id)
    retry = await processor.process(item.observation_id)

    assert first == "accepted"
    assert retry == "replayed"
    assert persisted == ledger.durable_winner
    assert persisted.state == terminal_status
    assert (await inbox.load(item.observation_id)).state == "completed"
    assert sink.values == []
    assert hitl.read_interaction_for_test("interaction-1") is None
    assert hitl.is_abandoned_for_test("interaction-1") is (site == "attach")


@pytest.mark.parametrize("site", ["initial", "attach"])
@pytest.mark.parametrize("winner_visibility", ["missing", "unloadable"])
async def test_hitl_attach_unclassifiable_winner_remains_retryable(
    site, winner_visibility
):
    ledger = HITLAttachRaceLedger(
        site=site,
        terminal_status="canceled",
        reported_outcome="conflict",
        winner_visibility=winner_visibility,
    )
    await lineage_ledger(ledger)
    item = observation(
        event_kind="input_required",
        status=None,
        interaction_spec=interaction_spec(),
    )
    processor, inbox, ledger, sink, hitl, _ = await setup_processor(item, ledger=ledger)

    assert await processor.process(item.observation_id) == "retryable"
    assert (await inbox.load(item.observation_id)).state == "pending"
    assert hitl.read_interaction_for_test("interaction-1") is None
    assert await processor.process(item.observation_id) == "accepted"
    assert await processor.process(item.observation_id) == "replayed"
    persisted = await ledger.load_by_record_id(ledger_record().call_record_id)
    assert persisted.state == "canceled"
    assert (await inbox.load(item.observation_id)).state == "completed"
    assert sink.values == []
    assert hitl.read_interaction_for_test("interaction-1") is None
    assert hitl.is_abandoned_for_test("interaction-1") is (site == "attach")


async def assert_interaction_rejects_answers(hitl):
    with pytest.raises(KeyError):
        await hitl.answer(
            interaction_id="interaction-1",
            interaction_revision=1,
            route_fingerprint="unused",
            answers=[],
            authenticated_answerer_id="user-1",
            verified_auth_reference_digests=[],
            verified_auth_references=[],
        )


@pytest.mark.parametrize(
    "event_kind,terminal_status",
    [
        ("input_required", "canceled"),
        ("input_required", "expired"),
        ("auth_required", "canceled"),
        ("auth_required", "rejected"),
        ("auth_required", "expired"),
    ],
)
@pytest.mark.parametrize(
    "close_mode",
    ["accepted", "replayed", "absent", "conflict", "error", "outage", "ack_loss"],
)
async def test_attached_interaction_closes_on_ordinary_terminal_observation(
    event_kind, terminal_status, close_mode
):
    item = observation(
        event_kind=event_kind,
        status=None,
        interaction_spec=interaction_spec(event_kind),
    )
    hitl = AbandonFaultHITL(close_mode)
    processor, inbox, ledger, sink, hitl, ingress = await setup_processor(
        item, hitl=hitl
    )
    assert await processor.process(item.observation_id) == "accepted"
    assert hitl.read_interaction_for_test("interaction-1") is not None

    terminal = observation(
        observation_id=f"terminal-{terminal_status}",
        source_identity=f"terminal:{terminal_status}",
        event_kind="terminal",
        status=terminal_status,
        interaction_spec=None,
    )
    await ingress.record(terminal)
    first = await processor.process(terminal.observation_id)
    if close_mode in {"conflict", "error", "outage", "ack_loss"}:
        assert first == "retryable"
        assert (await inbox.load(terminal.observation_id)).state == "pending"
        assert await processor.process(terminal.observation_id) == "accepted"
    else:
        assert first == "accepted"
    assert await processor.process(terminal.observation_id) == "replayed"

    persisted = await ledger.load_by_record_id(ledger_record().call_record_id)
    assert persisted.state == terminal_status
    assert (await inbox.load(terminal.observation_id)).state == "completed"
    assert hitl.read_interaction_for_test("interaction-1") is None
    assert hitl.is_abandoned_for_test("interaction-1") is (close_mode != "absent")
    await assert_interaction_rejects_answers(hitl)
    assert sink.values[0][1].outcome.status == event_kind
    assert sink.values[-1][1].outcome.status == terminal_status


@pytest.mark.parametrize(
    "event_kind,terminal_status",
    [
        ("input_required", "canceled"),
        ("input_required", "expired"),
        ("auth_required", "canceled"),
        ("auth_required", "rejected"),
        ("auth_required", "expired"),
    ],
)
async def test_activation_window_terminal_closes_without_stale_suspension(
    event_kind, terminal_status
):
    ledger = await lineage_ledger()
    hitl = ActivationTerminalHITL(ledger=ledger, terminal_status=terminal_status)
    item = observation(
        event_kind=event_kind,
        status=None,
        interaction_spec=interaction_spec(event_kind),
    )
    processor, inbox, ledger, sink, hitl, _ = await setup_processor(
        item, ledger=ledger, hitl=hitl
    )

    assert await processor.process(item.observation_id) == "accepted"
    assert await processor.process(item.observation_id) == "replayed"
    persisted = await ledger.load_by_record_id(ledger_record().call_record_id)
    assert persisted.state == terminal_status
    assert (await inbox.load(item.observation_id)).state == "completed"
    assert sink.values == []
    assert hitl.read_interaction_for_test("interaction-1") is None
    assert hitl.is_abandoned_for_test("interaction-1")
    await assert_interaction_rejects_answers(hitl)


@pytest.mark.parametrize("event_kind", ["input_required", "auth_required"])
@pytest.mark.parametrize("already_attached", [False, True])
@pytest.mark.parametrize(
    "loss_site", ["immediately_before", "during", "immediately_after"]
)
@pytest.mark.parametrize(
    "close_mode",
    ["accepted", "replayed", "absent", "conflict", "error", "outage", "ack_loss"],
)
async def test_activation_epoch_loss_closes_before_inbox_consumption(
    event_kind, already_attached, loss_site, close_mode
):
    ledger = await lineage_ledger()
    epochs = ActivationEpochStore()
    await epochs.activate("room-1", "create-1", activated_at=NOW)
    hitl = ActivationEpochHITL(
        epochs=epochs, close_mode=close_mode, loss_site=loss_site
    )
    item = observation(
        event_kind=event_kind,
        status=None,
        interaction_spec=interaction_spec(event_kind),
    )
    if already_attached:
        working = await ledger.load_by_record_id(ledger_record().call_record_id)
        pending = transition_call(
            working, to_state="continuation_pending", updated_at=NOW
        )
        assert (
            await ledger.cas(pending, expected_state_version=working.state_version)
            == "accepted"
        )
        attached = transition_call(
            pending,
            to_state=event_kind,
            updated_at=NOW,
            pending_interaction_id="interaction-1",
            interaction_revision=1,
            interaction_fingerprint=interaction_fingerprint(event_kind),
        )
        assert (
            await ledger.cas(attached, expected_state_version=pending.state_version)
            == "accepted"
        )
        await hitl.create_or_replay(
            call=attached,
            interaction=A2AInteractionSpec.model_validate(interaction_spec(event_kind)),
            interaction_fingerprint=attached.interaction_fingerprint,
        )
    processor, inbox, _, sink, hitl, _ = await setup_processor(
        item, ledger=ledger, hitl=hitl, epochs=epochs
    )

    first = await processor.process(item.observation_id)
    if close_mode in {"accepted", "replayed", "absent"}:
        assert first == "accepted"
    else:
        assert first == "retryable"
        assert (await inbox.load(item.observation_id)).state == "pending"
        assert await processor.process(item.observation_id) == "accepted"
    assert await processor.process(item.observation_id) == "replayed"
    assert (await inbox.load(item.observation_id)).state == "completed"
    assert sink.values == []
    assert hitl.deactivated is True
    assert hitl.read_interaction_for_test("interaction-1") is None
    assert hitl.is_abandoned_for_test("interaction-1") is (close_mode != "absent")
    await assert_interaction_rejects_answers(hitl)


@pytest.mark.parametrize("mode", ["error", "outage", "ack_loss"])
async def test_attach_winner_abandon_failure_stays_pending_until_converged(mode):
    ledger = HITLAttachRaceLedger(
        site="attach",
        terminal_status="canceled",
        reported_outcome="conflict",
    )
    await lineage_ledger(ledger)
    hitl = AbandonFaultHITL(mode)
    item = observation(
        event_kind="input_required",
        status=None,
        interaction_spec=interaction_spec(),
    )
    processor, inbox, _, sink, hitl, _ = await setup_processor(
        item, ledger=ledger, hitl=hitl
    )

    assert await processor.process(item.observation_id) == "retryable"
    assert (await inbox.load(item.observation_id)).state == "pending"
    assert sink.values == []
    assert await processor.process(item.observation_id) == "accepted"
    assert (await inbox.load(item.observation_id)).state == "completed"
    assert hitl.is_abandoned_for_test("interaction-1")
    assert hitl.read_interaction_for_test("interaction-1") is None


async def test_absent_interaction_abandon_is_idempotent_noop():
    ledger = await lineage_ledger()
    working = await ledger.load_by_record_id(ledger_record().call_record_id)
    pending = transition_call(working, to_state="continuation_pending", updated_at=NOW)
    assert (
        await ledger.cas(pending, expected_state_version=working.state_version)
        == "accepted"
    )
    attached = transition_call(
        pending,
        to_state="input_required",
        updated_at=NOW,
        pending_interaction_id="interaction-1",
        interaction_revision=1,
        interaction_fingerprint="missing-owner-record",
    )
    assert (
        await ledger.cas(attached, expected_state_version=pending.state_version)
        == "accepted"
    )
    item = observation(
        event_kind="terminal",
        status="canceled",
        interaction_spec=None,
    )
    processor, inbox, _, sink, hitl, _ = await setup_processor(item, ledger=ledger)
    assert await processor.process(item.observation_id) == "accepted"
    assert (await inbox.load(item.observation_id)).state == "completed"
    assert sink.values[-1][1].outcome.status == "canceled"
    assert hitl.read_interaction_for_test("interaction-1") is None
