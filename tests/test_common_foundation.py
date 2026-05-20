import inspect
import tomllib
import warnings
from datetime import datetime, timezone
from pathlib import Path

import pytest

from common.errors import AppError, NotFoundError, ValidationError
from common.dto import (
    AgentEvent,
    AgentCardSnapshot,
    AgentInfo,
    AgentRegistered,
    AgentMessageFinal,
    AgentMessagePartial,
    CancellationEvent,
    CompactionResult,
    ContextBlock,
    CreateRoomRequest,
    DebateRoundEvent,
    DeliveryEnvelope,
    DeliveryEvent,
    DeliveryEventBase,
    EmbeddingResult,
    ExecutionAck,
    ExecutionRequest,
    ExecutionResult,
    FileMetadata,
    GatewayRoute,
    HITLRequest,
    HITLRequestEvent,
    HITLResolvedEvent,
    HITLResponse,
    HubAgentEvent,
    HubAgentResponseInternal,
    HubAgentStatus,
    HubConnectionInfo,
    InternalDomainEvent,
    LLMRequest,
    LLMResponse,
    MembershipSeed,
    MembershipUpdateRequest,
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
    RoomInfo,
    RoomMembership,
    RoomSummary,
    RunEventNotification,
    RunInfo,
    RunState,
    SavedUserMessage,
    SSEEvent,
    SortOrder,
    WorkflowState,
    ProcessingStatusEvent,
)


def test_frozen_dto_is_immutable():
    agent = AgentInfo(agent_id="a1", name="Agent", status="active")

    with pytest.raises(Exception):
        agent.name = "Changed"


def test_frozen_dto_container_fields_are_immutable():
    agent = AgentInfo(agent_id="a1", capabilities=["search"])
    delivery = DeliveryEnvelope(room_id="r1", event_type="message", payload={"x": 1})

    with pytest.raises(Exception):
        agent.capabilities += ("write",)

    with pytest.raises(Exception):
        delivery.payload["x"] = 2

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert '"capabilities":["search"]' in agent.model_dump_json()
        assert '"payload":{"x":1}' in delivery.model_dump_json()


def test_common_foundation_subpackages_are_packaged():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    packages = set(pyproject["tool"]["setuptools"]["packages"])

    assert {
        "common.config",
        "common.dto",
        "common.errors",
        "common.observability",
        "common.protocols",
    }.issubset(packages)


def test_common_foundation_dtos_can_be_instantiated():
    now = datetime.now(timezone.utc)

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
    seed = MembershipSeed(mode="manual", agent_ids=["a1"])
    MembershipUpdateRequest(add_agent_ids=["a2"], remove_agent_ids=["a1"])
    RoomInfo(
        room_id="r1",
        room_name="Room",
        owner_id="u1",
        owner_name="User",
        agent_ids=["a1"],
        created_at=now,
    )
    CreateRoomRequest(
        owner_id="u1",
        owner_name="User",
        room_name="Room",
        membership_seed=seed,
    )
    RoomCreationParams(
        owner_id="u1",
        owner_name="User",
        room_name="Room",
        membership_seed=seed,
    )
    SavedUserMessage(
        room_id="r1",
        message_id="m1",
        user_id="u1",
        user_name="User",
        message={},
    )
    ExecutionRequest(
        room_id="r1", message_text="hello", sender_id="u1", sender_name="User"
    )
    ExecutionAck(
        room_id="r1",
        message_id="m1",
        user_id="u1",
        user_name="User",
        message={},
    )
    ExecutionResult(success=True)
    WorkflowState(run_id="run1", room_id="r1", state="queued", updated_at=now)
    RunInfo(run_id="run1", room_id="r1", state=RunState.PROCESSING)
    HITLRequest(
        request_id="hitl1",
        room_id="r1",
        user_message_id="m1",
        prompt="Continue?",
        source="agent",
    )
    HITLResponse(request_id="hitl1", response_text="yes", responder_id="u1")
    AgentEvent(room_id="r1", agent_id="a1", message_id="m1", event_type="partial")
    ContextBlock(block_id="b1", room_id="r1", content="context", token_count=3)
    CompactionResult(room_id="r1", compacted_count=1, tokens_saved=10)
    MemorySearchResult(room_id="r1", content="memory", score=0.5)
    DeliveryEnvelope(room_id="r1", event_type="processing_status", payload={})
    SSEEvent(event="message", data={})
    ProcessingStatusEvent(room_id="r1", message_id="m1", status="processing")
    RunEventNotification(
        room_id="r1",
        event_id="e1",
        run_id="run1",
        seq=1,
        run_event_type="agent_started",
    )
    AgentMessagePartial(
        room_id="r1",
        message_id="m1",
        agent_id="a1",
        content_delta="hello",
    )
    AgentMessageFinal(
        room_id="r1",
        message_id="m1",
        agent_id="a1",
        content={"text": "done"},
    )
    CancellationEvent(room_id="r1", message_id="m1")
    HITLRequestEvent(
        room_id="r1",
        request_id="h1",
        prompt="Continue?",
        prompt_type="text",
        source="agent",
        message_id="m1",
    )
    HITLResolvedEvent(room_id="r1", request_id="h1", message_id="m1")
    HubAgentEvent(
        room_id="r1",
        hub_id="h1",
        agent_id="a1",
        message_id="m1",
        status="working",
        timestamp=now,
    )
    DebateRoundEvent(room_id="r1", round_number=1, agent_id="a1", message_id="m1")
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
    HubAgentResponseInternal(
        hub_id="h1",
        agent_id="a1",
        task_id="t1",
        room_id="r1",
        is_terminal=True,
        payload={},
        timestamp=now,
    )


