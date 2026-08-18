from __future__ import annotations

import asyncio
import hashlib
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.dto.hitl import HITLApplicationRoute, HITLRouteSnapshot
from common.utils.time import utcnow
from dal.runtime_store.parts.hitl_lifecycle_store import HITLLifecycleRuntimeStorePart
from execution.facade import ExecutionFacade
from execution.hitl.application import HITLApplicationCoordinator
from execution.hitl.delivery import HITLDeliveryDisposition, HITLDeliveryError
from execution.hitl.exceptions import (
    HITLExpiredError,
    HITLRequestProjectionError,
    HITLRoutingFailedError,
)
from execution.hitl.reconciler import HITLLifecycleReconciler
from execution.hitl.service import HITLService
from execution.task_tracking import A2ATaskTrackingService
from models.hitl import HITLInteractionStatus, HITLResumeCommandStatus


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _row(*, source: str = "supervisor", answer: str = "answer") -> dict:
    route = "a2a_resume" if source == "agent" else "supervisor_run"
    return {
        "schema_version": 3,
        "request_id": "request-1",
        "interaction_id": "interaction-1",
        "question_index": 0,
        "question_count": 1,
        "room_id": "room-1",
        "user_message_id": "user-message-1",
        "orchestration_run_id": "run-1",
        "application_route": route,
        "public_source": source,
        "evidence_origin": source,
        "prompt": "Question?",
        "prompt_type": "text",
        "status": "answer_recorded",
        "user_input": answer,
        "answer_digest": _digest(answer),
        "responded_by_user_id": "user-1",
        "expires_at": utcnow() + timedelta(hours=1),
        **(
            {
                "agent_id": "agent-1",
                "a2a_task_id": "remote-task",
                "a2a_context_id": "remote-context",
                "continuation_message_id": "agent-message",
                "display_message_id": "agent-message",
            }
            if source == "agent"
            else {}
        ),
    }


@pytest.mark.asyncio
async def test_journaled_run_projection_schedules_supervisor_recovery():
    facade = object.__new__(ExecutionFacade)
    saved_state = SimpleNamespace(run_id="run-1")
    facade._record_resolved_hitl_on_orchestration_run = AsyncMock(
        return_value=saved_state
    )
    facade._schedule_orchestration_after_hitl_if_needed = MagicMock()
    hitl_result = {
        "request_id": "request-1",
        "interaction_id": "interaction-1",
        "source": "supervisor",
    }

    result = await facade._record_and_schedule_resolved_hitl(
        hitl_result=hitl_result,
        response="answer",
    )

    assert result is saved_state
    facade._record_resolved_hitl_on_orchestration_run.assert_awaited_once_with(
        hitl_result=hitl_result,
        response="answer",
    )
    facade._schedule_orchestration_after_hitl_if_needed.assert_called_once_with(
        state=saved_state,
        hitl_result=hitl_result,
    )


