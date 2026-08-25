from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from execution.orchestrator.a2a_runtime.in_memory import (
    InMemoryAgentCallLedgerStore,
    InMemoryObservationConflictStore,
    InMemoryObservationInboxStore,
)
from execution.orchestrator.a2a_runtime.ingress import (
    A2AObservationIngress,
    RejectExternalIngressAuthenticator,
)
from execution.orchestrator.a2a_runtime.ledger import (
    ownership_alias_keys,
    transition_call,
)
from execution.orchestrator.a2a_runtime.models import A2AOwnershipAlias
from execution.orchestrator_routing import (
    DualRuntimeRouter,
    _observation_from_webhook_payload,
)

from ._orchestrator_a2a_helpers import ledger_record
from ._orchestrator_helpers import NOW

TYPED_HITL_WEBHOOK_PAYLOAD = {
    "id": "1",
    "jsonrpc": "2.0",
    "result": {
        "contextId": "ctx-1",
        "final": True,
        "kind": "status-update",
        "status": {
            "message": {
                "messageId": "status-msg-hitl-1",
                "parts": [
                    {
                        "kind": "text",
                        "text": "How many days will you stay in NYC?",
                    }
                ],
                "role": "agent",
                "metadata": {
                    "hybro.ai/a2a/interaction": {
                        "schema_version": 1,
                        "interaction_id": "travel-planner:abc123",
                        "questions": [
                            {
                                "question_id": "travel-details:abc123",
                                "interaction_kind": "questionnaire",
                                "prompt": "How many days will you stay in NYC?",
                                "answer_kind": "text",
                                "required": True,
                            }
                        ],
                    }
                },
            },
            "state": "input-required",
        },
        "taskId": "task-1",
    },
}

WORKING_WEBHOOK_PAYLOAD = {
    "id": "1",
    "jsonrpc": "2.0",
    "result": {
        "contextId": "ctx-1",
        "final": False,
        "kind": "status-update",
        "status": {
            "message": {
                "messageId": "status-msg-working-1",
                "parts": [{"kind": "text", "text": "Planning…"}],
                "role": "agent",
            },
            "state": "working",
        },
        "taskId": "task-1",
    },
}

COMPLETED_WEBHOOK_PAYLOAD = {
    "id": "1",
    "jsonrpc": "2.0",
    "result": {
        "contextId": "ctx-1",
        "final": True,
        "kind": "status-update",
        "status": {
            "message": {
                "messageId": "status-msg-completed-1",
                "parts": [{"kind": "text", "text": "Trip plan ready"}],
                "role": "agent",
            },
            "state": "completed",
        },
        "taskId": "task-1",
    },
}

MESSAGE_WEBHOOK_PAYLOAD = {
    "id": "1",
    "jsonrpc": "2.0",
    "result": {
        "kind": "message",
        "messageId": "agent-message-1",
        "contextId": "ctx-1",
        "role": "agent",
        "parts": [{"kind": "text", "text": "Direct message reply"}],
    },
}


def _bound_call():
    call = ledger_record().model_copy(
        update={
            "a2a_task_id": "task-1",
            "a2a_context_id": "ctx-1",
        }
    )
    call = transition_call(call, to_state="ready_to_dispatch", updated_at=NOW)
    call = transition_call(call, to_state="dispatching", updated_at=NOW)
    aliases = [A2AOwnershipAlias(kind="task", value="task-1", binding_scope="endpoint")]
    return transition_call(
        call,
        to_state="working",
        updated_at=NOW,
        a2a_task_id="task-1",
        a2a_context_id="ctx-1",
        ownership_aliases=aliases,
        ownership_alias_keys=ownership_alias_keys(aliases),
    )


def test_observation_from_webhook_payload_preserves_typed_hitl_metadata():
    call = _bound_call()

    observation = _observation_from_webhook_payload(TYPED_HITL_WEBHOOK_PAYLOAD, call)

    assert observation.event_kind == "input_required"
    assert observation.interaction_spec is not None
    assert observation.interaction_spec["interaction_id"] == "travel-planner:abc123"
    assert observation.task_id == "task-1"
    assert observation.context_id == "ctx-1"
    assert observation.cursor == "msg:status-msg-hitl-1"
    assert "input_required" in observation.source_identity
    assert "msg:status-msg-hitl-1" in observation.source_identity


