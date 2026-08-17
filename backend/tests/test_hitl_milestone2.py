from __future__ import annotations

import hashlib
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from common.utils.time import utcnow
from dal.runtime_store.parts.hitl_lifecycle_store import _validate_legacy_group
from dal.runtime_store.parts.hitl_store import HITLRuntimeStorePart
from execution.hitl.application import HITLApplicationCoordinator
from execution.hitl.exceptions import HITLRoutingFailedError
from execution.hitl.service import HITLService
from models.hitl import (
    HITLInteraction,
    HITLInteractionStatus,
    HITLResumeCommand,
    HITLResumeCommandStatus,
    HITLStatus,
)
from scripts.backfill_hitl_interactions import build_backfill_plan


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _request_doc(**updates):
    doc = {
        "request_id": "request-1",
        "interaction_id": "interaction-1",
        "room_id": "room-1",
        "user_message_id": "user-message-1",
        "source": "supervisor",
        "prompt": "Choose a market",
        "status": "pending",
        "expires_at": utcnow() + timedelta(hours=1),
        "orchestration_run_id": "run-1",
    }
    doc.update(updates)
    return doc


def _interaction(**updates):
    doc = {
        "interaction_id": "interaction-1",
        "room_id": "room-1",
        "user_message_id": "user-message-1",
        "source": "supervisor",
        "request_ids": ["request-1"],
        "required_request_ids": ["request-1"],
        "expected_request_count": 1,
        "status": "open",
        "version": 2,
        "application_revision": 0,
    }
    doc.update(updates)
    return doc


def test_models_reject_provisional_remote_command_ids():
    with pytest.raises(ValidationError):
        HITLResumeCommand(
            command_id="command-1",
            interaction_id="interaction-1",
            application_revision=1,
            task_id="pending-local",
            context_id="remote-context",
            continuation_message_id="message-1",
            outbound_message_id="outbound-1",
            answer_request_ids=["request-1"],
            answer_digest="digest",
        )


def test_interaction_model_rejects_impossible_applied_state():
    with pytest.raises(ValidationError):
        HITLInteraction(
            interaction_id="interaction-1",
            room_id="room-1",
            user_message_id="user-message-1",
            source="supervisor",
            request_ids=["request-1"],
            required_request_ids=["request-1"],
            expected_request_count=1,
            answer_request_ids=["request-1"],
            status=HITLInteractionStatus.APPLIED,
        )


def test_backfill_reports_conflicting_group_metadata_without_guessing():
    rows = [
        _request_doc(group_id="group-1", room_id="room-1", group_total=2),
        _request_doc(
            request_id="request-2",
            group_id="group-1",
            room_id="room-2",
            group_total=2,
        ),
    ]
    aggregates, conflicts = build_backfill_plan(rows)
    assert aggregates == []
    assert conflicts[0]["conflicting_fields"]["room_id"] == ["room-1", "room-2"]


def test_backfilled_applied_interaction_remains_pending_run_reconciliation():
    aggregates, conflicts = build_backfill_plan(
        [_request_doc(status="responded", user_input="Lloyd's")]
    )

    assert conflicts == []
    assert aggregates[0]["status"] == "applied"
    assert aggregates[0]["run_projection_status"] == "pending"
    assert aggregates[0]["terminal_reconciled"] is False


def test_lazy_legacy_group_rejects_duplicate_indices_and_overfull_members():
    rows = [
        _request_doc(
            request_id="request-1",
            interaction_id="group-1",
            group_id="group-1",
            group_total=2,
            group_index=0,
        ),
        _request_doc(
            request_id="request-2",
            interaction_id="group-1",
            group_id="group-1",
            group_total=2,
            group_index=1,
        ),
        _request_doc(
            request_id="request-3",
            interaction_id="group-1",
            group_id="group-1",
            group_total=2,
            group_index=1,
        ),
    ]

    with pytest.raises(ValueError, match="more requests than group_total"):
        _validate_legacy_group(rows, "group-1", 2)

    with pytest.raises(ValueError, match="duplicate group indices"):
        _validate_legacy_group(rows[1:], "group-1", 2)


