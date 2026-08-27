from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import TypeAdapter, ValidationError

from common.dto.delivery import DeliveryEmitStatus
from common.dto.hitl import (
    A2AInteractionSpec,
    HITLQuestionAnswer,
    HITLRouteSnapshot,
    HITLRouteSnapshotUnion,
    HITLRouteSnapshotV2,
)
from execution.orchestrator.a2a_runtime.hitl import InMemoryHITLApplicationPort
from execution.orchestrator.a2a_runtime.interaction_outcome import (
    emit_hitl_request_events,
    emit_hitl_resolved_events,
)
from execution.orchestrator_routing import DualRuntimeRouter

from ._orchestrator_a2a_helpers import ledger_record


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


@pytest.mark.asyncio
async def test_emit_hitl_request_events_close_the_canonical_turn_contract():
    emitted: list[object] = []
    delivery = SimpleNamespace(
        emit=AsyncMock(side_effect=lambda event: emitted.append(event) or True)
    )
    run = SimpleNamespace(
        lifecycle_family="canonical",
        request=SimpleNamespace(user_message_id="user-1"),
        client_request_id="cr-1",
        tool_batches=[
            SimpleNamespace(
                entries=[
                    SimpleNamespace(
                        call_id="call-1",
                        opaque_public_call_id="inv_travel_0001",
                    )
                ]
            )
        ],
        tool_catalog=SimpleNamespace(
            entries=[
                SimpleNamespace(
                    definition=SimpleNamespace(
                        name="agent_abc",
                        label="Travel Planner Agent - itinerary",
                    ),
                    agent_display_name="Travel Planner Agent",
                )
            ]
        ),
    )
    run_store = SimpleNamespace(load=AsyncMock(return_value=run))
    control = AsyncMock()
    record = ledger_record(run_id="run-1", call_id="call-1")

    await emit_hitl_request_events(
        record=record,
        interaction=interaction(),
        interaction_id="interaction-1",
        hitl_delivery=delivery,
        run_store=run_store,
        canonical_control=control,
    )

    assert len(emitted) == 1
    event = emitted[0]
    assert event.run_id == "run-1"
    assert event.message_id == "orchestrator:run-1:inv_travel_0001"
    assert event.related_user_message_id == "user-1"
    assert event.related_message_id is None
    assert event.agent_label == "Travel Planner Agent"
    assert event.agent_id is None
    assert event.source_step_id is None
    assert event.client_request_id == "cr-1"
    control.assert_awaited_once_with(
        "run_waiting_input",
        "run-1",
        "interaction-1",
        ["q1"],
    )


@pytest.mark.asyncio
async def test_canonical_hitl_request_stops_before_control_when_delivery_fails():
    delivery = SimpleNamespace(
        emit_checked=AsyncMock(return_value=DeliveryEmitStatus.FAILED)
    )
    run = SimpleNamespace(
        lifecycle_family="canonical",
        request=SimpleNamespace(user_message_id="user-1"),
        client_request_id="cr-1",
        tool_batches=[
            SimpleNamespace(
                entries=[
                    SimpleNamespace(
                        call_id="call-1",
                        opaque_public_call_id="inv_travel_0001",
                    )
                ]
            )
        ],
        tool_catalog=SimpleNamespace(
            entries=[
                SimpleNamespace(
                    definition=SimpleNamespace(
                        name="agent_abc", label="Travel Planner Agent"
                    ),
                    agent_display_name="Travel Planner Agent",
                )
            ]
        ),
    )
    control = AsyncMock()

    with pytest.raises(RuntimeError, match="not durably delivered"):
        await emit_hitl_request_events(
            record=ledger_record(run_id="run-1", call_id="call-1"),
            interaction=interaction(),
            interaction_id="interaction-1",
            hitl_delivery=delivery,
            run_store=SimpleNamespace(load=AsyncMock(return_value=run)),
            canonical_control=control,
        )

    control.assert_not_awaited()


