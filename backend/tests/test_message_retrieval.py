import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import (
    Artifact,
    Message,
    MessageRole,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from models.request import RoomCenterRoomMessageRequest, RoomCenterUserMessageRequest
from models.room import (
    MessageContent,
    RoomAgentMessage,
    RoomUserMessage,
    UserAttachment,
)
from room.compat.runtime import RoomServices


@pytest.fixture
def room_runtime():
    svc = RoomServices()
    svc._store = MagicMock()
    svc.delivery = MagicMock()
    return svc


def _make_msg_with_attachment(s3_key="uploads/r/f1/photo.png"):
    att = UserAttachment(
        file_id="f1", s3_key=s3_key, mime_type="image/png",
        file_name="photo.png", size_bytes=100,
    )
    return RoomUserMessage(
        room_id="room1", message_id="msg1", message_type="user",
        message_content=MessageContent(message_text="hi", attachments=[att]),
    )


class TestMessageRetrieval:
    async def test_presigned_url_injected(self, room_runtime):
        msg = _make_msg_with_attachment()
        facade = MagicMock()
        facade.get_user_messages_for_room = AsyncMock(return_value=[msg])
        room_runtime.bind_facade(facade)

        mock_s3 = SimpleNamespace(
            get_presigned_url=AsyncMock(return_value="https://presigned")
        )
        room_runtime.bind_s3_service(mock_s3)
        result = await room_runtime.inquiry_user_messages_by_room_id(
            RoomCenterUserMessageRequest(room_id="room1")
        )

        assert result.success
        assert result.message_list[0].message_content.attachments[0].file_url == "https://presigned"
        mock_s3.get_presigned_url.assert_awaited_once_with(
            "uploads/r/f1/photo.png",
            filename="photo.png",
        )

    async def test_presigned_urls_are_generated_concurrently_for_unique_attachments(
        self,
        room_runtime,
    ):
        first = _make_msg_with_attachment("uploads/r/f1/photo.png")
        second = _make_msg_with_attachment("uploads/r/f2/diagram.png")
        second.message_id = "msg2"
        second.message_content.attachments[0].file_id = "f2"
        second.message_content.attachments[0].file_name = "diagram.png"
        facade = MagicMock()
        facade.get_user_messages_for_room = AsyncMock(return_value=[first, second])
        room_runtime.bind_facade(facade)

        class ConcurrentPresigner:
            def __init__(self):
                self.active = 0
                self.max_active = 0
                self.calls = []

            async def get_presigned_url(self, key, filename=None):
                self.calls.append((key, filename))
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                await asyncio.sleep(0.01)
                self.active -= 1
                return f"https://presigned/{filename}"

        presigner = ConcurrentPresigner()
        room_runtime.bind_s3_service(presigner)

        result = await room_runtime.inquiry_user_messages_by_room_id(
            RoomCenterUserMessageRequest(room_id="room1")
        )

        assert result.success
        assert presigner.max_active == 2
        assert sorted(presigner.calls) == [
            ("uploads/r/f1/photo.png", "photo.png"),
            ("uploads/r/f2/diagram.png", "diagram.png"),
        ]

    async def test_no_attachments_no_s3_call(self, room_runtime):
        msg = RoomUserMessage(
            room_id="room1", message_id="msg1", message_type="user",
            message_content=MessageContent(message_text="hi"),
        )
        facade = MagicMock()
        facade.get_user_messages_for_room = AsyncMock(return_value=[msg])
        room_runtime.bind_facade(facade)

        result = await room_runtime.inquiry_user_messages_by_room_id(
            RoomCenterUserMessageRequest(room_id="room1")
        )

        assert result.success


class TestRoomMessageRetrieval:
    async def test_agent_artifact_only_output_is_returned_as_message_text(
        self,
        room_runtime,
    ):
        task = Task(
            id="task-1",
            status=TaskStatus(state=TaskState.completed),
            artifacts=[
                Artifact(
                    artifact_id="artifact-1",
                    parts=[Part(root=TextPart(text="final artifact answer"))],
                )
            ],
            history=[
                Message(
                    role=MessageRole.AGENT,
                    parts=[Part(root=TextPart(text="intermediate status"))],
                )
            ],
        )
        agent_msg = RoomAgentMessage(
            room_id="room1",
            message_id="agent-msg1",
            agent_id="agent1",
            message_content=MessageContent(message_task=task),
        )
        facade = MagicMock()
        facade.get_user_messages_for_room = AsyncMock(return_value=[])
        facade.get_agent_messages_for_room = AsyncMock(return_value=[agent_msg])
        room_runtime.bind_facade(facade)
        room_runtime.bind_s3_service(
            SimpleNamespace(get_presigned_url=AsyncMock())
        )

        result = await room_runtime.inquiry_room_messages_by_room_id(
            RoomCenterRoomMessageRequest(room_id="room1")
        )

        assert result.success
        assert len(result.message_list) == 1
        assert (
            result.message_list[0].message_content.message_text
            == "final artifact answer"
        )

    @pytest.mark.parametrize(
        ("extend_info", "expected_label"),
        [
            (
                {"public_task_label": "Requesting Claims Agent"},
                "Requesting Claims Agent",
            ),
            ({}, "Requesting agent1"),
        ],
    )
    async def test_legacy_agent_task_projection_redacts_private_dispatch_fields(
        self,
        room_runtime,
        extend_info,
        expected_label,
    ):
        private_sentinel = "PRIVATE_SENTINEL_legacy_room_projection"
        task = Task(
            id="task-private",
            status=TaskStatus(state=TaskState.completed),
            artifacts=[
                Artifact(
                    artifact_id="artifact-public",
                    parts=[Part(root=TextPart(text="Final public agent answer"))],
                )
            ],
            history=[
                Message(
                    role=MessageRole.USER,
                    parts=[Part(root=TextPart(text=private_sentinel))],
                ),
                Message(
                    role=MessageRole.AGENT,
                    parts=[Part(root=TextPart(text="Final public agent answer"))],
                ),
            ],
            metadata={
                "agent_id": "agent1",
                "preflight_failure_code": "safe-code",
                "requires_policy": True,
                "hitl_request_id": "hitl-request-1",
                "hitl_prompt": "Choose a public option",
                "hitl_prompt_type": "choice",
                "hitl_choices": ["A", "B"],
                "hitl_a2a_task_id": "a2a-task-1",
                "hitl_a2a_context_id": "a2a-context-1",
                "hitl_group_id": "hitl-group-1",
                "hitl_group_total": 2,
                "hitl_group_index": 1,
                "user_answer": "A",
                "task_content": private_sentinel,
                "internal_task_payload": {"instructions": private_sentinel},
            },
        )
        agent_msg = RoomAgentMessage(
            room_id="room1",
            message_id="agent-private",
            agent_id="agent1",
            message_content=MessageContent(
                message_text=private_sentinel,
                message_task=task,
            ),
            task_content=private_sentinel,
            extend_info=extend_info,
        )
        facade = MagicMock()
        facade.get_user_messages_for_room = AsyncMock(return_value=[])
        facade.get_agent_messages_for_room = AsyncMock(return_value=[agent_msg])
        room_runtime.bind_facade(facade)
        room_runtime.bind_s3_service(SimpleNamespace(get_presigned_url=AsyncMock()))

        result = await room_runtime.inquiry_room_messages_by_room_id(
            RoomCenterRoomMessageRequest(room_id="room1")
        )

        projected = result.message_list[0]
        projected_task = projected.message_content.message_task
        assert projected.message_content.message_text == "Final public agent answer"
        assert projected.task_content == expected_label
        assert projected.extend_info == {"public_task_label": expected_label}
        assert projected_task is not None
        assert projected_task.history is None
        assert projected_task.metadata is None
        assert private_sentinel not in json.dumps(result.model_dump(mode="json"))


class TestRefreshArtifactPresignedUrls:
    """Test that _refresh_artifact_presigned_urls passes filenames through."""

    async def test_filenames_passed_to_get_presigned_url(self, room_runtime):
        """When artifact file parts have a name, it should be passed to
        get_presigned_url so the Content-Disposition header is set."""
        from a2a.types import (
            Artifact,
            FilePart,
            FileWithUri,
            Part,
            Task,
            TaskState,
            TaskStatus,
        )

        task = Task(
            id="task-1",
            context_id="ctx-1",
            status=TaskStatus(state=TaskState.completed),
            artifacts=[
                Artifact(
                    artifact_id="art-1",
                    parts=[
                        Part(
                            root=FilePart(
                                file=FileWithUri(
                                    uri="https://old-presigned",
                                    name="report.xlsx",
                                    mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                ),
                                metadata={"s3_key": "artifacts/room1/msg1/inline-0.xlsx"},
                            )
                        )
                    ],
                )
            ],
        )
        msg = RoomAgentMessage(
            room_id="room1",
            message_id="msg1",
            agent_id="agent1",
            message_content=MessageContent(
                message_text="Here is the report",
                message_task=task,
            ),
        )

        mock_s3 = SimpleNamespace(
            get_presigned_url=AsyncMock(return_value="https://new-presigned")
        )
        room_runtime.bind_s3_service(mock_s3)
        await room_runtime._refresh_artifact_presigned_urls([msg])

        mock_s3.get_presigned_url.assert_awaited_once_with(
            "artifacts/room1/msg1/inline-0.xlsx",
            filename="report.xlsx",
        )

        # URI should be updated
        refreshed_uri = msg.message_content.message_task.artifacts[0].parts[0].root.file.uri
        assert refreshed_uri == "https://new-presigned"

    async def test_no_filename_omits_from_filenames_dict(self, room_runtime):
        """File parts without a name should not appear in the filenames dict."""
        from a2a.types import (
            Artifact,
            FilePart,
            FileWithUri,
            Part,
            Task,
            TaskState,
            TaskStatus,
        )

        task = Task(
            id="task-1",
            context_id="ctx-1",
            status=TaskStatus(state=TaskState.completed),
            artifacts=[
                Artifact(
                    artifact_id="art-1",
                    parts=[
                        Part(
                            root=FilePart(
                                file=FileWithUri(
                                    uri="https://old-presigned",
                                    mime_type="image/png",
                                ),
                                metadata={"s3_key": "artifacts/room1/msg1/inline-0.png"},
                            )
                        )
                    ],
                )
            ],
        )
        msg = RoomAgentMessage(
            room_id="room1",
            message_id="msg1",
            agent_id="agent1",
            message_content=MessageContent(
                message_text="Image",
                message_task=task,
            ),
        )

        mock_s3 = SimpleNamespace(
            get_presigned_url=AsyncMock(return_value="https://new-presigned")
        )
        room_runtime.bind_s3_service(mock_s3)
        await room_runtime._refresh_artifact_presigned_urls([msg])

        mock_s3.get_presigned_url.assert_awaited_once_with(
            "artifacts/room1/msg1/inline-0.png",
            filename=None,
        )

    async def test_artifact_presigned_urls_are_generated_concurrently(
        self,
        room_runtime,
    ):
        from a2a.types import (
            Artifact,
            FilePart,
            FileWithUri,
            Part,
            Task,
            TaskState,
            TaskStatus,
        )

        task = Task(
            id="task-1",
            context_id="ctx-1",
            status=TaskStatus(state=TaskState.completed),
            artifacts=[
                Artifact(
                    artifact_id="art-1",
                    parts=[
                        Part(
                            root=FilePart(
                                file=FileWithUri(
                                    uri="https://old-one",
                                    name="one.png",
                                    mime_type="image/png",
                                ),
                                metadata={"s3_key": "artifacts/room1/msg1/one.png"},
                            )
                        ),
                        Part(
                            root=FilePart(
                                file=FileWithUri(
                                    uri="https://old-two",
                                    name="two.png",
                                    mime_type="image/png",
                                ),
                                metadata={"s3_key": "artifacts/room1/msg1/two.png"},
                            )
                        ),
                    ],
                )
            ],
        )
        msg = RoomAgentMessage(
            room_id="room1",
            message_id="msg1",
            agent_id="agent1",
            message_content=MessageContent(message_task=task),
        )

        class ConcurrentPresigner:
            def __init__(self):
                self.active = 0
                self.max_active = 0
                self.calls = []

            async def get_presigned_url(self, key, filename=None):
                self.calls.append((key, filename))
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                await asyncio.sleep(0.01)
                self.active -= 1
                return f"https://presigned/{filename}"

        presigner = ConcurrentPresigner()
        room_runtime.bind_s3_service(presigner)

        await room_runtime._refresh_artifact_presigned_urls([msg])

        assert presigner.max_active == 2
        assert sorted(presigner.calls) == [
            ("artifacts/room1/msg1/one.png", "one.png"),
            ("artifacts/room1/msg1/two.png", "two.png"),
        ]
