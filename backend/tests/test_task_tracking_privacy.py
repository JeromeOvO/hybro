import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import (
    Artifact,
    FileContent,
    FilePart,
    Message,
    MessageRole,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from execution.task_tracking import (
    A2ATaskTrackingService,
    public_persisted_task_data,
)
from models.room import MessageContent, RoomAgentMessage


def _message(role: MessageRole, text: str) -> Message:
    return Message(
        message_id=f"{role.value}-message",
        role=role,
        parts=[Part(root=TextPart(text=text))],
    )


def _status_text(task_data: dict) -> str | None:
    status_message = task_data.get("status", {}).get("message")
    if not isinstance(status_message, dict):
        return None
    parts = status_message.get("parts") or []
    if not parts:
        return None
    first = parts[0]
    root = first.get("root", first) if isinstance(first, dict) else {}
    return root.get("text") if isinstance(root, dict) else None


def test_remote_task_sanitizer_drops_non_agent_status_and_all_metadata():
    private_sentinel = "PRIVATE_SENTINEL_remote_task_metadata"
    task = Task(
        id="remote-task",
        context_id="remote-context",
        status=TaskStatus(
            state=TaskState.failed,
            message=_message(MessageRole.USER, private_sentinel),
        ),
        metadata={
            "prompt": private_sentinel,
            "hitl_prompt": private_sentinel,
            "choices": [private_sentinel],
            "hitl_choices": [private_sentinel],
            "hitl_request_id": private_sentinel,
        },
    )

    persisted = public_persisted_task_data(task)

    assert _status_text(persisted) == "Task failed"
    assert persisted["metadata"] is None
    assert private_sentinel not in json.dumps(persisted)


def test_completed_remote_task_sanitizer_drops_status_message():
    private_sentinel = "PRIVATE_SENTINEL_completed_status_message"
    task = Task(
        id="remote-task",
        context_id="remote-context",
        status=TaskStatus(
            state=TaskState.completed,
            message=Message(
                message_id="agent-status",
                role=MessageRole.AGENT,
                parts=[Part(root=TextPart(text=private_sentinel))],
                metadata={"private": "do not persist"},
            ),
        ),
    )

    persisted = public_persisted_task_data(task)

    assert persisted["status"]["message"] is None
    assert private_sentinel not in json.dumps(persisted)
    assert "do not persist" not in json.dumps(persisted)


def test_completed_remote_task_sanitizer_drops_all_history():
    private_sentinel = "PRIVATE_SENTINEL_completed_history"
    task = Task(
        id="remote-task",
        context_id="remote-context",
        status=TaskStatus(state=TaskState.completed),
        history=[
            _message(MessageRole.USER, private_sentinel),
            _message(MessageRole.AGENT, "Completed agent-role history"),
        ],
    )

    persisted = public_persisted_task_data(task)

    assert persisted["history"] is None
    assert "Completed agent-role history" not in json.dumps(persisted)
    assert private_sentinel not in json.dumps(persisted)


@pytest.mark.parametrize(
    ("state", "safe_status_text"),
    [
        (TaskState.working, None),
        (TaskState.input_required, None),
        (TaskState.auth_required, None),
        (TaskState.policy_required, None),
        (TaskState.failed, "Task failed"),
        (TaskState.canceled, "Task was canceled"),
        (TaskState.rejected, "Task was rejected by the agent"),
        (TaskState.expired, "Task expired"),
    ],
)
def test_non_completed_public_projection_drops_remote_history_status_artifacts_and_metadata(
    state,
    safe_status_text,
):
    private_sentinel = f"PRIVATE_SENTINEL_{state.value}_public_projection"
    task = Task(
        id="remote-task",
        context_id="remote-context",
        status=TaskStatus(
            state=state,
            message=_message(MessageRole.AGENT, private_sentinel),
        ),
        history=[
            _message(MessageRole.USER, private_sentinel),
            _message(MessageRole.AGENT, private_sentinel),
        ],
        artifacts=[
            Artifact(
                artifact_id="artifact-1",
                name="remote-output",
                parts=[Part(root=TextPart(text="Public artifact text"))],
                metadata={"private": private_sentinel},
            )
        ],
        metadata={
            "prompt": private_sentinel,
            "hitl_prompt": private_sentinel,
            "hitl_request_id": private_sentinel,
        },
    )

    persisted = public_persisted_task_data(task)

    assert persisted.get("metadata") is None
    assert persisted.get("history") in (None, [])
    assert persisted.get("artifacts") in (None, [])
    assert _status_text(persisted) == safe_status_text
    assert private_sentinel not in json.dumps(persisted)


@pytest.mark.parametrize(
    ("state", "safe_status_text"),
    [
        ("policy-required", None),
        ("expired", "Task expired"),
    ],
)
def test_raw_remote_policy_and_expired_states_use_public_projection(
    state,
    safe_status_text,
):
    private_sentinel = f"PRIVATE_SENTINEL_{state}_raw_projection"

    class RawTask:
        def model_dump(self, *_, **__):
            return {
                "id": "remote-task",
                "kind": "task",
                "contextId": "remote-context",
                "status": {
                    "state": state,
                    "message": {
                        "kind": "message",
                        "role": "agent",
                        "messageId": "private-status",
                        "parts": [{"kind": "text", "text": private_sentinel}],
                    },
                },
                "history": [
                    {
                        "kind": "message",
                        "role": "agent",
                        "messageId": "private-history",
                        "parts": [{"kind": "text", "text": private_sentinel}],
                    }
                ],
                "metadata": {"prompt": private_sentinel},
            }

    persisted = public_persisted_task_data(RawTask())

    assert persisted.get("history") in (None, [])
    assert _status_text(persisted) == safe_status_text
    assert persisted.get("metadata") is None
    assert private_sentinel not in json.dumps(persisted)


def test_completed_projection_sanitizes_artifact_metadata_but_preserves_delivery_fields():
    private_sentinel = "PRIVATE_SENTINEL_completed_artifact_metadata"
    task = Task(
        id="remote-task",
        context_id="remote-context",
        status=TaskStatus(state=TaskState.completed),
        artifacts=[
            Artifact(
                artifact_id="artifact-1",
                name="result-file",
                parts=[
                    Part(
                        root=FilePart(
                            file=FileContent(
                                uri="https://storage.example/result.csv",
                                mimeType="text/csv",
                                name="result.csv",
                            ),
                            metadata={
                                "s3_key": "artifacts/room/msg/result.csv",
                                "private": private_sentinel,
                            },
                        )
                    )
                ],
                metadata={"private": private_sentinel},
            )
        ],
    )

    persisted = public_persisted_task_data(task)
    part = persisted["artifacts"][0]["parts"][0]
    part_root = part.get("root", part)

    assert persisted["artifacts"][0]["metadata"] is None
    assert part_root["file"]["uri"] == "https://storage.example/result.csv"
    assert part_root["file"]["mimeType"] == "text/csv"
    assert part_root["file"]["name"] == "result.csv"
    assert part_root["metadata"] == {"s3_key": "artifacts/room/msg/result.csv"}
    assert private_sentinel not in json.dumps(persisted)


def test_completed_projection_drops_unaddressable_inline_file_part():
    private_bytes = "PRIVATE_SENTINEL_inline_file_bytes"
    task = Task(
        id="remote-task",
        context_id="remote-context",
        status=TaskStatus(state=TaskState.completed),
        artifacts=[
            Artifact(
                artifact_id="artifact-1",
                name="result-file",
                parts=[
                    Part(
                        root=FilePart(
                            file=FileContent(
                                bytes=private_bytes,
                                mimeType="text/plain",
                                name="result.txt",
                            ),
                            metadata={
                                "s3_key": "artifacts/room/msg/result.txt",
                                "private": "drop-me",
                            },
                        )
                    )
                ],
            )
        ],
    )

    persisted = public_persisted_task_data(task)

    assert persisted["artifacts"][0]["parts"] == []
    assert private_bytes not in json.dumps(persisted)
    Task.model_validate(persisted)


@pytest.mark.asyncio
async def test_persist_failed_task_uses_safe_public_error_text():
    private_sentinel = "PRIVATE_SENTINEL_contact_agent_error"
    store = MagicMock()
    store.update_task_on_message = AsyncMock(return_value=True)
    service = A2ATaskTrackingService(store)

    await service._persist_failed_task(
        "agent-message-1",
        "context-1",
        f"Failed to contact agent: {private_sentinel}",
    )

    persisted = store.update_task_on_message.await_args.args[1]
    assert _status_text(persisted) == "Task failed"
    assert private_sentinel not in json.dumps(persisted)


@pytest.mark.asyncio
async def test_terminal_failed_task_result_projects_before_persisting_and_responding():
    private_sentinel = "PRIVATE_SENTINEL_terminal_failed_result"
    store = MagicMock()
    store.update_task_on_message = AsyncMock(return_value=True)
    service = A2ATaskTrackingService(store)
    task = Task(
        id="remote-task",
        context_id="remote-context",
        status=TaskStatus(
            state=TaskState.failed,
            message=_message(MessageRole.AGENT, private_sentinel),
        ),
        history=[
            _message(MessageRole.USER, private_sentinel),
            _message(MessageRole.AGENT, private_sentinel),
        ],
        artifacts=[
            Artifact(
                artifact_id="partial-artifact",
                name="partial",
                parts=[Part(root=TextPart(text=private_sentinel))],
                metadata={"private": private_sentinel},
            )
        ],
        metadata={"remote_error": private_sentinel},
    )

    result = await service._handle_terminal_task_result(
        task,
        message_id="agent-message-1",
        room_id="room-1",
    )

    persisted = store.update_task_on_message.await_args.args[1]
    update_kwargs = store.update_task_on_message.await_args.kwargs
    assert persisted["status"]["state"] == "failed"
    assert _status_text(persisted) == "Task failed"
    assert persisted["artifacts"] is None
    assert persisted["history"] is None
    assert persisted["metadata"] is None
    assert update_kwargs["message_text"] is None
    assert result["status"] == "failed"
    assert result["content"] is None
    assert result["error"] == "Task failed"
    assert "parts" not in result
    assert private_sentinel not in json.dumps(persisted)
    assert private_sentinel not in json.dumps(update_kwargs)
    assert private_sentinel not in json.dumps(result)


@pytest.mark.asyncio
async def test_terminal_completed_task_result_ignores_status_message_fallback():
    private_sentinel = "PRIVATE_SENTINEL_terminal_completed_status"
    store = MagicMock()
    store.update_task_on_message = AsyncMock(return_value=True)
    service = A2ATaskTrackingService(store)
    task = Task(
        id="remote-task",
        context_id="remote-context",
        status=TaskStatus(
            state=TaskState.completed,
            message=_message(MessageRole.AGENT, private_sentinel),
        ),
    )

    result = await service._handle_terminal_task_result(
        task,
        message_id="agent-message-1",
        room_id="room-1",
    )

    persisted = store.update_task_on_message.await_args.args[1]
    update_kwargs = store.update_task_on_message.await_args.kwargs
    assert persisted["status"]["message"] is None
    assert update_kwargs["message_text"] is None
    assert result["status"] == "completed"
    assert result["content"] is None
    assert "message" not in result
    assert private_sentinel not in json.dumps(persisted)
    assert private_sentinel not in json.dumps(update_kwargs)
    assert private_sentinel not in json.dumps(result)


@pytest.mark.asyncio
async def test_blocking_hitl_reply_rebuilds_trusted_hitl_metadata_from_local_request():
    private_sentinel = "PRIVATE_SENTINEL_remote_hitl_spoof"
    spoofed_task_metadata = {
        "hitl_request_id": "local-hitl-request",
        "hitl_prompt": private_sentinel,
        "hitl_prompt_type": "choice",
        "hitl_choices": [private_sentinel],
        "hitl_a2a_task_id": "spoofed-task",
        "hitl_a2a_context_id": "spoofed-context",
        "hitl_group_id": "spoofed-group",
        "hitl_group_total": 99,
        "hitl_group_index": 98,
        "user_answer": private_sentinel,
    }
    authoritative_hitl_metadata = {
        "hitl_request_id": "local-hitl-request",
        "hitl_prompt": "Choose the approved public option",
        "hitl_prompt_type": "choice",
        "hitl_choices": ["Approve", "Reject"],
        "hitl_a2a_task_id": "remote-task",
        "hitl_a2a_context_id": "remote-context",
        "hitl_group_id": "local-group",
        "hitl_group_total": 2,
        "hitl_group_index": 1,
        "user_answer": "Approve",
    }
    existing_task = Task(
        id="remote-task",
        context_id="remote-context",
        status=TaskStatus(state=TaskState.input_required),
        metadata=spoofed_task_metadata,
    )
    message = RoomAgentMessage(
        room_id="room-1",
        message_id="agent-message-1",
        agent_id="agent-1",
        agent_url="https://agent.example",
        message_content=MessageContent(message_task=existing_task),
    )
    store = MagicMock()
    store.get_room_agent_message_by_message_id = AsyncMock(return_value=message)
    store.generate_webhook_token.return_value = "webhook-token"
    store.hash_webhook_token.return_value = "webhook-token-hash"
    store.update_webhook_token_hash_on_message = AsyncMock(return_value=True)
    store.update_task_on_message = AsyncMock(return_value=True)
    store.get_hitl_request = AsyncMock(
        return_value={
            "request_id": "local-hitl-request",
            "room_id": "room-1",
            "source": "agent",
            "agent_id": "agent-1",
            "display_message_id": "agent-message-1",
            "a2a_task_id": "remote-task",
            "a2a_context_id": "remote-context",
            "prompt": "Choose the approved public option",
            "prompt_type": "choice",
            "choices": ["Approve", "Reject"],
            "group_id": "local-group",
            "group_total": 2,
            "group_index": 1,
            "user_input": "Approve",
        }
    )
    service = A2ATaskTrackingService(store)

    remote_response = {
        "kind": "task",
        "result": {
            "kind": "task",
            "id": "remote-task",
            "contextId": "remote-context",
            "status": {
                "state": "completed",
                "message": {
                    "kind": "message",
                    "messageId": "private-status",
                    "role": "user",
                    "parts": [{"kind": "text", "text": private_sentinel}],
                },
            },
            "metadata": {
                "prompt": private_sentinel,
                "hitl_prompt": private_sentinel,
                "choices": [private_sentinel],
                "hitl_choices": [private_sentinel],
            },
            "artifacts": [
                {
                    "artifactId": "final-artifact",
                    "name": "response",
                    "parts": [
                        {"kind": "text", "text": "Public final agent result"}
                    ],
                }
            ],
        },
        "error": None,
    }
    send_hitl_reply = AsyncMock(return_value=remote_response)

    result = await service.reply_to_task(
        message_id=message.message_id,
        task_id="remote-task",
        context_id="remote-context",
        user_input="Approve",
        webhook_base_url="",
        push_notification_timeout=5.0,
        default_request_timeout=30.0,
        send_hitl_reply=send_hitl_reply,
    )

    persisted = store.update_task_on_message.await_args.args[1]
    assert persisted["id"] == "remote-task"
    assert persisted["contextId"] == "remote-context"
    assert persisted["status"]["state"] == "completed"
    assert persisted["status"]["message"] is None
    assert persisted["metadata"] == authoritative_hitl_metadata
    assert persisted["artifacts"][0]["parts"][0]["text"] == (
        "Public final agent result"
    )
    assert private_sentinel not in json.dumps(persisted)
    assert result == {
        "status": "sent",
        "blocking": True,
        "task_state": "completed",
        "response_text": "Public final agent result",
    }


@pytest.mark.asyncio
async def test_blocking_hitl_reply_drops_spoofed_existing_hitl_metadata():
    private_sentinel = "PRIVATE_SENTINEL_existing_hitl_metadata_spoof"
    existing_task = Task(
        id="remote-task",
        context_id="remote-context",
        status=TaskStatus(state=TaskState.input_required),
        metadata={
            "hitl_request_id": "spoofed-hitl-request",
            "hitl_prompt": private_sentinel,
            "hitl_prompt_type": "choice",
            "hitl_choices": [private_sentinel],
            "hitl_group_id": private_sentinel,
            "hitl_group_total": 2,
            "hitl_group_index": 0,
            "user_answer": private_sentinel,
        },
    )
    message = RoomAgentMessage(
        room_id="room-1",
        message_id="agent-message-1",
        agent_id="agent-1",
        agent_url="https://agent.example",
        message_content=MessageContent(message_task=existing_task),
    )
    store = MagicMock()
    store.get_room_agent_message_by_message_id = AsyncMock(return_value=message)
    store.get_hitl_request = AsyncMock(
        return_value={
            "request_id": "spoofed-hitl-request",
            "room_id": "other-room",
            "source": "agent",
            "display_message_id": "agent-message-1",
            "prompt": private_sentinel,
        }
    )
    store.generate_webhook_token.return_value = "webhook-token"
    store.hash_webhook_token.return_value = "webhook-token-hash"
    store.update_webhook_token_hash_on_message = AsyncMock(return_value=True)
    store.update_task_on_message = AsyncMock(return_value=True)
    store.get_hitl_request = AsyncMock(
        return_value={
            "request_id": "local-hitl-request",
            "room_id": "room-1",
            "source": "agent",
            "agent_id": "agent-1",
            "display_message_id": "agent-message-1",
            "a2a_task_id": "remote-task",
            "a2a_context_id": "remote-context",
            "prompt": "Continue?",
            "prompt_type": "text",
            "choices": ["Continue"],
        }
    )
    service = A2ATaskTrackingService(store)
    send_hitl_reply = AsyncMock(
        return_value={
            "kind": "task",
            "result": {
                "kind": "task",
                "id": "remote-task",
                "contextId": "remote-context",
                "status": {"state": "completed"},
                "artifacts": [
                    {
                        "artifactId": "final-artifact",
                        "name": "response",
                        "parts": [{"kind": "text", "text": "Public final result"}],
                    }
                ],
            },
            "error": None,
        }
    )

    await service.reply_to_task(
        message_id=message.message_id,
        task_id="remote-task",
        context_id="remote-context",
        user_input="Approve",
        webhook_base_url="",
        push_notification_timeout=5.0,
        default_request_timeout=30.0,
        send_hitl_reply=send_hitl_reply,
    )

    persisted = store.update_task_on_message.await_args.args[1]
    assert persisted["metadata"] is None
    assert private_sentinel not in json.dumps(persisted)


@pytest.mark.asyncio
async def test_blocking_hitl_reply_uses_projected_task_for_public_response_text():
    private_sentinel = "PRIVATE_SENTINEL_blocking_reply_non_completed"
    existing_task = Task(
        id="remote-task",
        context_id="remote-context",
        status=TaskStatus(state=TaskState.input_required),
        metadata={"hitl_request_id": "local-hitl-request"},
    )
    message = RoomAgentMessage(
        room_id="room-1",
        message_id="agent-message-1",
        agent_id="agent-1",
        agent_url="https://agent.example",
        message_content=MessageContent(message_task=existing_task),
    )
    store = MagicMock()
    store.get_room_agent_message_by_message_id = AsyncMock(return_value=message)
    store.generate_webhook_token.return_value = "webhook-token"
    store.hash_webhook_token.return_value = "webhook-token-hash"
    store.update_webhook_token_hash_on_message = AsyncMock(return_value=True)
    store.update_task_on_message = AsyncMock(return_value=True)
    store.get_hitl_request = AsyncMock(
        return_value={
            "request_id": "local-hitl-request",
            "room_id": "room-1",
            "source": "agent",
            "agent_id": "agent-1",
            "display_message_id": "agent-message-1",
            "a2a_task_id": "remote-task",
            "a2a_context_id": "remote-context",
            "prompt": "Continue?",
            "prompt_type": "text",
            "choices": ["Continue"],
        }
    )
    service = A2ATaskTrackingService(store)
    remote_response = {
        "kind": "task",
        "result": {
            "kind": "task",
            "id": "remote-task",
            "contextId": "remote-context",
            "status": {
                "state": "input-required",
                "message": {
                    "kind": "message",
                    "messageId": "private-status",
                    "role": "agent",
                    "parts": [{"kind": "text", "text": private_sentinel}],
                },
            },
            "history": [
                {
                    "kind": "message",
                    "messageId": "private-history",
                    "role": "agent",
                    "parts": [{"kind": "text", "text": private_sentinel}],
                }
            ],
            "artifacts": [
                {
                    "artifactId": "private-artifact",
                    "parts": [{"kind": "text", "text": private_sentinel}],
                }
            ],
            "metadata": {"prompt": private_sentinel},
        },
        "error": None,
    }
    send_hitl_reply = AsyncMock(return_value=remote_response)

    result = await service.reply_to_task(
        message_id=message.message_id,
        task_id="remote-task",
        context_id="remote-context",
        user_input="Approve",
        webhook_base_url="",
        push_notification_timeout=5.0,
        default_request_timeout=30.0,
        send_hitl_reply=send_hitl_reply,
    )

    persisted = store.update_task_on_message.await_args.args[1]
    update_kwargs = store.update_task_on_message.await_args.kwargs
    assert persisted["status"]["state"] == "input-required"
    assert persisted["status"]["message"] is None
    assert persisted.get("history") in (None, [])
    assert persisted.get("artifacts") in (None, [])
    assert persisted["metadata"] == {
        "hitl_request_id": "local-hitl-request",
        "hitl_prompt": "Continue?",
        "hitl_prompt_type": "text",
        "hitl_choices": ["Continue"],
        "hitl_a2a_task_id": "remote-task",
        "hitl_a2a_context_id": "remote-context",
    }
    assert update_kwargs["message_text"] is None
    assert result == {
        "status": "sent",
        "blocking": True,
        "task_state": "input-required",
        "response_text": None,
    }
    assert private_sentinel not in json.dumps(persisted)
    assert private_sentinel not in json.dumps(update_kwargs)
    assert private_sentinel not in json.dumps(result)


@pytest.mark.asyncio
async def test_immediate_message_result_persists_public_projection_after_artifact_conversion():
    private_sentinel = "PRIVATE_SENTINEL_immediate_message_metadata"
    public_text = "Visible immediate answer"
    store = MagicMock()
    store.update_task_on_message = AsyncMock(return_value=True)
    service = A2ATaskTrackingService(store)
    message = Message(
        role=MessageRole.AGENT,
        message_id="remote-message",
        parts=[
            Part(
                root=TextPart(
                    text=public_text,
                    metadata={"private": private_sentinel},
                )
            )
        ],
        metadata={"private": private_sentinel},
    )

    result = await service._handle_message_result(
        message,
        context_id="remote-context",
        message_id="agent-message-1",
        room_id="room-1",
    )

    persisted = store.update_task_on_message.await_args.args[1]
    assert result["content"] == public_text
    assert persisted["status"]["state"] == "completed"
    assert public_text in json.dumps(persisted)
    assert private_sentinel not in json.dumps(persisted)