def _interaction(*, source: str = "supervisor", status: str = "applying") -> dict:
    if source == "agent":
        snapshot = HITLRouteSnapshot(
            route=HITLApplicationRoute.A2A_RESUME,
            task_id="remote-task",
            context_id="remote-context",
            continuation_message_id="agent-message",
            agent_id="agent-1",
        )
    else:
        snapshot = HITLRouteSnapshot(
            route=HITLApplicationRoute.SUPERVISOR_RUN,
            orchestration_run_id="run-1",
        )
    inventory = {
        "request_id": "request-1",
        "prompt": "Question?",
        "prompt_type": "text",
    }
    if source == "agent":
        inventory.update(
            agent_id="agent-1",
            continuation_message_id="agent-message",
            display_message_id="agent-message",
        )
    return {
        "schema_version": 3,
        "interaction_id": "interaction-1",
        "room_id": "room-1",
        "user_message_id": "user-message-1",
        "orchestration_run_id": "run-1",
        "application_route": snapshot.route.value,
        "public_source": source,
        "evidence_origin": source,
        "route_snapshot": snapshot.model_dump(mode="python"),
        "route_fingerprint": snapshot.fingerprint,
        "creation_inventory": [inventory],
        "request_ids": ["request-1"],
        "required_request_ids": ["request-1"],
        "answer_request_ids": ["request-1"],
        "answer_refs": [{"request_id": "request-1", "digest": _digest("answer")}],
        "expected_request_count": 1,
        "status": status,
        "version": 4,
        "application_revision": 1,
        "application_claim_id": "application-claim",
        "application_lease_expires_at": utcnow() - timedelta(seconds=1),
        "run_projection_status": "applied",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command_status",
    [
        HITLResumeCommandStatus.ACKNOWLEDGED.value,
        HITLResumeCommandStatus.PROJECTED.value,
    ],
)
async def test_confirmed_command_drives_aggregate_application(command_status: str):
    command = {
        "schema_version": 3,
        "command_id": "command-1",
        "kind": "supervisor_resume",
        "interaction_id": "interaction-1",
        "application_revision": 1,
        "orchestration_run_id": "run-1",
        "answer_request_ids": ["request-1"],
        "answer_digest": "digest",
        "status": command_status,
        "response_snapshot": {"task_state": "completed"},
    }
    lifecycle = MagicMock()

    async def due_commands(_now, *, limit):
        del limit
        yield command

    lifecycle.iter_due_resume_commands = due_commands
    lifecycle.mark_resume_command_state = AsyncMock(
        return_value={**command, "status": HITLResumeCommandStatus.PROJECTED.value}
    )
    lifecycle.get_interaction_strict = AsyncMock(return_value=_interaction())
    application = MagicMock()
    application._request_rows = AsyncMock(return_value=[_row()])
    application.apply_interaction = AsyncMock(return_value={"status": "applied"})
    reconciler = HITLLifecycleReconciler(
        lifecycle=lifecycle,
        service=SimpleNamespace(),
        application=application,
    )

    repaired = await reconciler._reconcile_commands()

    assert repaired == 1
    application.apply_interaction.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirmed_uncertain_command_resumes_without_resend():
    command = {
        "schema_version": 3,
        "command_id": "command-1",
        "kind": "a2a_resume",
        "interaction_id": "interaction-1",
        "application_revision": 1,
        "task_id": "remote-task",
        "context_id": "remote-context",
        "continuation_message_id": "agent-message",
        "agent_id": "agent-1",
        "outbound_message_id": "outbound-1",
        "answer_request_ids": ["request-1"],
        "answer_digest": "digest",
        "status": HITLResumeCommandStatus.DELIVERY_UNCERTAIN.value,
        "uncertain_since": utcnow(),
    }
    lifecycle = MagicMock()

    async def due_commands(_now, *, limit):
        del limit
        yield command

    lifecycle.iter_due_resume_commands = due_commands
    lifecycle.mark_resume_command_state = AsyncMock(
        side_effect=[
            {**command, "status": HITLResumeCommandStatus.ACKNOWLEDGED.value},
            {**command, "status": HITLResumeCommandStatus.PROJECTED.value},
        ]
    )
    lifecycle.get_interaction_strict = AsyncMock(
        return_value=_interaction(
            source="agent",
            status=HITLInteractionStatus.DELIVERY_UNCERTAIN.value,
        )
    )
    lifecycle.resume_uncertain_interaction = AsyncMock(
        return_value=_interaction(source="agent")
    )
    application = MagicMock()
    application._request_rows = AsyncMock(return_value=[_row(source="agent")])
    application.apply_interaction = AsyncMock(return_value={"status": "applied"})
    reconciler = HITLLifecycleReconciler(
        lifecycle=lifecycle,
        service=SimpleNamespace(),
        application=application,
        inspect_remote_command=AsyncMock(return_value={"advanced": True}),
    )

    assert await reconciler._reconcile_commands() == 1
    lifecycle.resume_uncertain_interaction.assert_awaited_once()
    application.apply_interaction.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_command_reclaim_matches_scan_version_claim_and_lease():
    commands = MagicMock()
    commands.find_one_and_update = AsyncMock(return_value=None)
    part = HITLLifecycleRuntimeStorePart(
        interactions=MagicMock(),
        resume_commands=commands,
        hitl_requests=MagicMock(),
    )
    observed_lease = utcnow() - timedelta(seconds=1)
    now = utcnow()

    result = await part.reclaim_stale_resume_command(
        "command-1",
        observed_claim_id="old-claim",
        observed_version=7,
        observed_lease_expires_at=observed_lease,
        now=now,
        status=HITLResumeCommandStatus.DELIVERY_UNCERTAIN.value,
        error_code="worker_lost",
        error_message="lost",
    )

    assert result is None  # a concurrent renewal wins and is not overwritten
    query = commands.find_one_and_update.await_args.args[0]
    assert query["claim_id"] == "old-claim"
    assert query["version"] == 7
    assert query["lease_expires_at"] == observed_lease
    assert query["$and"] == [{"lease_expires_at": {"$lte": now}}]


