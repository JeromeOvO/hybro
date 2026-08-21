from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from common.dto.hitl import (
    A2AInteractionSpec,
    HITLQuestionAnswer,
    HITLRouteSnapshot,
    HITLRouteSnapshotUnion,
    HITLRouteSnapshotV2,
)
from execution.orchestrator.a2a_runtime.hitl import InMemoryHITLApplicationPort

from ._orchestrator_v3_a2a_helpers import ledger_record


def interaction():
    return A2AInteractionSpec.model_validate(
        {
            "schema_version": 1,
            "interaction_id": "interaction-1",
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


def test_v1_hitl_route_round_trips_unchanged():
    route = HITLRouteSnapshot(route="supervisor_run", orchestration_run_id="legacy-run")
    restored = TypeAdapter(HITLRouteSnapshotUnion).validate_json(
        route.model_dump_json()
    )
    assert restored == route
    assert restored.schema_version == 1


def test_v2_route_is_invocation_owned_and_rejects_provisional_aliases():
    route = HITLRouteSnapshotV2(
        orchestration_run_id="run-1",
        call_record_id="record-1",
        invocation_id="call-1",
        room_id="room-1",
        room_epoch=1,
        binding_id="binding-1",
        agent_id="agent-1",
        task_id="task-1",
        context_id="context-1",
        interaction_revision=1,
        interaction_fingerprint="fingerprint",
    )
    restored = TypeAdapter(HITLRouteSnapshotUnion).validate_json(
        route.model_dump_json()
    )
    assert restored == route
    assert route.fingerprint == restored.fingerprint
    with pytest.raises(ValidationError, match="authoritative"):
        route.model_copy(update={"task_id": "relay-pending-1"}).model_dump()
        HITLRouteSnapshotV2.model_validate(
            {**route.model_dump(), "task_id": "relay-pending-1"}
        )


async def test_typed_answers_validate_exact_question_inventory_and_replay():
    owner = InMemoryHITLApplicationPort()
    call = ledger_record().model_copy(
        update={"a2a_task_id": "task-1", "a2a_context_id": "context-1"}
    )
    spec = interaction()
    interaction_id = await owner.create_or_replay(
        call=call,
        interaction=spec,
        interaction_fingerprint="fingerprint",
    )
    assert owner.read_interaction_for_test(interaction_id) is None
    assert (
        await owner.activate(
            interaction_id,
            call_record_id=call.call_record_id,
            interaction_fingerprint="fingerprint",
        )
        == "accepted"
    )
    _, route, _ = owner.read_interaction_for_test(interaction_id)
    answers = [
        HITLQuestionAnswer.model_validate(
            {
                "question_id": "q1",
                "answer": {"kind": "single_choice", "choice": "a"},
            }
        )
    ]
    first = await owner.answer(
        interaction_id=interaction_id,
        interaction_revision=1,
        route_fingerprint=route.fingerprint,
        answers=answers,
        authenticated_answerer_id="user-1",
        verified_auth_reference_digests=[],
        verified_auth_references=[],
    )
    replay = await owner.answer(
        interaction_id=interaction_id,
        interaction_revision=1,
        route_fingerprint=route.fingerprint,
        answers=answers,
        authenticated_answerer_id="user-1",
        verified_auth_reference_digests=[],
        verified_auth_references=[],
    )
    assert first == replay
    with pytest.raises(ValueError, match="inventory"):
        await owner.answer(
            interaction_id=interaction_id,
            interaction_revision=1,
            route_fingerprint=route.fingerprint,
            answers=[],
            authenticated_answerer_id="user-1",
            verified_auth_reference_digests=[],
            verified_auth_references=[],
        )
