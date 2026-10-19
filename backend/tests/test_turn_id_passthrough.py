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
