from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import Message, Part, Task, TaskState, TaskStatus, TextPart
from common.types import MessageRole as Role
from execution.hitl.adapters import HITLTerminalLifecycleAdapter
from execution.hitl.service import HITLService
from execution.orchestration.run_reducer import record_hitl_terminalization
from execution.orchestration.run_store import InMemoryOrchestrationRunStore
from execution.task_tracking import A2ATaskTrackingService
from models.hitl import HITLPromptType, HITLRequest, HITLStatus
from models.orchestration import OrchestrationRunState, OrchestrationStatus
from models.run import RunState


def _interactive_task(*, prompt: str = "What is the insured email address?") -> Task:
    return Task(
        id="remote-task-1",
        context_id="remote-context-1",
        status=TaskStatus(
            state=TaskState.input_required,
            message=Message(
                role=Role.AGENT,
                message_id="remote-message-1",
                parts=[Part(root=TextPart(text=prompt))],
            ),
        ),
    )


@pytest.mark.asyncio
async def test_interactive_task_persists_authoritative_remote_ids_and_prompt():
    store = MagicMock()
    store.update_task_on_message = AsyncMock(return_value=True)
    service = A2ATaskTrackingService(store)

    result = await service._handle_task_result(
        _interactive_task(),
        message_id="agent-message-1",
        room_id="room-1",
        agent_name="Cyber Broker",
    )

    persisted = store.update_task_on_message.await_args.args[1]
    assert persisted["id"] == "remote-task-1"
    assert persisted["contextId"] == "remote-context-1"
    assert result["task_id"] == "remote-task-1"
    assert result["context_id"] == "remote-context-1"
    assert result["message"] == "What is the insured email address?"


@pytest.mark.asyncio
async def test_reply_json_rpc_error_becomes_failed_task_not_input_required():
    store = MagicMock()
    store.get_room_agent_message_by_message_id = AsyncMock(
        return_value=SimpleNamespace(
            message_id="agent-message-1",
            agent_id=None,
            agent_url="https://agent.example/a2a",
            message_content=None,
        )
    )
    store.generate_webhook_token.return_value = "token"
    store.hash_webhook_token.return_value = "hash"
    store.update_webhook_token_hash_on_message = AsyncMock(return_value=True)
    store.update_task_on_message = AsyncMock(return_value=True)
    service = A2ATaskTrackingService(store)

    result = await service.reply_to_task(
        message_id="agent-message-1",
        task_id="remote-task-1",
        context_id="remote-context-1",
        user_input="insured@example.com",
        webhook_base_url="",
        push_notification_timeout=30,
        default_request_timeout=30,
        send_hitl_reply=AsyncMock(
            return_value={
                "kind": "error",
                "error": {"code": -32602, "message": "Invalid params"},
            }
        ),
    )

    assert result["task_state"] == "failed"
    assert result["error_code"] == "a2a_protocol_error"
    assert result["blocking"] is True
    assert store.update_task_on_message.await_args.args[1]["id"] == "remote-task-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt",
    [
        "",
        "The agent needs additional information.",
        "PRIVATE_SENTINEL hidden",
        "Send a JSON object or DataPart containing client.name and client.industry.",
    ],
)
async def test_agent_hitl_rejects_missing_generic_or_private_prompt(prompt: str):
    service = HITLService()
    persistence = MagicMock()
    persistence.create_or_reuse_pending_hitl_request = AsyncMock()
    service._persistence = persistence

    result = await service.request_input(
        room_id="room-1",
        user_message_id="user-message-1",
        source="agent",
        prompt=prompt,
        a2a_task_id="remote-task-1",
        a2a_context_id="remote-context-1",
        continuation_message_id="agent-message-1",
    )

    assert result is None
    persistence.create_or_reuse_pending_hitl_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_auth_hitl_preserves_authentication_prompt_type():
    service = HITLService()
    persistence = MagicMock()
    persistence.count_hitl_requests_for_message = AsyncMock(return_value=0)
    persistence.get_room_user_message_by_message_id = AsyncMock(return_value=None)
    persistence.get_room_agent_message_by_message_id = AsyncMock(return_value=None)
    persistence.create_or_reuse_pending_hitl_request = AsyncMock(
        side_effect=lambda doc: (doc, True)
    )
    persistence.persist_pending_hitl_on_agent_message = AsyncMock(return_value=True)
    delivery = MagicMock()
    delivery.emit = AsyncMock()
    service._persistence = persistence
    service._delivery = delivery

    request = await service.request_input(
        room_id="room-1",
        user_message_id="user-message-1",
        source="agent",
        prompt="Authenticate with the carrier portal",
        prompt_type=HITLPromptType.AUTHENTICATION,
        a2a_task_id="remote-task-1",
        a2a_context_id="remote-context-1",
        continuation_message_id="agent-message-1",
    )

    assert request is not None
    assert request.prompt_type == HITLPromptType.AUTHENTICATION
    emitted = delivery.emit.await_args.args[0]
    assert emitted.prompt_type == "authentication"


