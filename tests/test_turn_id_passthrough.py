from unittest.mock import MagicMock

from models.room import MessageContent


class TestTurnIdOnRoomAgentMessage:
    def test_create_agent_message_includes_turn_id(self):
        from app_shell.room_runtime import RoomServices
        svc = RoomServices.__new__(RoomServices)
        svc._generate_agent_message_content = MagicMock(
            return_value=MessageContent(message_text="task")
        )
        msg = svc.create_agent_message(
            room_id="room_1",
            related_message_id="user_msg_1",
            agent_id="agent_1",
            content="task",
            turn_id="user_msg_1",
        )
        assert msg.turn_id == "user_msg_1"

    def test_create_agent_message_without_turn_id_is_none(self):
        from app_shell.room_runtime import RoomServices
        svc = RoomServices.__new__(RoomServices)
        svc._generate_agent_message_content = MagicMock(
            return_value=MessageContent(message_text="task")
        )
        msg = svc.create_agent_message(
            room_id="room_1",
            related_message_id="user_msg_1",
            agent_id="agent_1",
            content="task",
        )
        assert msg.turn_id is None


class TestAgentEventTurnId:
    def test_agent_event_has_turn_id(self):
        from execution.dispatch.agent_event import AgentEvent
        event = AgentEvent(
            kind="response",
            message_id="msg_1",
            room_id="room_1",
            agent_id="agent_1",
            turn_id="turn_1",
        )
        assert event.turn_id == "turn_1"
