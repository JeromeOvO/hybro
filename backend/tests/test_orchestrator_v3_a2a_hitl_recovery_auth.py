from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from common.dto.hitl import A2AInteractionSpec, HITLQuestionAnswer
from execution.orchestrator.a2a_runtime.errors import RecoverableCheckpointError
from execution.orchestrator.a2a_runtime.hitl import (
    A2AContinuationCoordinator,
    InMemoryHITLApplicationPort,
)
from execution.orchestrator.a2a_runtime.in_memory import (
    InMemoryAgentCallLedgerStore,
    InMemoryAgentToolBindingStore,
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
    ownership_alias_keys,
    transition_call,
)
from execution.orchestrator.a2a_runtime.models import (
    A2ADispatchReceipt,
    NormalizedA2AObservation,
)
from execution.orchestrator.a2a_runtime.recovery import A2AContinuationRecoveryService

from ._orchestrator_v3_a2a_helpers import binding, ledger_record
from ._orchestrator_v3_helpers import NOW


class Authorization:
    async def authorize(self, **kwargs):
        return "authorized"


class Dispatch:
    def __init__(self, *, terminal_status=None):
        self.commands = []
        self.terminal_status = terminal_status

    async def continue_task(self, command):
        self.commands.append(command)
        if self.terminal_status is not None:
            return A2ADispatchReceipt(
                outcome="terminal",
                terminal_observation=NormalizedA2AObservation(
                    observation_id=f"receipt-{self.terminal_status}",
                    call_record_id=command.call_record_id,
                    source_kind="direct",
                    source_identity=f"receipt:{self.terminal_status}",
                    binding_scope="endpoint",
                    event_kind="terminal",
                    observed_at=NOW,
                    task_id=command.task_id,
                    context_id=command.context_id,
                    status=self.terminal_status,
                ),
            )
        return A2ADispatchReceipt(outcome="accepted")

    async def inspect_continuation(self, command):
        return A2ADispatchReceipt(outcome="accepted")


class TrustedAuthReferences:
    def __init__(self):
        self.records = {}
        self.uses = {}

    def issue(
        self,
        reference,
        *,
        answerer_id="user-1",
        call_record_id,
        binding_id="binding-run-1",
        binding_digest="binding-digest-run-1",
        room_id="room-1",
        room_epoch=1,
        expires_at=None,
        **challenge_identity,
    ):
        self.records[reference] = {
            "answerer_id": answerer_id,
            "call_record_id": call_record_id,
            "binding_id": binding_id,
            "binding_digest": binding_digest,
            "room_id": room_id,
            "room_epoch": room_epoch,
            "expires_at": expires_at or datetime.now(UTC) + timedelta(minutes=5),
            **challenge_identity,
        }

    async def verify(self, authorization_reference, **context):
        record = self.records.get(authorization_reference)
        if record is None:
            raise PermissionError("forged authorization reference")
        if record["expires_at"] <= datetime.now(UTC):
            raise PermissionError("expired authorization reference")
        expected = {
            "answerer_id": context["authenticated_answerer_id"],
            "call_record_id": context["call_record_id"],
            "binding_id": context["binding_id"],
            "binding_digest": context["binding_digest"],
            "room_id": context["room_id"],
            "room_epoch": context["room_epoch"],
        }
        expected.update(
            interaction_id=context["interaction_id"],
            interaction_revision=context["interaction_revision"],
            route_fingerprint=context["route_fingerprint"],
            interaction_fingerprint=context["interaction_fingerprint"],
            question_id=context["question_id"],
            challenge_digest=context["challenge_digest"],
            answer_digest=context["answer_digest"],
        )
        if any(
            record.get(key) != value for key, value in expected.items() if key in record
        ):
            raise PermissionError(
                "cross-call or cross-challenge authorization reference"
            )
        use_identity = tuple(sorted(expected.items()))
        prior = self.uses.get(authorization_reference)
        if prior is not None and prior != use_identity:
            raise PermissionError("unsafe authorization reference replay")
        self.uses.setdefault(authorization_reference, use_identity)
        return sha256(f"{authorization_reference}:{use_identity}".encode()).hexdigest()