@pytest.mark.asyncio
async def test_reused_legacy_agent_hitl_atomically_backfills_authoritative_ids():
    service = HITLService()
    persistence = MagicMock()
    persistence.count_hitl_requests_for_message = AsyncMock(return_value=0)
    persistence.get_room_user_message_by_message_id = AsyncMock(return_value=None)
    persistence.get_room_agent_message_by_message_id = AsyncMock(return_value=None)
    persistence.persist_pending_hitl_on_agent_message = AsyncMock(return_value=True)
    persistence.cas_update_hitl_request = AsyncMock(return_value=True)

    async def reuse_legacy(request_data):
        legacy = dict(request_data)
        legacy.update(
            {
                "request_id": "legacy-hitl-1",
                "a2a_task_id": "pending-old-context",
                "a2a_context_id": None,
            }
        )
        return legacy, False

    persistence.create_or_reuse_pending_hitl_request = AsyncMock(
        side_effect=reuse_legacy
    )
    delivery = MagicMock()
    delivery.emit = AsyncMock()
    service._persistence = persistence
    service._delivery = delivery

    result = await service.request_input(
        room_id="room-1",
        user_message_id="user-message-1",
        source="agent",
        prompt="What is the insured email address?",
        agent_id="agent-1",
        a2a_task_id="remote-task-2",
        a2a_context_id="remote-context-2",
        continuation_message_id="agent-message-1",
    )

    assert result is not None
    assert result.request_id == "legacy-hitl-1"
    assert result.a2a_task_id == "remote-task-2"
    assert result.a2a_context_id == "remote-context-2"
    persistence.cas_update_hitl_request.assert_awaited_once_with(
        "legacy-hitl-1",
        expected_status=HITLStatus.PENDING.value,
        a2a_task_id="remote-task-2",
        a2a_context_id="remote-context-2",
    )
    projection = persistence.persist_pending_hitl_on_agent_message.await_args.kwargs
    assert projection["a2a_task_id"] == "remote-task-2"
    assert projection["a2a_context_id"] == "remote-context-2"


