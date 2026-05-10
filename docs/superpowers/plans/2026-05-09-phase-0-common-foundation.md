# Phase 0 Common Foundation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Phase 0 common foundation layer for DTOs, protocols, config, errors, and observability without changing runtime behavior.

**Architecture:** This phase creates importable common contracts only. New DTOs use primitive/Pydantic fields derived from existing `models/` and `common/types.py`; new Protocols describe current service boundaries without importing concrete services. Existing application code continues to import `config.settings` through a compatibility shim.

**Tech Stack:** Python 3.11+ syntax with `str | None`, Pydantic v2, pydantic-settings, stdlib `typing.Protocol`, pytest.

---

## Scope

Phase 0 here means the common foundation requested in `docs/MODULAR_DECOUPLING_DESIGN.md` plus the Claude reference:

- Include: `common/dto/`, `common/protocols/`, `common/errors/`, `common/config/`, `common/observability/`, `tests/test_phase0_common.py`, and the optional compatibility shim in `config/settings.py`.
- Exclude: Phase 0b `os.getenv()` migration, Phase 0c frontend checklist, Phase 0d legacy endpoint decommissioning, `container.py` skeleton, import-linter config.
- Do not modify: `services/`, `api/`, `modules/`, `models/`, `database/`, `main.py`.
- No new dependencies. Do not add `structlog` or OpenTelemetry imports.

Current branch notes:

- Branch is already `refactor/phase-0-common`.
- `common/dto`, `common/protocols`, `common/errors`, `common/config`, and `common/observability` directories exist but contain only `__pycache__`; recreate source files and ignore bytecode caches.
- Claude references `services/room_service.py`, `services/orchestration_service.py`, and `services/supervisor_service.py`, but this checkout has `services/room_services.py`, `services/room_supervisor_service.py`, `services/run_command_handler.py`, `services/run_lifecycle_service.py`, `services/debate_service.py`, and `services/hitl_service.py`.

## File Map

Create:

- `common/dto/base.py`: frozen DTO base.
- `common/dto/agent.py`: agent boundary DTOs.
- `common/dto/room.py`: room and message boundary DTOs.
- `common/dto/execution.py`: execution, run, and HITL DTOs.
- `common/dto/context_memory.py`: context assembly, compaction, and memory search DTOs.
- `common/dto/delivery.py`: frontend-visible delivery event DTOs.
- `common/dto/internal_events.py`: internal domain event DTOs.
- `common/dto/hub.py`: hub relay DTOs.
- `common/dto/llm.py`: LLM request/response/model DTOs.
- `common/dto/platform.py`: gateway, rate limit, and file DTOs.
- `common/dto/dal.py`: DAL utility DTOs.
- `common/dto/a2a.py`: internal A2A adapter DTOs required by `a2a_protocols.py`.
- `common/dto/__init__.py`: public DTO re-exports.
- `common/protocols/*.py`: protocol modules listed in the task.
- `common/protocols/__init__.py`: public protocol re-exports.
- `common/errors/base.py`: app error hierarchy.
- `common/errors/__init__.py`: error re-exports.
- `common/config/settings.py`: canonical copy of current settings.
- `common/config/__init__.py`: `Settings` and `settings` re-exports.
- `common/observability/logging.py`: wrapper around `common.utils.logger`.
- `common/observability/metrics.py`: stub protocol/no-op collector.
- `common/observability/tracing.py`: stub protocol/no-op tracer helpers.
- `common/observability/__init__.py`: observability re-exports.
- `tests/test_phase0_common.py`: focused Phase 0 tests.

Modify:

- `config/settings.py`: replace with compatibility re-export from `common.config.settings`.

Do not modify:

- `pyproject.toml` in this phase unless the owner explicitly approves packaging changes. Current `[tool.setuptools].packages` lists only top-level packages, so subpackages may not be included in `uv build`; call this out after implementation if build packaging is in scope.

## Source Mapping

Use these source files when deriving fields and signatures:

- Agent: `models/agent.py`, `services/agent_service.py`, `services/agent_matcher.py`, `common/types.py`.
- Room: `models/room.py`, `models/request.py`, `models/response.py`, `services/room_services.py`.
- Execution/HITL: `models/run.py`, `models/hitl.py`, `services/run_command_handler.py`, `services/run_lifecycle_service.py`, `services/room_supervisor_service.py`, `services/debate_service.py`, `services/hitl_service.py`.
- Context/memory: `models/memory.py`, `models/compaction.py`, `models/search.py`, `services/context_assembly_service.py`, `services/compaction_service.py`, `services/memory_service.py`, `services/memory_search_service.py`.
- Delivery: `services/sse_services.py`, `services/notification_service.py`, `services/task_notification_service.py`.
- Hub: `models/hub.py`, `services/relay_service.py`.
- LLM: `services/openai_service.py`, `services/bedrock_service.py`, `services/gemini_service.py`.
- Platform: `models/gateway.py`, `models/file_upload.py`, `services/gateway_service.py`, `services/file_upload_service.py`, `services/rate_limit_service.py`, `services/gateway_rate_limit_service.py`, `services/discovery_rate_limit_service.py`.
- DAL: `database/mongodb.py`, `database/pinecone_db.py`, `database/repository.py`.
- Settings: `config/settings.py`.

## Task 1: Add Failing Phase 0 Tests

**Files:**
- Create: `tests/test_phase0_common.py`

- [ ] **Step 1: Write imports and shared fixtures**

Use timezone-aware datetimes and import only new common packages.

```python
from datetime import datetime, timezone
import inspect

import pytest

from common.errors import AppError, NotFoundError, ValidationError
from common.dto import (
    AgentInfo,
    AgentCardSnapshot,
    RoomSummary,
    RoomMembership,
    MessageRecord,
    RoomCreationParams,
    ExecutionRequest,
    ExecutionResult,
    WorkflowState,
    ContextBlock,
    CompactionResult,
    MemorySearchResult,
    DeliveryEnvelope,
    DeliveryEvent,
    SSEEvent,
    NotificationPayload,
    HubConnectionInfo,
    HubAgentStatus,
    RelayPayload,
    LLMRequest,
    LLMResponse,
    EmbeddingResult,
    ModelInfo,
    RateLimitInfo,
    FileMetadata,
    GatewayRoute,
    QueryFilter,
    PaginationParams,
    SortOrder,
    InternalDomainEvent,
    AgentRegistered,
    RoomCreated,
)
```

Do not import `Settings` at module import time. Importing `common.config.settings` constructs the module-level `settings` singleton, which can hide environment changes made by `monkeypatch` in settings tests.

- [ ] **Step 2: Test `FrozenDTO` immutability**

```python
def test_frozen_dto_is_immutable():
    agent = AgentInfo(agent_id="a1", name="Agent", status="active")

    with pytest.raises(Exception):
        agent.name = "Changed"
```

- [ ] **Step 3: Test representative DTO instantiation**

Instantiate every requested DTO at least once. Prefer minimal required fields:

```python
def test_phase0_dtos_can_be_instantiated():
    now = datetime.now(timezone.utc)

    AgentInfo(agent_id="a1", name="Agent", status="active")
    AgentCardSnapshot(agent_id="a1", url="http://agent", name="Agent", raw_card={})
    RoomSummary(room_id="r1", room_name="Room", owner_id="u1", owner_name="User", created_at=now)
    RoomMembership(room_id="r1", agent_ids=["a1"])
    MessageRecord(room_id="r1", message_id="m1", message_type="user", content={}, created_at=now)
    RoomCreationParams(owner_id="u1", owner_name="User", room_name="Room")
    ExecutionRequest(room_id="r1", message_text="hello", sender_id="u1", sender_name="User")
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
    ModelInfo(model_id="m1", logical_name="test", provider="openai", capabilities=[], max_context_tokens=1)
    RateLimitInfo(limit=10, remaining=9, reset_at=now)
    FileMetadata(file_id="f1", room_id="r1", user_id="u1", s3_key="uploads/r1/f1/x.txt", mime_type="text/plain", file_name="x.txt", size_bytes=1)
    GatewayRoute(agent_id="a1", gateway_url="/gateway/a1")
    QueryFilter(criteria={"room_id": "r1"})
    PaginationParams(page=1, limit=10)
    SortOrder(field="created_at", direction="desc")
    InternalDomainEvent(timestamp=now)
    AgentRegistered(agent_id="a1", timestamp=now)
    RoomCreated(room_id="r1", owner_id="u1", timestamp=now)
```

