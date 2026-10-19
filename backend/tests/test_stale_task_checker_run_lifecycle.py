import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from common.types import (
    Artifact,
    FileContent,
    FilePart,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from jobs.stale_task_checker import StaleTaskChecker
from models.room import MessageContent, RoomAgentMessage


@pytest.mark.asyncio
async def test_journal_finalization_is_never_taken_over_by_stale_poller():
    msg = RoomAgentMessage(
        room_id="room-1",
        message_id="agent-message-1",
        user_id="user-1",
        agent_id="agent-1",
        agent_url="https://agent.example",
        has_task_tracking=True,
        terminal_finalization={
            "state": "finalizing",
            "recovery_source": "journal",
            "recovery_id": "journal-1",
        },
        message_content=MessageContent(
            message_task=Task(
                id="remote-task",
                context_id="remote-context",
                status=TaskStatus(state=TaskState.working),
            )
        ),
    )
    checker = StaleTaskChecker()
    checker._get_task_from_agent = AsyncMock()

    await checker._process_stale_task(msg)

    checker._get_task_from_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_recovery_persists_public_projection_without_inline_file_bytes():
    private_sentinel = "PRIVATE_SENTINEL_stale_recovery_raw_task"
    current_task = Task(
        id="remote-task",
        context_id="remote-context",
        status=TaskStatus(state=TaskState.completed),
        artifacts=[
            Artifact(
                artifact_id="artifact-1",
                name="response",
                parts=[
                    Part(root=TextPart(text="Recovered public result")),
                    Part(
                        root=FilePart(
                            file=FileContent(
                                bytes=private_sentinel,
                                mimeType="text/plain",
                                name="secret.txt",
                            ),
                            metadata={"s3_key": "artifacts/room/msg/secret.txt"},
                        )
                    ),
                ],
            )
        ],
        metadata={"hitl_prompt": private_sentinel},
    )
    existing_task = Task(
        id="remote-task",
        context_id="remote-context",
        status=TaskStatus(state=TaskState.working),
    )
    msg = RoomAgentMessage(
        room_id="room-1",
        message_id="agent-message-1",
        user_id="user-1",
        agent_id="agent-1",
        agent_url="https://agent.example",
        has_task_tracking=True,
        message_content=MessageContent(message_task=existing_task),
    )
    store = SimpleNamespace(
        update_task_on_message=AsyncMock(return_value=True),
        touch_task_message=AsyncMock(),
    )
    checker = StaleTaskChecker()
    checker.set_runtime_deps(
        SimpleNamespace(
            store=store,
            rooms_collection=None,
            notify_task_update=AsyncMock(),
            a2a_service=SimpleNamespace(
                get_agent_card_from_url=AsyncMock(return_value=SimpleNamespace())
            ),
        )
    )
    checker._get_task_from_agent = AsyncMock(return_value=current_task)

    async def convert_artifacts_side_effect(artifacts, **_kwargs):
        artifacts[0].parts[1].root.file.uri = "https://storage.example/secret.txt"
        artifacts[0].parts[1].root.metadata = {
            "file_id": "a" * 32,
            "file_name": "secret.txt",
            "mime_type": "text/plain",
            "size_bytes": 1,
            "sha256": "hash",
        }

    with patch(
        "jobs.stale_task_checker.materialize_artifacts",
        new=AsyncMock(side_effect=convert_artifacts_side_effect),
    ) as convert_artifacts:
        await checker._process_stale_task(msg)

    persisted = store.update_task_on_message.await_args.args[1]
    update_kwargs = store.update_task_on_message.await_args.kwargs
    convert_artifacts.assert_awaited_once()
    assert persisted["status"]["state"] == "completed"
    assert update_kwargs["message_text"] == "Recovered public result"
    assert persisted["metadata"] is None
    file_part = persisted["artifacts"][0]["parts"][1]
    file_root = file_part.get("root", file_part)
    assert "file" not in file_root
    assert file_root["metadata"] == {
        "file_id": "a" * 32,
        "file_name": "secret.txt",
        "mime_type": "text/plain",
        "size_bytes": 1,
        "sha256": "hash",
    }
    assert private_sentinel not in json.dumps(persisted)


@pytest.mark.asyncio
async def test_stale_recovery_drops_inline_file_when_s3_conversion_fails():
    private_sentinel = "PRIVATE_SENTINEL_failed_stale_s3_conversion"
    current_task = Task(
        id="remote-task",
        context_id="remote-context",
        status=TaskStatus(state=TaskState.completed),
        artifacts=[
            Artifact(
                artifact_id="artifact-1",
                name="response",
                parts=[
                    Part(root=TextPart(text="Recovered public result")),
                    Part(
                        root=FilePart(
                            file=FileContent(
                                bytes=private_sentinel,
                                mimeType="text/plain",
                                name="secret.txt",
                            )
                        )
                    ),
                ],
            )
        ],
    )
    msg = RoomAgentMessage(
        room_id="room-1",
        message_id="agent-message-1",
        user_id="user-1",
        agent_id="agent-1",
        agent_url="https://agent.example",
        has_task_tracking=True,
        message_content=MessageContent(
            message_task=Task(
                id="remote-task",
                context_id="remote-context",
                status=TaskStatus(state=TaskState.working),
            )
        ),
    )
    store = SimpleNamespace(
        update_task_on_message=AsyncMock(return_value=True),
        touch_task_message=AsyncMock(),
    )
    checker = StaleTaskChecker()
    checker.set_runtime_deps(
        SimpleNamespace(
            store=store,
            rooms_collection=None,
            notify_task_update=AsyncMock(),
            a2a_service=SimpleNamespace(
                get_agent_card_from_url=AsyncMock(return_value=SimpleNamespace())
            ),
        )
    )
    checker._get_task_from_agent = AsyncMock(return_value=current_task)

    with patch(
        "jobs.stale_task_checker.materialize_artifacts",
        new=AsyncMock(side_effect=RuntimeError("storage unavailable")),
    ):
        await checker._process_stale_task(msg)

    persisted = store.update_task_on_message.await_args.args[1]
    assert persisted["artifacts"][0]["parts"] == [
        Part(root=TextPart(text="Recovered public result")).model_dump(mode="json")
    ]
    assert store.update_task_on_message.await_args.kwargs["message_text"] == (
        "Recovered public result"
    )
    assert private_sentinel not in json.dumps(persisted)
    store.touch_task_message.assert_not_awaited()