@pytest.mark.asyncio
async def test_mismatched_reused_agent_hitl_is_not_silently_canceled():
    service = HITLService()
    persistence = MagicMock()
    persistence.count_hitl_requests_for_message = AsyncMock(return_value=0)
    persistence.get_room_user_message_by_message_id = AsyncMock(return_value=None)
    persistence.get_room_agent_message_by_message_id = AsyncMock(return_value=None)
    persistence.cas_update_hitl_request = AsyncMock(return_value=True)

    async def reuse_other_request(request_data):
        existing = dict(request_data)
        existing.update(
            {
                "request_id": "existing-hitl-1",
                "continuation_message_id": "different-agent-message",
                "display_message_id": "different-agent-message",
            }
        )
        return existing, False

    persistence.create_or_reuse_pending_hitl_request = AsyncMock(
        side_effect=reuse_other_request
    )
    service._persistence = persistence

    result = await service.request_input(
        room_id="room-1",
        user_message_id="user-message-1",
        source="agent",
        prompt="What is the insured email address?",
        agent_id="agent-1",
        a2a_task_id="remote-task-2",
        a2a_context_id="remote-context-2",
        continuation_message_id="agent-message-1",
    )

    assert result is None
    persistence.cas_update_hitl_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_hydration_excludes_malformed_agent_hitl_records():
    valid_agent = HITLRequest(
        request_id="valid-agent-hitl",
        room_id="room-1",
        user_message_id="user-message-1",
        source="agent",
        prompt="What is the insured email address?",
        prompt_type=HITLPromptType.TEXT,
        a2a_task_id="remote-task-1",
        a2a_context_id="remote-context-1",
        continuation_message_id="agent-message-1",
        display_message_id="agent-message-1",
    ).model_dump(mode="json")
    supervisor = HITLRequest(
        request_id="supervisor-hitl",
        room_id="room-1",
        user_message_id="user-message-1",
        source="supervisor",
        prompt="Choose a market",
        prompt_type=HITLPromptType.TEXT,
    ).model_dump(mode="json")
    malformed = []
    for request_id, updates in (
        ("missing-task", {"a2a_task_id": None}),
        ("provisional-task", {"a2a_task_id": "pending-context"}),
        ("missing-context", {"a2a_context_id": None}),
        ("provisional-context", {"a2a_context_id": "relay-pending-context"}),
        ("generic-prompt", {"prompt": "The agent needs additional information."}),
    ):
        document = dict(valid_agent)
        document.update({"request_id": request_id, **updates})
        malformed.append(document)

    persistence = MagicMock()
    persistence.get_pending_hitl_requests = AsyncMock(
        return_value=[valid_agent, supervisor, *malformed]
    )
    persistence.get_pending_hitl_requests_for_message = AsyncMock(
        return_value=[valid_agent, supervisor, *malformed]
    )
    service = HITLService()
    service._persistence = persistence

    room_pending = await service.get_pending_requests("room-1")
    message_pending = await service.get_pending_requests_for_message("user-message-1")

    assert [request.request_id for request in room_pending] == [
        "valid-agent-hitl",
        "supervisor-hitl",
    ]
    assert [request.request_id for request in message_pending] == [
        "valid-agent-hitl",
        "supervisor-hitl",
    ]