@pytest.mark.asyncio
async def test_deadline_is_part_of_claim_and_pending_queries_without_truncation():
    collection = MagicMock()
    collection.find_one_and_update = AsyncMock(return_value=None)
    collection.find = AsyncMock(return_value=[])
    part = HITLRuntimeStorePart(
        hitl_requests=collection,
        room_agent_messages=MagicMock(),
        room_user_messages=MagicMock(),
    )

    await part.claim_hitl_request("request-1", status="answer_recorded")
    claim_query = collection.find_one_and_update.await_args.args[0]
    assert claim_query["$and"][0] == {
        "request_id": "request-1",
        "status": "pending",
    }
    assert {
        "expires_at": {"$gt": claim_query["$and"][1]["$or"][0]["expires_at"]["$gt"]}
    } in claim_query["$and"][1]["$or"]

    await part.get_pending_hitl_requests("room-1")
    pending_kwargs = collection.find.await_args.kwargs
    assert pending_kwargs["exhaust"] is True
    assert "limit" not in pending_kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "interaction_status",
    ["answers_recorded", "applying", "delivery_uncertain"],
)
async def test_pending_projection_restores_nonterminal_application_states(
    interaction_status: str,
):
    row = _request_doc(
        status="answer_recorded",
        user_input="Market A",
        answer_digest=_sha("Market A"),
    )
    persistence = MagicMock()
    persistence.get_pending_hitl_requests_strict = AsyncMock(return_value=[row])
    lifecycle = MagicMock()
    lifecycle.get_interaction_strict = AsyncMock(
        return_value=_interaction(
            status=interaction_status,
            answer_request_ids=["request-1"],
            answer_refs=[{"request_id": "request-1", "digest": _sha("Market A")}],
            application_claim_id=(
                "claim-1" if interaction_status == "applying" else None
            ),
            application_error=(
                "Answer delivery is uncertain"
                if interaction_status == "delivery_uncertain"
                else None
            ),
        )
    )
    service = HITLService(lifecycle=lifecycle)
    service._persistence = persistence

    pending = await service.get_pending_requests("room-1")

    assert len(pending) == 1
    assert pending[0].interaction_status.value == interaction_status
    assert pending[0].application_status == interaction_status
    if interaction_status == "delivery_uncertain":
        assert pending[0].application_error == "Answer delivery is uncertain"


@pytest.mark.asyncio
async def test_partial_group_records_answer_without_application():
    request = _request_doc(group_id="group-1", group_total=2, group_index=0)
    persistence = MagicMock()
    persistence.get_hitl_request = AsyncMock(return_value=request)
    persistence.claim_hitl_request = AsyncMock(return_value=request)
    lifecycle = MagicMock()
    lifecycle.get_interaction_strict = AsyncMock(
        return_value=_interaction(
            interaction_id="group-1",
            request_ids=["request-1", "request-2"],
            required_request_ids=["request-1", "request-2"],
            expected_request_count=2,
        )
    )
    lifecycle.record_interaction_answer = AsyncMock(
        return_value=_interaction(
            interaction_id="group-1",
            request_ids=["request-1", "request-2"],
            required_request_ids=["request-1", "request-2"],
            expected_request_count=2,
            answer_request_ids=["request-1"],
            status=HITLInteractionStatus.PARTIALLY_ANSWERED.value,
        )
    )
    lifecycle.claim_interaction_application = AsyncMock()
    coordinator = HITLApplicationCoordinator(lifecycle=lifecycle)
    service = SimpleNamespace(persistence=persistence)

    result = await coordinator.handle_response(
        service,
        room_id="room-1",
        request_id="request-1",
        user_input="Market A",
        user_id="user-1",
    )

    assert result["status"] == "accepted"
    assert persistence.claim_hitl_request.await_args.kwargs["status"] == (
        HITLStatus.ANSWER_RECORDED.value
    )
    lifecycle.claim_interaction_application.assert_not_awaited()