- [ ] **Step 4: Test all protocols are runtime-checkable**

```python
def test_protocols_are_runtime_checkable():
    import common.protocols as protocols

    for name in protocols.__all__:
        obj = getattr(protocols, name)
        if inspect.isclass(obj):
            assert getattr(obj, "_is_runtime_protocol", False), name
```

- [ ] **Step 5: Test event exports do not collide**

```python
def test_event_exports_are_distinct():
    assert DeliveryEvent is not InternalDomainEvent
    assert InternalDomainEvent.__name__ == "InternalDomainEvent"
```

- [ ] **Step 6: Test `Settings()` construction reads env**

```python
def test_settings_class_loads_from_env(monkeypatch):
    monkeypatch.setenv("MONGODB_DB_NAME", "phase0_test_db")
    from common.config.settings import Settings

    settings = Settings()

    assert settings.mongodb_db_name == "phase0_test_db"
```

- [ ] **Step 7: Test legacy and common settings share one singleton**

```python
def test_legacy_settings_singleton_is_common_singleton():
    from common.config import settings as common_settings
    from config.settings import settings as legacy_settings

    assert legacy_settings is common_settings
```

- [ ] **Step 8: Test error hierarchy**

```python
def test_error_hierarchy():
    err = NotFoundError("Agent", "a1")

    assert isinstance(err, AppError)
    assert err.code == "NOT_FOUND"
    assert err.details["entity_type"] == "Agent"

    validation = ValidationError("Invalid input", details={"field": "name"})
    assert str(validation) == "Invalid input"
    assert validation.details == {"field": "name"}
```

- [ ] **Step 9: Run test to verify it fails**

Run:

```bash
python -m pytest tests/test_phase0_common.py -v
```

Expected: FAIL with import errors for missing `common.dto`, `common.protocols`, `common.errors`, or `common.config` source files.

## Task 2: Implement DTO Base and DTO Packages

**Files:**
- Create: all files under `common/dto/`
- Test: `tests/test_phase0_common.py`

- [ ] **Step 1: Add `common/dto/base.py`**

```python
from pydantic import BaseModel, ConfigDict


class FrozenDTO(BaseModel):
    """Base for immutable module-boundary DTOs."""

    model_config = ConfigDict(frozen=True)
```

- [ ] **Step 2: Add agent DTOs in `common/dto/agent.py`**

Use fields from `models/agent.Agent` and `common/types.AgentCard`, but keep DTOs independent from both modules.

Required public classes:

- `AgentInfo`
- `AgentCardSnapshot`
- `AgentMatchResult`
- `HubAgentDescriptor`
- `SyncedHubAgent`

Minimum field set:

```python
class AgentInfo(FrozenDTO):
    agent_id: str
    name: str | None = None
    description: str | None = None
    url: str | None = None
    provider_id: str | None = None
    status: str = "active"
    capabilities: list[str] = Field(default_factory=list)
    source: str = "cloud"
    hub_id: str | None = None
    is_hub_online: bool | None = None
    is_public: bool = True
    public_url: str | None = None
    rate_limit_per_user_per_hour: int | None = None
    rate_limit_system_per_hour: int | None = None
    call_count: int = 0
```

- [ ] **Step 3: Add room DTOs in `common/dto/room.py`**

Implement the user-requested names plus protocol support names from the design doc. Use aliases only when they are exact equivalents.

Required public classes/names:

- `RoomSummary`
- `RoomMembership`
- `MessageRecord`
- `RoomCreationParams`
- `MembershipSeed`
- `MembershipUpdateRequest`
- `RoomInfo`
- `CreateRoomRequest`
- `UserMessageInput`
- `AgentMessageInput`
- `SavedUserMessage`
- `RoomMessageInfo`

Mapping:

- `RoomCreationParams` mirrors current `models.request.RoomCenterRoomSettingRequest` creation fields.
- `CreateRoomRequest = RoomCreationParams` is acceptable if signatures need the design-doc name.
- `MessageRecord` mirrors common fields in `models.room.RoomMessage`.
- `RoomMessageInfo = MessageRecord` is acceptable.

- [ ] **Step 4: Add execution DTOs in `common/dto/execution.py`**

Required public classes/names:

- `ExecutionRequest`
- `ExecutionResult`
- `WorkflowState`
- `ExecutionAck`
- `RunState`
- `RunInfo`
- `HITLRequest`
- `HITLResponse`
- `AgentEvent`

Use `models/run.py` values for `RunState`. `WorkflowState` is a compatibility name for future execution state and must not reference the decommissioned legacy workflow models.

- [ ] **Step 5: Add context/memory DTOs in `common/dto/context_memory.py`**

Required public classes:

- `ContextBlock`
- `AssembledContext`
- `CompactionResult`
- `MemorySearchResult`
- `RoomMemoryInfo`
- `UserMemory`

Use field names from `models/memory.py`, `models/compaction.py`, and `models/search.py`, but keep the DTOs compact.

- [ ] **Step 6: Add delivery DTOs in `common/dto/delivery.py`**

Required public classes/names:

- `DeliveryEnvelope`
- `SSEEvent`
- `NotificationPayload`
- `DeliveryEventBase`
- `ProcessingStatusEvent`
- `RunEventNotification`
- `AgentMessagePartial`
- `AgentMessageFinal`
- `CancellationEvent`
- `HITLRequestEvent`
- `HITLResolvedEvent`
- `HubAgentEvent`
- `DebateRoundEvent`
- `DeliveryEvent`

Use `DeliveryEventBase` as the base for frontend-visible event DTOs. Use `Annotated[..., Field(discriminator="event_type")]` for the `DeliveryEvent` union. Do not define or export a delivery-side class named `DomainEvent`; `common/dto/internal_events.py` also needs an internal event base and the two names collide in `common/dto/__init__.py`.

- [ ] **Step 7: Add internal event DTOs in `common/dto/internal_events.py`**

Required public classes/names:

- `InternalDomainEvent`
- `AgentRegistered`
- `RoomCreated`
- `MessageCommitted`
- `RunStateChanged`
- `HubAgentResponseInternal`
- `InternalEvent`

Do not define or export an internal-events class named `DomainEvent`. Use `InternalDomainEvent` for the internal event base so `common/dto/__init__.py` can safely re-export both `DeliveryEvent` and `InternalDomainEvent`.

- [ ] **Step 8: Add hub, llm, platform, dal, and a2a DTOs**

Implement:

- `common/dto/hub.py`: `HubConnectionInfo`, `HubAgentStatus`, `RelayPayload`, `HubDispatchCommand`, `HubDispatchResult`, `HubInfo`.
- `common/dto/llm.py`: `LLMRequest`, `LLMResponse`, `EmbeddingResult`, `ModelInfo`, `LLMUsage`, `LLMStructuredResponse`.
- `common/dto/platform.py`: `RateLimitInfo`, `FileMetadata`, `GatewayRoute`, `GatewayRequest`, `GatewayResponse`, `RateLimitResult`, `FileInfo`.
- `common/dto/dal.py`: `QueryFilter`, `PaginationParams`, `SortOrder`, `VectorRecord`, `VectorSearchResult`.
- `common/dto/a2a.py`: `InternalAgentMessage`, `AgentTaskResult`, `AgentStreamEvent`.

- [ ] **Step 9: Add `common/dto/__init__.py`**

Re-export all public DTOs needed by tests and protocols. Keep `__all__` explicit and sorted by module grouping.

Re-export `DeliveryEvent` and `InternalDomainEvent` explicitly. Do not re-export any unqualified `DomainEvent` alias from either `delivery.py` or `internal_events.py`; that recreates the naming collision this plan is avoiding.

- [ ] **Step 10: Run DTO tests**

Run:

```bash
python -m pytest tests/test_phase0_common.py::test_phase0_dtos_can_be_instantiated tests/test_phase0_common.py::test_frozen_dto_is_immutable -v
```

Expected: PASS for DTO tests; protocol/config/error tests may still fail.

## Task 3: Implement Error Hierarchy

**Files:**
- Create: `common/errors/base.py`
- Create: `common/errors/__init__.py`
- Test: `tests/test_phase0_common.py`

- [ ] **Step 1: Add `AppError` and required subclasses**

Required user-facing hierarchy:

```python
class AppError(Exception):
    code: str
    message: str
    details: dict

    def __init__(self, message: str, code: str = "APP_ERROR", details: dict | None = None):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(message)
```

Subclasses:

- `NotFoundError(AppError)`: code `NOT_FOUND`, constructor `(entity_type: str, entity_id: str)`.
- `ValidationError(AppError)`: code `VALIDATION`, constructor `(message: str, details: dict | None = None)`.
- `AuthorizationError(AppError)`: code `AUTHORIZATION`, constructor `(message: str = "Not authorized", details: dict | None = None)`.
- `ExternalServiceError(AppError)`: code `EXTERNAL_SERVICE`, constructor `(message: str, service: str | None = None, details: dict | None = None)`.

- [ ] **Step 2: Add design-doc compatibility names**

Add these as non-breaking extensions because `docs/MODULAR_DECOUPLING_DESIGN.md` names them:

- `HybroError = AppError`
- `ConflictError(AppError)`, code `CONFLICT`
- `TransientError(AppError)`, code `TRANSIENT`, include `retry_after: int | None`
- `UpstreamError(ExternalServiceError)`, code `UPSTREAM`

Do not define a class named `PermissionError`; it shadows the builtin. Use `AuthorizationError`.

- [ ] **Step 3: Re-export errors**

`common/errors/__init__.py` should import from `.base` and define explicit `__all__`.

- [ ] **Step 4: Run error tests**

Run:

```bash
python -m pytest tests/test_phase0_common.py::test_error_hierarchy -v
```

Expected: PASS.

## Task 4: Implement Config Canonical Path and Shim

**Files:**
- Create: `common/config/settings.py`
- Create: `common/config/__init__.py`
- Modify: `config/settings.py`
- Test: `tests/test_phase0_common.py`

- [ ] **Step 1: Copy current settings source**

Copy `config/settings.py` into `common/config/settings.py` with the same `Settings` class, validators, defaults, and `settings = Settings()` singleton.

When copying, fix the `.env` path calculation because the file moves one directory deeper:

```python
# common/config/settings.py
base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
env_file = os.path.join(base_dir, ".env")
```

Do not migrate other `os.getenv()` usage in this task; that belongs to Phase 0b.

- [ ] **Step 2: Add package re-export**

`common/config/__init__.py`:

```python
from common.config.settings import Settings, settings

__all__ = ["Settings", "settings"]
```

- [ ] **Step 3: Replace legacy settings module with shim**

`config/settings.py`:

```python
from common.config.settings import Settings, settings

__all__ = ["Settings", "settings"]
```

This is the only existing source-file modification in this plan.

- [ ] **Step 4: Run config tests**

Run:

```bash
python -m pytest tests/test_phase0_common.py::test_settings_class_loads_from_env tests/test_phase0_common.py::test_legacy_settings_singleton_is_common_singleton -v
```

Expected: PASS.

## Task 5: Implement Protocol Definitions

**Files:**
- Create: all files under `common/protocols/`
- Test: `tests/test_phase0_common.py`

General protocol rules:

- Every protocol class must be decorated with `@runtime_checkable`.
- Import only from stdlib typing and `common.dto`.
- Do not import `services.*`, `models.*`, `database.*`, `a2a.*`, or provider SDK modules.
- Prefer signatures from the design doc when they describe future module facades; keep parameter names aligned with current services where those methods already exist.
- Use `dict` for external SDK payloads at common boundaries.

- [ ] **Step 1: Add `common/protocols/agent_protocols.py`**

Protocols:

- `AgentRegistry`
- `AgentMatcher`
- `AgentManagement`
- `AgentRegistryWriter`

Map to current sources:

- `AgentService.query_agent_by_agent_id`, `get_agents_by_provider_id`, `get_all_agents`, `get_all_active_agents`, `register_agent`, `update_agent`, `remove_agent`.
- `AgentMatcher.match(...)`.
- Hub sync behavior from `RelayService.sync_agents(...)` and `mark_hub_agents_offline(...)`.

- [ ] **Step 2: Add `common/protocols/room_protocols.py`**

Protocols:

- `RoomRegistry`
- `RoomManagement`
- `RoomMessageStore`
- `RoomHistoryReader`
- `RoomOwnershipReader`

Map to current sources:

- Room lifecycle: `RoomServices.create_new_room`, `inquiry_room_setting`, `inquiry_rooms_by_room_owner_id`, `update_room_agent_set`, `update_room_name`, `delete_room_by_room_id`.
- User messages: `send_message_to_room`, `create_and_parse_user_message`, `_persist_user_message`.
- Agent messages/history: `process_agent_message`, `update_agent_message_by_message_id`, `inquiry_user_messages_by_room_id`, `inquiry_agent_messages_by_room_id`, `inquiry_room_messages_by_room_id`.

- [ ] **Step 3: Add `common/protocols/execution_protocols.py`**

Protocols:

- `ExecutionEngine`
- `HITLManager`
- `WorkflowController`

The Claude reference lists `WorkflowController`; the design doc also includes `HubAgentResponseSink`. Include `HubAgentResponseSink` if `HubAgentResponseInternal` is imported by later protocols.

Map to current sources:

- `RoomServices.send_message_to_room` as current execution entrypoint.
- `RunCommandHandler.record_processing_status`, `heal_head_from_events`, `append_run_timeout_failure`.
- `RunLifecycleService.record_processing_status`.
- `HITLService.request_input`, `handle_response`, `get_pending_requests`, `cancel_request`, `cancel_requests_for_message`.

- [ ] **Step 4: Add context/memory, delivery, hub, llm, platform protocols**

Files and protocols:

- `context_memory_protocols.py`: `ContextAssembler`, `MemoryManager`, `MemoryProjector`.
- `delivery_protocols.py`: `SSETransport`, `EventPublisher`.
- `hub_protocols.py`: `HubManagement`, `HubLivenessReader`, `HubDispatchPort`, `HubAgentResponseSink`.
- `llm_protocols.py`: `LLMProvider`, `ModelRegistry`.
- `platform_protocols.py`: `GatewayService`, `RateLimiter`, `FileStorage`.

Map to current sources:

- `ContextAssemblyService.build_supervisor_context`, `build_agent_execution_context`.
- `CompactionService.should_compact`, `compact_if_needed`, `compact_room_memory`.
- `SSEManager.add_connection`, `remove_connection`, `broadcast_to_room`, `send_processing_status`, cancellation methods.
- `RelayService.register_hub`, `connect_hub`, `process_publish`, `push_to_hub`, `cancel_relay_task`, `reply_to_relay_task`, `get_hub_status`.
- `OpenAIService`, `BedrockService`, and `GeminiService` methods through generic `generate`, `generate_structured`, and `embed` future facade signatures.
- `GatewayService.send_message`, `prepare_stream`; rate limit services `check_rate_limit`, `record_request`; `FileUploadService.upload`.

`delivery_protocols.EventPublisher.emit(...)` should accept `DeliveryEvent`, and `emit_internal(...)` should accept `InternalEvent`. Do not import or reference a `DomainEvent` name from `common.dto`.

- [ ] **Step 5: Add DAL/repository/A2A protocols**

Files and protocols:

- `dal_protocols.py`: `MongoDAL`, `MongoCollection`, `RedisKV`, `RedisPubSub`, `RedisStreams`, `VectorDAL`, `ObjectStorageDAL`, `DistributedLock`, `LeaderElector`, `IndexRegistry`.
- `repository_protocols.py`: `AgentRepository`, `RoomRepository`, `TaskRepository`, plus a generic CRUD protocol if useful.
- `a2a_protocols.py`: `AgentTransport`, `AgentCardResolver`.

Map to current sources:

- `database/mongodb.MongoDB` collection access and lifecycle.
- `database/pinecone_db.PineconeDB.query`, `upsert`, `delete`.
- `database/repository.Repository` CRUD shape.
- A2A behavior in `services/a2a_service.py` and `common/client/card_resolver.py`.

- [ ] **Step 6: Add `common/protocols/__init__.py`**

Re-export all protocols with explicit `__all__`. The runtime-checkable test will iterate over this list, so include protocol classes only.

- [ ] **Step 7: Run protocol tests**

Run:

```bash
python -m pytest tests/test_phase0_common.py::test_protocols_are_runtime_checkable -v
```

Expected: PASS.

## Task 6: Implement Observability Stubs

**Files:**
- Create: `common/observability/logging.py`
- Create: `common/observability/metrics.py`
- Create: `common/observability/tracing.py`
- Create: `common/observability/__init__.py`

- [ ] **Step 1: Add logging wrapper**

Use the existing logger and avoid new dependencies:

```python
from common.utils.logger import get_logger


def configure_logging(settings=None) -> None:
    """Compatibility hook for future structured logging setup."""
    return None


__all__ = ["configure_logging", "get_logger"]
```

- [ ] **Step 2: Add metrics protocol and no-op collector**

```python
from typing import Protocol, runtime_checkable


@runtime_checkable
class MetricsCollector(Protocol):
    def increment(self, name: str, value: float = 1.0, tags: dict[str, str] | None = None) -> None: ...
    def gauge(self, name: str, value: float, tags: dict[str, str] | None = None) -> None: ...
    def timing(self, name: str, value_ms: float, tags: dict[str, str] | None = None) -> None: ...


class NoopMetricsCollector:
    def increment(self, name: str, value: float = 1.0, tags: dict[str, str] | None = None) -> None:
        return None
    def gauge(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        return None
    def timing(self, name: str, value_ms: float, tags: dict[str, str] | None = None) -> None:
        return None
```

- [ ] **Step 3: Add tracing protocol and no-op provider**

Use stdlib `contextlib.nullcontext`; do not import OpenTelemetry.

- [ ] **Step 4: Add observability re-exports**

Re-export `get_logger`, `configure_logging`, `MetricsCollector`, `NoopMetricsCollector`, `TracingProvider`, and `NoopTracingProvider`.

## Task 7: Run Full Phase 0 Verification

**Files:**
- Test: `tests/test_phase0_common.py`

- [ ] **Step 1: Run requested test command**

Run:

```bash
python -m pytest tests/test_phase0_common.py -v
```

Expected: all tests PASS.

- [ ] **Step 2: Run import smoke checks**

Run:

```bash
python -m compileall common/dto common/protocols common/errors common/config common/observability tests/test_phase0_common.py
```

Expected: no syntax/import failures.

- [ ] **Step 3: Check changed files**

Run:

```bash
git status --short
```

Expected changes are limited to:

- `common/dto/*`
- `common/protocols/*`
- `common/errors/*`
- `common/config/*`
- `common/observability/*`
- `config/settings.py`
- `tests/test_phase0_common.py`

- [ ] **Step 4: Optional broader regression smoke**

If time permits, run a small existing import-focused test subset:

```bash
python -m pytest tests/test_models.py tests/test_common_api_key_auth.py -v
```

Expected: PASS. If unrelated environment-dependent failures occur, document them and keep the Phase 0 test as the required gate.

## Task 8: Commit

**Files:**
- All files changed in this plan.

- [ ] **Step 1: Review diff**

Run:

```bash
git diff --stat
git diff -- config/settings.py
```

Expected: `config/settings.py` is only a re-export shim.

- [ ] **Step 2: Stage scoped changes**

Run:

```bash
git add common/dto common/protocols common/errors common/config common/observability config/settings.py tests/test_phase0_common.py
```

- [ ] **Step 3: Commit with required message**

Run:

```bash
git commit -m "feat: add common foundation layer (protocols, DTOs, config, errors)"
```

Expected: one commit on `refactor/phase-0-common`.

## Implementation Guardrails

- Keep DTOs frozen and side-effect free.
- New common DTOs must not import existing `models.*`; derive fields by reading models, not by coupling to them.
- New protocols must not import concrete service classes.
- Do not make current services conform to protocols in this phase.
- Do not delete ignored `__pycache__` directories unless explicitly cleaning the workspace.
- Keep `common/types.py` unchanged; it remains A2A-facing.
- Keep runtime behavior unchanged; the only existing-source behavior should be `config.settings` resolving the same singleton from a new canonical path.

## Known Risks

- The design doc and Claude brief disagree on some names. Implement both when cheap: for example `RoomCreationParams` plus `CreateRoomRequest`, `ExecutionResult` plus `ExecutionAck`, and user-requested error names plus design-doc aliases.
- `common/observability` in the design doc mentions `structlog` and OpenTelemetry, but the Phase 0 constraints forbid new dependencies. Use no-op protocols and wrappers only.
- Packaging may omit new subpackages because `pyproject.toml` has an explicit package list. Do not change it in this phase unless the owner approves expanding scope.
- `common/utils/logger.py` still uses `os.getenv()`. Do not migrate it here; that belongs to Phase 0b config unification.