@pytest.mark.asyncio
async def test_route_webhook_records_typed_hitl_observation():
    call = _bound_call()
    recorded: list[object] = []
    runtime = SimpleNamespace(
        observation_ingress=SimpleNamespace(
            record=AsyncMock(side_effect=lambda obs: recorded.append(obs))
        ),
        call_ledger=SimpleNamespace(
            find_by_task_id=AsyncMock(return_value=call),
            load_by_record_id=AsyncMock(return_value=None),
        ),
    )
    router = DualRuntimeRouter(
        runtime=runtime,
        webhook_token_verifier=AsyncMock(return_value=(True, None)),
    )

    await router.route_webhook(
        message_id="task-1",
        payload=TYPED_HITL_WEBHOOK_PAYLOAD,
        token="token-1",
    )

    assert len(recorded) == 1
    observation = recorded[0]
    assert observation.event_kind == "input_required"
    assert observation.interaction_spec is not None
    assert observation.interaction_spec["interaction_id"] == "travel-planner:abc123"


@pytest.mark.asyncio
async def test_sequential_webhook_updates_record_through_real_ingress():
    """working -> HITL -> terminal must not collide on source_identity."""
    call = _bound_call()
    ledger = InMemoryAgentCallLedgerStore()
    await ledger.insert(call)
    inbox = InMemoryObservationInboxStore()
    conflicts = InMemoryObservationConflictStore()
    ingress = A2AObservationIngress(
        inbox=inbox,
        conflicts=conflicts,
        ledger=ledger,
        authenticator=RejectExternalIngressAuthenticator(),
    )

    outcomes = []
    for payload in (
        WORKING_WEBHOOK_PAYLOAD,
        TYPED_HITL_WEBHOOK_PAYLOAD,
        COMPLETED_WEBHOOK_PAYLOAD,
    ):
        observation = _observation_from_webhook_payload(payload, call)
        outcomes.append(await ingress.record(observation))

    assert [outcome for outcome, _ in outcomes] == [
        "accepted",
        "accepted",
        "accepted",
    ]
    identities = {record.source_identity for _, record in outcomes}
    assert len(identities) == 3
    assert await conflicts.list_for_source(outcomes[0][1].source_identity) == []


def test_message_webhook_replay_is_idempotent():
    call = _bound_call()

    first = _observation_from_webhook_payload(MESSAGE_WEBHOOK_PAYLOAD, call)
    second = _observation_from_webhook_payload(MESSAGE_WEBHOOK_PAYLOAD, call)

    assert first.observation_id == second.observation_id
    assert first.source_identity == second.source_identity
    assert first.task_id == "task-1"
    assert first.cursor == "msg:agent-message-1"
    assert "msg:agent-message-1" in first.source_identity


@pytest.mark.asyncio
async def test_message_webhook_replay_dedupes_in_ingress():
    call = _bound_call()
    ledger = InMemoryAgentCallLedgerStore()
    await ledger.insert(call)
    inbox = InMemoryObservationInboxStore()
    conflicts = InMemoryObservationConflictStore()
    ingress = A2AObservationIngress(
        inbox=inbox,
        conflicts=conflicts,
        ledger=ledger,
        authenticator=RejectExternalIngressAuthenticator(),
    )

    first = _observation_from_webhook_payload(MESSAGE_WEBHOOK_PAYLOAD, call)
    second = _observation_from_webhook_payload(MESSAGE_WEBHOOK_PAYLOAD, call)
    assert first.observation_id == second.observation_id
    assert first.source_identity == second.source_identity

    first_outcome, first_record = await ingress.record(first)
    second_outcome, second_record = await ingress.record(second)

    assert first_outcome == "accepted"
    assert second_outcome == "replayed"
    assert second_record.observation_id == first_record.observation_id
    records, by_source = inbox.read_authority_for_test()
    assert len(records) == 1
    assert by_source[first.source_identity] == first.observation_id
    assert await conflicts.list_for_source(first.source_identity) == []


def test_malformed_task_envelope_falls_back_without_raising():
    call = _bound_call()
    observation = _observation_from_webhook_payload(
        {"result": {"task": None}},
        call,
    )
    assert observation.source_kind == "webhook"
    assert observation.event_kind == "working"
    assert observation.task_id == "task-1"