@pytest.mark.asyncio
async def test_stale_scan_includes_null_lease_and_delivery_uncertain():
    collection = MagicMock()
    collection.find = AsyncMock(return_value=[])
    part = HITLLifecycleRuntimeStorePart(
        interactions=collection,
        resume_commands=MagicMock(),
        hitl_requests=MagicMock(),
    )

    assert [row async for row in part.iter_stale_applications(utcnow())] == []
    query = collection.find.await_args.args[0]
    applying = next(
        clause
        for clause in query["$or"]
        if clause.get("status") == HITLInteractionStatus.APPLYING.value
    )
    assert {"application_lease_expires_at": None} in applying["$or"]
    assert {"status": HITLInteractionStatus.DELIVERY_UNCERTAIN.value} in query["$or"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "disposition"),
    [
        (ConnectionRefusedError("refused"), HITLDeliveryDisposition.RETRYABLE),
        (TimeoutError("timed out"), HITLDeliveryDisposition.DELIVERY_UNCERTAIN),
    ],
)
async def test_transport_failures_have_typed_delivery_disposition(error, disposition):
    store = MagicMock()
    store.get_room_agent_message_by_message_id = AsyncMock(
        return_value=SimpleNamespace(
            message_id="agent-message",
            agent_url="https://agent.example/a2a",
            agent_id=None,
            message_content=None,
        )
    )
    store.generate_webhook_token.return_value = "token"
    store.hash_webhook_token.return_value = "hash"
    store.update_webhook_token_hash_on_message = AsyncMock(return_value=True)
    service = A2ATaskTrackingService(store)

    with pytest.raises(HITLDeliveryError) as caught:
        await service.reply_to_task(
            message_id="agent-message",
            task_id="remote-task",
            context_id="remote-context",
            user_input="answer",
            webhook_base_url="",
            push_notification_timeout=30,
            default_request_timeout=30,
            send_hitl_reply=AsyncMock(side_effect=error),
            outbound_message_id="stable-message",
        )

    assert caught.value.disposition == disposition


@pytest.mark.asyncio
async def test_long_remote_call_renews_leases_before_concurrent_reconciliation():
    row = _row(source="agent")
    interaction = _interaction(source="agent")
    persistence = MagicMock()
    persistence.get_hitl_request = AsyncMock(return_value=row)
    lifecycle = MagicMock()
    lifecycle.create_resume_command = AsyncMock(side_effect=lambda value: value)
    lifecycle.claim_resume_command = AsyncMock(return_value={"status": "delivering"})
    lease_renewed = asyncio.Event()

    async def renew_application(*_args, **_kwargs):
        lease_renewed.set()
        return True

    lifecycle.renew_interaction_application = AsyncMock(side_effect=renew_application)
    lifecycle.renew_resume_command = AsyncMock(return_value=True)
    lifecycle.mark_resume_command_state = AsyncMock(
        side_effect=[{"status": "acknowledged"}, {"status": "projected"}]
    )
    coordinator = HITLApplicationCoordinator(lifecycle=lifecycle)
    coordinator.HEARTBEAT_SECONDS = 0.01
    service = SimpleNamespace(
        persistence=persistence,
        _handle_agent_response=AsyncMock(),
    )

    async def delayed_response(*_args, **_kwargs):
        await asyncio.sleep(0.04)
        return {"task_state": "completed"}

    service._handle_agent_response.side_effect = delayed_response
    delivery_task = asyncio.create_task(
        coordinator._apply_agent(
            service,
            request=SimpleNamespace(
                a2a_task_id="remote-task",
                a2a_context_id="remote-context",
                continuation_message_id="agent-message",
                display_message_id="agent-message",
            ),
            interaction=interaction,
            claim_id="application-claim",
            user_input="answer",
        )
    )
    await asyncio.wait_for(lease_renewed.wait(), timeout=1)

    async def no_stale_application(_now, *, limit):
        del limit
        if False:
            yield None

    lifecycle.iter_stale_applications = no_stale_application
    competing_application = MagicMock()
    competing_application.apply_interaction = AsyncMock()
    reconciler = HITLLifecycleReconciler(
        lifecycle=lifecycle,
        service=service,
        application=competing_application,
    )
    assert await reconciler._resume_applications() == 0
    competing_application.apply_interaction.assert_not_awaited()
    await delivery_task

    assert lifecycle.renew_interaction_application.await_count >= 2
    assert lifecycle.renew_resume_command.await_count >= 2


