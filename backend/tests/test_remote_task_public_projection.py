import json

import pytest

from common.a2a_constants import HYBRO_A2A_INTERACTION_METADATA_KEY
from common.a2a_task_projection import public_persisted_task_data
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


def _typed_interaction_metadata(*prompts: str) -> dict:
    return {
        HYBRO_A2A_INTERACTION_METADATA_KEY: {
            "schema_version": 1,
            "interaction_id": "typed-interaction",
            "questions": [
                {
                    "question_id": f"question-{index}",
                    "interaction_kind": "questionnaire",
                    "prompt": prompt,
                    "answer_kind": "text",
                }
                for index, prompt in enumerate(prompts, start=1)
            ],
        }
    }


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
                                "file_id": "a" * 32,
                                "file_name": "result.csv",
                                "mime_type": "text/csv",
                                "size_bytes": 12,
                                "sha256": "hash",
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
    assert "file" not in part_root
    assert part_root["metadata"] == {
        "file_id": "a" * 32,
        "file_name": "result.csv",
        "mime_type": "text/csv",
        "size_bytes": 12,
        "sha256": "hash",
    }
    assert private_sentinel not in json.dumps(persisted)
    Task.model_validate(persisted)


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
