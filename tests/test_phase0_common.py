import inspect
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError as PydanticValidationError

from common.dto import (
    AgentCardSnapshot,
    AgentInfo,
    AgentRegistered,
    CompactionResult,
    ContextBlock,
    CreateRoomRequest,
    DeliveryEnvelope,
    DeliveryEvent,
    EmbeddingResult,
    ExecutionRequest,
    ExecutionResult,
    FileMetadata,
    GatewayRoute,
    HubAgentStatus,
    HubConnectionInfo,
    InternalDomainEvent,
    LLMRequest,
    LLMResponse,
    MembershipSeed,
    MemorySearchResult,
    MessageRecord,
    ModelInfo,
    NotificationPayload,
    PaginationParams,
    QueryFilter,
    RateLimitInfo,
    RelayPayload,
    RoomCreated,
    RoomCreationParams,
    RoomMembership,
    RoomSummary,
    SortOrder,
    SSEEvent,
    WorkflowState,
)
from common.errors import AppError, NotFoundError, ValidationError


def test_frozen_dto_is_immutable():
    agent = AgentInfo(agent_id="a1", name="Agent", status="active")

    with pytest.raises(PydanticValidationError):
        agent.name = "Changed"


def test_phase0_dtos_can_be_instantiated():
    now = datetime.now(UTC)

    AgentInfo(agent_id="a1", name="Agent", status="active")
    AgentCardSnapshot(agent_id="a1", url="http://agent", name="Agent", raw_card={})
    RoomSummary(
        room_id="r1",
        room_name="Room",
        owner_id="u1",
        owner_name="User",
        created_at=now,
    )
    RoomMembership(room_id="r1", agent_ids=["a1"])
    MessageRecord(
        room_id="r1",
        message_id="m1",
        message_type="user",
        content={},
        created_at=now,
    )
    RoomCreationParams(owner_id="u1", owner_name="User", room_name="Room")
    ExecutionRequest(
        room_id="r1", message_text="hello", sender_id="u1", sender_name="User"
    )
    ExecutionResult(success=True)
    WorkflowState(run_id="run1", room_id="r1", state="queued", updated_at=now)
    ContextBlock(block_id="b1", room_id="r1", content="context", token_count=3)
    CompactionResult(room_id="r1", compacted_count=1, tokens_saved=10)
    MemorySearchResult(room_id="r1", content="memory", score=0.5)
    DeliveryEnvelope(room_id="r1", event_type="processing_status", payload={})
    SSEEvent(event="message", data={})
    NotificationPayload(room_id="r1", message="notice")
    HubConnectionInfo(hub_id="h1", owner_id="u1", is_online=True)
    HubAgentStatus(hub_id="h1", agent_id="a1", status="active")
    RelayPayload(hub_id="h1", payload={})
    LLMRequest(messages=[{"role": "user", "content": "hi"}])
    LLMResponse(content="ok", model="test")
    EmbeddingResult(text="hi", embedding=[0.1])
    ModelInfo(
        model_id="m1",
        logical_name="test",
        provider="openai",
        capabilities=[],
        max_context_tokens=1,
    )
    RateLimitInfo(limit=10, remaining=9, reset_at=now)
    FileMetadata(
        file_id="f1",
        room_id="r1",
        user_id="u1",
        s3_key="uploads/r1/f1/x.txt",
        mime_type="text/plain",
        file_name="x.txt",
        size_bytes=1,
    )
    GatewayRoute(agent_id="a1", gateway_url="/gateway/a1")
    QueryFilter(criteria={"room_id": "r1"})
    PaginationParams(page=1, limit=10)
    SortOrder(field="created_at", direction="desc")
    InternalDomainEvent(timestamp=now)
    AgentRegistered(agent_id="a1", timestamp=now)
    RoomCreated(room_id="r1", owner_id="u1", timestamp=now)


def test_room_creation_params_default_seed_does_not_weaken_create_request():
    params = RoomCreationParams(owner_id="u1", owner_name="User", room_name="Room")

    assert isinstance(params, CreateRoomRequest)
    assert params.membership_seed == MembershipSeed(mode="manual")
    assert "membership_seed" in params.model_dump(exclude_unset=True)
    with pytest.raises(PydanticValidationError) as exc_info:
        CreateRoomRequest(owner_id="u1", owner_name="User", room_name="Room")
    assert exc_info.value.errors()[0]["loc"] == ("membership_seed",)


def test_room_creation_params_compare_like_create_request_payloads():
    seed = MembershipSeed(mode="manual")
    params = RoomCreationParams(
        owner_id="u1", owner_name="User", room_name="Room", membership_seed=seed
    )
    request = CreateRoomRequest(
        owner_id="u1", owner_name="User", room_name="Room", membership_seed=seed
    )

    assert params == request
    assert request == params
    with pytest.raises(TypeError):
        hash(params)
    with pytest.raises(TypeError):
        hash(request)


def test_room_creation_params_and_create_request_export_room_creation_contracts():
    seed = MembershipSeed(mode="manual")
    request = CreateRoomRequest(
        owner_id="u1",
        owner_name="User",
        room_name="Room",
        membership_seed=seed,
    )
    params = RoomCreationParams(owner_id="u1", owner_name="User", room_name="Room")

    assert request.membership_seed == seed
    assert params.membership_seed == MembershipSeed(mode="manual")
    assert "membership_seed" in CreateRoomRequest.model_fields
    assert "membership_seed" in RoomCreationParams.model_fields


def test_create_room_request_is_unhashable_to_avoid_any_payload_hash_contract():
    request = CreateRoomRequest(
        owner_id="u1",
        owner_name="User",
        room_name="Room",
        membership_seed=MembershipSeed(mode="manual"),
        extend_info={"a": 1},
    )

    with pytest.raises(TypeError):
        hash(request)


def test_create_room_request_payloads_are_frozen():
    request = CreateRoomRequest(
        owner_id="u1",
        owner_name="User",
        room_name="Room",
        membership_seed=MembershipSeed(mode="manual"),
        extend_info={"a": 1},
    )

    assert type(request.extend_info).__name__ == "FrozenDict"
    with pytest.raises(TypeError):
        request.extend_info["a"] = 2


def test_protocols_are_runtime_checkable():
    import common.protocols as protocols

    for name in protocols.__all__:
        obj = getattr(protocols, name)
        if inspect.isclass(obj):
            assert getattr(obj, "_is_runtime_protocol", False), name


def test_event_exports_are_distinct():
    assert DeliveryEvent is not InternalDomainEvent
    assert InternalDomainEvent.__name__ == "InternalDomainEvent"


def test_settings_class_loads_from_env(monkeypatch):
    monkeypatch.setenv("MONGODB_DB_NAME", "phase0_test_db")
    from common.config.settings import Settings

    settings = Settings()

    assert settings.mongodb_db_name == "phase0_test_db"


def test_common_settings_package_exports_settings_singleton():
    from common.config import settings as common_settings
    from common.config.settings import settings as exported_settings

    assert exported_settings is common_settings


def test_error_hierarchy():
    err = NotFoundError("Agent", "a1")

    assert isinstance(err, AppError)
    assert err.code == "NOT_FOUND"
    assert err.details["entity_type"] == "Agent"

    validation = ValidationError("Invalid input", details={"field": "name"})
    assert str(validation) == "Invalid input"
    assert validation.details == {"field": "name"}