def test_delivery_dtos_accept_optional_trace_and_correlation_fields():
    envelope = DeliveryEnvelope(
        room_id="room-1",
        event_type="processing_status",
        payload={},
        trace_id="trace-1",
    )
    base = ProcessingStatusEvent(
        room_id="room-1",
        message_id="msg-1",
        status="processing",
        trace_id="trace-2",
    )
    run_event = RunEventNotification(
        room_id="room-1",
        event_id="evt-1",
        run_id="run-1",
        seq=1,
        run_event_type="agent_started",
        correlation_id="cr-1",
    )
    omitted = RunEventNotification(
        room_id="room-1",
        event_id="evt-2",
        run_id="run-1",
        seq=2,
        run_event_type="agent_finished",
    )

    assert envelope.trace_id == "trace-1"
    assert base.trace_id == "trace-2"
    assert run_event.correlation_id == "cr-1"
    assert omitted.correlation_id is None


def test_room_info_preserves_legacy_membership_status_default():
    room = RoomInfo(room_id="r1", room_name="Room", owner_id="u1")

    assert room.membership_origin_status == "active"


def test_protocols_are_runtime_checkable():
    import common.protocols as protocols

    for name in protocols.__all__:
        obj = getattr(protocols, name)
        if inspect.isclass(obj):
            assert getattr(obj, "_is_runtime_protocol", False), name


def test_hub_liveness_validation_rejects_sync_runtime_protocol_match():
    import common.protocols as protocols
    from common.protocols.hub_protocols import validate_hub_liveness_reader

    class SyncHubLivenessReader:
        def is_hub_online(self, hub_id: str) -> bool:
            return True

        async def get_hub_owner_id(self, hub_id: str) -> str | None:
            return "user-1"

    reader = SyncHubLivenessReader()

    assert isinstance(reader, protocols.HubLivenessReader)
    with pytest.raises(TypeError, match="is_hub_online must be async"):
        validate_hub_liveness_reader(reader)


def test_hub_liveness_reader_validation_rejects_non_callable_method():
    import common.protocols as protocols
    from common.protocols.hub_protocols import validate_hub_liveness_reader

    class NonCallableHubLivenessReader:
        is_hub_online = True

        async def get_hub_owner_id(self, hub_id: str) -> str | None:
            return "user-1"

    reader = NonCallableHubLivenessReader()

    assert isinstance(reader, protocols.HubLivenessReader)
    with pytest.raises(
        TypeError,
        match="HubLivenessReader.is_hub_online must be callable",
    ):
        validate_hub_liveness_reader(reader)


def test_event_exports_are_distinct():
    assert DeliveryEvent is not InternalDomainEvent
    assert InternalDomainEvent.__name__ == "InternalDomainEvent"