@pytest.mark.asyncio
async def test_crash_after_answer_recording_never_marks_request_responded():
    request = _request_doc()
    persistence = MagicMock()
    persistence.get_hitl_request = AsyncMock(return_value=request)
    persistence.claim_hitl_request = AsyncMock(return_value=request)
    lifecycle = MagicMock()
    lifecycle.get_interaction_strict = AsyncMock(return_value=_interaction())
    lifecycle.record_interaction_answer = AsyncMock(return_value=None)
    coordinator = HITLApplicationCoordinator(lifecycle=lifecycle)

    with pytest.raises(HITLRoutingFailedError):
        await coordinator.handle_response(
            SimpleNamespace(persistence=persistence),
            room_id="room-1",
            request_id="request-1",
            user_input="Market A",
            user_id="user-1",
        )

    assert persistence.claim_hitl_request.await_args.kwargs["status"] == (
        HITLStatus.ANSWER_RECORDED.value
    )
    assert all(
        call.kwargs.get("status") != HITLStatus.RESPONDED.value
        for call in persistence.mock_calls
    )


@pytest.mark.asyncio
async def test_last_group_answer_applies_one_combined_payload():
    rows = [
        _request_doc(
            request_id="request-1",
            interaction_id="group-1",
            group_id="group-1",
            prompt="First?",
            user_input="A",
            answer_digest=_sha("A"),
            status="answer_recorded",
        ),
        _request_doc(
            request_id="request-2",
            interaction_id="group-1",
            group_id="group-1",
            prompt="Second?",
            user_input="B",
            answer_digest=_sha("B"),
            status="answer_recorded",
        ),
    ]
    applying = _interaction(
        interaction_id="group-1",
        request_ids=["request-1", "request-2"],
        required_request_ids=["request-1", "request-2"],
        answer_request_ids=["request-1", "request-2"],
        expected_request_count=2,
        status="applying",
        application_claim_id="claim",
        application_revision=1,
        answer_refs=[
            {"request_id": "request-1", "digest": _sha("A")},
            {"request_id": "request-2", "digest": _sha("B")},
        ],
    )
    applied = {**applying, "status": "applied", "version": 4}
    persistence = MagicMock()
    persistence.get_hitl_request = AsyncMock(
        side_effect=lambda rid: next(row for row in rows if row["request_id"] == rid)
    )
    persistence.get_hitl_group_requests = AsyncMock(return_value=rows)
    lifecycle = MagicMock()
    lifecycle.claim_interaction_application = AsyncMock(return_value=applying)
    lifecycle.mark_interaction_application_state = AsyncMock(return_value=applied)
    lifecycle.get_resume_command_for_interaction_strict = AsyncMock(return_value=None)
    coordinator = HITLApplicationCoordinator(lifecycle=lifecycle)
    coordinator.finalize_applied = AsyncMock()
    coordinator._apply_supervisor = AsyncMock(return_value={})
    service = SimpleNamespace(persistence=persistence)

    result = await coordinator.apply_interaction(
        service,
        {**applying, "status": "answers_recorded", "application_revision": 0},
    )

    assert result["status"] == "applied"
    coordinator._apply_supervisor.assert_awaited_once()
    assert coordinator._apply_supervisor.await_args.kwargs["user_input"] == (
        "Q: First?\nA: A\n\nQ: Second?\nA: B"
    )
    lifecycle.claim_interaction_application.assert_awaited_once()