@pytest.mark.asyncio
async def test_aggregate_deadline_race_expires_every_projection_and_returns_410():
    row = _row()
    row["status"] = "pending"
    persistence = MagicMock()
    persistence.get_hitl_request = AsyncMock(return_value=row)
    persistence.claim_hitl_request = AsyncMock(return_value=row)
    persistence.cas_update_hitl_request_strict = AsyncMock(return_value=True)
    lifecycle = MagicMock()
    interaction = {
        **_interaction(status=HITLInteractionStatus.OPEN.value),
        "expires_at": utcnow() - timedelta(seconds=1),
    }
    lifecycle.get_interaction_strict = AsyncMock(return_value=interaction)
    lifecycle.record_interaction_answer = AsyncMock(return_value=None)
    lifecycle.terminalize_interaction = AsyncMock(
        return_value={**interaction, "status": HITLInteractionStatus.EXPIRED.value}
    )
    coordinator = HITLApplicationCoordinator(lifecycle=lifecycle)

    async def reconcile_terminal(terminal):
        row.update(
            status="expired",
            owning_run_terminal_status="failed",
            owning_run_terminal_reason=terminal.get("terminal_reason"),
        )
        await persistence.cas_update_hitl_request_strict(
            "request-1", expected_status="pending", status="expired"
        )

    service = SimpleNamespace(
        persistence=persistence,
        reconcile_terminal_interaction=AsyncMock(side_effect=reconcile_terminal),
    )

    with pytest.raises(HITLExpiredError):
        await coordinator.handle_response(
            service,
            room_id="room-1",
            request_id="request-1",
            user_input="answer",
            user_id="user-1",
        )

    assert persistence.cas_update_hitl_request_strict.await_args.kwargs["status"] == (
        "expired"
    )
    service.reconcile_terminal_interaction.assert_awaited_once()


@pytest.mark.asyncio
async def test_open_group_projects_every_member_in_group_order_with_markers():
    rows = [
        {
            **_row(),
            "request_id": f"request-{index}",
            "interaction_id": "group-1",
            "question_count": 2,
            "question_index": index - 1,
            "status": "pending",
            "display_message_id": "display-message",
        }
        for index in (1, 2)
    ]
    persistence = MagicMock()
    persistence.get_hitl_request = AsyncMock(
        side_effect=lambda request_id: next(
            row for row in rows if row["request_id"] == request_id
        )
    )
    persistence.claim_hitl_open_projection = AsyncMock(side_effect=rows)
    persistence.update_agent_message_task_state = AsyncMock(return_value=True)
    persistence.persist_hitl_user_answer = AsyncMock(return_value=True)
    persistence.persist_hitl_request_id_on_message = AsyncMock(return_value=True)
    persistence.persist_hitl_interaction_metadata = AsyncMock(return_value=True)
    persistence.complete_hitl_open_projection = AsyncMock(return_value=True)
    persistence.release_hitl_open_projection = AsyncMock(return_value=True)
    service = HITLService()
    service._persistence = persistence
    service._emit_hitl_event = AsyncMock()

    count = await service.recover_open_interaction_projection(
        {
            **_interaction(
                source="supervisor", status=HITLInteractionStatus.OPEN.value
            ),
            "interaction_id": "group-1",
            "creation_inventory": [
                {
                    "request_id": f"request-{index}",
                    "prompt": "Question?",
                    "prompt_type": "text",
                    "display_message_id": "display-message",
                }
                for index in (1, 2)
            ],
            "request_ids": ["request-1", "request-2"],
            "required_request_ids": ["request-1", "request-2"],
            "expected_request_count": 2,
        }
    )

    assert count == 2
    emitted = [
        call.kwargs["request"].request_id
        for call in service._emit_hitl_event.await_args_list
    ]
    assert emitted == ["request-1", "request-2"]
    assert persistence.complete_hitl_open_projection.await_count == 2


