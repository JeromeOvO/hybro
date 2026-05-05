# Modular Decoupling Design Document

> **Status**: Proposal (v3)  
> **Date**: 2026-05-04  
> **Scope**: Refactor hybro-multi-agents-backend into interface-driven modular architecture  
> **Constraint**: All existing features remain unchanged; no new technology stack; zero backend breaking changes

---

## 1. Executive Summary

The current codebase delivers a full-featured multi-agent orchestration platform with rooms, supervisor/debate workflows, HITL, hub relay, memory compaction, and a discovery/gateway API. However, it suffers from tight coupling via singleton imports, a service-locator anti-pattern, no interface abstractions, and monolithic initialization.

This document proposes restructuring the codebase into **well-defined modules** connected through **Python Protocol interfaces**, managed by **module-scoped sub-containers**, while preserving every existing feature and API endpoint. The modular structure enables future technology stack replacement (DBOS, AG-UI, etc.) by creating clean seams — but this document does not introduce any new technology.

### Design Principles

1. **纯解耦，不换栈** — 保持 MongoDB + Redis + Pinecone + FastAPI，只改结构
2. **Protocol 边界** — 模块间只通过 Common 中定义的 Protocol 通信
3. **统一 DAL** — 数据访问统一封装，模块基于 DAL 构建 Repository
4. **防腐层** — A2A 协议和 LLM Provider 各有独立 adapter 层，业务模块不直接 import 外部 SDK
5. **可落地性** — 分阶段迁移，三层防线保证不 break

---

## 2. Current Architecture Problems

### 2.1 Coupling Analysis

| Problem | Example | Impact |
|---------|---------|--------|
| Service Locator | `from services.room_services import room_services` | Cannot inject mocks; hidden dependencies |
| Global Settings | `from config.settings import settings` everywhere | No test isolation; env coupling |
| Monolithic Init | main.py 548-line lifespan with phased startup | Startup order is implicit; fragile |
| No Interface Layer | Services import concrete classes | Change ripples across entire codebase |
| SSE God Object | `sse_manager` has Redis, DB, broker, change stream + run_command_handler side effects | Single class with 6+ responsibilities |
| LLM Scatter | openai/gemini/bedrock services independently called | Provider switch requires touching business code |
| A2A Leakage | `a2a-sdk` types used directly in business modules | Protocol version bump cascades everywhere |
| Hub Coupling | RelayService directly writes agents_collection | Hub logic and agent lifecycle tangled |
| Config Scatter | 30+ `os.getenv()` calls outside settings.py | Settings model incomplete; silent key mismatches |

### 2.2 Current Dependency Graph (Implicit)

```
api/* ──→ modules/* ──→ services/* ──→ database/*
  │           │              │              │
  └───────────┼──────────────┼──────────────┘
              │              │         (all import settings, sse_manager, mongodb,
              └──────────────┘          openai_service, a2a_service directly)
```

Every layer reaches into any other layer via singleton imports. No enforced boundary.

---

## 3. Target Architecture

### 3.1 Layer Diagram

