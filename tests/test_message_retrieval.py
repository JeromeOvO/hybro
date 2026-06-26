from unittest.mock import AsyncMock, MagicMock

import pytest

from models.request import RoomCenterUserMessageRequest
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

        mock_s3 = MagicMock()
        mock_s3.batch_presigned_urls = AsyncMock(
            return_value={"uploads/r/f1/photo.png": "https://presigned"}
        )
        room_runtime.bind_s3_service(mock_s3)
        result = await room_runtime.inquiry_user_messages_by_room_id(
            RoomCenterUserMessageRequest(room_id="room1")
        )

        assert result.success
        assert result.message_list[0].message_content.attachments[0].file_url == "https://presigned"

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


class TestRefreshArtifactPresignedUrls:
    """Test that _refresh_artifact_presigned_urls passes filenames through."""

    async def test_filenames_passed_to_batch_presigned_urls(self, room_runtime):
        """When artifact file parts have a name, it should be passed to
        batch_presigned_urls so the Content-Disposition header is set."""
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

        mock_s3 = MagicMock()
        mock_s3.batch_presigned_urls = AsyncMock(
            return_value={"artifacts/room1/msg1/inline-0.xlsx": "https://new-presigned"}
        )
        room_runtime.bind_s3_service(mock_s3)
        await room_runtime._refresh_artifact_presigned_urls([msg])

        mock_s3.batch_presigned_urls.assert_called_once()
        call_kwargs = mock_s3.batch_presigned_urls.call_args
        assert call_kwargs.kwargs["filenames"] == {
            "artifacts/room1/msg1/inline-0.xlsx": "report.xlsx"
        }

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

        mock_s3 = MagicMock()
        mock_s3.batch_presigned_urls = AsyncMock(
            return_value={"artifacts/room1/msg1/inline-0.png": "https://new-presigned"}
        )
        room_runtime.bind_s3_service(mock_s3)
        await room_runtime._refresh_artifact_presigned_urls([msg])

        call_kwargs = mock_s3.batch_presigned_urls.call_args
        assert call_kwargs.kwargs["filenames"] == {}