@pytest.mark.asyncio
async def test_open_singleton_projects_from_authoritative_request_ids():
    row = {
        **_row(),
        "status": "pending",
        "display_message_id": "display-message",
    }
    persistence = MagicMock()
    persistence.get_hitl_request = AsyncMock(return_value=row)
    persistence.claim_hitl_open_projection = AsyncMock(return_value=row)
    persistence.update_agent_message_task_state = AsyncMock(return_value=True)
    persistence.persist_hitl_user_answer = AsyncMock(return_value=True)
    persistence.persist_hitl_request_id_on_message = AsyncMock(return_value=True)
    persistence.persist_hitl_interaction_metadata = AsyncMock(return_value=True)
    persistence.complete_hitl_open_projection = AsyncMock(return_value=True)
    persistence.release_hitl_open_projection = AsyncMock(return_value=True)
    service = HITLService()
    service._persistence = persistence
    service._emit_hitl_event = AsyncMock()

    interaction = _interaction(
        source="supervisor", status=HITLInteractionStatus.OPEN.value
    )
    interaction["creation_inventory"][0]["display_message_id"] = "display-message"
    count = await service.recover_open_interaction_projection(interaction)

    assert count == 1
    persistence.get_hitl_request.assert_awaited_once_with("request-1")
    persistence.complete_hitl_open_projection.assert_awaited_once()


@pytest.mark.asyncio
async def test_open_projection_rejects_conflicting_authoritative_member():
    row = {
        **_row(),
        "interaction_id": "different-interaction",
        "status": "pending",
    }
    persistence = MagicMock()
    persistence.get_hitl_request = AsyncMock(return_value=row)
    service = HITLService()
    service._persistence = persistence

    with pytest.raises(HITLRequestProjectionError, match="identity mismatch"):
        await service.recover_open_interaction_projection(
            _interaction(source="supervisor", status=HITLInteractionStatus.OPEN.value)
        )


@pytest.mark.asyncio
async def test_digest_inventory_mismatch_blocks_network_application():
    interaction = _interaction(source="agent", status="answers_recorded")
    interaction["answer_refs"] = [{"request_id": "request-1", "digest": "tampered"}]
    lifecycle = MagicMock()
    lifecycle.claim_interaction_application = AsyncMock(
        return_value={**interaction, "status": "applying"}
    )
    lifecycle.record_interaction_answer = AsyncMock(return_value=interaction)
    lifecycle.mark_interaction_application_state = AsyncMock(return_value={})
    persistence = MagicMock()
    persistence.get_hitl_request = AsyncMock(return_value=_row(source="agent"))
    service = SimpleNamespace(
        persistence=persistence,
        _handle_agent_response=AsyncMock(),
    )

    with pytest.raises(HITLRoutingFailedError, match="answer references"):
        await HITLApplicationCoordinator(lifecycle=lifecycle).apply_interaction(
            service, interaction
        )
    service._handle_agent_response.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["agent", "supervisor"])
async def test_run_answer_projection_is_journaled_for_both_sources(source: str):
    lifecycle = MagicMock()
    interaction = _interaction(source=source)
    interaction["run_projection_status"] = "pending"
    lifecycle.claim_run_answer_projection = AsyncMock(
        return_value={**interaction, "run_projection_status": "applying"}
    )
    lifecycle.mark_run_answer_projection = AsyncMock(
        return_value={**interaction, "run_projection_status": "applied"}
    )
    projector = AsyncMock(return_value=SimpleNamespace())
    coordinator = HITLApplicationCoordinator(lifecycle=lifecycle)
    coordinator.bind_run_answer_projector(projector)

    result = await coordinator._project_run_answers(
        interaction,
        [_row(source=source)],
        "answer",
        {"task_state": "completed"} if source == "agent" else None,
    )

    assert result["run_projection_status"] == "applied"
    projector.assert_awaited_once()
    lifecycle.mark_run_answer_projection.assert_awaited_once()