```
┌────────────────────────────────────────────────────────────────────────────┐
│                            Application Shell                                │
│   main.py: container assembly, lifespan, sub-container wiring, job sched   │
└─────┬──────────────────────────────────────────────────────────────────────┘
      │
      ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                         API Layer (thin adapters)                           │
│   agent / room / execution / delivery / memory / hub / gateway / ...       │
│   parse request → sub-container → Protocol call → format response          │
└─────┬──────────────────────────────────────────────────────────────────────┘
      │ sub-container 注入 Protocol
      ▼
┌─────────┐┌─────────┐┌───────────┐┌──────────┐┌──────────┐┌──────────┐┌───────────────┐
│  Agent  ││  Room   ││ Execution ││ Context  ││ Delivery ││ Platform ││HubRuntime     │
│  Module ││ Module  ││  Module   ││ & Memory ││  Module  ││  Module  ││Bridge         │
│         ││         ││           ││  Module  ││          ││          ││               │
│-agent   ││-room    ││-run/      ││-assembly ││-sse      ││-gateway  ││-connection    │
│ CRUD    ││ CRUD    ││-orchestr/ ││-compactn ││-event    ││-rate     ││-relay         │
│-health  ││-member  ││-hitl/     ││-search   ││ broker   ││ limit    ││-liveness      │
│-match   ││-message ││-workflow/ ││-user mem ││-dedup    ││-file     ││-offline queue │
│-resolve ││ graph   ││-dispatch/ ││-token    ││-translate││ storage  ││-agent sync    │
│-card    ││-visible ││-state/    ││ budget   ││          ││          ││               │
└────┬────┘└────┬────┘└─────┬─────┘└────┬─────┘└────┬─────┘└────┬─────┘└───────┬───────┘
     │          │           │           │           │           │            │
     └──────────┼───────────┼───────────┼───────────┼───────────┼────────────┘
                │           │           │           │           │
                ▼           ▼           ▼           ▼           ▼
     ┌────────────────────────────────────────────────────────────────────────┐
     │                      Adapter Layer (防腐层)                             │
     │                                                                        │
     │   ┌─────────────────────────┐    ┌─────────────────────────┐          │
     │   │  A2A Protocol Adapter   │    │      LLM Gateway        │          │
     │   │  AgentTransport         │    │  generate / embed       │          │
     │   │  AgentCardResolver      │    │  model registry         │          │
     │   │  内部 DTO ↔ a2a-sdk     │    │  routing / fallback     │          │
     │   └─────────────────────────┘    └─────────────────────────┘          │
     └───────────────────────────┬────────────────────────────────────────────┘
                                 │
                                 ▼
     ┌────────────────────────────────────────────────────────────────────────┐
     │                      Data Access Layer (DAL)                            │
     │   MongoDAL / RedisKV / RedisPubSub / RedisStreams / VectorDAL / S3DAL │
     └───────────────────────────┬────────────────────────────────────────────┘
                                 │
                                 ▼
     ┌────────────────────────────────────────────────────────────────────────┐
     │                         Common Module                                   │
     │   Protocols / DTOs / Auth / Config / Utils / Errors                    │
     └────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Module Inventory

| # | Module | Responsibility | Current Source |
|---|--------|---------------|----------------|
| 1 | **Common** | Protocols, DTOs, auth, config, utils, errors | `common/`, `models/`, `config/` |
| 2 | **DAL** | Unified data access clients (split by concern) | `database/`, `infrastructure/redis_service.py`, `services/s3_service.py` |
| 3 | **A2A Protocol Adapter** | 防腐 a2a-sdk, internal model ↔ A2A types | `services/a2a_service.py`, `common/client/` |
| 4 | **LLM Gateway** | Unified LLM invocation, provider routing, capability registry | `services/openai_service.py`, `services/gemini_service.py`, `services/bedrock_service.py` |
| 5 | **Agent** | Agent lifecycle, health, matching, discovery | `services/agent_*.py`, `api/agent.py`, `api/discovery.py` |
| 6 | **Room** | Room CRUD, membership, raw message persistence, message graph | `modules/RoomCenter.py`, `services/room_*.py` |
| 7 | **Context & Memory** | Context assembly, compaction, search, user memory, chat contexts | `services/memory_*.py`, `services/compaction_service.py`, `services/context_assembly_service.py` |
| 8 | **Execution** | Run lifecycle, supervisor, debate, HITL, dispatch (NOT workflow) | `modules/SupervisorExecutor.py`, `modules/RoomMessageCenter.py`, `services/run_*.py`, `services/hitl_service.py` |
| 9 | **Workflow** | Task decomposition, meta-task execution, chat context summary | `modules/WorkflowCenter.py`, `services/task_service.py` |
| 10 | **Delivery** | SSE connections, event broker, dedup, domain→frontend event translation | `services/sse_services.py`, `infrastructure/event_broker.py`, `infrastructure/brokers/` |
| 11 | **Platform** | Gateway API, rate limiting, file storage | `services/gateway_service.py`, `services/*_rate_limit_service.py`, `services/file_upload_service.py` |
| 12 | **HubRuntimeBridge** | Hub connection, relay, liveness, offline queue, agent sync | `services/relay_service.py`, `infrastructure/relay_streams.py`, `api/relay.py`, `api/hub.py` |
| 13 | **Jobs** | Background tasks with leader election | `jobs/*`, `infrastructure/leader_election.py` |

> **NOTE (B2 fix)**: Execution and Workflow are **separate modules** because they have independent lifecycles:
> - **Execution** operates on `runs` / `run_events` / `room_agent_messages`, uses supervisor state machine, returns via SSE streaming
> - **Workflow** operates on `base_tasks` / `meta_tasks` / `task_sessions` / `chat_contexts`, returns HTTP 207 partial-success, triggers `update_chat_context_by_session_id`
> They share no storage and no state machine. Workflow may invoke Execution's `ExecutionEngine` for individual meta-task agent calls, but their lifecycles are orthogonal.

### 3.3 Dependency Rules (Hard Constraints)

```
Rule 1:  Common depends on NOTHING (leaf module)
Rule 2:  DAL depends only on Common
Rule 3:  Adapter Layer (A2A, LLM Gateway) depends on DAL + Common
Rule 4:  Business modules depend on Common + DAL + Adapter Protocols
Rule 5:  Business modules communicate ONLY through Protocols (no direct imports)
Rule 6:  Delivery depends on Common + DAL ONLY (no business module dependency)
Rule 7:  API Layer depends on module Protocols via sub-container (not implementations)
Rule 8:  Application Shell is the ONLY place that knows concrete implementations
Rule 9:  No module imports `main.py` or `container.py`
Rule 10: a2a-sdk types NEVER appear outside A2A Protocol Adapter
Rule 11: LLM provider SDK types NEVER appear outside LLM Gateway
```

> **NOTE (A1 fix)**: Rule 6 means Delivery is a **pure transport**. The current `sse_manager.send_processing_status()` which calls `run_command_handler.record_processing_status()` **violates** this rule. Resolution: Execution must call `RunLifecyclePort.record_processing_status()` **before** calling `EventPublisher.emit("processing_status", ...)`. Delivery only translates and delivers. See §4.5 for the enforced call-site contract.

### 3.4 Cross-Module Communication Rules

| From | To | Mechanism | Notes |
|------|----|-----------|-------|
| Execution → Agent | `AgentRegistry` Protocol | Sync call | |
| Execution → Room | `RoomRegistry` + `RoomMessageStore` | Sync call | |
| Execution → Context & Memory | `ContextAssembler` Protocol | Sync call | |
| Execution → Delivery | `EventPublisher` Protocol | Fire-and-forget | Execution records side effects BEFORE emitting |
| Execution → A2A Adapter | `AgentTransport` Protocol | Async call | |
| Execution → HubRuntimeBridge | `HubDispatchPort` Protocol | Async call | |
| Workflow → Execution | `ExecutionEngine` Protocol | Async call | For individual meta-task agent calls |
| Workflow → Agent | `AgentMatcher` Protocol | Sync call | For meta-task agent assignment |
| Workflow → Context & Memory | `ChatContextManager` Protocol | Sync call | For task session memory |
| Context & Memory → Room | `RoomHistoryReader` Protocol | Sync call | |
| Context & Memory ← (domain events) | `MessageCommitted` | **In-process** on emitting worker + **Redis Pub/Sub** for other workers | See §4.5 |
| HubRuntimeBridge → Agent | `AgentRegistryWriter` Protocol | Sync call | |
| HubRuntimeBridge → Execution | `AgentEventSink` Protocol | Sync call | |
| HubRuntimeBridge → Room | `RoomOwnershipReader` Protocol | Sync call | |
| Agent → HubRuntimeBridge | `HubLivenessReader` Protocol | Sync call | For agent hydration: `is_hub_online` |
| Agent ↔ Room | NO direct dependency | — | |

---

## 4. Protocol Definitions

### 4.1 Agent Module Protocols

```python
# common/protocols/agent_protocols.py

@runtime_checkable
class AgentRegistry(Protocol):
    """Read-only agent lookup — used by Execution, HubRuntimeBridge, Platform."""

    async def get_agent(self, agent_id: str) -> AgentInfo | None: ...
    async def get_agent_card(self, agent_id: str) -> AgentCardSnapshot | None: ...
    async def get_agents_by_ids(self, agent_ids: list[str]) -> list[AgentInfo]: ...
    async def is_agent_healthy(self, agent_id: str) -> bool: ...
    async def is_directly_callable(self, agent_id: str) -> bool: ...
        """Returns False for hub-source agents (must go via HubDispatchPort).
        Platform/Gateway uses this to return 502 for hub agents."""


@runtime_checkable
class AgentMatcher(Protocol):
    """Agent selection — used by Execution, Workflow."""

    async def match_agents(
        self,
        query: str,
        limit: int = 5,
        filter_ids: list[str] | None = None,
        respect_visibility: bool = True,
        requesting_user_id: str | None = None,
    ) -> list[AgentMatchResult]: ...
        """respect_visibility=False for Discovery API (public, no filter);
        respect_visibility=True for internal Listings/matching (owner-scoped, URL masked).
        When respect_visibility=True, requesting_user_id MUST be provided —
        used to resolve _build_visibility_filter(user_id). If omitted with
        respect_visibility=True, returns only fully-public agents."""


@runtime_checkable
class AgentManagement(Protocol):
    """Full agent lifecycle — used by API layer."""

    async def register_agent(self, url: str, provider_id: str, **kwargs) -> AgentInfo: ...
    async def delete_agent(self, agent_id: str, provider_id: str) -> bool: ...
    async def update_agent(self, agent_id: str, updates: dict) -> AgentInfo | None: ...
    async def list_agents(self, provider_id: str) -> list[AgentInfo]: ...
    async def list_public_agents(self, limit: int = 50) -> list[AgentInfo]: ...


@runtime_checkable
class AgentRegistryWriter(Protocol):
    """Hub agent sync — used by HubRuntimeBridge only."""

    async def sync_hub_agents(
        self,
        hub_id: str,
        owner_user_id: str,
        agents: list[HubAgentDescriptor],
        prune_missing: bool = True,
    ) -> list[SyncedHubAgent]: ...

    async def mark_hub_agents_offline(self, hub_id: str) -> None: ...
```

### 4.2 Room Module Protocols

```python
# common/protocols/room_protocols.py

@runtime_checkable
class RoomRegistry(Protocol):
    """Room state lookup — used by Execution, Workflow, HubRuntimeBridge."""

    async def get_room(self, room_id: str) -> RoomInfo | None: ...
    async def get_room_agents(self, room_id: str) -> list[str]: ...
    async def get_room_owner(self, room_id: str) -> str | None: ...


@runtime_checkable
class RoomManagement(Protocol):
    """Full room lifecycle — used by API layer."""

    async def create_room(self, request: CreateRoomRequest) -> RoomInfo: ...
    async def delete_room(self, room_id: str, owner_id: str) -> bool: ...
    async def update_room(self, room_id: str, updates: dict) -> RoomInfo | None: ...
    async def update_membership(self, room_id: str, request: MembershipUpdateRequest) -> RoomInfo: ...


@runtime_checkable
class RoomMessageStore(Protocol):
    """Raw message persistence — used by Execution."""

    async def save_user_message(self, room_id: str, message: UserMessageInput) -> SavedUserMessage: ...
    async def save_agent_message(self, room_id: str, message: AgentMessageInput) -> str: ...
    async def update_agent_message_status(self, message_id: str, status: str, **kwargs) -> bool: ...
    async def get_message(self, message_id: str) -> RoomMessageInfo | None: ...


@runtime_checkable
class RoomHistoryReader(Protocol):
    """Raw message history — used by Context & Memory for projection."""

    async def get_messages_for_room(
        self, room_id: str, limit: int = 100, before: datetime | None = None
    ) -> list[RoomMessageInfo]: ...

    async def get_messages_by_ids(self, message_ids: list[str]) -> list[RoomMessageInfo]: ...
    async def get_message_thread(self, parent_message_id: str) -> list[RoomMessageInfo]: ...


@runtime_checkable
class RoomOwnershipReader(Protocol):
    """Room ownership verification — used by HubRuntimeBridge."""

    async def verify_room_agent_membership(self, room_id: str, agent_id: str) -> bool: ...
    async def verify_room_hub_ownership(self, room_id: str, hub_id: str) -> bool: ...
```

**Key DTOs (A3 fix):**

```python
# common/dto/room.py

class CreateRoomRequest(BaseModel):
    """Captures full room creation semantics including membership seeding."""
    owner_id: str
    owner_name: str
    room_name: str
    membership_seed: MembershipSeed

class MembershipSeed(BaseModel):
    """Encapsulates all membership initialization strategies.
    API layer transparently passes this; Room facade handles resolution."""
    mode: Literal["manual", "saved_group", "all_current_agents"]
    agent_ids: list[str] | None = None           # for mode="manual"
    group_id: str | None = None                  # for mode="saved_group"
    requesting_user_id: str | None = None        # for ownership check on group

class MembershipUpdateRequest(BaseModel):
    add_agent_ids: list[str] | None = None
    remove_agent_ids: list[str] | None = None

class SavedUserMessage(BaseModel):
    """Returned after user message persistence — matches current RoomCenterUserMessageResponse."""
    room_id: str
    message_id: str
    dispatch_root_message_id: str | None
    user_id: str
    user_name: str
    message: dict  # Full RoomUserMessage serialized
    scope_resolution_error: dict | None = None

class RoomInfo(BaseModel):
    room_id: str
    room_name: str
    owner_id: str
    owner_name: str
    agent_ids: list[str]
    membership_origin: str  # "MANUAL" | "SAVED_GROUP" | "ALL_CURRENT_AGENTS"
    membership_origin_status: str
    source_group_id: str | None = None
    source_group_name: str | None = None
    created_at: datetime
    processing_message_id: str | None = None
```

### 4.3 Context & Memory Module Protocols

```python
# common/protocols/context_memory_protocols.py

@runtime_checkable
class ContextAssembler(Protocol):
    """Context assembly for agent invocation — used by Execution."""

    async def assemble_context(
        self,
        room_id: str,
        message_id: str,
        token_budget: int,
        agent_id: str | None = None,
    ) -> AssembledContext: ...


@runtime_checkable
class MemoryManager(Protocol):
    """Memory lifecycle — used by API layer."""

    async def get_room_memory(self, room_id: str) -> RoomMemoryInfo | None: ...
    async def search_memory(self, room_id: str, query: str, limit: int = 10) -> list[MemorySearchResult]: ...
    async def get_user_memories(self, user_id: str) -> list[UserMemory]: ...
    async def delete_room_memory(self, room_id: str) -> bool: ...


@runtime_checkable
class ChatContextManager(Protocol):
    """Workflow-specific chat context — used by Workflow module.

    Two update paths:
    - summarize_and_update(): REST endpoint — receives raw user_input + agent_response,
      internally calls LLM Gateway to generate summarized context, then persists.
    - update_chat_context(): internal direct-write — for callers that already have
      processed context_data (e.g., workflow executor post-summarization).
    """

    async def get_chat_context(self, session_id: str) -> ChatContextInfo | None: ...
    async def update_chat_context(self, session_id: str, context_data: dict) -> None: ...
    async def summarize_and_update(
        self, session_id: str, user_input: str, agent_response: str
    ) -> ChatContextInfo: ...
        """Called by API layer for /memoryCenter/updateChatContextBySessionId.
        Internally invokes LLM Gateway to summarize, then persists result."""
    async def create_chat_context(self, session_id: str, user_name: str) -> ChatContextInfo: ...


@runtime_checkable
class MemoryProjector(Protocol):
    """Trigger projection from raw messages — used internally or by events."""

    async def project_message(self, room_id: str, message_id: str) -> None: ...
    async def run_compaction(self, room_id: str) -> CompactionResult: ...
```

### 4.4 Execution Module Protocols

```python
# common/protocols/execution_protocols.py

@runtime_checkable
class ExecutionEngine(Protocol):
    """Execute agent interactions within a room — used by API layer, Workflow, HubRuntimeBridge.

    IMPORTANT: execute() is fire-and-forget from HTTP perspective.
    It persists the user message, starts orchestration as a background task,
    and returns immediately with the saved message info.
    Agent responses arrive via EventPublisher → SSE stream.
    """

    async def execute(self, request: ExecutionRequest) -> ExecutionAck: ...
    async def cancel(self, room_id: str, message_id: str) -> bool: ...
    async def get_run(self, run_id: str) -> RunInfo | None: ...
    async def get_runs_for_room(self, room_id: str) -> list[RunInfo]: ...


@runtime_checkable
class HITLManager(Protocol):
    """Human-in-the-loop management — used by API layer."""

    async def create_hitl_request(
        self,
        room_id: str,
        user_message_id: str,
        prompt: str,
        source: Literal["agent", "supervisor"],
        agent_id: str | None = None,
        a2a_task_id: str | None = None,
        a2a_context_id: str | None = None,
        continuation_message_id: str | None = None,
    ) -> HITLRequest | None: ...

    async def resolve_hitl(
        self, request_id: str, response: str, responder_id: str
    ) -> HITLResponse: ...

    async def get_pending_hitl(self, room_id: str) -> list[HITLRequest]: ...
    async def cancel_hitl(self, request_id: str) -> bool: ...


@runtime_checkable
class AgentEventSink(Protocol):
    """Receive agent events from external sources — used by HubRuntimeBridge."""

    async def handle_agent_event(self, event: AgentEvent) -> None: ...
```

**Key DTOs (A2 fix):**

```python
# common/dto/execution.py

class ExecutionRequest(BaseModel):
    """Matches current RoomCenterUserMessageRequest semantics."""
    room_id: str
    message_text: str
    sender_id: str
    sender_name: str
    attachments: list[dict] | None = None
    target_agent_ids: list[str] | None = None
    parent_message_id: str | None = None
    client_request_id: str | None = None
    mode: Literal["direct", "supervisor", "debate"] = "direct"

class ExecutionAck(BaseModel):
    """Returned immediately after user message persistence.
    Matches current RoomCenterUserMessageResponse contract exactly.
    Orchestration happens asynchronously; results via SSE."""
    room_id: str
    message_id: str
    dispatch_root_message_id: str | None
    user_id: str
    user_name: str
    message: dict  # Full RoomUserMessage serialized
    message_list: list[dict] | None = None
    scope_resolution_error: dict | None = None
    success: bool
    error: str | None = None
    status_code: int = 200

class RunState(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"

class RunInfo(BaseModel):
    run_id: str
    room_id: str
    agent_id: str | None
    state: RunState
    parent_run_id: str | None
    seq: int
    error_code: str | None = None
    created_at: datetime

class HITLRequest(BaseModel):
    request_id: str
    room_id: str
    user_message_id: str
    prompt: str
    prompt_type: Literal["text", "confirmation"]
    source: Literal["agent", "supervisor"]
    status: Literal["pending", "resolved", "expired", "canceled"]
    agent_id: str | None = None
    a2a_task_id: str | None = None
    continuation_message_id: str | None = None
    created_at: datetime

class HITLResponse(BaseModel):
    request_id: str
    response_text: str
    responder_id: str
    resolved_at: datetime

class AgentEvent(BaseModel):
    """Normalized agent event from any source (direct, hub, webhook)."""
    room_id: str
    agent_id: str
    message_id: str
    event_type: Literal["partial", "final", "status_update", "error", "input_required"]
    payload: dict
    hub_id: str | None = None
```

### 4.5 Delivery Module Protocols

```python
# common/protocols/delivery_protocols.py

@runtime_checkable
class EventPublisher(Protocol):
    """Publish domain events to frontend — used by Execution, Room, HubRuntimeBridge.

    CONTRACT (A1 fix):
    - Caller MUST complete all business side effects BEFORE calling emit().
    - In particular: run_command_handler.record_processing_status() must execute
      before emit("processing_status", ...).
    - EventPublisher is a pure delivery pipe — it NEVER calls back into business modules.
    - EventPublisher MAY deduplicate terminal status events (idempotency).
    """

    async def emit(self, event: DomainEvent) -> None: ...


@runtime_checkable
class SSETransport(Protocol):
    """SSE connection management — used by API layer.

    RUNTIME CONSTRAINT (A6 fix):
    - Cancellation watcher runs in EVERY worker (not leader-elected).
    - Each worker independently monitors change stream on cancelled_messages collection.
    - This is required because cancellation must propagate to in-memory TTLCache per worker.
    """

    async def connect(self, room_id: str, connection_id: str) -> AsyncIterator[dict]: ...
    async def disconnect(self, connection_id: str) -> None: ...
    def is_cancelled(self, message_id: str) -> bool: ...
    async def mark_cancelled(self, message_id: str) -> None: ...
    def set_draining(self, draining: bool) -> None: ...
```

**DomainEvent Discriminated Union (B1 fix):**

```python
# common/dto/delivery.py

class DomainEventBase(BaseModel):
    room_id: str
    timestamp: datetime

class ProcessingStatusEvent(DomainEventBase):
    event_type: Literal["processing_status"] = "processing_status"
    message_id: str
    status: Literal["queued", "processing", "completed", "failed", "canceled"]
    agent_id: str | None = None
    details: dict | None = None
    client_request_id: str | None = None
    agents: list[dict] | None = None

class RunEventNotification(DomainEventBase):
    event_type: Literal["run_event"] = "run_event"
    event_id: str
    run_id: str
    seq: int
    run_event_type: str
    payload: dict

class AgentMessagePartial(DomainEventBase):
    event_type: Literal["agent_message_partial"] = "agent_message_partial"
    message_id: str
    agent_id: str
    content_delta: str

class AgentMessageFinal(DomainEventBase):
    event_type: Literal["agent_message_final"] = "agent_message_final"
    message_id: str
    agent_id: str
    content: dict

class CancellationEvent(DomainEventBase):
    event_type: Literal["cancellation"] = "cancellation"
    message_id: str
    reason: str | None = None

class HITLRequestEvent(DomainEventBase):
    event_type: Literal["hitl_request"] = "hitl_request"
    request_id: str
    prompt: str
    prompt_type: str
    source: str
    message_id: str

class HITLResolvedEvent(DomainEventBase):
    event_type: Literal["hitl_resolved"] = "hitl_resolved"
    request_id: str
    message_id: str

class HubAgentEvent(DomainEventBase):
    event_type: Literal["hub_agent_event"] = "hub_agent_event"
    hub_id: str
    agent_id: str
    message_id: str
    payload: dict

class DebateRoundEvent(DomainEventBase):
    event_type: Literal["debate_round"] = "debate_round"
    round_number: int
    agent_id: str
    message_id: str

# Discriminated union type
DomainEvent = Annotated[
    ProcessingStatusEvent | RunEventNotification | AgentMessagePartial |
    AgentMessageFinal | CancellationEvent | HITLRequestEvent | HITLResolvedEvent |
    HubAgentEvent | DebateRoundEvent,
    Field(discriminator="event_type"),
]
```

**Domain Event Delivery Semantics:**
- Event **payload schema** is owned by the emitting module (Execution defines ProcessingStatusEvent fields)
- Event **wire format translation** (domain → SSE frame) is owned by Delivery module
- Cross-worker delivery: in-process on emitting worker, Redis Pub/Sub fan-out to other workers
- HubRuntimeBridge publish events: arrive on one worker via HTTP, emit locally, Redis Pub/Sub to others

**Internal Domain Events (N8 fix):**

The `DomainEvent` union above is **frontend-visible** (delivered via SSE). There are also **internal-only** events used for cross-module coordination that are NOT sent to frontend:

```python
# common/dto/internal_events.py

class InternalEventBase(BaseModel):
    timestamp: datetime

class MessageCommitted(InternalEventBase):
    """Emitted by Room after user/agent message persisted.
    Context & Memory subscribes to trigger projection/compaction."""
    event_type: Literal["message_committed"] = "message_committed"
    room_id: str
    message_id: str
    message_type: Literal["user", "agent"]
    agent_id: str | None = None

class RunStateChanged(InternalEventBase):
    """Emitted by Execution after run state transition.
    Jobs module may subscribe for monitoring."""
    event_type: Literal["run_state_changed"] = "run_state_changed"
    run_id: str
    room_id: str
    old_state: str
    new_state: str

InternalEvent = MessageCommitted | RunStateChanged
```

**Internal event delivery mechanism:**
- Same `EventPublisher.register_internal_handler()` mechanism as §7.3
- Internal events go through `EventPublisher.emit_internal(event: InternalEvent)` — a separate method that:
  - Dispatches to registered internal handlers (same worker)
  - Fan-out via Redis Pub/Sub to other workers' internal handlers
  - Does NOT deliver to SSE clients
- This keeps one bus implementation with two entry points (`emit` for frontend-visible, `emit_internal` for module-to-module)

### 4.6 Workflow Module Protocols

```python
# common/protocols/workflow_protocols.py

@runtime_checkable
class WorkflowEngine(Protocol):
    """Task decomposition and multi-step workflow execution.
    
    DISTINCT FROM Execution: operates on base_tasks/meta_tasks, not runs/run_events.
    Returns HTTP 207 partial-success semantics. Does NOT use RunLifecyclePort.
    """

    async def decompose_task(self, task_id: str, room_id: str) -> list[MetaTaskInfo]: ...
    async def assign_agents(self, task_id: str, room_id: str) -> list[MetaTaskInfo]: ...
    async def run_workflow(self, task_id: str, room_id: str) -> WorkflowResult: ...
    async def get_task(self, task_id: str) -> BaseTaskInfo | None: ...
    async def get_meta_task(self, task_id: str) -> MetaTaskInfo | None: ...
    async def create_task(self, room_id: str, session_id: str, user_name: str, task_content: dict) -> BaseTaskInfo: ...
    async def retry_meta_task(self, task_id: str) -> MetaTaskInfo: ...
    async def summarize_for_base_task(self, task_id: str) -> BaseTaskInfo: ...
        """Calls LLM Gateway to summarize meta-task results into base task chat context."""
    async def list_sessions_for_user(self, user_name: str) -> list[TaskSessionInfo]: ...
    async def list_base_tasks_for_session(self, session_id: str) -> list[BaseTaskInfo]: ...
    async def list_meta_tasks_for_parent(self, parent_task_id: str) -> list[MetaTaskInfo]: ...
```

**Key DTOs:**

```python
# common/dto/workflow.py

class MetaTaskInfo(BaseModel):
    task_id: str
    parent_task_id: str
    agent_id: str | None
    task_description: str | None
    execution_order: int
    depends_on_tasks: list[str] | None = None
    status: str | None = None

class WorkflowResult(BaseModel):
    """Preserves HTTP 207 partial-success semantics."""
    task_id: str
    meta_tasks: list[MetaTaskInfo]
    partial_success: bool
    completed_count: int
    failed_count: int
    agent_messages: list[dict]  # Results from completed meta-tasks
    errors: list[dict] | None = None  # Errors from failed meta-tasks

class BaseTaskInfo(BaseModel):
    task_id: str
    session_id: str
    user_name: str
    task_content: dict
    created_at: datetime

class ChatContextInfo(BaseModel):
    memory_id: str
    session_id: str
    user_name: str
    context_data: dict
    created_at: datetime
    updated_at: datetime

class TaskSessionInfo(BaseModel):
    session_id: str
    user_name: str
    room_id: str
    created_at: datetime
```

**N4: WorkflowResult → OrchestrationResponse contract translation:**

> API layer maps `WorkflowResult` → current `OrchestrationResponse` as follows:
> - `OrchestrationResponse.meta_task_ids` = `[mt.task_id for mt in result.meta_tasks if mt.status == "completed"]`  
>   (only **successful** meta-task IDs — this is the existing contract)
> - `OrchestrationResponse.agent_messages` = `result.agent_messages`
> - HTTP status = `207` if `result.partial_success and result.failed_count > 0` else `200`
>
> Golden tests MUST assert that `meta_task_ids` excludes failed tasks.

### 4.7 HubRuntimeBridge Protocols

```python
# common/protocols/hub_protocols.py

@runtime_checkable
class HubDispatchPort(Protocol):
    """Send commands to hub agents — used by Execution/dispatch."""

    async def send_to_hub(self, command: HubDispatchCommand) -> HubDispatchResult: ...
    async def cancel_hub_task(self, hub_id: str, task_id: str) -> bool: ...
    async def reply_to_hub_task(self, hub_id: str, task_id: str, reply: dict) -> bool: ...
    def is_hub_online(self, hub_id: str) -> bool: ...


@runtime_checkable
class HubManagement(Protocol):
    """Hub lifecycle — used by API layer."""

    async def register_hub(self, hub_id: str, owner_id: str, **kwargs) -> HubInfo: ...
    async def get_hub(self, hub_id: str) -> HubInfo | None: ...
    async def list_hubs(self, owner_id: str) -> list[HubInfo]: ...
    async def connect_hub_stream(self, hub_id: str) -> AsyncIterator[dict]: ...
    async def publish_from_hub(self, hub_id: str, payload: dict) -> None: ...


@runtime_checkable
class HubLivenessReader(Protocol):
    """Hub online status — used by Agent module for agent hydration (A7 fix)."""

    def is_hub_online(self, hub_id: str) -> bool: ...
    async def get_hub_owner_id(self, hub_id: str) -> str | None: ...
```

### 4.8 Platform Module Protocols

```python
# common/protocols/platform_protocols.py

@runtime_checkable
class GatewayService(Protocol):
    """External gateway API — used by API layer.
    
    NOTE (A7 fix): Hub-source agents must return 502. Platform calls
    AgentRegistry.is_directly_callable() — Platform does NOT understand hub semantics,
    it just respects the boolean.
    """

    async def send_message(self, api_key: str, request: GatewayRequest) -> GatewayResponse: ...
    async def stream_message(self, api_key: str, request: GatewayRequest) -> AsyncIterator[dict]: ...


@runtime_checkable
class RateLimiter(Protocol):
    """Rate limiting — used by API layer."""

    async def check(self, key: str, limit: int, window: int) -> RateLimitResult: ...
    async def check_global(self, limit: int, window: int) -> RateLimitResult: ...


@runtime_checkable
class FileStorage(Protocol):
    """File upload/retrieval — used by API layer, Execution.
    S3 key: uploads/{room_id}/{file_id}/{filename}. Orphan cleanup joins on room_id."""

    async def upload(self, file_bytes: bytes, filename: str, owner_id: str, room_id: str, **kwargs) -> FileInfo: ...
    async def get_url(self, file_id: str, ttl: int = 3600) -> str | None: ...
    async def delete(self, file_id: str) -> bool: ...
    async def list_for_room(self, room_id: str) -> list[FileInfo]: ...
```

### 4.9 Adapter Layer Protocols

```python
# common/protocols/a2a_protocols.py

@runtime_checkable
class AgentTransport(Protocol):
    """Send messages to remote agents — used by Execution."""

    async def send_message(
        self, agent_url: str, message: InternalAgentMessage, **kwargs
    ) -> AgentTaskResult: ...

    async def stream_message(
        self, agent_url: str, message: InternalAgentMessage, **kwargs
    ) -> AsyncIterator[AgentStreamEvent]: ...


@runtime_checkable
class AgentCardResolver(Protocol):
    """Resolve agent cards from URLs — used by Agent module."""

    async def resolve_card(self, agent_url: str) -> AgentCardSnapshot | None: ...
    async def supports_push_notifications(self, agent_url: str) -> bool: ...
    async def supports_streaming(self, agent_url: str) -> bool: ...
```

```python
# common/protocols/llm_protocols.py

@runtime_checkable
class LLMProvider(Protocol):
    """Unified LLM invocation — used by Execution, Context & Memory, Agent, Workflow."""

    async def generate(
        self, messages: list[dict], model: str | None = None, **kwargs
    ) -> LLMResponse: ...

    async def generate_structured(
        self, messages: list[dict], schema: dict, model: str | None = None, **kwargs
    ) -> LLMStructuredResponse: ...

    async def embed(self, text: str, model: str | None = None) -> list[float]: ...
    async def embed_batch(self, texts: list[str], model: str | None = None) -> list[list[float]]: ...


@runtime_checkable
class ModelRegistry(Protocol):
    """Model capability lookup — used by business modules for model selection."""

    def get_model(self, logical_name: str) -> ModelInfo: ...
    def supports_capability(self, model: str, capability: str) -> bool: ...
    def list_models(self, capability: str | None = None) -> list[ModelInfo]: ...
```

**LLM DTOs:**

```python
# common/dto/llm.py

class LLMResponse(BaseModel):
    content: str
    model: str
    usage: LLMUsage | None = None
    finish_reason: str | None = None

class LLMStructuredResponse(BaseModel):
    data: dict
    model: str
    usage: LLMUsage | None = None

class LLMUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class ModelInfo(BaseModel):
    model_id: str
    logical_name: str
    provider: str  # "openai" | "gemini" | "bedrock"
    capabilities: list[str]  # "json_schema", "tool_use", "vision", "embedding"
    max_context_tokens: int
    embedding_dimensions: int | None = None
```

### 4.10 DAL Protocols (B4 fix: split by concern)

```python
# common/protocols/dal_protocols.py

@runtime_checkable
class MongoDAL(Protocol):
    """MongoDB operations — used by module Repositories."""

    def collection(self, name: str) -> MongoCollection: ...
    async def connect(self) -> None: ...
    async def close(self) -> None: ...


@runtime_checkable
class MongoCollection(Protocol):
    """Single collection operations."""

    async def find_one(self, query: dict, **kwargs) -> dict | None: ...
    async def find(self, query: dict, **kwargs) -> list[dict]: ...
    async def insert_one(self, document: dict) -> str: ...
    async def insert_many(self, documents: list[dict]) -> list[str]: ...
    async def update_one(self, query: dict, update: dict, **kwargs) -> bool: ...
    async def update_many(self, query: dict, update: dict) -> int: ...
    async def delete_one(self, query: dict) -> bool: ...
    async def delete_many(self, query: dict) -> int: ...
    async def count(self, query: dict) -> int: ...
    async def aggregate(self, pipeline: list[dict]) -> list[dict]: ...
    async def create_index(self, keys: list[tuple], **kwargs) -> str: ...


@runtime_checkable
class RedisKV(Protocol):
    """Redis key-value + atomic ops — general purpose cache/state."""

    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int | None = None) -> None: ...
    async def delete(self, key: str) -> bool: ...
    async def increment(self, key: str, amount: int = 1) -> int: ...
    async def setnx(self, key: str, value: str, ttl: int) -> bool: ...
    async def exists(self, key: str) -> bool: ...
    async def close(self) -> None: ...


@runtime_checkable
class RedisPubSub(Protocol):
    """Redis Pub/Sub — cross-instance event fan-out. Separate connection pool."""

    async def publish(self, channel: str, message: str) -> None: ...
    async def subscribe(self, channel: str) -> AsyncIterator[str]: ...
    async def close(self) -> None: ...


@runtime_checkable
class RedisStreams(Protocol):
    """Redis Streams — durable ordered messaging. Separate connection pool (blocking XREAD)."""

    async def xadd(self, stream: str, fields: dict, maxlen: int | None = None) -> str: ...
    async def xread(self, streams: dict, block: int = 0, count: int = 100) -> list[dict]: ...
    async def close(self) -> None: ...


@runtime_checkable
class VectorDAL(Protocol):
    """Vector search — used by Agent, Context & Memory."""

    async def search(
        self, index: str, vector: list[float], top_k: int, filter: dict | None = None
    ) -> list[VectorSearchResult]: ...

    async def upsert(self, index: str, records: list[VectorRecord]) -> None: ...
    async def delete(self, index: str, ids: list[str]) -> None: ...


@runtime_checkable
class ObjectStorageDAL(Protocol):
    """S3-compatible object storage."""

    async def put(self, key: str, data: bytes, content_type: str = "") -> str: ...
    async def get_presigned_url(self, key: str, ttl: int = 3600) -> str: ...
    async def delete(self, key: str) -> bool: ...


@runtime_checkable
class DistributedLock(Protocol):
    """Short-lived critical section lock (per-room, per-operation). NOT for leader election."""

    async def acquire(self, key: str, owner: str, ttl: int = 60) -> bool: ...
    async def release(self, key: str, owner: str) -> bool: ...
    async def renew(self, key: str, owner: str, ttl: int = 60) -> bool: ...


@runtime_checkable
class LeaderElector(Protocol):
    """Long-lived leader election for background jobs. Separate from DistributedLock (B5 fix)."""

    async def try_acquire(self, job_name: str, ttl: int = 60) -> bool: ...
    async def renew(self, job_name: str, ttl: int = 60) -> bool: ...
    async def release(self, job_name: str) -> None: ...
    async def release_all(self, job_names: list[str]) -> None: ...


@runtime_checkable
class IndexRegistry(Protocol):
    """Centralized index management (B6 fix). Modules register their indexes; 
    Shell ensures all created before serving traffic."""

    def register(self, module_name: str, collection: str, index_spec: list[tuple], **kwargs) -> None: ...
    async def ensure_all(self) -> None: ...
```

---

## 5. Execution Module Internal Architecture

### 5.1 Internal Structure

```
execution/
├── __init__.py
├── facade.py                      # ExecutionFacade: implements ExecutionEngine + HITLManager + AgentEventSink
├── ports.py                       # Internal Protocols (module-private)
│
├── orchestrator/                  # Supervisor loop, debate, queue execution
│   ├── __init__.py
│   ├── supervisor_executor.py     # Adaptive step-at-a-time supervisor loop
│   ├── queue_executor.py          # Simple sequential execution
│   └── debate_dispatcher.py       # Debate mode dispatch
│
├── run/                           # Run lifecycle state machine
│   ├── __init__.py
│   ├── lifecycle.py               # Run creation, state transitions
│   ├── command_handler.py         # Run command processing + record_processing_status
│   ├── projector.py               # Run state projection from events
│   ├── reducer.py                 # Pure function: events → state
│   └── metrics.py                 # Pure helper
│
├── hitl/                          # Human-in-the-loop
│   ├── __init__.py
│   ├── hitl_service.py            # HITL request lifecycle
│   └── hitl_detector.py           # Prompt type detection (pure function)
│
├── dispatch/                      # Agent dispatch routing
│   ├── __init__.py
│   ├── dispatcher.py              # Route to direct/hub transport
│   ├── direct_transport.py        # Direct A2A streaming (uses AgentTransport)
│   └── response_handler.py        # Agent response normalization
│
├── state/                         # In-process state tracking
│   ├── __init__.py
│   ├── task_state_manager.py      # Per-run agent task state
│   └── locking.py                 # Room-level distributed lock wrapper
│
└── repository/
    ├── __init__.py
    ├── run_repo.py
    ├── run_event_repo.py
    └── hitl_repo.py
```

### 5.2 Internal Protocol Seams

```python
# execution/ports.py (module-private, NOT in common/)

class HITLCoordinator(Protocol):
    """Orchestrator → HITL: create/resolve/check interruptions."""

    async def request_input(
        self, room_id: str, user_message_id: str, prompt: str, source: str, **kwargs
    ) -> HITLRequest | None: ...

    async def is_hitl_pending(self, room_id: str, user_message_id: str) -> bool: ...
    async def get_active_hitl(self, user_message_id: str) -> HITLRequest | None: ...


class AgentDispatchPort(Protocol):
    """Orchestrator → Dispatch: send message to agent (direct or hub)."""

    async def dispatch(self, command: DispatchCommand) -> DispatchResult: ...
    async def cancel(self, agent_id: str, task_id: str) -> bool: ...


class RunLifecyclePort(Protocol):
    """Orchestrator → Run: state transitions.
    
    INVARIANT (A1): record_processing_status() MUST be called before
    EventPublisher.emit(ProcessingStatusEvent(...)) — Delivery never calls back here.
    """

    async def create_run(self, room_id: str, agent_id: str | None, **kwargs) -> RunInfo: ...
    async def start_run(self, run_id: str) -> None: ...
    async def complete_run(self, run_id: str, result: dict | None = None) -> None: ...
    async def fail_run(self, run_id: str, error: str) -> None: ...
    async def pause_run(self, run_id: str, reason: str) -> None: ...
    async def cancel_run(self, run_id: str) -> None: ...
    async def emit_event(self, run_id: str, event_type: str, payload: dict) -> None: ...
    async def record_processing_status(
        self, room_id: str, status: str, message_id: str,
        client_request_id: str | None = None, details: dict | None = None
    ) -> dict | None: ...
    async def heal_diverged_runs(self, limit: int = 500) -> int: ...
```

### 5.3 What Does NOT Get a Protocol (Direct Import)

| Component | Why No Protocol |
|-----------|----------------|
| `run/reducer.py` | Pure function, no side effects |
| `run/metrics.py` | Derived value computation |
| `hitl/hitl_detector.py` | Pure heuristic (pattern matching) |
| `state/task_state_manager.py` | In-memory bookkeeping, only used by orchestrator |
| `dispatch/response_handler.py` | Stateless transformer, tightly coupled to dispatch |

### 5.4 Processing Status Call Flow (A1 Resolution)

```
Orchestrator: agent response arrives
    │
    ├─ 1. run_lifecycle.record_processing_status(room_id, "completed", message_id, ...)
    │      → writes to runs + run_events collections
    │      → returns last_run_event_payload (for run_event SSE)
    │
    ├─ 2. event_publisher.emit(ProcessingStatusEvent(room_id, message_id, "completed", ...))
    │      → Delivery translates to SSE frame → delivers to clients
    │
    └─ 3. (if run_event_sse_enabled) event_publisher.emit(RunEventNotification(...))
           → Delivery translates to SSE frame → delivers to clients

Delivery NEVER calls run_command_handler. It is a pure pipe.
```

---

## 6. Workflow Module Internal Architecture

```
workflow/
├── __init__.py
├── facade.py                      # WorkflowFacade: implements WorkflowEngine
├── service/
│   ├── __init__.py
│   ├── decomposer.py             # Task → MetaTasks via LLM
│   ├── assigner.py                # MetaTask → Agent assignment
│   ├── executor.py                # Workflow step execution (calls ExecutionEngine per meta-task)
│   └── summarizer.py             # Result summarization
├── repository/
│   ├── __init__.py
│   ├── task_repo.py               # base_tasks / meta_tasks persistence
│   └── task_session_repo.py       # task_sessions persistence
└── models.py
```

**Workflow vs Execution boundary:**

| Aspect | Execution | Workflow |
|--------|-----------|---------|
| Storage | `runs`, `run_events`, `room_agent_messages` | `base_tasks`, `meta_tasks`, `task_sessions` |
| State machine | RunState (queued→processing→completed/failed) | MetaTask ordering + dependency resolution |
| HTTP response | 200 (ack) + SSE streaming | 200/207 (partial-success, synchronous) |
| Orchestration | Supervisor loop (adaptive steps) | Sequential/parallel meta-task dispatch |
| Memory | Room memory via ContextAssembler | Chat contexts via ChatContextManager |

---

## 7. Application Shell & Lifespan

### 7.1 Lifespan Sequence (A4, A5 fixes)

```python
# main.py lifespan (pseudocode showing all phases)

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    container = await create_container(settings)
    app.state.container = container

    # === Phase 1: Infrastructure connections ===
    # (handled inside create_container: mongo.connect, redis.connect, pinecone.connect)

    # === Phase 1.5: Indexes (per-module, parallel-safe) ===
    await container.dal.index_registry.ensure_all()

    # === Phase 1.6: Heal diverged runs (A5 fix) ===
    # MUST happen after indexes, BEFORE serving traffic.
    # Event sourcing integrity: if event committed but head update lost during crash,
    # reconstruct run head from events.
    healed = await container.execution.execution_engine.heal_diverged_runs(limit=500)
    if healed:
        logger.info(f"Healed {healed} diverged runs on startup")

    # === Phase 1.7: Multi-worker guard (N5 fix: check ALL Redis subsystems) ===
    if is_gunicorn():
        missing = [name for name, conn in [
            ("redis_kv", container.dal.redis_kv),
            ("redis_pubsub", container.dal.redis_pubsub),
            ("redis_streams", container.dal.redis_streams),
        ] if conn is None]
        if missing:
            raise RuntimeError(f"Multi-worker requires all Redis subsystems: missing {missing}")

    # === Phase 2: Background services (A4 fix: conditional) ===
    scheduler = JobScheduler(leader_elector=container.dal.leader_elector, ...)
    scheduler.register(AgentHealthJob(...))
    scheduler.register(CompactionSweepJob(...))
    scheduler.register(OrphanedUploadJob(...))

    # Conditional: A2A long-running tasks subsystem
    if settings.webhook_signing_key:
        scheduler.register(StaleTaskCheckerJob(...))
        logger.info("A2A long-running tasks support initialized")
    else:
        logger.warning("WEBHOOK_SIGNING_KEY not set — A2A long-running tasks disabled")

    await scheduler.start()

    # === Phase 2.5: SSE infrastructure ===
    # Cancellation watcher: runs in EVERY worker (A6), NOT leader-elected
    await container.delivery.sse_transport.start_cancellation_watcher()
    # Event broker (Redis Pub/Sub): runs in every worker
    await container.delivery.event_publisher.start()

    # === Phase 3: HubRuntimeBridge background ===
    await container.hub.hub_management.start_heartbeat_monitor()

    # === Serve ===
    yield

    # === Graceful shutdown (N10: cancel in-flight orchestration) ===
    container.delivery.sse_transport.set_draining(True)
    # Cancel tracked background orchestration tasks; each cancelled run → RunState.CANCELED
    await container.execution.execution_engine.cancel_inflight_tasks()
    await asyncio.sleep(settings.shutdown_drain_seconds)
    await scheduler.stop()
    await container.hub.hub_management.stop()
    await container.delivery.event_publisher.stop()
    await container.dal.mongo.close()
    await container.dal.redis_kv.close()
    await container.dal.redis_pubsub.close()
    await container.dal.redis_streams.close()
```

### 7.2 Sub-Container Design

```python
# container.py

@dataclass(frozen=True)
class DALContainer:
    mongo: MongoDAL
    redis_kv: RedisKV | None          # None in single-worker no-redis mode
    redis_pubsub: RedisPubSub | None  # Separate pool (B4 fix)
    redis_streams: RedisStreams | None # Separate pool for blocking XREAD (B4 fix)
    vector: VectorDAL
    object_storage: ObjectStorageDAL
    distributed_lock: DistributedLock  # Short-lived critical sections
    leader_elector: LeaderElector      # Long-lived job leader (B5 fix)
    index_registry: IndexRegistry      # Centralized index management (B6 fix)


@dataclass(frozen=True)
class AdapterContainer:
    agent_transport: AgentTransport
    agent_card_resolver: AgentCardResolver
    llm_provider: LLMProvider
    model_registry: ModelRegistry


@dataclass(frozen=True)
class AgentDeps:
    agent_registry: AgentRegistry
    agent_matcher: AgentMatcher
    agent_management: AgentManagement
    agent_registry_writer: AgentRegistryWriter


@dataclass(frozen=True)
class RoomDeps:
    room_registry: RoomRegistry
    room_management: RoomManagement
    room_message_store: RoomMessageStore
    room_history_reader: RoomHistoryReader
    room_ownership_reader: RoomOwnershipReader


@dataclass(frozen=True)
class ContextMemoryDeps:
    context_assembler: ContextAssembler
    memory_manager: MemoryManager
    memory_projector: MemoryProjector
    chat_context_manager: ChatContextManager


@dataclass(frozen=True)
class ExecutionDeps:
    execution_engine: ExecutionEngine
    hitl_manager: HITLManager
    agent_event_sink: AgentEventSink


@dataclass(frozen=True)
class WorkflowDeps:
    workflow_engine: WorkflowEngine


@dataclass(frozen=True)
class DeliveryDeps:
    event_publisher: EventPublisher
    sse_transport: SSETransport


@dataclass(frozen=True)
class HubDeps:
    hub_dispatch: HubDispatchPort
    hub_management: HubManagement
    hub_liveness: HubLivenessReader


@dataclass(frozen=True)
class PlatformDeps:
    gateway_service: GatewayService
    rate_limiter: RateLimiter
    file_storage: FileStorage


@dataclass(frozen=True)
class AppContainer:
    """Root container — only used by Application Shell for wiring."""
    dal: DALContainer
    adapters: AdapterContainer
    agent: AgentDeps
    room: RoomDeps
    context_memory: ContextMemoryDeps
    execution: ExecutionDeps
    workflow: WorkflowDeps
    delivery: DeliveryDeps
    hub: HubDeps
    platform: PlatformDeps
```

### 7.3 Circular Dependency Resolution: Execution ⇆ Hub (N1 fix)

**Problem:** `ExecutionFacade` needs `HubDispatchPort` (send to hub), and `HubFacade` needs `AgentEventSink` (hub publish → resume execution). This is a real bi-directional dependency from current `relay_service.publish_from_hub → RoomMessageCenter.resume_queue_from_continuation`.

**Solution:** Hub does NOT hold a direct reference to `AgentEventSink`. Instead, Hub publishes through `EventPublisher.emit(HubAgentEvent(...))`, and Execution subscribes to that event type internally. This breaks the construction-time cycle.

```python
# container.py — create_container() wiring order

async def create_container(settings: Settings) -> AppContainer:
    # --- Phase A: Leaf layers (no cycles possible) ---
    dal = await _create_dal(settings)
    adapters = _create_adapters(settings, dal)

    # --- Phase B: Delivery (depends on DAL only) ---
    delivery = _create_delivery(dal)

    # --- Phase C: Business modules (Agent, Room, C&M — no cycles among these) ---
    agent = _create_agent(dal, adapters)
    room = _create_room(dal)
    context_memory = _create_context_memory(dal, adapters, room.room_history_reader)

    # --- Phase D: Hub (depends on Agent, Delivery — NOT Execution) ---
    # Hub publishes agent events via EventPublisher (delivery.event_publisher),
    # NOT via AgentEventSink. This breaks Execution ⇆ Hub cycle.
    hub = _create_hub(dal, adapters, agent.agent_registry_writer, delivery.event_publisher)

    # --- Phase E: Execution (depends on everything above, subscribes to HubAgentEvent) ---
    execution = _create_execution(
        dal, adapters, agent.agent_registry, room.room_message_store,
        context_memory.context_assembler, delivery.event_publisher,
        hub.hub_dispatch,  # Execution → Hub (one-way, no cycle)
    )
    # Execution registers internal listener for HubAgentEvent on the event bus
    delivery.event_publisher.register_internal_handler(
        "hub_agent_event", execution.agent_event_sink.handle_agent_event
    )

    # --- Phase F: Workflow (depends on Execution, Agent, C&M) ---
    workflow = _create_workflow(
        dal, adapters, execution.execution_engine,
        agent.agent_matcher, context_memory.chat_context_manager,
    )

    # --- Phase G: Platform (depends on Agent, Execution, Delivery) ---
    platform = _create_platform(dal, adapters, agent.agent_registry, execution.execution_engine)

    return AppContainer(
        dal=dal, adapters=adapters, agent=agent, room=room,
        context_memory=context_memory, execution=execution,
        workflow=workflow, delivery=delivery, hub=hub, platform=platform,
    )
```

**Key insight:** `HubFacade.publish_from_hub()` calls `event_publisher.emit(HubAgentEvent(...))` instead of directly calling `AgentEventSink`. Execution subscribes to `HubAgentEvent` via an internal handler registered post-construction. This is consistent with the existing event-driven pattern and eliminates the bidirectional compile-time dependency.

**EventPublisher extension for internal handlers:**

```python
# delivery/event_publisher.py (implementation detail)

class EventPublisherImpl:
    def __init__(self, ...):
        self._internal_handlers: dict[str, list[Callable]] = {}

    def register_internal_handler(self, event_type: str, handler: Callable) -> None:
        """Register module-internal event handler. Called during container assembly."""
        self._internal_handlers.setdefault(event_type, []).append(handler)

    async def emit(self, event: DomainEvent) -> None:
        # 1. Deliver to SSE clients (frontend)
        await self._deliver_to_sse(event)
        # 2. Fan-out to other workers (Redis Pub/Sub)
        await self._fanout_cross_instance(event)
        # 3. Dispatch to internal handlers (same worker)
        for handler in self._internal_handlers.get(event.event_type, []):
            await handler(event)
```

---

## 8. Configuration Management (A8 fix)

### 8.1 Config Unification Strategy

**Current problem:** 30+ `os.getenv()` calls scattered across services, some with defaults that shadow Settings fields. Settings model has fields with `""` defaults that silently produce empty strings instead of failing.

**Resolution:**

```python
# common/config/settings.py

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Required (fail-fast if missing) ---
    mongodb_url: str
    openai_api_key: str
    clerk_secret_key: str
    pinecone_api_key: str

    # --- Optional with safe defaults ---
    app_env: str = "development"
    redis_url: str | None = None  # None = single-instance mode, no Redis features
    google_api_key: str | None = None
    webhook_signing_key: str | None = None  # None = A2A long-running tasks disabled
    s3_bucket_name: str | None = None  # None = file uploads disabled

    # --- LLM routing (logical names) ---
    lead_ai_model: str = "gpt-4.1-mini"
    classifier_ai_model: str = "gpt-4.1-mini"
    embedding_model: str = "text-embedding-3-small"
    bedrock_supervisor_model: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    use_bedrock_supervisor: bool = False

    # --- Feature Flags (explicit, not derived from key presence) ---
    feature_run_dual_write: bool = True
    feature_run_event_sse: bool = True
    feature_memory_search: bool = True
    feature_a2a_long_running: bool = False  # auto-set True if webhook_signing_key present

    # --- Tuning (all values from current os.getenv defaults) ---
    match_vector_weight: float = 0.7
    match_gap_threshold: float = 0.15
    supervisor_max_steps: int = 8
    compaction_concurrency: int = 3
    run_watchdog_stale_minutes: int = 30
    # ... (full list from current env vars)

    @model_validator(mode="after")
    def derive_feature_flags(self) -> "Settings":
        if self.webhook_signing_key:
            object.__setattr__(self, "feature_a2a_long_running", True)
        return self
```

**Enforcement:**
- Phase 0b adds AST scan rule: `no os.getenv() outside common/config/settings.py`
- All existing `os.getenv` calls migrated to Settings fields with matching env var names
- Unit test: `test_settings_loads_from_env()` with all required vars

### 8.2 Module-Scoped Config Access

Modules receive only their relevant settings section (not the full Settings object):

```python
# Application Shell extracts per-module configs during container assembly
agent_config = AgentConfig(
    health_check_enabled=settings.agent_health_check_enabled,
    cloud_health_check_timeout=settings.cloud_health_check_timeout,
    ...
)
```

---

## 9. Migration Strategy

### 9.1 Three-Layer Defense

#### Layer 1: Contract Freeze

| Contract Element | Frozen As |
|-----------------|-----------|
| Request body schema | Shared Pydantic request model |
| Response body schema | Shared Pydantic response model |
| Status codes | Enumerated per endpoint (including 207 for workflow) |
| Error shapes | HybroError → HTTP mapping table |
| Auth requirements | Clerk JWT / API key per route |
| Side effects | Documented per endpoint: message created? SSE sent? run started? |
| null vs missing fields | Explicit in golden fixtures |
| datetime format | ISO 8601 UTC, explicit assertion |

#### Layer 2: Golden Integration Tests

For each endpoint migrated:

1. **Write test against OLD implementation first** (captures current behavior)
2. **Assert full response shape** including null/missing distinction:
   ```python
   async def test_send_message_golden(client, seeded_room):
       response = await client.post("/api/v1/roomCenter/sendMessage", json=FIXTURE)
       assert response.status_code == 200
       body = response.json()
       # Exact contract assertion
       assert body["success"] is True
       assert body["room_id"] == seeded_room.room_id
       assert body["message_id"] is not None
       assert body["dispatch_root_message_id"] is not None
       assert "message" in body
       assert body["message"]["sender_id"] == USER_ID
       # Side effect assertions
       msg_doc = await db.room_user_messages.find_one({"message_id": body["message_id"]})
       assert msg_doc is not None
   ```
3. **Migrate endpoint**
4. **Same test passes unchanged**

#### Layer 3: Targeted Shadow Mode (High-Risk Only)

| Endpoint | Risk Reason | Shadow Strategy |
|----------|------------|-----------------|
| `POST /roomCenter/sendMessage` | Core path; triggers run + SSE + dispatch | Shadow read path only (skip dispatch) |
| `POST /hitl/resolve` | Resumes supervisor; state machine | Shadow state check only |
| `POST /orchestrationCenter/runWorkflow` | Multi-step; HTTP 207 | Shadow decompose only |
| `GET /sse/subscribe` | Streaming; event ordering | Dual-emit to shadow connection |
| `POST /relay/hub/{id}/publish` | Hub → Cloud; multi-module | Shadow validation only |

Rules:
- Return OLD path result to client always
- New path runs parallel (read-only or isolated)
- Log diff if responses diverge
- No dual writes — shadow skips side effects
- Remove after 1 week stable with zero drift

### 9.2 Migration Phases (C1 fix: realistic timeline)

> **Total estimated: 18-22 weeks** (vs original 9 weeks)  
> Strategy: "Facade wrap first, internal rewrite second" — each phase has a wrap sub-phase (fast, low risk) and a rewrite sub-phase (slower, needs golden tests).

#### Phase 0a: Common Foundation (1 week)

- `common/protocols/` — all Protocol definitions (from this doc)
- `common/dto/` — all shared DTOs with complete field definitions
- `common/errors/`, `common/auth/`, `common/utils/`
- `container.py` skeleton with sub-container dataclass types
- Import linter configuration (§11)

**Gate:** Existing code compiles. All tests pass. No behavior change.

#### Phase 0b: Config Unification (1 week) — A8 fix

- Migrate all `os.getenv()` to Settings fields
- Add model_validator for derived feature flags
- Add AST scan CI check: "no os.getenv outside common/config/"
- Add `test_settings_loads_from_env()` unit test

**Gate:** `grep -r "os.getenv" --include="*.py" | grep -v common/config/ | wc -l` == 0

#### Phase 0c: Frontend Coordination Checklist (0.5 week)

- Document all frontend-coupled feature flags: `FEATURE_RUN_EVENT_SSE`, `NEXT_PUBLIC_FEATURE_RUN_EVENT_SSE`
- Create lockstep deploy checklist for SSE schema changes
- Identify frontend smoke tests that must pass per migration phase
- No code changes — documentation and process only

**Gate:** Checklist exists and reviewed by frontend team.

#### Phase 1: DAL (2 weeks)

- `dal/mongo/` wrapping existing Motor client → `MongoDAL` + `MongoCollection`
- `dal/redis/` split into `RedisKV` + `RedisPubSub` + `RedisStreams` (three pools)
- `dal/pinecone/` → `VectorDAL`
- `dal/s3/` → `ObjectStorageDAL`
- `dal/redis/lock.py` → `DistributedLock` (short-lived) + `LeaderElector` (long-lived)
- `IndexRegistry` implementation

**Complexity note:** mongodb.py is ~101KB with 200+ methods. This phase wraps the Motor client in MongoDAL/MongoCollection — it does NOT refactor queries. Individual modules will own their queries later.

**Gate:** Container instantiates full DAL. Old singletons delegate to new DAL. All tests pass.

#### Phase 2: Adapter Layer (2.5 weeks)

- `a2a_adapter/` with all translators (InternalAgentMessage ↔ a2a.Message, etc.)
- `llm_gateway/` with OpenAI/Gemini/Bedrock providers + ModelRegistry + retry/fallback
- Verify: no business module imports `a2a`, `openai`, `google.genai`, `aioboto3`

**Complexity note:** `openai_service.py` has 18+ distinct LLM call points each with different prompt/schema/model selection. The gateway wraps the *calling convention*, not the prompts.

**Gate:** All LLM and A2A calls route through adapters. Import linter passes for SDK confinement rules.

#### Phase 3: Agent Module (2 weeks)

- `agent/facade.py` implementing AgentRegistry, AgentMatcher, AgentManagement, AgentRegistryWriter
- Golden tests for: register, delete, list, match, discovery, health
- Includes `is_directly_callable()` and `HubLivenessReader` integration

**Gate:** Agent endpoints return identical responses. Golden tests pass.

#### Phase 4: Room Module (2 weeks)

- `room/facade.py` implementing all Room Protocols
- Includes `MembershipSeed` resolution logic (saved_group, all_current_agents)
- Includes `RoomHistoryReader` for Context & Memory
- Golden tests for: create (all 3 seed modes), delete, sendMessage user-persist, membership

**Gate:** Room endpoints return identical responses. `dispatch_root_message_id` present.

#### Phase 5: Context & Memory (1.5 weeks)

- `context_memory/facade.py` implementing ContextAssembler, MemoryManager, ChatContextManager, MemoryProjector
- Token budgeting logic preserved exactly
- Compaction logic preserved
- Domain event listener for `MessageCommitted`

**Gate:** `assemble_context()` produces identical token-budget results for test fixtures.

#### Phase 6+7: Delivery + Execution (4 weeks, partially parallel — N9 fix)

> **Why interleaved:** Delivery cannot be pure until callers stop using `send_processing_status()` 
> (which embeds `record_processing_status`). Callers are in Execution. These phases must overlap:

**Phase 7a (week 1-2): Execution caller migration — "record-then-emit"**
- Modify all Execution callers to explicitly call `record_processing_status()` THEN `sse_manager.send_*()` (separating the record from the send)
- This works against the OLD sse_manager (which now has a redundant no-op record inside send)
- Golden tests written for sendMessage, hitl/resolve against OLD implementation

**Phase 6 (week 2-3): Delivery module extraction**
- `delivery/facade.py` implementing EventPublisher, SSETransport
- DomainEvent → SSE frame translator
- Cross-instance pub/sub (Redis)
- Cancellation watcher (every worker, change stream)
- Deduplication (TTLCache per terminal status)
- `register_internal_handler()` + `emit_internal()` for internal events
- **No business logic** — verify by asserting no business module imports in delivery/
- At this point, old `sse_manager.send_processing_status()` record call is dead code (callers already record separately)

**Phase 7b (week 3-4): Execution internal rewrite**
- `execution/facade.py` with full orchestrator, HITL, dispatch
- Internal Protocol seams: HITLCoordinator, AgentDispatchPort, RunLifecyclePort
- Callers now emit via new `EventPublisher.emit(ProcessingStatusEvent(...))`
- `_heal_diverged_runs_on_startup` preserved
- Room-level locking preserved
- Shadow mode on high-risk endpoints (sendMessage, hitl/resolve)

**Gate:** Full message flow end-to-end. HITL pause/resume. Shadow mode zero drift. No `run_command_handler` calls from Delivery.

#### Phase 8: Workflow + HubRuntimeBridge (2.5 weeks)

- `workflow/facade.py` implementing WorkflowEngine
- HTTP 207 partial-success preserved
- `hub_runtime_bridge/facade.py` implementing HubDispatchPort, HubManagement, HubLivenessReader
- Hub → Agent sync via AgentRegistryWriter
- Hub → Execution via AgentEventSink

**Gate:** Workflow decompose/assign/run returns 207. Hub relay works. Agent sync via Protocol.

#### Phase 9: Platform + API Layer + Cleanup (2 weeks)

- `platform/facade.py` implementing GatewayService, RateLimiter, FileStorage
- `api/` thin adapter layer (all routes extracted)
- Remove old `modules/`, `services/` directories
- Remove singleton imports
- Full import linter enforcement
- Remove migration adapters

**Gate:** CI green. No old code. Import linter passes all contracts.

### 9.3 Migration Adapter Pattern (C3 fix)

During transition, old singletons delegate to new facades:

```python
# services/agent_service.py (during Phase 3)

class AgentService:
    """Legacy wrapper — delegates to new AgentFacade.
    
    NOTE (C3 fix): This is NOT an import-time singleton anymore.
    Container calls bind_facade() during startup.
    Before bind_facade(): raises RuntimeError (fail-fast, not silent legacy path).
    """

    def __init__(self):
        self._facade: AgentFacade | None = None
        self._bound = False

    def bind_facade(self, facade: AgentFacade):
        self._facade = facade
        self._bound = True

    async def get_agent(self, agent_id: str):
        if not self._bound:
            raise RuntimeError("AgentService.bind_facade() not called — startup incomplete")
        return dto_to_legacy_agent(await self._facade.get_agent(agent_id))
```

**Enforcement:** Add lint rule during transition: "no module-level service instantiation after Phase 3 completes". All services become container-managed.

---

## 10. Feature Mapping

| Feature | Current Location | Target Module | Target Component |
|---------|-----------------|---------------|-----------------|
| **Agent lifecycle** | | | |
| Agent registration | `services/agent_service.py` | Agent | `service/agent_crud.py` |
| Agent health checking | `services/agent_health_service.py` | Agent + Jobs | `service/agent_health.py` |
| Agent matching (vector) | `services/agent_selection_service.py` | Agent | `service/agent_matching.py` |
| Agent groups | `api/agent_group.py` | Agent | `repository/agent_group_repo.py` |
| Agent card fetching | `services/a2a_service.py` | A2A Adapter | `card_resolver.py` |
| Agent inspection | `modules/InspectionCenter.py` | Agent | `service/agent_crud.py` |
| Discovery API (no visibility filter) | `services/discovery_service.py` | Agent | `facade.match_agents(respect_visibility=False)` |
| Listings (owner-scoped, masked) | `services/agent_service.py` | Agent | `facade.match_agents(respect_visibility=True)` |
| is_directly_callable (hub 502) | implicit in gateway_service | Agent | `facade.is_directly_callable()` |
| Hub agent enrichment (is_hub_online) | mongodb._enrich_hub_fields | Agent + HubRuntimeBridge | Agent calls `HubLivenessReader` |
| **Room & messages** | | | |
| Room CRUD | `modules/RoomCenter.py` | Room | `service/room_crud.py` |
| Room membership (3 seed modes) | `services/room_services.py` | Room | `service/room_membership.py` (handles MembershipSeed) |
| User message persistence | `services/room_services.py` | Room | `service/message_service.py` |
| Agent message persistence | `services/room_services.py` | Room | `service/message_service.py` |
| Message graph | `services/room_services.py` | Room | `repository/message_repo.py` |
| **Context & Memory** | | | |
| Context assembly | `services/context_assembly_service.py` | Context & Memory | `service/context_assembly.py` |
| Memory compaction | `services/compaction_service.py` | Context & Memory | `service/compaction.py` |
| Memory search | `services/memory_search_service.py` | Context & Memory | `service/memory_search.py` |
| User memories | `services/memory_service.py` | Context & Memory | `service/user_memory.py` |
| Chat contexts (workflow) | `services/memory_service.py` | Context & Memory | via `ChatContextManager` Protocol |
| **Execution** | | | |
| Message dispatch | `modules/RoomMessageCenter.py` | Execution | `orchestrator/` + `dispatch/` |
| Supervisor loop | `modules/SupervisorExecutor.py` | Execution | `orchestrator/supervisor_executor.py` |
| Debate mode | `modules/debate_dispatcher.py` | Execution | `orchestrator/debate_dispatcher.py` |
| Queue execution | `modules/QueueExecutor.py` | Execution | `orchestrator/queue_executor.py` |
| Run lifecycle | `services/run_lifecycle_service.py` | Execution | `run/lifecycle.py` |
| Run events | `services/run_command_handler.py` | Execution | `run/command_handler.py` |
| record_processing_status | `services/sse_services.py` (WRONG!) | Execution | `run/command_handler.py` (MOVED!) |
| HITL requests | `services/hitl_service.py` | Execution | `hitl/hitl_service.py` |
| Room-level locking | `modules/RoomMessageCenter.py` | Execution | `state/locking.py` |
| heal_diverged_runs_on_startup | `main.py` | Execution | `run/lifecycle.py` (exposed via Protocol) |
| A2A long-running tasks | `api/a2a_tasks.py` | Execution (API) | `api/a2a_task_routes.py` |
| Webhooks | `api/webhooks.py` | Execution (API) | `api/webhook_routes.py` |
| **Workflow** (separate from Execution) | | | |
| Task decomposition | `modules/WorkflowCenter.py` | Workflow | `service/decomposer.py` |
| Task assignment | `modules/WorkflowCenter.py` | Workflow | `service/assigner.py` |
| Workflow execution (207) | `modules/WorkflowCenter.py` | Workflow | `service/executor.py` |
| Task CRUD | `services/task_service.py` | Workflow | `repository/task_repo.py` |
| Workflow summarization | `modules/WorkflowCenter.py` | Workflow | `service/summarizer.py` |
| **Delivery** | | | |
| SSE broadcasting | `services/sse_services.py` | Delivery | `sse/manager.py` |
| SSE connections | `services/sse_services.py` | Delivery | `sse/connection.py` |
| Event dedup | `services/sse_services.py` | Delivery | `sse/deduplication.py` |
| Cross-instance pub/sub | `infrastructure/brokers/redis_broker.py` | Delivery | `event_bus/cross_instance.py` |
| Cancellation watcher | `services/sse_services.py` | Delivery | `sse/cancellation_watcher.py` |
| Domain→SSE translation | `services/sse_services.py` | Delivery | `translator.py` |
| **Platform** | | | |
| Gateway API | `services/gateway_service.py` | Platform | `gateway/gateway_service.py` |
| Rate limiting | `services/*_rate_limit_service.py` | Platform | `rate_limit/rate_limiter.py` |
| File uploads | `services/file_upload_service.py` | Platform | `files/upload_service.py` |
| Content storage | `services/content_storage_service.py` | Platform | `files/content_storage.py` |
| **HubRuntimeBridge** | | | |
| Hub relay | `services/relay_service.py` | HubRuntimeBridge | `service/hub_relay.py` |
| Hub liveness | `services/relay_service.py` | HubRuntimeBridge | `service/hub_liveness.py` |
| Hub connection | `api/hub.py` | HubRuntimeBridge | `service/hub_connection.py` |
| Hub publish intake | `services/relay_service.py` | HubRuntimeBridge | `service/hub_publish.py` |
| Offline queue | `services/relay_service.py` | HubRuntimeBridge | `transport/offline_queue.py` |
| Redis Streams relay | `infrastructure/relay_streams.py` | HubRuntimeBridge | `transport/relay_transport.py` |
| Hub agent sync | `services/relay_service.py` | HubRuntimeBridge → Agent | via `AgentRegistryWriter` |
| **LLM Gateway** | | | |
| OpenAI calls | `services/openai_service.py` | LLM Gateway | `providers/openai_provider.py` |
| Gemini calls | `services/gemini_service.py` | LLM Gateway | `providers/gemini_provider.py` |
| Bedrock calls | `services/bedrock_service.py` | LLM Gateway | `providers/bedrock_provider.py` |
| Embedding generation | `services/openai_service.py` | LLM Gateway | `gateway.py` (embed) |
| **A2A Adapter** | | | |
| A2A message send/stream | `services/a2a_service.py` | A2A Adapter | `transport.py` |
| A2A card resolution | `common/client/card_resolver.py` | A2A Adapter | `card_resolver.py` |
| A2A type mapping | scattered | A2A Adapter | `translators/` |
| Push notification auth | `common/utils/push_notification_auth.py` | A2A Adapter | `push_notification.py` |
| **DAL** | | | |
| MongoDB client | `database/mongodb.py` | DAL | `mongo/client.py` |
| Redis KV | `infrastructure/redis_service.py` | DAL | `redis/kv.py` |
| Redis Pub/Sub | `infrastructure/brokers/redis_broker.py` | DAL | `redis/pubsub.py` |
| Redis Streams | `infrastructure/relay_streams.py` | DAL | `redis/streams.py` |
| Pinecone | `database/pinecone_db.py` | DAL | `pinecone/client.py` |
| S3 | `services/s3_service.py` | DAL | `s3/client.py` |
| Leader election | `infrastructure/leader_election.py` | DAL | `redis/leader.py` |
| **Jobs** | | | |
| Health check | `jobs/agent_health_service.py` | Jobs | `agent_health_job.py` |
| Compaction sweep | `jobs/compaction_sweep.py` | Jobs | `compaction_sweep_job.py` |
| Stale task checker | `jobs/stale_task_checker.py` | Jobs | `stale_task_checker_job.py` (conditional) |
| Orphaned upload | `jobs/cleanup_orphaned_uploads.py` | Jobs | `orphaned_upload_job.py` |

---

## 11. Invariants

1. **No module imports another module's internal code** — only Protocols and DTOs from `common/`
2. **`container.py` is the ONLY place that imports concrete implementations** across module boundaries
3. **Every cross-module method is async** — no blocking I/O
4. **DTOs are immutable** (frozen Pydantic models)
5. **EventPublisher.emit() is fire-and-forget** — emitter does not wait for subscriber
6. **Business side effects MUST complete before EventPublisher.emit()** — Delivery never calls back into business modules (A1)
7. **Room execution lock must be held** before supervisor/queue execution starts
8. **Settings are read-only after container creation**
9. **Background jobs run under LeaderElector** — exactly once per job
10. **Cancellation watcher runs in EVERY worker** — not leader-elected (A6)
11. **a2a-sdk types never appear outside `a2a_adapter/`**
12. **LLM provider SDK types never appear outside `llm_gateway/`**
13. **Room writes canonical messages; Context & Memory projects derived artifacts**
14. **HubRuntimeBridge never writes to agents_collection directly** — calls AgentRegistryWriter
15. **Execution internal Protocols** (HITLCoordinator, AgentDispatchPort, RunLifecyclePort) defined in `execution/ports.py`, not `common/`
16. **Run head must be healed from events on startup** before serving traffic (A5)
17. **Graceful shutdown must set_draining(True) and sleep shutdown_drain_seconds** before closing connections
18. **Startup failure must NOT set_draining** — prevents singleton state poisoning on partial init
19. **WorkflowEngine operates on base_tasks/meta_tasks** — never touches runs/run_events (B2)
20. **Discovery API (X-API-Key) does NOT filter by visibility** — any indexed agent discoverable (B3)
21. **Graceful shutdown must cancel in-flight background orchestration tasks** — `asyncio.create_task`-spawned orchestrations must be tracked and cancelled during shutdown; cancelled runs transition to `RunState.CANCELED` (N10)
22. **`heal_diverged_runs(limit=500)` is best-effort at startup** — runs beyond the limit are caught by `StaleTaskCheckerJob._fail_stale_runs` (RUN_WATCHDOG_STALE_MINUTES=90) as a fallback (N10)

---

## 12. Import Enforcement

```toml
# pyproject.toml
[tool.import-linter]
root_packages = ["common", "dal", "a2a_adapter", "llm_gateway", "agent", "room", "context_memory", "execution", "workflow", "delivery", "platform", "hub_runtime_bridge", "jobs", "api"]

[[tool.import-linter.contracts]]
name = "Common has no dependencies"
type = "forbidden"
source_modules = ["common"]
forbidden_modules = ["dal", "a2a_adapter", "llm_gateway", "agent", "room", "context_memory", "execution", "workflow", "delivery", "platform", "hub_runtime_bridge", "jobs", "api"]

[[tool.import-linter.contracts]]
name = "DAL depends only on Common"
type = "layers"
layers = ["dal", "common"]

[[tool.import-linter.contracts]]
name = "Adapters depend only on DAL and Common"
type = "forbidden"
source_modules = ["a2a_adapter", "llm_gateway"]
forbidden_modules = ["agent", "room", "context_memory", "execution", "workflow", "delivery", "platform", "hub_runtime_bridge", "jobs", "api"]

[[tool.import-linter.contracts]]
name = "Business modules never import each other's internals"
type = "independence"
modules = ["agent", "room", "context_memory", "execution", "workflow", "delivery", "platform", "hub_runtime_bridge"]
ignore_imports = ["common", "dal", "a2a_adapter", "llm_gateway"]

[[tool.import-linter.contracts]]
name = "No module imports container"
type = "forbidden"
source_modules = ["common", "dal", "a2a_adapter", "llm_gateway", "agent", "room", "context_memory", "execution", "workflow", "delivery", "platform", "hub_runtime_bridge", "jobs"]
forbidden_modules = ["container"]

[[tool.import-linter.contracts]]
name = "A2A SDK confined to adapter"
type = "forbidden"
source_modules = ["agent", "room", "context_memory", "execution", "workflow", "delivery", "platform", "hub_runtime_bridge", "jobs", "api"]
forbidden_modules = ["a2a"]

[[tool.import-linter.contracts]]
name = "LLM SDKs confined to gateway"
type = "forbidden"
source_modules = ["agent", "room", "context_memory", "execution", "workflow", "delivery", "platform", "hub_runtime_bridge", "jobs", "api"]
forbidden_modules = ["openai", "google.genai", "aioboto3"]

[[tool.import-linter.contracts]]
name = "No os.getenv outside config"
type = "forbidden"
source_modules = ["agent", "room", "context_memory", "execution", "workflow", "delivery", "platform", "hub_runtime_bridge", "jobs", "api", "dal", "a2a_adapter", "llm_gateway"]
forbidden_modules = []
# NOTE: This contract is supplemented by AST scan (import-linter can't check function calls)
```

**Additional AST scan (CI):**
```python
# scripts/check_no_getenv.py
# Fails CI if os.getenv() found outside common/config/settings.py
```

---

## 13. Testing Strategy

### 13.1 Unit Tests (Per Module, Mocked Protocols)

```python
# tests/unit/execution/test_supervisor_executor.py

@pytest.fixture
def mock_deps():
    return {
        "agent_registry": AsyncMock(spec=AgentRegistry),
        "context_assembler": AsyncMock(spec=ContextAssembler),
        "event_publisher": AsyncMock(spec=EventPublisher),
        "agent_transport": AsyncMock(spec=AgentTransport),
        "hitl_coordinator": AsyncMock(spec=HITLCoordinator),
        "run_lifecycle": AsyncMock(spec=RunLifecyclePort),
        "dispatch": AsyncMock(spec=AgentDispatchPort),
    }

async def test_supervisor_pauses_on_hitl(mock_deps):
    mock_deps["hitl_coordinator"].request_input.return_value = HITLRequest(...)
    executor = SupervisorExecutor(**mock_deps)
    result = await executor.execute_step(...)
    assert result.state == RunState.AWAITING_INPUT
    # Verify: record_processing_status called BEFORE emit (A1 invariant)
    mock_deps["run_lifecycle"].record_processing_status.assert_called_once()
    mock_deps["event_publisher"].emit.assert_called_once()
```

### 13.2 Integration Tests

```python
# tests/integration/conftest.py

@pytest.fixture
async def container():
    """Full container with real MongoDB (testcontainers) + in-memory Redis."""
    # NOTE (D item): mongomock doesn't support async Motor.
    # Use testcontainers-python for real MongoDB in CI.
    async with MongoContainer() as mongo:
        settings = Settings(mongodb_url=mongo.get_connection_url(), redis_url=None)
        c = await create_container(settings)
        yield c
        await c.dal.mongo.close()
```

### 13.3 Golden Tests (Contract Freeze)

```python
# tests/golden/test_send_message_contract.py

GOLDEN_REQUEST = {
    "room_id": "test-room-001",
    "message_text": "Hello agents",
    "sender_id": "user_clerk_123",
    "sender_name": "Test User",
}

async def test_send_message_response_contract(client, seeded_room):
    resp = await client.post("/api/v1/roomCenter/sendMessage", json=GOLDEN_REQUEST)
    assert resp.status_code == 200
    body = resp.json()

    # Contract: exact field set
    required_fields = {"room_id", "message_id", "dispatch_root_message_id",
                       "user_id", "user_name", "message", "success", "status_code"}
    assert required_fields.issubset(set(body.keys()))

    # Contract: types
    assert body["success"] is True
    assert isinstance(body["message_id"], str)
    assert isinstance(body["dispatch_root_message_id"], (str, type(None)))
    assert isinstance(body["message"], dict)
    assert body["message"]["sender_id"] == GOLDEN_REQUEST["sender_id"]

    # Contract: side effects
    msg = await db.room_user_messages.find_one({"message_id": body["message_id"]})
    assert msg is not None
    assert msg["room_id"] == GOLDEN_REQUEST["room_id"]
```

---

## 14. Key Design Decisions

| # | Decision | Rationale | Alternative |
|---|----------|-----------|-------------|
| 1 | Python `Protocol` over ABC | Structural typing; no inheritance coupling | ABC requires inheritance |
| 2 | Sub-container per module (frozen dataclass) | Physical isolation > discipline | Single container (no isolation) |
| 3 | Facade per module | Clear API surface; easy mock | Fine-grained exports |
| 4 | Thin API layer separate from modules | Zero HTTP knowledge in modules | Routes inside modules (bypass risk) |
| 5 | A2A Adapter independent module | a2a-sdk version isolation | Inline (leaks SDK types) |
| 6 | LLM Gateway with registry + routing + retry | Provider swap without business change | Direct calls (current state) |
| 7 | HubRuntimeBridge doesn't own agent data | Single truth for agents | Hub owns its agents (split truth) |
| 8 | Context & Memory reads Room via Protocol | No dual canonical source | C&M owns turns (dual truth) |
| 9 | Mixed Protocol inside Execution | Core seams testable; helpers simple | All Protocol (forest) / all direct |
| 10 | Redis split: KV / PubSub / Streams | Blocking XREAD can't share pool | Single pool (blocking risk) |
| 11 | DistributedLock ≠ LeaderElector | Different TTL/semantics/lifecycle | Single lock abstraction |
| 12 | Three-layer migration defense | Proportional protection | Single strategy |
| 13 | Execution and Workflow are separate modules | Different storage, lifecycle, HTTP semantics | Combined (lifecycle contamination) |
| 14 | Execution owns record_processing_status | Delivery must be pure transport | Delivery calls back (violates Rule 6) |
| 15 | IndexRegistry centralized | Startup ordering constraint (heal needs indexes) | Per-module init (ordering unclear) |
| 16 | Config unification in Phase 0b | Silent breakage risk from env var mismatch | Defer (risk accumulates) |

---

## 15. Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Subtle behavior drift | Medium | High | Golden tests + targeted shadow |
| Protocol proliferation | Low | Medium | Only at module boundaries + Execution internal seams |
| Config migration breaks prod | Medium | High | Phase 0b AST scan + env validation test |
| Redis pool split breaks existing behavior | Medium | Medium | Shadow mode on SSE endpoint |
| Workflow/Execution boundary misidentified | Low | High | Golden test for HTTP 207 vs 200 contract |
| MongoDB query ownership unclear (cross-collection) | Medium | Medium | Agent hydration uses HubLivenessReader Protocol |
| Frontend desync on feature flag changes | Medium | High | Phase 0c lockstep checklist |
| Migration timeline slips | High | Medium | Facade-first strategy; each phase independently shippable |
| Testcontainers CI complexity | Low | Low | Docker-in-Docker CI setup documented |

---

## 16. Compatibility with Future Architecture

| Future Change | Enabled By | Module Touched |
|--------------|-----------|----------------|
| DBOS adoption | Replace `execution/run/` with DBOS workflows | Execution only |
| AG-UI streaming | Replace `delivery/translator.py` with AG-UI format | Delivery only |
| Postgres for execution | Replace `execution/repository/` | Execution only |
| NATS/Kafka for events | Replace `delivery/event_bus/` | Delivery only |
| Multi-tenant isolation | Add tenant_id to DAL queries | DAL only |
| New LLM provider | Add provider in `llm_gateway/providers/` | LLM Gateway only |
| A2A protocol v2 | Update translators in `a2a_adapter/translators/` | A2A Adapter only |

---

## Appendix A: Cross-Collection Join Resolution (B6)

Current `_enrich_hub_fields` joins `agents × hubs` to set `hub_owner_id` and `is_hub_online`.

**After decoupling:**
- Agent module does NOT access `hubs` collection
- Agent module calls `HubLivenessReader.is_hub_online(hub_id)` to get the boolean
- `hub_owner_id` is stored on the agent record at sync time (written by `AgentRegistryWriter.sync_hub_agents()`)
- This eliminates the cross-collection join with a simple Protocol call

**For indexes:**
- Each module registers its indexes via `IndexRegistry.register(module, collection, spec)` during container assembly
- `IndexRegistry.ensure_all()` runs them all (idempotent) in Phase 1.5 of lifespan
- Modules do NOT call `create_index` at arbitrary times

---

## Appendix B: Decision Log

| Date | Decision | Context |
|------|----------|---------|
| 2026-05-04 | Unified DAL (not per-module DocumentStore) | `database_service` is already a DAL embryo |
| 2026-05-04 | Execution stays one module, Workflow separates | Different storage + HTTP semantics (B2) |
| 2026-05-04 | Delivery is pure transport (no business callbacks) | SSE side effect trap documented in EVENT_PIPELINE_DESIGN.md (A1) |
| 2026-05-04 | Mixed Protocol inside Execution | Core seams testable; helpers stay concrete |
| 2026-05-04 | Sub-container per module | Physical isolation > self-discipline |
| 2026-05-04 | API layer independent (thin adapter) | Modules must not know HTTP |
| 2026-05-04 | Three-layer migration defense | Proportional to risk level per endpoint |
| 2026-05-04 | A2A + LLM as independent adapter modules | External protocol/SDK changes isolated |
| 2026-05-04 | Context & Memory independent from Room | Room writes facts; C&M projects (via RoomHistoryReader) |
| 2026-05-04 | HubRuntimeBridge (bridge naming) | Expresses adapter nature between Cloud and Hub Runtime |
| 2026-05-04 | Redis split into 3 pools | Blocking XREAD monopolizes connection (B4) |
| 2026-05-04 | DistributedLock ≠ LeaderElector | Different semantics prevent confusion (B5) |
| 2026-05-04 | Config unification mandatory in Phase 0b | Silent prod breakage from env var mismatch (A8) |
| 2026-05-04 | DomainEvent discriminated union | Frontend SSE dispatcher depends on typed events (B1) |
| 2026-05-04 | Cancellation watcher NOT leader-elected | Per-worker in-memory TTLCache requires per-worker watcher (A6) |
| 2026-05-04 | heal_diverged_runs preserved in lifespan | Event sourcing integrity constraint (A5) |
| 2026-05-04 | AgentRegistry.is_directly_callable() | Platform doesn't understand "hub" — just reads boolean (A7) |
| 2026-05-04 | Migration timeline 18-22 weeks | Original 9-week estimate was 2-3x too low (C1) |
| 2026-05-05 | Hub→Execution cycle broken via EventPublisher internal handler | Hub emits HubAgentEvent; Execution subscribes post-construction (N1) |
| 2026-05-05 | AgentMatcher.match_agents adds requesting_user_id | Visibility filter requires user_id for owner-scoped agents (N2) |
| 2026-05-05 | WorkflowEngine expanded to 11 methods | Covers retry, summarize, session/task list endpoints (N3) |
| 2026-05-05 | WorkflowResult → OrchestrationResponse mapping documented | meta_task_ids only includes successful tasks (N4) |
| 2026-05-05 | Multi-worker guard checks all 3 Redis subsystems | Single missing pool causes silent runtime failure (N5) |
| 2026-05-05 | ChatContextManager dual-path: summarize_and_update vs update | REST needs LLM summarization; internal needs direct write (N6) |
| 2026-05-05 | FileStorage.upload requires room_id explicitly | S3 key + orphan cleanup both need room_id (N7) |
| 2026-05-05 | Internal domain events (MessageCommitted) via emit_internal | Separate from frontend-visible DomainEvent; shared bus impl (N8) |
| 2026-05-05 | Phase 6+7 interleaved (record-then-emit first) | Cannot extract pure Delivery until callers stop embedding record (N9) |
| 2026-05-05 | Shutdown cancels in-flight orchestration; heal fallback documented | Invariants 21+22 (N10) |