@pytest.mark.asyncio
async def test_emit_hitl_resolved_events_precede_canonical_run_resume():
    emitted: list[object] = []
    delivery = SimpleNamespace(
        emit=AsyncMock(side_effect=lambda event: emitted.append(event) or True)
    )
    run = SimpleNamespace(
        lifecycle_family="canonical",
        request=SimpleNamespace(user_message_id="user-1"),
        client_request_id="cr-1",
        tool_batches=[
            SimpleNamespace(
                entries=[
                    SimpleNamespace(
                        call_id="call-1",
                        opaque_public_call_id="inv_travel_0001",
                    )
                ]
            )
        ],
    )
    run_store = SimpleNamespace(load=AsyncMock(return_value=run))
    order: list[str] = []

    async def control(*args):
        order.append("control")
        assert args == ("run_resumed", "run-1", "interaction-1", ["q1"])

    async def emit(event):
        emitted.append(event)
        order.append("response")
        return True

    delivery.emit.side_effect = emit
    await emit_hitl_resolved_events(
        record=ledger_record(run_id="run-1", call_id="call-1"),
        interaction=interaction(),
        interaction_id="interaction-1",
        status="responded",
        hitl_delivery=delivery,
        run_store=run_store,
        canonical_control=control,
        answer_ref="answer-digest",
    )

    assert order == ["response", "control"]
    assert emitted[0].run_id == "run-1"
    assert emitted[0].message_id == "orchestrator:run-1:inv_travel_0001"
    assert emitted[0].related_user_message_id == "user-1"
    assert emitted[0].answer_ref == "answer-digest"


@pytest.mark.asyncio
async def test_router_full_cancellation_closes_public_hitl_before_run_abort():
    record = ledger_record(run_id="run-1", call_id="call-1").model_copy(
        update={"state": "input_required", "pending_interaction_id": "interaction-1"}
    )
    route = SimpleNamespace(
        orchestration_run_id="run-1",
        call_record_id=record.call_record_id,
        interaction_revision=1,
    )
    run = SimpleNamespace(
        run_id="run-1",
        room_id="room-1",
        lifecycle_family="canonical",
        request=SimpleNamespace(user_message_id="user-1"),
        client_request_id="cr-1",
        tool_batches=[
            SimpleNamespace(
                entries=[
                    SimpleNamespace(
                        call_id="call-1", opaque_public_call_id="inv_travel_0001"
                    )
                ]
            )
        ],
    )
    order: list[str] = []

    async def emit_checked(_event):
        order.append("hitl_response")
        return DeliveryEmitStatus.DELIVERED

    async def cancel_run(*_args, **_kwargs):
        order.append("cancel_calls")
        return {"call-1": "canceled"}

    async def abort_run(_run):
        order.append("abort_run")

    router = DualRuntimeRouter.__new__(DualRuntimeRouter)
    router._runtime = SimpleNamespace(
        hitl_port=SimpleNamespace(
            get_eligible_interactions=AsyncMock(
                return_value=[(interaction(), route, "fingerprint")]
            ),
            abandon=AsyncMock(return_value="accepted"),
        ),
        hitl_delivery=SimpleNamespace(emit_checked=emit_checked),
        run_store=SimpleNamespace(
            load_by_user_message_id=AsyncMock(return_value=run),
            load=AsyncMock(return_value=run),
        ),
        call_ledger=SimpleNamespace(load_by_record_id=AsyncMock(return_value=record)),
        continuation=SimpleNamespace(canonical_hitl_control=AsyncMock()),
        cancellation_coordinator=SimpleNamespace(cancel_run=cancel_run),
        session_host=SimpleNamespace(abort_run=abort_run),
    )

    await router.route_cancellation_by_user_message("user-1", reason="user:user-1")

    assert order == ["hitl_response", "cancel_calls", "abort_run"]