@pytest.mark.asyncio
async def test_persisting_applying_keeps_fencing_claim_and_expires_lease():
    interactions = MagicMock()
    interactions.find_one_and_update = AsyncMock(return_value={"status": "applying"})
    part = HITLLifecycleRuntimeStorePart(
        interactions=interactions,
        resume_commands=MagicMock(),
        hitl_requests=MagicMock(),
    )

    await part.mark_interaction_application_state(
        "interaction-1",
        claim_id="claim-1",
        status=HITLInteractionStatus.APPLYING.value,
        error="retry",
    )

    updates = interactions.find_one_and_update.await_args.args[1]["$set"]
    assert "application_claim_id" not in updates
    assert updates["application_lease_expires_at"] is not None


@pytest.mark.asyncio
async def test_answer_record_cas_contains_authoritative_aggregate_deadline():
    interactions = MagicMock()
    interactions.find_one = AsyncMock(
        return_value={
            **_interaction(status=HITLInteractionStatus.OPEN.value),
            "answer_request_ids": [],
            "answer_refs": [],
            "expires_at": utcnow() + timedelta(minutes=1),
        }
    )
    interactions.find_one_and_update = AsyncMock(return_value=None)
    part = HITLLifecycleRuntimeStorePart(
        interactions=interactions,
        resume_commands=MagicMock(),
        hitl_requests=MagicMock(),
    )

    await part.record_interaction_answer(
        "interaction-1", request_id="request-1", answer_digest=_digest("answer")
    )

    query = interactions.find_one_and_update.await_args.args[0]
    assert any("expires_at" in clause for clause in query["$or"])


@pytest.mark.asyncio
async def test_protocol_rejection_is_typed_permanent_delivery_failure():
    store = MagicMock()
    store.get_room_agent_message_by_message_id = AsyncMock(
        return_value=SimpleNamespace(
            message_id="agent-message",
            agent_url="https://agent.example/a2a",
            agent_id=None,
            message_content=None,
        )
    )
    store.generate_webhook_token.return_value = "token"
    store.hash_webhook_token.return_value = "hash"
    store.update_webhook_token_hash_on_message = AsyncMock(return_value=True)

    with pytest.raises(HITLDeliveryError) as caught:
        await A2ATaskTrackingService(store).reply_to_task(
            message_id="agent-message",
            task_id="remote-task",
            context_id="remote-context",
            user_input="answer",
            webhook_base_url="",
            push_notification_timeout=30,
            default_request_timeout=30,
            send_hitl_reply=AsyncMock(
                return_value={
                    "kind": "error",
                    "error": {"code": -32602, "message": "Invalid params"},
                }
            ),
            outbound_message_id="stable-message",
        )

    assert caught.value.disposition == HITLDeliveryDisposition.PERMANENT


@pytest.mark.asyncio
async def test_post_send_local_persistence_failure_is_delivery_uncertain():
    store = MagicMock()
    store.get_room_agent_message_by_message_id = AsyncMock(
        return_value=SimpleNamespace(
            message_id="agent-message",
            agent_url="https://agent.example/a2a",
            agent_id=None,
            message_content=None,
        )
    )
    store.generate_webhook_token.return_value = "token"
    store.hash_webhook_token.return_value = "hash"
    store.update_webhook_token_hash_on_message = AsyncMock(return_value=True)
    store.update_task_on_message = AsyncMock(return_value=False)

    with pytest.raises(HITLDeliveryError) as caught:
        await A2ATaskTrackingService(store).reply_to_task(
            message_id="agent-message",
            task_id="remote-task",
            context_id="remote-context",
            user_input="answer",
            webhook_base_url="",
            push_notification_timeout=30,
            default_request_timeout=30,
            send_hitl_reply=AsyncMock(
                return_value={
                    "kind": "task",
                    "result": {
                        "kind": "task",
                        "id": "remote-task",
                        "contextId": "remote-context",
                        "status": {"state": "completed"},
                    },
                }
            ),
            outbound_message_id="stable-message",
        )

    assert caught.value.disposition == HITLDeliveryDisposition.DELIVERY_UNCERTAIN