def test_delivery_event_schemas_match_design_doc():
    expected_fields = {
        DeliveryEventBase: {"room_id", "timestamp", "trace_id"},
        ProcessingStatusEvent: {
            "room_id",
            "timestamp",
            "trace_id",
            "event_type",
            "message_id",
            "status",
            "agent_id",
            "details",
            "client_request_id",
            "agents",
        },
        RunEventNotification: {
            "room_id",
            "timestamp",
            "trace_id",
            "event_type",
            "event_id",
            "run_id",
            "seq",
            "run_event_type",
            "payload",
            "correlation_id",
        },
        AgentMessagePartial: {
            "room_id",
            "timestamp",
            "trace_id",
            "event_type",
            "message_id",
            "agent_id",
            "content_delta",
        },
        AgentMessageFinal: {
            "room_id",
            "timestamp",
            "trace_id",
            "event_type",
            "message_id",
            "agent_id",
            "content",
        },
        CancellationEvent: {
            "room_id",
            "timestamp",
            "trace_id",
            "event_type",
            "message_id",
            "reason",
        },
        HITLRequestEvent: {
            "room_id",
            "timestamp",
            "trace_id",
            "event_type",
            "request_id",
            "prompt",
            "prompt_type",
            "source",
            "message_id",
        },
        HITLResolvedEvent: {
            "room_id",
            "timestamp",
            "trace_id",
            "event_type",
            "request_id",
            "message_id",
        },
        HubAgentEvent: {
            "room_id",
            "timestamp",
            "trace_id",
            "event_type",
            "hub_id",
            "agent_id",
            "message_id",
            "status",
            "partial",
        },
        DebateRoundEvent: {
            "room_id",
            "timestamp",
            "trace_id",
            "event_type",
            "round_number",
            "agent_id",
            "message_id",
        },
    }

    for dto, fields in expected_fields.items():
        assert set(dto.model_fields) == fields

    expected_required_fields = {
        DeliveryEventBase: {"room_id"},
        ProcessingStatusEvent: {"room_id", "message_id", "status"},
        RunEventNotification: {
            "room_id",
            "event_id",
            "run_id",
            "seq",
            "run_event_type",
        },
        AgentMessagePartial: {"room_id", "message_id", "agent_id", "content_delta"},
        AgentMessageFinal: {"room_id", "message_id", "agent_id"},
        CancellationEvent: {"room_id", "message_id"},
        HITLRequestEvent: {
            "room_id",
            "request_id",
            "prompt",
            "prompt_type",
            "source",
            "message_id",
        },
        HITLResolvedEvent: {"room_id", "request_id", "message_id"},
        HubAgentEvent: {"room_id", "hub_id", "agent_id", "message_id", "status"},
        DebateRoundEvent: {"room_id", "round_number", "agent_id", "message_id"},
    }

    for dto, fields in expected_required_fields.items():
        required_fields = {
            name for name, field in dto.model_fields.items() if field.is_required()
        }
        assert required_fields == fields


def _public_protocol_methods(protocol):
    return {
        name
        for name, member in protocol.__dict__.items()
        if inspect.isfunction(member) and not name.startswith("_")
    }


def _assert_methods(protocol, expected):
    assert _public_protocol_methods(protocol) == set(expected)


def _assert_params(method, expected):
    assert list(inspect.signature(method).parameters) == expected


