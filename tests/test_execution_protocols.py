import inspect

from common.dto import AgentEvent, ExecutionAck, ExecutionRequest, HITLRequest, RunInfo


def test_execution_request_matches_send_message_payload_shape():
    req = ExecutionRequest(
        room_id="room-1",
        sender_id="user-1",
        sender_name="User",
        message={"message_content": {"message_text": "hello"}},
        attachments=[{"file_id": "file-1"}],
        inline_file_ids=["file-inline"],
        client_request_id="cr-1",
        target_group="room_team",
        target_group_id=None,
        mentioned_agent_ids=["agent-1"],
        mode="supervisor",
    )
    assert req.message["message_content"]["message_text"] == "hello"
    assert req.client_request_id == "cr-1"


def test_run_info_preserves_active_run_ref_shape():
    info = RunInfo(
        run_id="run-1",
        room_id="room-1",
        state="processing",
        trigger_message_id="user-msg-1",
        agent_id="agent-1",
        seq=3,
    )
    assert info.trigger_message_id == "user-msg-1"


def test_hitl_request_preserves_pending_api_shape():
    req = HITLRequest(
        request_id="hitl-1",
        room_id="room-1",
        user_message_id="user-msg-1",
        message_id="display-msg-1",
        source="supervisor",
        prompt="Choose",
        prompt_type="choice",
        choices=["A", "B"],
        agent_id="agent-1",
        agent_name="Researcher",
        display_message_id="display-msg-1",
        group_id="group-1",
        group_total=2,
        group_index=1,
        status="pending",
    )
    assert req.message_id == "display-msg-1"
    assert req.choices == ["A", "B"]


def test_hitl_request_populates_message_id_from_display_or_continuation_or_user_message():
    display = HITLRequest(
        request_id="hitl-1",
        room_id="room-1",
        user_message_id="user-msg-1",
        display_message_id="display-msg-1",
        source="agent",
        prompt="Choose",
    )
    continuation = HITLRequest(
        request_id="hitl-2",
        room_id="room-1",
        user_message_id="user-msg-1",
        continuation_message_id="cont-msg-1",
        source="agent",
        prompt="Choose",
    )
    fallback = HITLRequest(
        request_id="hitl-3",
        room_id="room-1",
        user_message_id="user-msg-1",
        source="agent",
        prompt="Choose",
    )

    assert display.message_id == "display-msg-1"
    assert continuation.message_id == "cont-msg-1"
    assert fallback.message_id == "user-msg-1"


def test_execution_request_preserves_missing_message_as_none():
    req = ExecutionRequest(
        room_id="room-1",
        sender_id="user-1",
        message=None,
    )
    assert req.message is None


def test_execution_ack_preserves_missing_message_error_shape():
    ack = ExecutionAck(
        message_id=None,
        message=None,
        success=False,
        error="Message is required",
        status_code=400,
    )
    assert ack.message_id is None
    assert ack.message is None


def test_common_agent_event_preserves_legacy_compatibility_shape():
    event = AgentEvent(
        room_id="r1",
        agent_id="a1",
        message_id="m1",
        event_type="final",
        payload={"text": "hello"},
        hub_id="hub-1",
    )
    assert event.event_type == "final"
    assert event.payload == {"text": "hello"}
    assert event.hub_id == "hub-1"


def test_execution_protocols_exported():
    from common.protocols import ExecutionEngine, HITLManager, HubAgentResponseSink

    assert ExecutionEngine.__name__ == "ExecutionEngine"
    assert HITLManager.__name__ == "HITLManager"
    assert HubAgentResponseSink.__name__ == "HubAgentResponseSink"
    assert getattr(ExecutionEngine, "_is_runtime_protocol", False)
    assert getattr(HITLManager, "_is_runtime_protocol", False)
    assert getattr(HubAgentResponseSink, "_is_runtime_protocol", False)


def test_execution_engine_cancel_requires_requested_by_user_id():
    from common.protocols import ExecutionEngine

    sig = inspect.signature(ExecutionEngine.cancel)
    assert "requested_by_user_id" in sig.parameters
    assert sig.parameters["requested_by_user_id"].kind == inspect.Parameter.KEYWORD_ONLY


def test_execution_engine_separates_execute_from_start_orchestration():
    from common.protocols import ExecutionEngine

    execute_sig = inspect.signature(ExecutionEngine.execute)
    start_sig = inspect.signature(ExecutionEngine.start_orchestration)
    assert list(execute_sig.parameters) == ["self", "request"]
    assert list(start_sig.parameters) == ["self", "request", "ack"]


def test_hitl_manager_sensitive_methods_require_room_id():
    from common.protocols import HITLManager

    resolve_sig = inspect.signature(HITLManager.resolve_hitl)
    cancel_sig = inspect.signature(HITLManager.cancel_hitl)
    assert "room_id" in resolve_sig.parameters
    assert "room_id" in cancel_sig.parameters


def test_hitl_manager_create_preserves_public_metadata_fields():
    from common.protocols import HITLManager

    sig = inspect.signature(HITLManager.create_hitl_request)
    for name in [
        "source_step_id",
        "agent_name",
        "display_message_id",
        "prompt_type",
        "choices",
        "group_id",
        "group_total",
        "group_index",
    ]:
        assert name in sig.parameters
