import pytest
from models.turn_event import (
    TurnEvent,
    TurnStartedPayload,
    PhaseChangedPayload,
    SlotOpenedPayload,
    SlotDeltaPayload,
    SlotSnapshotPayload,
    SlotTerminatedPayload,
    HitlRequestedPayload,
    HitlAnsweredPayload,
    HitlExpiredPayload,
    PhasePayload,
    PlanningPhase,
    DelegatingPhase,
    RoundPhase,
    WorkflowStepPhase,
)


class TestTurnEventEnvelope:
    def test_turn_started_event(self):
        event = TurnEvent(
            event_id="evt_1",
            turn_id="turn_1",
            seq=1,
            ts=1712880000000,
            type="turn_started",
            payload=TurnStartedPayload(
                user_input={"text": "hello", "attachments": []}
            ),
            client_request_id="req_abc",
        )
        assert event.event_id == "evt_1"
        assert event.seq == 1
        assert event.client_request_id == "req_abc"
        assert event.type == "turn_started"

    def test_turn_completed_event(self):
        from models.turn_event import TurnCompletedPayload

        event = TurnEvent(
            event_id="evt_2",
            turn_id="turn_1",
            seq=10,
            ts=1712880005000,
            type="turn_completed",
            payload=TurnCompletedPayload(duration_ms=5000),
        )
        assert event.payload.duration_ms == 5000

    def test_turn_failed_event_with_code(self):
        from models.turn_event import TurnFailedPayload

        event = TurnEvent(
            event_id="evt_3",
            turn_id="turn_1",
            seq=10,
            ts=1712880005000,
            type="turn_failed",
            payload=TurnFailedPayload(reason="rate limit", code="rate_limited"),
        )
        assert event.payload.code == "rate_limited"

    def test_client_request_id_optional(self):
        from models.turn_event import TurnCanceledPayload

        event = TurnEvent(
            event_id="evt_4",
            turn_id="turn_1",
            seq=2,
            ts=1712880001000,
            type="turn_canceled",
            payload=TurnCanceledPayload(),
        )
        assert event.client_request_id is None


class TestPhasePayload:
    def test_planning_phase(self):
        phase = PlanningPhase()
        assert phase.name == "planning"

    def test_delegating_phase(self):
        phase = DelegatingPhase(agent_names=["Agent A", "Agent B"], count=2)
        assert phase.name == "delegating"
        assert phase.count == 2

    def test_round_phase(self):
        phase = RoundPhase(current=1, total=3)
        assert phase.name == "round"

    def test_workflow_step_phase(self):
        phase = WorkflowStepPhase(current=2, total=5, step_name="Analysis")
        assert phase.name == "workflow_step"
        assert phase.step_name == "Analysis"

    def test_phase_changed_event(self):
        event = TurnEvent(
            event_id="evt_5",
            turn_id="turn_1",
            seq=2,
            ts=1712880001000,
            type="phase_changed",
            payload=PhaseChangedPayload(
                phase=DelegatingPhase(agent_names=["A"], count=1)
            ),
        )
        assert event.payload.phase.name == "delegating"


