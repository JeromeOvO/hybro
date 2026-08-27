from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from common.dto.hitl import HITLApplicationRoute, HITLRouteSnapshot
from execution.hitl.exceptions import HITLRoutingFailedError
from execution.hitl.service import HITLService
from execution.hitl.validation import (
    HITLAggregateCorruptionError,
    validate_command_route_consistency,
    validate_exact_member_inventory,
    validate_route_classifications,
)
from models.hitl import (
    HITLInteraction,
    HITLInteractionStatus,
    HITLRequest,
    HITLResumeCommand,
    HITLSupervisorEffectCommand,
)


def _aggregate() -> dict:
    snapshot = HITLRouteSnapshot(
        route=HITLApplicationRoute.A2A_RESUME,
        task_id="task-1",
        context_id="context-1",
        continuation_message_id="message-1",
        agent_id="agent-1",
    )
    return {
        "schema_version": 3,
        "interaction_id": "interaction-1",
        "room_id": "room-1",
        "user_message_id": "message-1",
        "orchestration_run_id": None,
        "application_route": "a2a_resume",
        "public_source": "agent",
        "evidence_origin": "agent",
        "route_snapshot": snapshot.model_dump(mode="python"),
        "route_fingerprint": snapshot.fingerprint,
        "creation_inventory": [
            {
                "request_id": f"request-{index + 1}",
                "prompt": f"Question {index + 1}?",
                "prompt_type": "text",
            }
            for index in range(2)
        ],
        "request_ids": ["request-1", "request-2"],
        "required_request_ids": ["request-1", "request-2"],
        "expected_request_count": 2,
        "version": 1,
    }


def _rows() -> list[dict]:
    return [
        {
            "schema_version": 3,
            "request_id": f"request-{index + 1}",
            "interaction_id": "interaction-1",
            "question_index": index,
            "question_count": 2,
            "room_id": "room-1",
            "user_message_id": "message-1",
            "orchestration_run_id": None,
            "application_route": "a2a_resume",
            "public_source": "agent",
            "evidence_origin": "agent",
            "prompt": f"Question {index + 1}?",
            "prompt_type": "text",
            "status": "pending",
        }
        for index in range(2)
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prompt", "Changed?"),
        ("choices", ["changed"]),
        ("continuation_message_id", "changed-message"),
    ],
)
def test_member_immutable_inventory_changes_fail_closed(field, value):
    aggregate = _aggregate()
    rows = _rows()
    aggregate["creation_inventory"][0][field] = value

    with pytest.raises(HITLAggregateCorruptionError, match="immutable"):
        validate_exact_member_inventory(aggregate, rows)


def test_missing_creation_inventory_and_schema_v3_fail_closed():
    for missing in ("creation_inventory", "schema_version"):
        aggregate = _aggregate()
        aggregate.pop(missing)
        with pytest.raises(HITLAggregateCorruptionError):
            validate_exact_member_inventory(aggregate, _rows())


def test_effect_commands_forbid_extra_authority_fields():
    a2a = {
        "schema_version": 3,
        "command_id": "command-1",
        "interaction_id": "interaction-1",
        "application_revision": 1,
        "task_id": "task-1",
        "context_id": "context-1",
        "continuation_message_id": "message-1",
        "agent_id": "agent-1",
        "outbound_message_id": "outbound-1",
        "answer_request_ids": ["request-1"],
        "answer_digest": "digest",
        "authority_override": "untrusted",
    }
    supervisor = {
        "schema_version": 3,
        "command_id": "command-2",
        "interaction_id": "interaction-1",
        "application_revision": 1,
        "orchestration_run_id": "run-1",
        "answer_request_ids": ["request-1"],
        "answer_digest": "digest",
        "authority_override": "untrusted",
    }
    with pytest.raises(ValidationError, match="Extra inputs"):
        HITLResumeCommand.model_validate(a2a)
    with pytest.raises(ValidationError, match="Extra inputs"):
        HITLSupervisorEffectCommand.model_validate(supervisor)