def test_hitl_terminalization_clears_pending_state_and_terminates_run():
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="user-message-1",
        goal="Prepare submission",
        candidate_agent_ids=["agent-1"],
        status=OrchestrationStatus.AWAITING_USER,
        pending_hitl_request_ids=["hitl-1"],
        open_questions=[{"request_id": "hitl-1", "status": "open", "resolved": False}],
    )

    updated = record_hitl_terminalization(
        state,
        request_id="hitl-1",
        terminal_status="failed",
        reason="Agent did not acknowledge the task",
    )

    assert updated.status == OrchestrationStatus.FAILED
    assert updated.pending_hitl_request_ids == []
    assert updated.open_questions[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_terminal_lifecycle_projects_orchestration_and_public_run():
    store = InMemoryOrchestrationRunStore()
    await store.create_run(
        OrchestrationRunState(
            run_id="run-1",
            room_id="room-1",
            user_message_id="user-message-1",
            goal="Prepare submission",
            candidate_agent_ids=["agent-1"],
            status=OrchestrationStatus.AWAITING_USER,
            pending_hitl_request_ids=["hitl-1"],
            open_questions=[{"request_id": "hitl-1", "status": "open"}],
        )
    )
    lifecycle = SimpleNamespace(project_run_state=AsyncMock(return_value={}))
    adapter = HITLTerminalLifecycleAdapter(store, lifecycle)
    request = SimpleNamespace(
        request_id="hitl-1",
        room_id="room-1",
        user_message_id="user-message-1",
        orchestration_run_id="run-1",
        client_request_id="client-1",
        status=HITLStatus.EXPIRED,
    )

    await adapter.terminalize_owning_run(
        request,
        terminal_status="failed",
        reason="Human input request expired",
    )

    saved = await store.get_run("run-1")
    assert saved is not None
    assert saved.status == OrchestrationStatus.FAILED
    assert saved.pending_hitl_request_ids == []
    assert (
        lifecycle.project_run_state.await_args.kwargs["target_state"] == RunState.FAILED
    )
    assert saved.terminal_reason == "Human input request expired"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task_id", "context_id"),
    [
        (None, "remote-context-1"),
        ("remote-task-1", None),
        ("", "remote-context-1"),
        ("pending-local", "remote-context-1"),
        ("relay-pending-local", "remote-context-1"),
    ],
)
async def test_agent_hitl_requires_authoritative_remote_ids(task_id, context_id):
    service = HITLService()
    persistence = MagicMock()
    persistence.create_or_reuse_pending_hitl_request = AsyncMock()
    service._persistence = persistence

    result = await service.request_input(
        room_id="room-1",
        user_message_id="user-message-1",
        source="agent",
        prompt="What is the insured email address?",
        a2a_task_id=task_id,
        a2a_context_id=context_id,
        continuation_message_id="agent-message-1",
    )

    assert result is None
    persistence.create_or_reuse_pending_hitl_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_reply_returns_rotated_authoritative_continuation_ids():
    store = MagicMock()
    store.get_room_agent_message_by_message_id = AsyncMock(
        return_value=SimpleNamespace(
            message_id="agent-message-1",
            agent_id=None,
            agent_url="https://agent.example/a2a",
            message_content=None,
        )
    )
    store.generate_webhook_token.return_value = "token"
    store.hash_webhook_token.return_value = "hash"
    store.update_webhook_token_hash_on_message = AsyncMock(return_value=True)
    store.update_task_on_message = AsyncMock(return_value=True)
    service = A2ATaskTrackingService(store)
    rotated = _interactive_task(prompt="What is the annual revenue?")
    rotated.id = "remote-task-2"
    rotated.context_id = "remote-context-2"

    result = await service.reply_to_task(
        message_id="agent-message-1",
        task_id="remote-task-1",
        context_id="remote-context-1",
        user_input="insured@example.com",
        webhook_base_url="",
        push_notification_timeout=30,
        default_request_timeout=30,
        send_hitl_reply=AsyncMock(
            return_value={
                "kind": "task",
                "result": rotated.model_dump(mode="json", by_alias=True),
            }
        ),
    )

    assert result["task_id"] == "remote-task-2"
    assert result["context_id"] == "remote-context-2"
    request = SimpleNamespace(
        continuation_message_id="agent-message-1",
        a2a_task_id="remote-task-1",
        a2a_context_id="remote-context-1",
        room_id="room-1",
        user_message_id="user-message-1",
        agent_id="agent-1",
        agent_name="Agent",
        display_message_id="agent-message-1",
        orchestration_run_id="run-1",
        agent_prompt_hash=None,
        prompt="What is the insured email address?",
    )
    hitl = HITLService()
    hitl._persistence = SimpleNamespace(reset_last_notified_state=AsyncMock())
    hitl._agent_reply = SimpleNamespace(reply_to_task=AsyncMock(return_value=result))
    followup = SimpleNamespace(
        request_id="hitl-2",
        prompt="What is the annual revenue?",
        prompt_type="text",
        agent_id="agent-1",
        agent_name="Agent",
        display_message_id="agent-message-1",
        continuation_message_id="agent-message-1",
        a2a_task_id="remote-task-2",
        a2a_context_id="remote-context-2",
    )
    hitl.request_input = AsyncMock(return_value=followup)

    chained = await hitl._handle_agent_response(request, "insured@example.com")

    assert hitl.request_input.await_args.kwargs["a2a_task_id"] == "remote-task-2"
    assert hitl.request_input.await_args.kwargs["a2a_context_id"] == "remote-context-2"
    assert chained["a2a_task_id"] == "remote-task-2"
    assert chained["a2a_context_id"] == "remote-context-2"