class TestSlotEvents:
    def test_slot_opened(self):
        event = TurnEvent(
            event_id="evt_6",
            turn_id="turn_1",
            seq=3,
            ts=1712880002000,
            type="slot_opened",
            payload=SlotOpenedPayload(
                slot_id="msg_123",
                slot_type="agent",
                agent_id="agent_1",
                agent_name="Agent A",
            ),
        )
        assert event.payload.slot_type == "agent"

    def test_slot_delta(self):
        event = TurnEvent(
            event_id="evt_7",
            turn_id="turn_1",
            seq=4,
            ts=1712880003000,
            type="slot_delta",
            payload=SlotDeltaPayload(slot_id="msg_123", text_delta="Hello "),
        )
        assert event.payload.text_delta == "Hello "

    def test_slot_snapshot(self):
        event = TurnEvent(
            event_id="evt_8",
            turn_id="turn_1",
            seq=8,
            ts=1712880004000,
            type="slot_snapshot",
            payload=SlotSnapshotPayload(
                slot_id="msg_123",
                content="Hello world",
                artifacts=[{"type": "text", "data": "test"}],
            ),
        )
        assert event.payload.content == "Hello world"
        assert len(event.payload.artifacts) == 1

    def test_slot_terminated_completed(self):
        event = TurnEvent(
            event_id="evt_9",
            turn_id="turn_1",
            seq=9,
            ts=1712880004500,
            type="slot_terminated",
            payload=SlotTerminatedPayload(
                slot_id="msg_123", status="completed"
            ),
        )
        assert event.payload.status == "completed"
        assert event.payload.error is None

    def test_slot_terminated_failed_with_partial(self):
        event = TurnEvent(
            event_id="evt_10",
            turn_id="turn_1",
            seq=9,
            ts=1712880004500,
            type="slot_terminated",
            payload=SlotTerminatedPayload(
                slot_id="msg_123",
                status="failed",
                error="timeout",
                has_partial_content=True,
            ),
        )
        assert event.payload.has_partial_content is True


class TestHitlEvents:
    def test_hitl_requested(self):
        event = TurnEvent(
            event_id="evt_11",
            turn_id="turn_1",
            seq=5,
            ts=1712880003000,
            type="hitl_requested",
            payload=HitlRequestedPayload(
                hitl_id="hitl_1",
                source="agent",
                agent_name="Agent A",
                prompt="Which option?",
                prompt_type="choice",
                choices=["A", "B", "C"],
                group_id="grp_1",
                group_total=2,
                group_index=0,
            ),
        )
        assert event.payload.prompt_type == "choice"
        assert len(event.payload.choices) == 3

    def test_hitl_answered(self):
        event = TurnEvent(
            event_id="evt_12",
            turn_id="turn_1",
            seq=6,
            ts=1712880003500,
            type="hitl_answered",
            payload=HitlAnsweredPayload(hitl_id="hitl_1", answer="Option A"),
        )
        assert event.payload.answer == "Option A"

    def test_hitl_expired(self):
        event = TurnEvent(
            event_id="evt_13",
            turn_id="turn_1",
            seq=6,
            ts=1712880003500,
            type="hitl_expired",
            payload=HitlExpiredPayload(hitl_id="hitl_1"),
        )
        assert event.payload.hitl_id == "hitl_1"

    def test_hitl_confirmation_type(self):
        event = TurnEvent(
            event_id="evt_14",
            turn_id="turn_1",
            seq=5,
            ts=1712880003000,
            type="hitl_requested",
            payload=HitlRequestedPayload(
                hitl_id="hitl_2",
                source="supervisor",
                prompt="Proceed with deletion?",
                prompt_type="confirmation",
            ),
        )
        assert event.payload.prompt_type == "confirmation"
        assert event.payload.source == "supervisor"


class TestSerialization:
    def test_to_wire_format_flat_snake_case(self):
        """Wire format must be flat: payload fields at top level, no nested 'payload' key."""
        event = TurnEvent(
            event_id="evt_1",
            turn_id="turn_1",
            seq=1,
            ts=1712880000000,
            type="turn_started",
            payload=TurnStartedPayload(
                user_input={"text": "hello"}
            ),
        )
        wire = event.to_wire()
        # Snake_case field names
        assert "event_id" in wire
        assert "turn_id" in wire
        assert "eventId" not in wire
        assert "turnId" not in wire
        # Flat: payload fields promoted to top level
        assert "user_input" in wire, "payload.user_input must be at top level"
        assert wire["user_input"] == {"text": "hello"}
        assert "payload" not in wire, "nested payload key must NOT appear in wire"

    def test_from_db_document(self):
        doc = {
            "event_id": "evt_1",
            "turn_id": "turn_1",
            "seq": 1,
            "ts": 1712880000000,
            "type": "turn_started",
            "payload": {"user_input": {"text": "hello"}},
        }
        event = TurnEvent.from_db(doc)
        assert event.event_id == "evt_1"
        assert event.type == "turn_started"