def test_protocol_methods_match_design_doc():
    from common import protocols

    expected_methods = {
        protocols.AgentRegistry: {
            "get_agent",
            "get_agent_card",
            "get_agent_by_url",
            "get_agents_by_ids",
            "is_agent_healthy",
            "is_directly_callable",
        },
        protocols.AgentMatcher: {"match_agents"},
        protocols.AgentMessageMatcher: {"match_for_message"},
        protocols.AgentExclusionReader: {"get_excluded_agent_ids"},
        protocols.AgentManagement: {
            "register_agent",
            "delete_agent",
            "update_agent",
            "list_agents",
            "list_public_agents",
        },
        protocols.AgentRegistryWriter: {"sync_hub_agents", "mark_hub_agents_offline"},
        protocols.RoomRegistry: {"get_room", "get_room_agents", "get_room_owner"},
        protocols.RoomManagement: {
            "create_room",
            "delete_room",
            "update_room",
            "update_membership",
        },
        protocols.RoomMessageStore: {
            "save_user_message",
            "save_agent_message",
            "update_agent_message_status",
            "get_message",
        },
        protocols.RoomHistoryReader: {
            "get_messages_for_room",
            "get_messages_by_ids",
            "get_message_thread",
        },
        protocols.RoomOwnershipReader: {
            "verify_room_agent_membership",
            "verify_room_hub_ownership",
        },
        protocols.ContextAssembler: {"assemble_context"},
        protocols.MemoryManager: {
            "get_room_memory",
            "search_memory",
            "get_user_memories",
            "delete_room_memory",
        },
        protocols.MemoryProjector: {"project_message", "run_compaction"},
        protocols.ExecutionEngine: {
            "execute",
            "start_orchestration",
            "cancel",
            "get_run",
            "get_runs_for_room",
            "cancel_inflight_tasks",
            "heal_diverged_runs",
        },
        protocols.HITLManager: {
            "create_hitl_request",
            "resolve_hitl",
            "get_pending_hitl",
            "cancel_hitl",
        },
        protocols.HubAgentResponseSink: {"handle_hub_agent_response"},
        protocols.EventPublisher: {
            "emit",
            "emit_internal",
            "register_internal_handler",
            "start",
            "stop",
        },
        protocols.SSETransport: {
            "connect",
            "disconnect",
            "is_cancelled",
            "mark_cancelled",
            "set_draining",
            "start_cancellation_watcher",
        },
        protocols.HubManagement: {
            "register_hub",
            "get_hub",
            "list_hubs",
            "connect_hub_stream",
            "publish_from_hub",
            "sync_agents",
            "get_hub_status",
            "record_hub_heartbeat",
            "hub_status_for_user",
            "start_heartbeat_monitor",
            "stop",
        },
        protocols.HubLivenessReader: {"is_hub_online", "get_hub_owner_id"},
        protocols.HubDispatchPort: {
            "send_to_hub",
            "cancel_hub_task",
            "reply_to_hub_task",
            "is_hub_online",
        },
        protocols.HubDispatchPolicy: {"can_dispatch_to_hub"},
        protocols.HubInternalResponseDispatcher: {"dispatch_hub_internal_response"},
        protocols.OfflineHubFailurePort: {"mark_hub_message_failed"},
        protocols.HubAgentStatusReader: {"count_hub_agents"},
        protocols.AgentCallCounter: {"increment_agent_call_count"},
        protocols.HubPublishAuthorizationReader: {"authorize_hub_publish"},
        protocols.HubPublishLineageReader: {"get_hub_publish_lineage"},
        protocols.MessageCancellationReader: {"is_message_cancelled"},
        protocols.RoomAgentTaskTracker: {"track_hub_task"},
        protocols.GatewayService: {
            "discover_agents",
            "get_agent_card",
            "prepare_stream",
            "send_message",
            "stream_message",
        },
        protocols.RateLimiter: {"check", "check_global"},
        protocols.FileStorage: {"upload", "get_url", "delete", "list_for_room"},
        protocols.AgentTransport: {"send_message", "stream_message"},
        protocols.AgentCardResolver: {
            "resolve_card",
            "supports_push_notifications",
            "supports_streaming",
        },
        protocols.LLMProvider: {
            "generate",
            "generate_structured",
            "embed",
            "embed_batch",
        },
        protocols.ModelRegistry: {
            "get_model",
            "supports_capability",
            "list_models",
        },
        protocols.MongoDAL: {"collection", "connect", "close", "ping"},
        protocols.MongoCollection: {
            "find_one",
            "find",
            "find_one_and_update",
            "insert_one",
            "insert_many",
            "update_one",
            "update_many",
            "delete_one",
            "delete_many",
            "count",
            "aggregate",
            "create_index",
            "find_one_by_stable_or_native_id",
            "watch",
        },
        protocols.RedisKV: {
            "get",
            "set",
            "delete",
            "increment",
            "setnx",
            "exists",
            "ping",
            "close",
        },
        protocols.RedisPubSub: {"publish", "subscribe", "ping", "close"},
        protocols.RedisStreams: {"xadd", "xread", "ping", "close"},
        protocols.VectorDAL: {"search", "upsert", "delete", "delete_by_filter", "ping"},
        protocols.ObjectStorageDAL: {"put", "get_text", "get_presigned_url", "delete"},
        protocols.DistributedLock: {"acquire", "release", "renew"},
        protocols.LeaderElector: {"try_acquire", "renew", "release", "release_all"},
        protocols.IndexRegistry: {"register", "ensure_all"},
        protocols.AgentRepository: {
            "get_by_id",
            "get_by_ids",
            "get_by_provider",
            "get_public",
            "upsert",
            "delete",
            "update_health",
            "mark_hub_agents_offline",
            "count_hub_agents",
            "increment_agent_call_count",
            "find_by_normalized_url",
            "list_visible",
            "update",
            "public_url_exists",
            "upsert_hub_agent",
            "prune_missing_hub_agents",
            "activate_agents",
            "get_indexed_description_hash",
            "set_indexed_description_hash",
        },
        protocols.RoomRepository: {
            "get_by_id",
            "get_by_owner",
            "create",
            "update",
            "update_fields",
            "set_membership",
            "delete",
        },
        protocols.MessageRepository: {
            "save_user_message",
            "save_agent_message",
            "get_by_id",
            "get_by_ids",
            "get_for_room",
            "get_thread",
            "update_status",
            "delete_for_room",
            "get_user_messages_for_room",
            "get_agent_messages_for_room",
        },
        protocols.RunRepository: {
            "create",
            "get_by_id",
            "get_for_room",
            "update_state",
            "get_diverged",
        },
        protocols.RunEventRepository: {"append", "get_for_run", "get_latest"},
        protocols.HITLRepository: {
            "create",
            "get_by_id",
            "get_pending_for_room",
            "resolve",
        },
        protocols.MemoryRepository: {
            "get_room_memory",
            "upsert_room_memory",
            "get_user_memories",
            "delete_room_memory",
            "create_room_memory",
            "ensure_room_memory",
            "get_room_memory_by_memory_id",
            "update_room_memory_by_room_id",
            "update_room_memory_by_memory_id",
            "delete_room_memory_by_memory_id",
            "push_and_trim_conversation_turn",
            "push_and_trim_conversation_turn_if_absent",
            "update_turn_notes",
            "get_room_summary_projection",
            "update_room_summary_atomic",
            "compact_turns_bulk",
            "list_room_ids_with_memory",
        },
        protocols.ContentStorageRepository: {
            "upsert_full_content",
            "get_content_by_document_id",
            "get_content_by_turn_id",
            "delete_content_by_turn_id",
            "delete_content_by_room_id",
            "get_content_stats_for_room",
            "text_search",
            "hydrate_turn_notes",
        },
        protocols.HubRepository: {
            "get_by_id",
            "get_by_owner",
            "upsert",
            "update_heartbeat",
            "get_stale",
            "list_online_hubs_for_liveness",
            "list_offline_hubs_for_recovery",
            "update_hub_status",
            "update_hub_status_if_current",
        },
        protocols.HubResponseJournal: {
            "ensure_indexes",
            "create_or_get",
            "claim_for_processing",
            "mark_processed",
            "mark_dead_letter",
            "find_replayable",
        },
        protocols.HubTaskOwnershipStore: {
            "ensure_indexes",
            "claim_or_refresh",
            "resolve_owner",
            "release",
        },
    }

    for protocol, methods in expected_methods.items():
        _assert_methods(protocol, methods)

    assert not hasattr(protocols, "CrudRepository")
    assert not hasattr(protocols, "TaskRepository")
    _assert_params(
        protocols.AgentMatcher.match_agents,
        [
            "self",
            "query",
            "limit",
            "filter_ids",
            "respect_visibility",
            "requesting_user_id",
        ],
    )
    _assert_params(
        protocols.AgentMessageMatcher.match_for_message,
        [
            "self",
            "query",
            "limit",
            "filter_ids",
            "requesting_user_id",
            "required_input_modes",
            "is_debate_mode",
        ],
    )
    assert inspect.iscoroutinefunction(protocols.HubLivenessReader.is_hub_online)
    _assert_params(protocols.RoomManagement.create_room, ["self", "request"])
    _assert_params(
        protocols.ExecutionEngine.cancel,
        ["self", "room_id", "message_id", "requested_by_user_id"],
    )
    _assert_params(protocols.MongoCollection.find, ["self", "query", "kwargs"])
    _assert_params(
        protocols.DistributedLock.acquire, ["self", "key", "owner", "ttl"]
    )


def test_run_state_contract_matches_persisted_values():
    persisted_values = {
        "queued",
        "processing",
        "awaiting_input",
        "completed",
        "failed",
        "canceled",
    }

    assert {state.value for state in RunState} == persisted_values
    RunInfo(run_id="run1", room_id="r1", state=RunState.PROCESSING)


def test_settings_class_loads_from_env(monkeypatch):
    monkeypatch.setenv("MONGODB_DB_NAME", "common_foundation_test_db")
    from common.config.settings import Settings

    settings = Settings()

    assert settings.mongodb_db_name == "common_foundation_test_db"


def test_legacy_settings_singleton_is_common_singleton():
    from common.config import settings as common_settings
    from config.settings import settings as legacy_settings

    assert legacy_settings is common_settings


def test_error_hierarchy():
    err = NotFoundError("Agent", "a1")

    assert isinstance(err, AppError)
    assert err.code == "NOT_FOUND"
    assert err.details["entity_type"] == "Agent"

    validation = ValidationError("Invalid input", details={"field": "name"})
    assert str(validation) == "Invalid input"
    assert validation.details == {"field": "name"}