@pytest.mark.asyncio
async def test_batch_records_every_answer_before_single_application():
    rows = {
        "request-1": _request_doc(
            request_id="request-1",
            interaction_id="group-1",
            group_id="group-1",
            group_total=2,
            group_index=0,
        ),
        "request-2": _request_doc(
            request_id="request-2",
            interaction_id="group-1",
            group_id="group-1",
            group_total=2,
            group_index=1,
        ),
    }
    state = _interaction(
        interaction_id="group-1",
        request_ids=["request-1", "request-2"],
        required_request_ids=["request-1", "request-2"],
        expected_request_count=2,
    )
    lifecycle = MagicMock()
    lifecycle.get_interaction_strict = AsyncMock(side_effect=lambda _id: state)

    async def record(_interaction_id, *, request_id, answer_digest):
        state["answer_request_ids"] = [
            *state.get("answer_request_ids", []),
            request_id,
        ]
        state["answer_refs"] = [
            *state.get("answer_refs", []),
            {"request_id": request_id, "digest": answer_digest},
        ]
        state["status"] = (
            "answers_recorded"
            if len(state["answer_request_ids"]) == 2
            else "partially_answered"
        )
        return dict(state)

    lifecycle.record_interaction_answer = AsyncMock(side_effect=record)
    persistence = MagicMock()
    persistence.get_hitl_request = AsyncMock(
        side_effect=lambda request_id: rows[request_id]
    )

    async def claim(request_id, **updates):
        previous = dict(rows[request_id])
        rows[request_id].update(updates)
        return previous

    persistence.claim_hitl_request = AsyncMock(side_effect=claim)
    coordinator = HITLApplicationCoordinator(lifecycle=lifecycle)

    async def apply(_service, interaction):
        assert interaction["status"] == "answers_recorded"
        assert set(interaction["answer_request_ids"]) == {"request-1", "request-2"}
        return {"status": "applied", "request_id": "request-1"}

    coordinator.apply_interaction = AsyncMock(side_effect=apply)
    result = await coordinator.handle_batch_response(
        SimpleNamespace(persistence=persistence),
        room_id="room-1",
        interaction_id="group-1",
        answers=[
            {"request_id": "request-1", "user_input": "A"},
            {"request_id": "request-2", "user_input": "B"},
        ],
        user_id="user-1",
    )

    assert result["status"] == "applied"
    assert persistence.claim_hitl_request.await_count == 2
    assert lifecycle.record_interaction_answer.await_count == 2
    coordinator.apply_interaction.assert_awaited_once()


@pytest.mark.asyncio
async def test_batch_rejects_mismatched_client_request_id_before_writes():
    row = _request_doc(client_request_id="authoritative-client")
    persistence = MagicMock()
    persistence.get_hitl_request = AsyncMock(return_value=row)
    persistence.claim_hitl_request = AsyncMock()
    lifecycle = MagicMock()
    lifecycle.get_interaction_strict = AsyncMock(return_value=_interaction())
    coordinator = HITLApplicationCoordinator(lifecycle=lifecycle)

    with pytest.raises(Exception, match="client_request_id does not match"):
        await coordinator.handle_batch_response(
            SimpleNamespace(persistence=persistence),
            room_id="room-1",
            interaction_id="interaction-1",
            answers=[{"request_id": "request-1", "user_input": "Market A"}],
            user_id="user-1",
            client_request_id="wrong-client",
        )

    persistence.claim_hitl_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_command_uses_stable_message_id_and_timeout_is_uncertain():
    request = SimpleNamespace(
        a2a_task_id="remote-task",
        a2a_context_id="remote-context",
        continuation_message_id="agent-message",
        display_message_id="agent-message",
    )
    interaction = _interaction(
        source="agent",
        status="applying",
        application_revision=1,
        answer_request_ids=["request-1"],
        answer_refs=[{"request_id": "request-1", "digest": _sha("answer")}],
    )
    row = _request_doc(
        source="agent",
        a2a_task_id="remote-task",
        a2a_context_id="remote-context",
        continuation_message_id="agent-message",
        user_input="answer",
        answer_digest=_sha("answer"),
        status="answer_recorded",
    )
    persistence = MagicMock()
    persistence.get_hitl_request = AsyncMock(return_value=row)
    lifecycle = MagicMock()
    lifecycle.create_resume_command = AsyncMock(side_effect=lambda doc: doc)
    lifecycle.claim_resume_command = AsyncMock(return_value={"status": "delivering"})
    lifecycle.renew_interaction_application = AsyncMock(return_value=True)
    lifecycle.renew_resume_command = AsyncMock(return_value=True)
    lifecycle.mark_resume_command_state = AsyncMock(
        return_value={"status": "delivery_uncertain"}
    )
    coordinator = HITLApplicationCoordinator(lifecycle=lifecycle)
    service = SimpleNamespace(
        persistence=persistence,
        _handle_agent_response=AsyncMock(side_effect=TimeoutError("lost reply")),
    )

    with pytest.raises(TimeoutError):
        await coordinator._apply_agent(
            service,
            request=request,
            interaction=interaction,
            claim_id="claim-1",
            user_input="answer",
        )

    command = lifecycle.create_resume_command.await_args.args[0]
    outbound_id = command["outbound_message_id"]
    assert outbound_id.startswith("hitl-message-")
    assert (
        service._handle_agent_response.await_args.kwargs["outbound_message_id"]
        == outbound_id
    )
    assert lifecycle.mark_resume_command_state.await_args.kwargs["status"] == (
        HITLResumeCommandStatus.DELIVERY_UNCERTAIN.value
    )