def test_route_fingerprint_corruption_fails_closed():
    aggregate = _aggregate()
    aggregate["route_fingerprint"] = "0" * 64

    with pytest.raises(HITLAggregateCorruptionError, match="fingerprint"):
        validate_route_classifications(aggregate)


def test_mixed_member_target_fails_before_effect_validation():
    rows = _rows()
    rows[1]["application_route"] = "supervisor_run"

    with pytest.raises(HITLAggregateCorruptionError, match="application_route"):
        validate_exact_member_inventory(_aggregate(), rows)


def test_member_order_mismatch_fails_closed():
    rows = list(reversed(_rows()))

    with pytest.raises(HITLAggregateCorruptionError, match="order"):
        validate_exact_member_inventory(_aggregate(), rows)


def test_command_target_mismatch_fails_before_effect():
    command = {
        "schema_version": 3,
        "command_id": "command-1",
        "kind": "a2a_resume",
        "interaction_id": "interaction-1",
        "task_id": "corrupt-task",
        "context_id": "context-1",
        "continuation_message_id": "message-1",
        "agent_id": "agent-1",
        "outbound_message_id": "outbound-1",
        "application_revision": 1,
        "answer_request_ids": ["request-1", "request-2"],
        "answer_digest": "digest",
    }

    with pytest.raises(HITLAggregateCorruptionError, match="target"):
        validate_command_route_consistency(_aggregate(), command)


def _persisted_rows() -> list[dict]:
    return _rows()


def _cancellation_service(
    aggregate: dict, rows: list[dict]
) -> tuple[HITLService, MagicMock, MagicMock]:
    persistence = MagicMock()

    async def get_request(request_id):
        return next(row for row in rows if row["request_id"] == request_id)

    async def cas_request(request_id, *, expected_status, **updates):
        row = next(row for row in rows if row["request_id"] == request_id)
        if row["status"] != expected_status:
            return False
        row.update(updates)
        return True

    persistence.get_hitl_request = AsyncMock(side_effect=get_request)
    persistence.cas_update_hitl_request_strict = AsyncMock(side_effect=cas_request)
    persistence.get_and_clear_continuation_on_message = AsyncMock()
    persistence.get_and_clear_continuation_on_user_message = AsyncMock()

    async def update_request(request_id, **updates):
        row = next(row for row in rows if row["request_id"] == request_id)
        row.update(updates)
        return True

    persistence.update_hitl_request = AsyncMock(side_effect=update_request)
    lifecycle = MagicMock()
    lifecycle.get_interaction_strict = AsyncMock(return_value=aggregate)
    lifecycle.mark_interaction_terminal_reconciled = AsyncMock(return_value=True)
    lifecycle.terminalize_interaction = AsyncMock(
        return_value={**aggregate, "status": "canceled"}
    )
    service = HITLService(lifecycle=lifecycle)
    service._persistence = persistence
    service._emit_hitl_event = AsyncMock()
    return service, persistence, lifecycle


@pytest.mark.asyncio
async def test_cancel_request_terminalizes_exact_aggregate_inventory():
    aggregate = {**_aggregate(), "status": "open"}
    rows = _persisted_rows()
    service, persistence, lifecycle = _cancellation_service(aggregate, rows)

    await service.cancel_request("request-1", room_id="room-1")

    lifecycle.terminalize_interaction.assert_awaited_once()
    assert persistence.cas_update_hitl_request_strict.await_count == 2
    assert service._emit_hitl_event.await_count == 2


