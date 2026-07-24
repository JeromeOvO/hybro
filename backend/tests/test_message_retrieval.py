import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import (
    Artifact,
    DataPart,
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


def _make_msg_with_attachment(file_id="f1"):
    att = UserAttachment(
        file_id=file_id,
        mime_type="image/png",
        file_name="photo.png",
        size_bytes=100,
    )
    return RoomUserMessage(
        room_id="room1",
        message_id="msg1",
        message_type="user",
        message_content=MessageContent(message_text="hi", attachments=[att]),
    )


class TestMessageRetrieval:
    async def test_stable_authenticated_url_is_returned(self, room_runtime):
        msg = _make_msg_with_attachment()
        facade = MagicMock()
        facade.get_user_messages_for_room = AsyncMock(return_value=[msg])
        room_runtime.bind_facade(facade)

        result = await room_runtime.inquiry_user_messages_by_room_id(
            RoomCenterUserMessageRequest(room_id="room1")
        )

        assert result.success
        assert (
            result.message_list[0].message_content.attachments[0].file_url
            == "/api/v1/files/f1/content"
        )

    async def test_stable_urls_are_derived_for_each_attachment(
        self,
        room_runtime,
    ):
        first = _make_msg_with_attachment("f1")
        second = _make_msg_with_attachment("f2")
        second.message_id = "msg2"
        second.message_content.attachments[0].file_id = "f2"
        second.message_content.attachments[0].file_name = "diagram.png"
        facade = MagicMock()
        facade.get_user_messages_for_room = AsyncMock(return_value=[first, second])
        room_runtime.bind_facade(facade)

        result = await room_runtime.inquiry_user_messages_by_room_id(
            RoomCenterUserMessageRequest(room_id="room1")
        )

        assert result.success
        assert [
            message.message_content.attachments[0].file_url
            for message in result.message_list
        ] == [
            "/api/v1/files/f1/content",
            "/api/v1/files/f2/content",
        ]

    async def test_no_attachments_no_s3_call(self, room_runtime):
        msg = RoomUserMessage(
            room_id="room1",
            message_id="msg1",
            message_type="user",
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

    async def test_agent_projection_preserves_dispatch_and_separate_response_text(
        self,
        room_runtime,
    ):
        task = Task(
            id="task-data-only",
            status=TaskStatus(state=TaskState.completed),
            artifacts=[
                Artifact(
                    artifact_id="quote-artifact",
                    name="cyber_quote_decision",
                    parts=[Part(root=DataPart(data={"premium": 35700}))],
                )
            ],
        )
        agent_msg = RoomAgentMessage(
            room_id="room1",
            message_id="agent-response",
            agent_id="insurer-agent",
            message_content=MessageContent(
                message_text="I can offer an indicative cyber quote.",
                message_task=task,
            ),
            task_content="Requesting Cyber Insurer Agent",
            extend_info={
                "public_task_label": "Requesting Cyber Insurer Agent",
                "public_dispatch_text": "Review the submission and return a quote.",
                "internal_planner_state": "must-not-leak",
            },
        )
        facade = MagicMock()
        facade.get_user_messages_for_room = AsyncMock(return_value=[])
        facade.get_agent_messages_for_room = AsyncMock(return_value=[agent_msg])
        room_runtime.bind_facade(facade)

        result = await room_runtime.inquiry_room_messages_by_room_id(
            RoomCenterRoomMessageRequest(room_id="room1")
        )

        projected = result.message_list[0]
        assert (
            projected.message_content.message_text
            == "I can offer an indicative cyber quote."
        )
        assert projected.task_content == "Requesting Cyber Insurer Agent"
        assert projected.extend_info == {
            "public_task_label": "Requesting Cyber Insurer Agent",
            "public_dispatch_text": "Review the submission and return a quote.",
        }

    async def test_system_hybro_projection_does_not_fabricate_request_label(
        self,
        room_runtime,
    ):
        agent_msg = RoomAgentMessage(
            room_id="room1",
            message_id="summary-user-message",
            agent_id="system:hybro",
            message_content=MessageContent(message_text="Final synthesized answer."),
            extend_info={
                "is_coordinator_summary": True,
                "source_user_message_id": "user-message",
                "summary_origin": "supervisor",
                "internal_planner_state": "must-not-leak",
            },
        )
        facade = MagicMock()
        facade.get_user_messages_for_room = AsyncMock(return_value=[])
        facade.get_agent_messages_for_room = AsyncMock(return_value=[agent_msg])
        room_runtime.bind_facade(facade)

        result = await room_runtime.inquiry_room_messages_by_room_id(
            RoomCenterRoomMessageRequest(room_id="room1")
        )

        projected = result.message_list[0]
        assert projected.message_content.message_text == "Final synthesized answer."
        assert projected.task_content is None
        assert projected.extend_info == {
            "is_coordinator_summary": True,
            "source_user_message_id": "user-message",
            "summary_origin": "supervisor",
        }
        assert "Requesting system:hybro" not in json.dumps(
            result.model_dump(mode="json")
        )
