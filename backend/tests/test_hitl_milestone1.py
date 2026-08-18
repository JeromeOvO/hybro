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
from models.hitl import HITLRequest, HITLStatus
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
        interaction_id="interaction-1",
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
    hitl.request_interaction = AsyncMock(return_value=[followup])

    chained = await hitl._handle_agent_response(request, "insured@example.com")

    snapshot = hitl.request_interaction.await_args.kwargs["route_snapshot"]
    assert snapshot.task_id == "remote-task-2"
    assert snapshot.context_id == "remote-context-2"
    assert chained["a2a_task_id"] == "remote-task-2"
    assert chained["a2a_context_id"] == "remote-context-2"


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


def _agent_hitl_request(**updates) -> HITLRequest:
    data = {
        "schema_version": 3,
        "request_id": "hitl-old",
        "interaction_id": "interaction-old",
        "question_index": 0,
        "question_count": 1,
        "room_id": "room-123",
        "user_message_id": "user-msg-456",
        "application_route": "a2a_resume",
        "public_source": "agent",
        "evidence_origin": "agent",
        "prompt": "Need more information.",
        "agent_id": "agent-1",
        "agent_name": "Agent",
        "a2a_task_id": "task-1",
        "a2a_context_id": "context-1",
        "continuation_message_id": "agent-message-1",
        "display_message_id": "agent-message-1",
        "orchestration_run_id": "run-1",
    }
    data.update(updates)
    return HITLRequest(**data)


@pytest.mark.asyncio
async def test_followup_without_interactive_state_fails_without_new_interaction():
    request = _agent_hitl_request()
    service = HITLService()
    service._persistence = SimpleNamespace(reset_last_notified_state=AsyncMock())
    service._agent_reply = SimpleNamespace(
        reply_to_task=AsyncMock(
            return_value={"blocking": True, "task_state": None, "response_text": ""}
        )
    )
    service.request_interaction = AsyncMock()

    result = await service._handle_agent_response(request, "answer")

    assert result["routing_failed"] is True
    assert result["error_code"] == "invalid_interactive_prompt"
    service.request_interaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_safe_followup_creates_new_interaction_with_rotated_route():
    request = _agent_hitl_request(prompt="Where do you want to go?")
    service = HITLService()
    service._persistence = SimpleNamespace(reset_last_notified_state=AsyncMock())
    service._agent_reply = SimpleNamespace(
        reply_to_task=AsyncMock(
            return_value={
                "blocking": True,
                "task_state": "input-required",
                "response_text": "What is your budget?",
                "task_id": "task-2",
                "context_id": "context-2",
            }
        )
    )
    followup = request.model_copy(
        update={"request_id": "hitl-next", "interaction_id": "interaction-next"}
    )
    service.request_interaction = AsyncMock(return_value=[followup])

    result = await service._handle_agent_response(request, "Paris")

    assert result["followup_hitl_request_id"] == "hitl-next"
    kwargs = service.request_interaction.await_args.kwargs
    assert kwargs["questions"][0]["prompt"] == "What is your budget?"
    assert kwargs["route_snapshot"].task_id == "task-2"
    assert kwargs["route_snapshot"].context_id == "context-2"


@pytest.mark.asyncio
async def test_unchanged_followup_returns_control_without_new_interaction():
    request = _agent_hitl_request(prompt="Please provide the complete submission.")
    service = HITLService()
    service._persistence = SimpleNamespace(reset_last_notified_state=AsyncMock())
    service._agent_reply = SimpleNamespace(
        reply_to_task=AsyncMock(
            return_value={
                "blocking": True,
                "task_state": "input-required",
                "response_text": " Please provide the complete submission. ",
            }
        )
    )
    service.request_interaction = AsyncMock()

    result = await service._handle_agent_response(request, "answer")

    assert result["agent_no_progress"] is True
    assert result["resume_execution"] is True
    service.request_interaction.assert_not_awaited()