@pytest.mark.asyncio
async def test_cancel_failure_persists_failed_run_outcome_for_reconciliation():
    service = HITLService()
    persistence = MagicMock()
    pending = {
        "request_id": "hitl-1",
        "room_id": "room-1",
        "user_message_id": "user-message-1",
        "source": "supervisor",
        "prompt": "Clarify?",
        "status": "pending",
        "orchestration_run_id": "run-1",
    }
    persistence.get_hitl_request = AsyncMock(return_value=pending)
    persistence.get_hitl_group_requests = AsyncMock(return_value=[])
    persistence.cas_update_hitl_request = AsyncMock(return_value=True)
    persistence.get_and_clear_continuation_on_message = AsyncMock()
    persistence.get_and_clear_continuation_on_user_message = AsyncMock()
    persistence.update_hitl_request = AsyncMock(return_value=True)
    service._persistence = persistence
    service._delivery = SimpleNamespace(emit=AsyncMock())
    lifecycle = SimpleNamespace(
        terminalize_owning_run=AsyncMock(side_effect=RuntimeError("temporary"))
    )
    service._terminal_lifecycle = lifecycle

    with pytest.raises(RuntimeError, match="side effects remain pending"):
        await service.cancel_request(
            "hitl-1", failure_reason="Agent did not acknowledge the task"
        )

    cas_kwargs = persistence.cas_update_hitl_request.await_args.kwargs
    assert cas_kwargs["owning_run_terminal_status"] == "failed"
    assert cas_kwargs["owning_run_terminal_reason"] == (
        "Agent did not acknowledge the task"
    )
    persisted_terminal = {
        **pending,
        "status": "canceled",
        "cancellation_reconciled": False,
        "owning_run_terminal_status": "failed",
        "owning_run_terminal_reason": "Agent did not acknowledge the task",
    }
    persistence.get_hitl_request.return_value = persisted_terminal
    lifecycle.terminalize_owning_run.side_effect = None

    await service.cancel_request("hitl-1")

    assert lifecycle.terminalize_owning_run.await_args.kwargs == {
        "terminal_status": "failed",
        "reason": "Agent did not acknowledge the task",
    }


@pytest.mark.asyncio
async def test_terminal_event_append_failure_is_retryable_and_exactly_once():
    store = InMemoryOrchestrationRunStore()
    await store.create_run(
        OrchestrationRunState(
            run_id="run-1",
            room_id="room-1",
            user_message_id="user-message-1",
            goal="Prepare submission",
            candidate_agent_ids=["agent-1"],
            status=OrchestrationStatus.AWAITING_USER,
            pending_hitl_request_ids=["hitl-1"],
            open_questions=[{"request_id": "hitl-1", "status": "open"}],
        )
    )
    original_append = store.append_event
    append_attempts = 0

    async def flaky_append(event):
        nonlocal append_attempts
        append_attempts += 1
        if append_attempts == 1:
            raise RuntimeError("temporary event failure")
        return await original_append(event)

    store.append_event = flaky_append
    lifecycle = SimpleNamespace(project_run_state=AsyncMock(return_value={}))
    adapter = HITLTerminalLifecycleAdapter(store, lifecycle)
    request = SimpleNamespace(
        request_id="hitl-1",
        room_id="room-1",
        user_message_id="user-message-1",
        orchestration_run_id="run-1",
        client_request_id="client-1",
    )

    with pytest.raises(RuntimeError, match="temporary event failure"):
        await adapter.terminalize_owning_run(
            request, terminal_status="failed", reason="Input expired"
        )

    await adapter.terminalize_owning_run(
        request, terminal_status="failed", reason="Input expired"
    )
    await adapter.terminalize_owning_run(
        request, terminal_status="failed", reason="Input expired"
    )

    assert append_attempts == 3
    assert len(store._events_by_run["run-1"]) == 1
    saved = await store.get_run("run-1")
    assert saved is not None
    assert saved.terminal_reason == "Input expired"