@pytest.mark.asyncio
async def test_cancel_request_fingerprint_corruption_fails_before_writes_or_effects():
    aggregate = {
        **_aggregate(),
        "status": "open",
        "route_fingerprint": "corrupt",
    }
    rows = _persisted_rows()
    service, persistence, lifecycle = _cancellation_service(aggregate, rows)

    with pytest.raises(HITLRoutingFailedError, match="fingerprint"):
        await service.cancel_request("request-1", room_id="room-1")

    lifecycle.terminalize_interaction.assert_not_awaited()
    persistence.cas_update_hitl_request_strict.assert_not_awaited()
    service._emit_hitl_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_request_records_terminal_state_before_retryable_effect_failure():
    aggregate = {**_aggregate(), "status": HITLInteractionStatus.OPEN.value}
    rows = _persisted_rows()
    service, persistence, _lifecycle = _cancellation_service(aggregate, rows)
    service._terminal_lifecycle = MagicMock()
    service._terminal_lifecycle.terminalize_owning_run = AsyncMock(
        side_effect=RuntimeError("temporary projection failure")
    )

    with pytest.raises(RuntimeError, match="side effects remain pending"):
        await service.cancel_request("request-1", room_id="room-1")

    assert persistence.cas_update_hitl_request_strict.await_count == 2
    assert persistence.update_hitl_request.await_count == 2


@pytest.mark.asyncio
async def test_request_interaction_validates_complete_inventory_before_first_write():
    persistence = MagicMock()
    persistence.create_hitl_request = AsyncMock(return_value=True)
    service = HITLService(lifecycle=MagicMock())
    service._persistence = persistence
    snapshot = HITLRouteSnapshot(
        route=HITLApplicationRoute.SUPERVISOR_RUN,
        orchestration_run_id="run-1",
    )

    with pytest.raises(ValueError, match="question 1"):
        await service.request_interaction(
            room_id="room-1",
            user_message_id="message-1",
            interaction_id="interaction-1",
            application_route=HITLApplicationRoute.SUPERVISOR_RUN,
            public_source="supervisor",
            evidence_origin="supervisor",
            route_snapshot=snapshot,
            questions=[{"prompt": "Valid?"}, {"prompt": "   "}],
            orchestration_run_id="run-1",
        )

    persistence.create_hitl_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_supervisor_route_run_mismatch_fails_before_first_write():
    persistence = MagicMock()
    persistence.create_hitl_request = AsyncMock(return_value=True)
    lifecycle = MagicMock()
    lifecycle.materialize_interaction = AsyncMock()
    service = HITLService(lifecycle=lifecycle)
    service._persistence = persistence
    snapshot = HITLRouteSnapshot(
        route=HITLApplicationRoute.SUPERVISOR_RUN,
        orchestration_run_id="snapshot-run",
    )

    with pytest.raises(ValueError, match="orchestration_run_id"):
        await service.request_interaction(
            room_id="room-1",
            user_message_id="message-1",
            interaction_id="interaction-1",
            application_route=HITLApplicationRoute.SUPERVISOR_RUN,
            public_source="supervisor",
            evidence_origin="supervisor",
            route_snapshot=snapshot,
            questions=[{"prompt": "Continue?"}],
            orchestration_run_id="member-run",
        )

    lifecycle.materialize_interaction.assert_not_awaited()
    persistence.create_hitl_request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_on_create", [1, 2])