class CrashAfterAnswerHITL(InMemoryHITLApplicationPort):
    def __init__(self):
        super().__init__()
        self.crash_once = True

    async def answer(self, **kwargs):
        result = await super().answer(**kwargs)
        if self.crash_once:
            self.crash_once = False
            raise OSError("crash after durable HITL answer")
        return result


class ContinuationWinnerRaceLedger(InMemoryAgentCallLedgerStore):
    def __init__(
        self,
        *,
        terminal_status,
        branch,
        reported_outcome,
        winner_visibility="visible",
    ):
        super().__init__()
        self.terminal_status = terminal_status
        self.branch = branch
        self.reported_outcome = reported_outcome
        self.winner_visibility = winner_visibility
        self.hide_next_winner = False
        self.raced = False
        self.durable_winner = None

    async def cas(self, record, *, expected_state_version):
        target_state = (
            "working" if self.branch == "working" else self.terminal_status
        )
        if (
            not self.raced
            and record.continuation_state == "accepted"
            and record.state == target_state
        ):
            current = await self.load_by_record_id(record.call_record_id)
            assert current is not None
            winner = apply_observation(
                current,
                NormalizedA2AObservation(
                    observation_id=f"race-{self.branch}-{self.terminal_status}",
                    call_record_id=current.call_record_id,
                    source_kind="direct",
                    source_identity=f"race:{self.branch}:{self.terminal_status}",
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


class FinalizerFaultHITL(InMemoryHITLApplicationPort):
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
                raise RecoverableCheckpointError("HITL owner unavailable")
            outcome = await super().abandon(interaction_id, **kwargs)
            assert outcome == "accepted"
            if self.mode == "replayed":
                return "replayed"
            raise RecoverableCheckpointError("HITL close acknowledgement lost")
        return await super().abandon(interaction_id, **kwargs)


class ProofTamperingHITL(InMemoryHITLApplicationPort):
    def __init__(self):
        super().__init__()
        self.tamper_proof = False

    async def read_answer_record(self, interaction_id, interaction_revision):
        record = await super().read_answer_record(interaction_id, interaction_revision)
        if (
            not self.tamper_proof
            or record is None
            or not record.verified_auth_references
        ):
            return record
        changed = record.verified_auth_references[0].model_copy(
            update={"proof_digest": "changed-proof-digest"}
        )
        return record.model_copy(update={"verified_auth_references": [changed]})


class FaultLedger(InMemoryAgentCallLedgerStore):
    def __init__(self, *, fail_resuming_cas=False, fail_renew_after_write=False):
        super().__init__()
        self.fail_resuming_cas = fail_resuming_cas
        self.fail_renew_after_write = fail_renew_after_write

    async def cas(self, record, *, expected_state_version):
        if self.fail_resuming_cas and record.state == "resuming":
            self.fail_resuming_cas = False
            raise OSError("crash before continuation command CAS")
        return await super().cas(record, expected_state_version=expected_state_version)

    async def renew(self, *args, **kwargs):
        result = await super().renew(*args, **kwargs)
        if self.fail_renew_after_write and result is not None:
            self.fail_renew_after_write = False
            raise OSError("crash after authorization and durable renew")
        return result


def questionnaire_spec(interaction_id="interaction-1"):
    return A2AInteractionSpec.model_validate(
        {
            "schema_version": 1,
            "interaction_id": interaction_id,
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


def auth_spec(interaction_id="auth-interaction"):
    return A2AInteractionSpec.model_validate(
        {
            "schema_version": 1,
            "interaction_id": interaction_id,
            "questions": [
                {
                    "question_id": "auth-q",
                    "interaction_kind": "auth_challenge",
                    "prompt": "Authenticate",
                    "answer_kind": "authorization_result",
                }
            ],
        }
    )


def questionnaire_answers():
    return [
        HITLQuestionAnswer.model_validate(
            {
                "question_id": "q1",
                "answer": {"kind": "single_choice", "choice": "a"},
            }
        )
    ]


def _digest_json(value):
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return sha256(canonical.encode()).hexdigest()


def auth_reference_identity(call, route, reference, *, answers=None):
    answers = answers or auth_answers(reference)
    answer_digest = _digest_json([answer.model_dump(mode="json") for answer in answers])
    challenge_digest = _digest_json(
        {
            "interaction_id": call.pending_interaction_id,
            "interaction_revision": call.interaction_revision,
            "route_fingerprint": route.fingerprint,
            "interaction_fingerprint": call.interaction_fingerprint,
            "question_id": "auth-q",
        }
    )
    return {
        "interaction_id": call.pending_interaction_id,
        "interaction_revision": call.interaction_revision,
        "route_fingerprint": route.fingerprint,
        "interaction_fingerprint": call.interaction_fingerprint,
        "question_id": "auth-q",
        "challenge_digest": challenge_digest,
        "answer_digest": answer_digest,
    }


def auth_answers(reference):
    return [
        HITLQuestionAnswer.model_validate(
            {
                "question_id": "auth-q",
                "answer": {
                    "kind": "authorization_result",
                    "authorization_reference": reference,
                },
            }
        )
    ]


async def setup_waiting(*, auth_required=False, ledger=None, hitl=None, dispatch=None):
    ledger = ledger or InMemoryAgentCallLedgerStore()
    hitl = hitl or InMemoryHITLApplicationPort()
    call = ledger_record()
    call = transition_call(call, to_state="ready_to_dispatch", updated_at=NOW)
    call = transition_call(call, to_state="dispatching", updated_at=NOW)
    aliases = []
    from execution.orchestrator.a2a_runtime.models import A2AOwnershipAlias

    aliases.append(
        A2AOwnershipAlias(kind="task", value="task-1", binding_scope="endpoint")
    )
    call = transition_call(
        call,
        to_state="working",
        updated_at=NOW,
        a2a_task_id="task-1",
        a2a_context_id="context-1",
        ownership_aliases=aliases,
        ownership_alias_keys=ownership_alias_keys(aliases),
    )
    call = transition_call(call, to_state="continuation_pending", updated_at=NOW)
    spec = auth_spec() if auth_required else questionnaire_spec()
    fingerprint = "auth-fingerprint" if auth_required else "question-fingerprint"
    call = transition_call(
        call,
        to_state="auth_required" if auth_required else "input_required",
        updated_at=NOW,
        pending_interaction_id=spec.interaction_id,
        interaction_revision=1,
        interaction_fingerprint=fingerprint,
    )
    await ledger.insert(call)
    await hitl.create_or_replay(
        call=call, interaction=spec, interaction_fingerprint=fingerprint
    )
    _, route, _ = hitl.read_interaction_for_test(spec.interaction_id)
    bindings = InMemoryAgentToolBindingStore()
    await bindings.insert(binding())
    epochs = InMemoryRoomEpochStore()
    await epochs.activate("room-1", "create-1", activated_at=NOW)
    observations = A2AObservationIngress(
        inbox=InMemoryObservationInboxStore(),
        conflicts=InMemoryObservationConflictStore(),
        ledger=ledger,
        authenticator=RejectExternalIngressAuthenticator(),
    )
    auth_refs = TrustedAuthReferences()
    dispatch = dispatch or Dispatch()
    coordinator = A2AContinuationCoordinator(
        ledger=ledger,
        bindings=bindings,
        hitl=hitl,
        room_epochs=epochs,
        authorization=Authorization(),
        auth_references=auth_refs,
        dispatch=dispatch,
        observations=observations,
    )
    return coordinator, ledger, hitl, auth_refs, dispatch, call, route


@pytest.mark.parametrize("branch", ["working", "terminal_marker"])
@pytest.mark.parametrize("reported_outcome", ["conflict", "error"])
@pytest.mark.parametrize(
    "terminal_status", ["completed", "failed", "canceled", "rejected", "expired"]
)
async def test_continuation_delivery_cas_race_returns_durable_terminal_winner(
    branch, reported_outcome, terminal_status
):
    ledger = ContinuationWinnerRaceLedger(
        terminal_status=terminal_status,
        branch=branch,
        reported_outcome=reported_outcome,
    )
    dispatch = Dispatch(
        terminal_status=terminal_status if branch == "terminal_marker" else None
    )
    coordinator, ledger, hitl, _, dispatch, call, route = await setup_waiting(
        ledger=ledger, dispatch=dispatch
    )
    resume_kwargs = {
        "call_record_id": call.call_record_id,
        "interaction_id": "interaction-1",
        "interaction_revision": 1,
        "route_fingerprint": route.fingerprint,
        "answers": questionnaire_answers(),
        "authenticated_answerer_id": "user-1",
    }

    first = await coordinator.resume(**resume_kwargs)
    persisted = await ledger.load_by_record_id(call.call_record_id)
    retry = await coordinator.resume(**resume_kwargs)

    assert ledger.raced is True
    assert persisted is not None
    assert persisted == ledger.durable_winner
    assert persisted.state == terminal_status
    assert first == retry == persisted.state
    assert len(dispatch.commands) == 1
    assert persisted.continuation_command == dispatch.commands[0]
    assert hitl.read_interaction_for_test("interaction-1") is None
    assert await hitl.read_answers("interaction-1", 1) is None
    with pytest.raises(KeyError):
        await hitl.answer(
            interaction_id="interaction-1",
            interaction_revision=1,
            route_fingerprint=route.fingerprint,
            answers=questionnaire_answers(),
            authenticated_answerer_id="user-1",
            verified_auth_reference_digests=[],
            verified_auth_references=[],
        )


@pytest.mark.parametrize(
    "terminal_status", ["completed", "failed", "canceled", "rejected", "expired"]
)
async def test_continuation_terminal_receipt_persists_winner_without_redispatch(
    terminal_status,
):
    dispatch = Dispatch(terminal_status=terminal_status)
    coordinator, ledger, hitl, _, dispatch, call, route = await setup_waiting(
        dispatch=dispatch
    )
    resume_kwargs = {
        "call_record_id": call.call_record_id,
        "interaction_id": "interaction-1",
        "interaction_revision": 1,
        "route_fingerprint": route.fingerprint,
        "answers": questionnaire_answers(),
        "authenticated_answerer_id": "user-1",
    }

    assert await coordinator.resume(**resume_kwargs) == terminal_status
    persisted = await ledger.load_by_record_id(call.call_record_id)
    assert persisted.state == terminal_status
    assert persisted.terminal_result is not None
    assert persisted.continuation_state == "accepted"
    assert await coordinator.resume(**resume_kwargs) == terminal_status
    assert len(dispatch.commands) == 1
    assert hitl.read_interaction_for_test("interaction-1") is None


@pytest.mark.parametrize(
    "terminal_status", ["completed", "failed", "canceled", "rejected", "expired"]
)
@pytest.mark.parametrize(
    "close_mode",
    ["accepted", "replayed", "absent", "conflict", "error", "outage", "ack_loss"],
)
async def test_continuation_terminal_return_requires_hitl_finalization(
    terminal_status, close_mode
):
    ledger = ContinuationWinnerRaceLedger(
        terminal_status=terminal_status,
        branch="working",
        reported_outcome="conflict",
    )
    hitl = (
        FinalizerFaultHITL(close_mode)
        if close_mode != "accepted"
        else InMemoryHITLApplicationPort()
    )
    coordinator, ledger, hitl, _, dispatch, call, route = await setup_waiting(
        ledger=ledger, hitl=hitl
    )
    resume_kwargs = {
        "call_record_id": call.call_record_id,
        "interaction_id": "interaction-1",
        "interaction_revision": 1,
        "route_fingerprint": route.fingerprint,
        "answers": questionnaire_answers(),
        "authenticated_answerer_id": "user-1",
    }

    first = await coordinator.resume(**resume_kwargs)
    persisted = await ledger.load_by_record_id(call.call_record_id)
    if close_mode in {"conflict", "error", "outage", "ack_loss"}:
        assert first == "delivery_uncertain"
    else:
        assert first == terminal_status
    retry = await coordinator.resume(**resume_kwargs)

    assert persisted.state == terminal_status
    assert retry == terminal_status
    assert hitl.read_interaction_for_test("interaction-1") is None
    assert await hitl.read_answers("interaction-1", 1) is None
    with pytest.raises(KeyError):
        await hitl.answer(
            interaction_id="interaction-1",
            interaction_revision=1,
            route_fingerprint=route.fingerprint,
            answers=questionnaire_answers(),
            authenticated_answerer_id="user-1",
            verified_auth_reference_digests=[],
            verified_auth_references=[],
        )
    assert len(dispatch.commands) == 1


@pytest.mark.parametrize("winner_visibility", ["missing", "unloadable"])
async def test_continuation_unclassifiable_winner_is_typed_recoverable(
    winner_visibility,
):
    ledger = ContinuationWinnerRaceLedger(
        terminal_status="completed",
        branch="working",
        reported_outcome="conflict",
        winner_visibility=winner_visibility,
    )
    coordinator, ledger, _, _, dispatch, call, route = await setup_waiting(
        ledger=ledger
    )
    resume_kwargs = {
        "call_record_id": call.call_record_id,
        "interaction_id": "interaction-1",
        "interaction_revision": 1,
        "route_fingerprint": route.fingerprint,
        "answers": questionnaire_answers(),
        "authenticated_answerer_id": "user-1",
    }

    assert await coordinator.resume(**resume_kwargs) == "delivery_uncertain"
    persisted = await ledger.load_by_record_id(call.call_record_id)
    assert persisted is not None
    assert persisted.state == "completed"
    assert await coordinator.resume(**resume_kwargs) == "completed"
    assert len(dispatch.commands) == 1


async def expire_claim(ledger, call_record_id):
    current = await ledger.load_by_record_id(call_record_id)
    expired = current.model_copy(
        update={
            "claim_expires_at": datetime.now(UTC) - timedelta(seconds=1),
            "state_version": current.state_version + 1,
        }
    )
    assert (
        await ledger.cas(expired, expected_state_version=current.state_version)
        == "accepted"
    )


async def test_recovery_reconciles_crash_after_answer_before_marker():
    hitl = CrashAfterAnswerHITL()
    coordinator, ledger, _, _, dispatch, call, route = await setup_waiting(hitl=hitl)
    with pytest.raises(OSError, match="durable HITL answer"):
        await coordinator.resume(
            call_record_id=call.call_record_id,
            interaction_id="interaction-1",
            interaction_revision=1,
            route_fingerprint=route.fingerprint,
            answers=questionnaire_answers(),
            authenticated_answerer_id="user-1",
        )
    persisted = await ledger.load_by_record_id(call.call_record_id)
    assert persisted.answer_applied is None
    recovery = A2AContinuationRecoveryService(coordinator, ledger)
    assert await recovery.recover_due(due_at=datetime.now(UTC)) == 1
    assert (await ledger.load_by_record_id(call.call_record_id)).state == "working"
    assert len(dispatch.commands) == 1


@pytest.mark.parametrize(
    "failure", ["after_claim", "after_authorization", "before_command_cas"]
)
async def test_answer_marker_recovers_all_pre_command_crash_boundaries(failure):
    ledger = FaultLedger(
        fail_resuming_cas=failure == "before_command_cas",
        fail_renew_after_write=failure == "after_authorization",
    )
    coordinator, ledger, _, _, dispatch, call, route = await setup_waiting(
        ledger=ledger
    )
    if failure == "after_claim":
        original_verify = coordinator.room_epochs.verify_active
        crashed = False

        async def crash_verify(room_id, epoch):
            nonlocal crashed
            if not crashed:
                crashed = True
                raise OSError("crash after claim")
            return await original_verify(room_id, epoch)

        coordinator.room_epochs.verify_active = crash_verify
    resume_kwargs = {
        "call_record_id": call.call_record_id,
        "interaction_id": "interaction-1",
        "interaction_revision": 1,
        "route_fingerprint": route.fingerprint,
        "answers": questionnaire_answers(),
        "authenticated_answerer_id": "user-1",
    }
    with pytest.raises(OSError):
        await coordinator.resume(**resume_kwargs)
    persisted = await ledger.load_by_record_id(call.call_record_id)
    assert persisted.answer_applied is not None
    assert persisted.continuation_command is None
    await expire_claim(ledger, call.call_record_id)
    assert await coordinator.resume(**resume_kwargs) == "working"
    assert len(dispatch.commands) == 1


async def test_multiple_hitl_cycles_persist_distinct_deterministic_commands():
    coordinator, ledger, hitl, _, dispatch, call, route = await setup_waiting()
    assert (
        await coordinator.resume(
            call_record_id=call.call_record_id,
            interaction_id="interaction-1",
            interaction_revision=1,
            route_fingerprint=route.fingerprint,
            answers=questionnaire_answers(),
            authenticated_answerer_id="user-1",
        )
        == "working"
    )
    first_command_id = (
        await ledger.load_by_record_id(call.call_record_id)
    ).continuation_command.command_id
    current = await ledger.load_by_record_id(call.call_record_id)
    pending = transition_call(
        current,
        to_state="continuation_pending",
        updated_at=NOW,
        pending_interaction_id=None,
        interaction_revision=None,
        interaction_fingerprint=None,
        answer_applied=None,
        continuation_command=None,
        continuation_state=None,
        continuation_attempts=0,
        next_attempt_at=None,
    )
    second_spec = questionnaire_spec("interaction-2")
    assert (
        await ledger.cas(pending, expected_state_version=current.state_version)
        == "accepted"
    )
    required = transition_call(
        pending,
        to_state="input_required",
        updated_at=NOW,
        pending_interaction_id="interaction-2",
        interaction_revision=1,
        interaction_fingerprint="second-fingerprint",
    )
    assert (
        await ledger.cas(required, expected_state_version=pending.state_version)
        == "accepted"
    )
    await hitl.create_or_replay(
        call=required,
        interaction=second_spec,
        interaction_fingerprint="second-fingerprint",
    )
    _, second_route, _ = hitl.read_interaction_for_test("interaction-2")
    assert (
        await coordinator.resume(
            call_record_id=call.call_record_id,
            interaction_id="interaction-2",
            interaction_revision=1,
            route_fingerprint=second_route.fingerprint,
            answers=questionnaire_answers(),
            authenticated_answerer_id="user-1",
        )
        == "working"
    )
    second_command_id = (
        await ledger.load_by_record_id(call.call_record_id)
    ).continuation_command.command_id
    assert first_command_id != second_command_id
    assert len(dispatch.commands) == 2


async def test_authref_is_bound_expiring_and_replay_safe_before_answer_persistence():
    coordinator, ledger, hitl, refs, dispatch, call, route = await setup_waiting(
        auth_required=True
    )
    with pytest.raises(PermissionError, match="forged"):
        await coordinator.resume(
            call_record_id=call.call_record_id,
            interaction_id="auth-interaction",
            interaction_revision=1,
            route_fingerprint=route.fingerprint,
            answers=auth_answers("authref:forged"),
            authenticated_answerer_id="user-1",
        )
    assert await hitl.read_answer_record("auth-interaction", 1) is None

    refs.issue(
        "authref:expired",
        call_record_id=call.call_record_id,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    with pytest.raises(PermissionError, match="expired"):
        await coordinator.resume(
            call_record_id=call.call_record_id,
            interaction_id="auth-interaction",
            interaction_revision=1,
            route_fingerprint=route.fingerprint,
            answers=auth_answers("authref:expired"),
            authenticated_answerer_id="user-1",
        )

    refs.issue("authref:cross", call_record_id="other-call")
    with pytest.raises(PermissionError, match="cross-call"):
        await coordinator.resume(
            call_record_id=call.call_record_id,
            interaction_id="auth-interaction",
            interaction_revision=1,
            route_fingerprint=route.fingerprint,
            answers=auth_answers("authref:cross"),
            authenticated_answerer_id="user-1",
        )

    refs.issue(
        "authref:valid",
        call_record_id=call.call_record_id,
        **auth_reference_identity(call, route, "authref:valid"),
    )
    assert (
        await coordinator.resume(
            call_record_id=call.call_record_id,
            interaction_id="auth-interaction",
            interaction_revision=1,
            route_fingerprint=route.fingerprint,
            answers=auth_answers("authref:valid"),
            authenticated_answerer_id="user-1",
        )
        == "working"
    )
    assert (
        await coordinator.resume(
            call_record_id=call.call_record_id,
            interaction_id="auth-interaction",
            interaction_revision=1,
            route_fingerprint=route.fingerprint,
            answers=auth_answers("authref:valid"),
            authenticated_answerer_id="user-1",
        )
        == "working"
    )
    assert len(dispatch.commands) == 1
    with pytest.raises(PermissionError, match="changed challenge identity"):
        await coordinator.resume(
            call_record_id=call.call_record_id,
            interaction_id="auth-interaction",
            interaction_revision=1,
            route_fingerprint=route.fingerprint,
            answers=auth_answers("authref:valid"),
            authenticated_answerer_id="user-2",
        )
    with pytest.raises(PermissionError, match="changed challenge identity"):
        await coordinator.resume(
            call_record_id=call.call_record_id,
            interaction_id="auth-interaction",
            interaction_revision=1,
            route_fingerprint="changed-route",
            answers=auth_answers("authref:valid"),
            authenticated_answerer_id="user-1",
        )
    with pytest.raises(PermissionError, match="changed challenge identity"):
        await coordinator.resume(
            call_record_id=call.call_record_id,
            interaction_id="auth-interaction",
            interaction_revision=1,
            route_fingerprint=route.fingerprint,
            answers=auth_answers("authref:changed-answer"),
            authenticated_answerer_id="user-1",
        )
    assert len(dispatch.commands) == 1
    answer_record = await hitl.read_answer_record("auth-interaction", 1)
    assert len(answer_record.verified_auth_reference_digests) == 1
    binding = answer_record.verified_auth_references[0]
    assert binding.interaction_id == "auth-interaction"
    assert binding.interaction_revision == 1
    assert binding.route_fingerprint == route.fingerprint
    assert binding.interaction_fingerprint == "auth-fingerprint"
    assert binding.question_id == "auth-q"
    assert binding.answer_digest == answer_record.answer_digest
    exact_identity = auth_reference_identity(call, route, "authref:valid")
    exact_proof = await refs.verify(
        "authref:valid",
        authenticated_answerer_id="user-1",
        call_record_id=call.call_record_id,
        binding_id=call.binding_id,
        binding_digest=call.binding_digest,
        room_id=call.room_id,
        room_epoch=call.room_epoch,
        **exact_identity,
    )
    assert (
        await refs.verify(
            "authref:valid",
            authenticated_answerer_id="user-1",
            call_record_id=call.call_record_id,
            binding_id=call.binding_id,
            binding_digest=call.binding_digest,
            room_id=call.room_id,
            room_epoch=call.room_epoch,
            **exact_identity,
        )
        == exact_proof
    )
    for changed in (
        {"interaction_id": "other-interaction"},
        {"question_id": "other-question"},
        {"answer_digest": "changed-answer"},
    ):
        unsafe = exact_identity | changed
        with pytest.raises(PermissionError, match="cross-challenge|unsafe"):
            await refs.verify(
                "authref:valid",
                authenticated_answerer_id="user-1",
                call_record_id=call.call_record_id,
                binding_id=call.binding_id,
                binding_digest=call.binding_digest,
                room_id=call.room_id,
                room_epoch=call.room_epoch,
                **unsafe,
            )
    with pytest.raises(PermissionError, match="cross-call|cross-challenge|unsafe"):
        await refs.verify(
            "authref:valid",
            authenticated_answerer_id="user-1",
            call_record_id="other-call",
            binding_id=call.binding_id,
            binding_digest=call.binding_digest,
            room_id=call.room_id,
            room_epoch=call.room_epoch,
            **auth_reference_identity(call, route, "authref:valid"),
        )


async def test_exact_auth_retry_rejects_changed_durable_proof():
    hitl = ProofTamperingHITL()
    coordinator, _, _, refs, dispatch, call, route = await setup_waiting(
        auth_required=True, hitl=hitl
    )
    reference = "authref:proof-tamper"
    refs.issue(
        reference,
        call_record_id=call.call_record_id,
        **auth_reference_identity(call, route, reference),
    )
    resume_kwargs = {
        "call_record_id": call.call_record_id,
        "interaction_id": "auth-interaction",
        "interaction_revision": 1,
        "route_fingerprint": route.fingerprint,
        "answers": auth_answers(reference),
        "authenticated_answerer_id": "user-1",
    }
    assert await coordinator.resume(**resume_kwargs) == "working"

    hitl.tamper_proof = True
    with pytest.raises(PermissionError, match="applied HITL marker changed"):
        await coordinator.resume(**resume_kwargs)
    assert len(dispatch.commands) == 1


async def test_same_call_later_auth_challenge_cannot_reuse_consumed_reference():
    coordinator, ledger, hitl, refs, dispatch, call, route = await setup_waiting(
        auth_required=True
    )
    reference = "authref:one-challenge"
    refs.issue(
        reference,
        call_record_id=call.call_record_id,
        **auth_reference_identity(call, route, reference),
    )
    assert (
        await coordinator.resume(
            call_record_id=call.call_record_id,
            interaction_id="auth-interaction",
            interaction_revision=1,
            route_fingerprint=route.fingerprint,
            answers=auth_answers(reference),
            authenticated_answerer_id="user-1",
        )
        == "working"
    )
    current = await ledger.load_by_record_id(call.call_record_id)
    pending = transition_call(
        current,
        to_state="continuation_pending",
        updated_at=NOW,
        pending_interaction_id=None,
        interaction_revision=None,
        interaction_fingerprint=None,
        answer_applied=None,
        continuation_command=None,
        continuation_state=None,
        continuation_attempts=0,
        next_attempt_at=None,
    )
    assert await ledger.cas(pending, expected_state_version=current.state_version) == (
        "accepted"
    )
    second_spec = auth_spec("auth-interaction-2")
    required = transition_call(
        pending,
        to_state="auth_required",
        updated_at=NOW,
        pending_interaction_id=second_spec.interaction_id,
        interaction_revision=1,
        interaction_fingerprint="second-auth-fingerprint",
    )
    assert await ledger.cas(required, expected_state_version=pending.state_version) == (
        "accepted"
    )
    await hitl.create_or_replay(
        call=required,
        interaction=second_spec,
        interaction_fingerprint="second-auth-fingerprint",
    )
    _, second_route, _ = hitl.read_interaction_for_test(second_spec.interaction_id)
    with pytest.raises(PermissionError, match="cross-challenge|consumed|unsafe"):
        await coordinator.resume(
            call_record_id=call.call_record_id,
            interaction_id=second_spec.interaction_id,
            interaction_revision=1,
            route_fingerprint=second_route.fingerprint,
            answers=auth_answers(reference),
            authenticated_answerer_id="user-1",
        )
    assert await hitl.read_answer_record(second_spec.interaction_id, 1) is None
    assert len(dispatch.commands) == 1