@pytest.mark.asyncio
async def test_router_direct_hitl_cancellation_aborts_the_owning_run():
    record = ledger_record(run_id="run-1", call_id="call-1").model_copy(
        update={"state": "input_required", "pending_interaction_id": "interaction-1"}
    )
    route = SimpleNamespace(
        room_id="room-1",
        orchestration_run_id="run-1",
        call_record_id=record.call_record_id,
        interaction_revision=1,
    )
    run = SimpleNamespace(
        run_id="run-1",
        room_id="room-1",
        lifecycle_family="canonical",
        request=SimpleNamespace(user_message_id="user-1"),
        client_request_id="cr-1",
        tool_batches=[
            SimpleNamespace(
                entries=[
                    SimpleNamespace(
                        call_id="call-1", opaque_public_call_id="inv_travel_0001"
                    )
                ]
            )
        ],
    )
    order: list[str] = []

    async def emit_checked(_event):
        order.append("hitl_response")
        return DeliveryEmitStatus.DELIVERED

    async def cancel_run(*_args, **_kwargs):
        order.append("cancel_calls")
        return {"call-1": "canceled"}

    async def abort_run(_run):
        order.append("abort_run")

    router = DualRuntimeRouter.__new__(DualRuntimeRouter)
    router._runtime = SimpleNamespace(
        hitl_port=SimpleNamespace(
            read_interaction=AsyncMock(
                return_value=(interaction(), route, "fingerprint")
            ),
            abandon=AsyncMock(return_value="accepted"),
        ),
        hitl_delivery=SimpleNamespace(emit_checked=emit_checked),
        run_store=SimpleNamespace(load=AsyncMock(return_value=run)),
        call_ledger=SimpleNamespace(load_by_record_id=AsyncMock(return_value=record)),
        continuation=SimpleNamespace(canonical_hitl_control=AsyncMock()),
        cancellation_coordinator=SimpleNamespace(cancel_run=cancel_run),
        session_host=SimpleNamespace(abort_run=abort_run),
    )

    version = await router.cancel_hitl_interaction(
        room_id="room-1",
        interaction_id="interaction-1",
        expected_version=1,
    )

    assert version == 1
    assert order == ["hitl_response", "cancel_calls", "abort_run"]


@pytest.mark.asyncio
async def test_router_pending_hitl_uses_public_activity_message_id():
    record = ledger_record(run_id="run-1", call_id="call-1").model_copy(
        update={
            "state": "input_required",
            "pending_interaction_id": "interaction-1",
        }
    )
    router = DualRuntimeRouter.__new__(DualRuntimeRouter)
    router._runtime = SimpleNamespace(
        hitl_port=SimpleNamespace(
            get_eligible_interactions=AsyncMock(
                return_value=[
                    (
                        interaction(),
                        SimpleNamespace(
                            orchestration_run_id="run-1",
                            call_record_id=record.call_record_id,
                            invocation_id="call-1",
                            agent_id="agent-1",
                            task_id="task-1",
                            context_id="context-1",
                            interaction_revision=1,
                        ),
                        "fingerprint",
                    )
                ]
            )
        ),
        run_store=SimpleNamespace(
            load=AsyncMock(
                return_value=SimpleNamespace(
                    lifecycle_family="canonical",
                    request=SimpleNamespace(user_message_id="user-1"),
                    client_request_id="cr-1",
                    tool_batches=[
                        SimpleNamespace(
                            entries=[
                                SimpleNamespace(
                                    call_id="call-1",
                                    opaque_public_call_id="inv_travel_0001",
                                )
                            ]
                        )
                    ],
                    tool_catalog=SimpleNamespace(
                        entries=[
                            SimpleNamespace(
                                definition=SimpleNamespace(
                                    name="agent_abc", label="Travel Planner Agent"
                                ),
                                agent_display_name="Travel Planner Agent",
                            )
                        ]
                    ),
                )
            )
        ),
        call_ledger=SimpleNamespace(load_by_record_id=AsyncMock(return_value=record)),
        public_secret_values=(),
    )

    pending = await router.get_pending_hitl("room-1")

    assert len(pending) == 1
    assert pending[0].message_id == "orchestrator:run-1:inv_travel_0001"
    assert pending[0].display_message_id == "orchestrator:run-1:inv_travel_0001"
    assert pending[0].agent_name == "Travel Planner Agent"
    assert pending[0].agent_id is None
    assert pending[0].source_step_id is None
    assert pending[0].a2a_task_id is None
    assert pending[0].a2a_context_id is None