async def test_materialization_retry_resumes_inventory_to_open_once(crash_on_create):
    documents: dict[str, dict] = {}
    create_attempt = 0
    transition_count = 0

    async def create_request(doc):
        nonlocal create_attempt
        create_attempt += 1
        if create_attempt == crash_on_create:
            raise RuntimeError("checkpoint crash")
        if doc["request_id"] in documents:
            return False
        documents[doc["request_id"]] = deepcopy(doc)
        return True

    persistence = MagicMock()
    persistence.create_hitl_request = AsyncMock(side_effect=create_request)
    persistence.get_hitl_request = AsyncMock(
        side_effect=lambda request_id: deepcopy(documents.get(request_id))
    )
    persistence.get_room_user_message_by_message_id = AsyncMock(return_value=None)
    persistence.resolve_client_request_id_for_message_id = AsyncMock(return_value=None)
    persistence.count_hitl_requests_for_message = AsyncMock(return_value=0)

    state: dict | None = None

    async def materialize(doc):
        nonlocal state
        if state is None:
            state = deepcopy(doc)
        return deepcopy(state)

    async def attach(_interaction_id, *, request_id, question_index, **_kwargs):
        nonlocal state, transition_count
        assert state is not None
        if request_id not in state["request_ids"]:
            state["request_ids"].append(request_id)
            state["required_request_ids"].append(request_id)
            state["request_ids"].sort(
                key=lambda item: next(
                    index
                    for index, spec in enumerate(state["creation_inventory"])
                    if spec["request_id"] == item
                )
            )
        if len(state["request_ids"]) == state["expected_request_count"]:
            if state["status"] != HITLInteractionStatus.OPEN.value:
                transition_count += 1
            state["status"] = HITLInteractionStatus.OPEN.value
        return deepcopy(state)

    lifecycle = MagicMock()
    lifecycle.materialize_interaction = AsyncMock(side_effect=materialize)
    lifecycle.attach_interaction_request = AsyncMock(side_effect=attach)
    lifecycle.get_interaction_strict = AsyncMock(
        side_effect=lambda _id: deepcopy(state)
    )
    service = HITLService(lifecycle=lifecycle)
    service._persistence = persistence
    service.recover_open_interaction_projection = AsyncMock(return_value=2)
    snapshot = HITLRouteSnapshot(
        route=HITLApplicationRoute.SUPERVISOR_RUN,
        orchestration_run_id="run-1",
    )
    kwargs = dict(
        room_id="room-1",
        user_message_id="message-1",
        interaction_id="interaction-1",
        application_route=HITLApplicationRoute.SUPERVISOR_RUN,
        public_source="supervisor",
        evidence_origin="supervisor",
        route_snapshot=snapshot,
        questions=[{"prompt": "First?"}, {"prompt": "Second?"}],
        orchestration_run_id="run-1",
    )

    with pytest.raises(RuntimeError, match="checkpoint crash"):
        await service.request_interaction(**kwargs)
    result = await service.request_interaction(**kwargs)

    assert result is not None and len(result) == 2
    assert len(documents) == 2
    assert state is not None and state["status"] == HITLInteractionStatus.OPEN.value
    assert transition_count == 1
    service.recover_open_interaction_projection.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("aggregate_status", "member_status", "owning_status"),
    [("canceled", "canceled", "canceled"), ("expired", "expired", "failed")],
)
async def test_terminal_retry_converges_after_per_member_crash(
    aggregate_status, member_status, owning_status
):
    rows = _persisted_rows()
    aggregate = {
        **_aggregate(),
        "status": aggregate_status,
        "member_terminal_status": member_status,
        "owning_run_terminal_status": owning_status,
        "terminal_reason": "terminal reason",
        "version": 4,
    }
    crash_once = True

    async def get_request(request_id):
        return deepcopy(next(row for row in rows if row["request_id"] == request_id))

    async def cas_request(request_id, *, expected_status, **updates):
        nonlocal crash_once
        row = next(row for row in rows if row["request_id"] == request_id)
        if request_id == "request-2" and crash_once:
            crash_once = False
            raise RuntimeError("member checkpoint crash")
        if row["status"] != expected_status:
            return False
        row.update(updates)
        return True

    async def update_request(request_id, **updates):
        next(row for row in rows if row["request_id"] == request_id).update(updates)
        return True

    persistence = MagicMock()
    persistence.get_hitl_request = AsyncMock(side_effect=get_request)
    persistence.cas_update_hitl_request_strict = AsyncMock(side_effect=cas_request)
    persistence.update_hitl_request = AsyncMock(side_effect=update_request)
    persistence.get_and_clear_continuation_on_message = AsyncMock()
    persistence.get_and_clear_continuation_on_user_message = AsyncMock()
    lifecycle = MagicMock()
    lifecycle.get_interaction_strict = AsyncMock(return_value=aggregate)
    lifecycle.mark_interaction_terminal_reconciled = AsyncMock(return_value=True)
    service = HITLService(lifecycle=lifecycle)
    service._persistence = persistence
    service._emit_hitl_event = AsyncMock()

    with pytest.raises(RuntimeError, match="member checkpoint crash"):
        await service.reconcile_terminal_interaction(aggregate)
    assert service._emit_hitl_event.await_count == 0

    await service.reconcile_terminal_interaction(aggregate)

    assert [row["status"] for row in rows] == [member_status, member_status]
    assert all(row["owning_run_terminal_status"] == owning_status for row in rows)
    assert service._emit_hitl_event.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("aggregate_status", "member_status", "event_type"),
    [
        ("expired", "expired", "input_expired"),
        ("canceled", "canceled", "input_canceled"),
        ("failed", "canceled", "input_canceled"),
    ],
)
async def test_multi_question_terminal_order_closes_all_hitl_before_run(
    aggregate_status, member_status, event_type
):
    rows = _persisted_rows()
    aggregate = {
        **_aggregate(),
        "status": aggregate_status,
        "member_terminal_status": member_status,
        "owning_run_terminal_status": (
            "canceled" if aggregate_status == "canceled" else "failed"
        ),
        "terminal_reason": "terminal reason",
        "version": 4,
    }
    order: list[str] = []

    async def get_request(request_id):
        return deepcopy(next(row for row in rows if row["request_id"] == request_id))

    async def cas_request(request_id, *, expected_status, **updates):
        row = next(row for row in rows if row["request_id"] == request_id)
        if row["status"] != expected_status:
            return False
        row.update(updates)
        return True

    async def update_request(request_id, **updates):
        next(row for row in rows if row["request_id"] == request_id).update(updates)
        return True

    persistence = MagicMock()
    persistence.get_hitl_request = AsyncMock(side_effect=get_request)
    persistence.cas_update_hitl_request_strict = AsyncMock(side_effect=cas_request)
    persistence.update_hitl_request = AsyncMock(side_effect=update_request)
    persistence.get_and_clear_continuation_on_message = AsyncMock()
    persistence.get_and_clear_continuation_on_user_message = AsyncMock()
    lifecycle = MagicMock()
    lifecycle.get_interaction_strict = AsyncMock(return_value=aggregate)
    lifecycle.mark_interaction_terminal_reconciled = AsyncMock(
        side_effect=lambda *_args, **_kwargs: order.append("ownership_cleared") or True
    )
    terminal = MagicMock()
    terminal.terminalize_owning_run = AsyncMock(
        side_effect=lambda *_args, **_kwargs: order.append("run_terminalized")
    )
    service = HITLService(lifecycle=lifecycle)
    service._persistence = persistence
    service._terminal_lifecycle = terminal

    async def emit(*_args, request, **_kwargs):
        order.append(f"hitl:{request.request_id}:{event_type}")

    service._emit_hitl_event = AsyncMock(side_effect=emit)
    await service.reconcile_terminal_interaction(aggregate)

    assert order == [
        "hitl:request-1:" + event_type,
        "hitl:request-2:" + event_type,
        "ownership_cleared",
        "run_terminalized",
    ]


def test_persisted_hitl_models_forbid_authority_looking_extras():
    row = _persisted_rows()[0]
    for extra in ("source", "group_id", "authority_override"):
        with pytest.raises(ValidationError, match="Extra inputs"):
            HITLRequest(**{**row, extra: "untrusted"})

    snapshot = HITLRouteSnapshot(
        route=HITLApplicationRoute.SUPERVISOR_RUN,
        orchestration_run_id="run-1",
    )
    interaction = {
        "schema_version": 3,
        "interaction_id": "interaction-1",
        "room_id": "room-1",
        "user_message_id": "message-1",
        "orchestration_run_id": "run-1",
        "application_route": "supervisor_run",
        "public_source": "supervisor",
        "evidence_origin": "supervisor",
        "route_snapshot": snapshot,
        "route_fingerprint": snapshot.fingerprint,
        "creation_inventory": [{"request_id": "request-1", "prompt": "Ok?"}],
        "expected_request_count": 1,
    }
    with pytest.raises(ValidationError, match="Extra inputs"):
        HITLInteraction(**{**interaction, "group_id": "legacy"})