@pytest.mark.asyncio
async def test_existing_uncertain_command_is_never_resent():
    lifecycle = MagicMock()
    lifecycle.create_resume_command = AsyncMock(
        return_value={"status": HITLResumeCommandStatus.DELIVERY_UNCERTAIN.value}
    )
    lifecycle.claim_resume_command = AsyncMock()
    coordinator = HITLApplicationCoordinator(lifecycle=lifecycle)
    row = _request_doc(
        source="agent",
        user_input="answer",
        answer_digest=_sha("answer"),
        status="answer_recorded",
    )
    persistence = MagicMock()
    persistence.get_hitl_request = AsyncMock(return_value=row)
    service = SimpleNamespace(
        persistence=persistence,
        _handle_agent_response=AsyncMock(),
    )
    request = SimpleNamespace(
        a2a_task_id="remote-task",
        a2a_context_id="remote-context",
        continuation_message_id="agent-message",
        display_message_id="agent-message",
    )

    with pytest.raises(TimeoutError):
        await coordinator._apply_agent(
            service,
            request=request,
            interaction=_interaction(
                source="agent",
                status="applying",
                application_revision=1,
                answer_request_ids=["request-1"],
                answer_refs=[{"request_id": "request-1", "digest": _sha("answer")}],
            ),
            claim_id="claim",
            user_input="answer",
        )

    lifecycle.claim_resume_command.assert_not_awaited()
    service._handle_agent_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_materializing_group_is_not_emitted():
    persistence = MagicMock()
    persistence.get_room_user_message_by_message_id = AsyncMock(return_value=None)
    persistence.get_room_agent_message_by_message_id = AsyncMock(return_value=None)
    persistence.resolve_client_request_id_for_message_id = AsyncMock(return_value=None)
    persistence.count_hitl_requests_for_message = AsyncMock(return_value=0)
    persistence.create_hitl_request = AsyncMock(return_value=True)
    lifecycle = MagicMock()
    lifecycle.materialize_interaction = AsyncMock(side_effect=lambda doc: doc)
    lifecycle.attach_interaction_request = AsyncMock(
        return_value={"status": HITLInteractionStatus.MATERIALIZING.value}
    )
    delivery = MagicMock()
    delivery.emit = AsyncMock()
    service = HITLService(lifecycle=lifecycle)
    service._persistence = persistence
    service._delivery = delivery

    result = await service.request_input(
        room_id="room-1",
        user_message_id="user-message-1",
        source="supervisor",
        prompt="First?",
        group_id="group-1",
        group_total=2,
        group_index=0,
    )

    assert result is not None
    lifecycle.attach_interaction_request.assert_awaited_once()
    delivery.emit.assert_not_awaited()
