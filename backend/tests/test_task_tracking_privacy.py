import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import (
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

    assert persisted["status"]["message"] is None
    assert persisted["metadata"] is None
    assert private_sentinel not in json.dumps(persisted)


def test_remote_task_sanitizer_preserves_agent_status_without_message_metadata():
    task = Task(
        id="remote-task",
        context_id="remote-context",
        status=TaskStatus(
            state=TaskState.completed,
            message=Message(
                message_id="agent-status",
                role=MessageRole.AGENT,
                parts=[Part(root=TextPart(text="Public final status"))],
                metadata={"private": "do not persist"},
            ),
        ),
    )

    persisted = public_persisted_task_data(task)

    assert persisted["status"]["message"]["role"] == "agent"
    assert persisted["status"]["message"]["metadata"] is None
    assert "Public final status" in json.dumps(persisted)
    assert "do not persist" not in json.dumps(persisted)


@pytest.mark.asyncio
async def test_blocking_hitl_reply_merges_only_existing_local_hitl_metadata():
    private_sentinel = "PRIVATE_SENTINEL_remote_hitl_spoof"
    trusted_hitl_metadata = {
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
        metadata=trusted_hitl_metadata,
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
    assert persisted["metadata"] == trusted_hitl_metadata
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