@pytest.mark.asyncio
async def test_renew_leases_return_false_when_fence_matches_nothing():
    zero_match = SimpleNamespace(matched_count=0)
    interactions = MagicMock()
    interactions.update_one = AsyncMock(return_value=zero_match)
    commands = MagicMock()
    commands.update_one = AsyncMock(return_value=zero_match)
    store = HITLLifecycleRuntimeStorePart(
        interactions=interactions,
        resume_commands=commands,
        hitl_requests=MagicMock(),
    )

    assert (
        await store.renew_interaction_application(
            "interaction-1", claim_id="lost", lease_seconds=30
        )
        is False
    )
    assert (
        await store.renew_resume_command("command-1", claim_id="lost", lease_seconds=30)
        is False
    )


@pytest.mark.asyncio
async def test_supervisor_effect_replays_with_stable_identity_after_ack_crash():
    row = _row()
    interaction = _interaction()
    command_state: dict = {}
    lifecycle = MagicMock()
    lifecycle.renew_interaction_application = AsyncMock(return_value=True)
    lifecycle.renew_resume_command = AsyncMock(return_value=True)

    async def create(command):
        if not command_state:
            command_state.update(command)
        return dict(command_state)

    lifecycle.create_resume_command = AsyncMock(side_effect=create)

    async def claim(command_id, **kwargs):
        command_state.update(
            status=HITLResumeCommandStatus.DELIVERING.value,
            claim_id=kwargs["claim_id"],
        )
        return dict(command_state)

    lifecycle.claim_resume_command = AsyncMock(side_effect=claim)
    acknowledgement_attempts = 0

    async def mark(command_id, **kwargs):
        nonlocal acknowledgement_attempts
        status = kwargs["status"]
        if status == HITLResumeCommandStatus.ACKNOWLEDGED.value:
            acknowledgement_attempts += 1
            if acknowledgement_attempts == 1:
                return None
        command_state.update(
            status=status,
            response_snapshot=kwargs.get("response_snapshot"),
            claim_id=None,
        )
        return dict(command_state)

    lifecycle.mark_resume_command_state = AsyncMock(side_effect=mark)
    persistence = MagicMock()
    persistence.get_hitl_request = AsyncMock(return_value=row)
    persistence.get_hitl_group_requests = AsyncMock(return_value=[row])
    applied_effect_ids: set[str] = set()
    actual_effects = 0

    async def supervisor_effect(_request, _answer, *, effect_id):
        nonlocal actual_effects
        if effect_id not in applied_effect_ids:
            applied_effect_ids.add(effect_id)
            actual_effects += 1

    service = SimpleNamespace(
        persistence=persistence,
        _handle_supervisor_response=AsyncMock(side_effect=supervisor_effect),
    )
    coordinator = HITLApplicationCoordinator(lifecycle=lifecycle)
    request = SimpleNamespace(orchestration_run_id="run-1")

    with pytest.raises(HITLRoutingFailedError):
        await coordinator._apply_supervisor(
            service,
            request=request,
            interaction=interaction,
            claim_id="application-claim",
            user_input="answer",
        )

    stable_id = command_state["command_id"]
    command_state.update(
        status=HITLResumeCommandStatus.RETRYABLE_ERROR.value,
        claim_id=None,
    )
    result = await coordinator._apply_supervisor(
        service,
        request=request,
        interaction=interaction,
        claim_id="application-claim",
        user_input="answer",
    )

    assert result["supervisor_effect_id"] == stable_id
    assert actual_effects == 1
    assert service._handle_supervisor_response.await_count == 2
    assert all(
        call.kwargs["effect_id"] == stable_id
        for call in service._handle_supervisor_response.await_args_list
    )


@pytest.mark.asyncio
async def test_fence_loss_prevents_effect_from_starting():
    lifecycle = MagicMock()
    lifecycle.renew_interaction_application = AsyncMock(return_value=False)
    lifecycle.renew_resume_command = AsyncMock(return_value=True)
    coordinator = HITLApplicationCoordinator(lifecycle=lifecycle)
    effect = AsyncMock()

    with pytest.raises(RuntimeError, match="lost HITL application lease"):
        await coordinator._run_fenced_effect(
            effect,
            interaction_id="interaction-1",
            application_claim_id="lost",
            command_id="command-1",
            command_claim_id="lost-command",
        )

    effect.assert_not_awaited()
