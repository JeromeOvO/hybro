# Modular Decoupling Design Document

> **Status**: Final / Accepted after Goal 7 architecture cleanup
> **Date**: 2026-06-23
> **Scope**: Refactor hybro-multi-agents-backend into interface-driven modular architecture
> **Constraint**: All existing features remain unchanged; no new technology stack; zero backend breaking changes (except explicitly decommissioned legacy workflow endpoints after Phase 0d deprecation)

---

## 1. Executive Summary

The original codebase delivered a full-featured multi-agent orchestration platform with rooms, supervisor/debate workflows, HITL, hub relay, memory compaction, and a discovery/gateway API, but it suffered from tight coupling via singleton imports, a service-locator anti-pattern, missing interface seams, and monolithic initialization.

This document records the accepted restructuring of the codebase into **well-defined modules** connected through **Python Protocol interfaces**, managed by **module-scoped sub-containers**, while preserving every non-decommissioned feature and API endpoint (legacy workflow endpoints are explicitly removed via Phase 0d deprecation). The modular structure enables future technology stack replacement (DBOS, AG-UI, etc.) by creating clean seams — but this document does not introduce any new technology.

### Design Principles

1. **Pure Decoupling, No Stack Swap** — Keep MongoDB + Redis + Pinecone + FastAPI, only restructure
2. **Protocol Boundaries** — Modules communicate only through Protocols defined in Common
3. **Unified DAL** — Unified data access encapsulation; modules build Repositories on top of DAL
4. **Anti-Corruption Layer** — A2A protocol and LLM Providers each have independent adapter layers; business modules never directly import external SDKs
5. **Practical Implementability** — Phased migration with three-layer defense to guarantee no breakage

---

## 2. Original Architecture Problems

### 2.1 Coupling Analysis

| Problem | Example | Impact |
|---------|---------|--------|
| Service Locator | Legacy singleton imports for room runtime dependencies | Cannot inject mocks; hidden dependencies |
| Global Settings | Imports from the old deleted config package | No test isolation; env coupling |
| Monolithic Init | main.py 548-line lifespan with phased startup | Startup order is implicit; fragile |
| No Interface Layer | Services import concrete classes | Change ripples across entire codebase |
| SSE God Object | `sse_manager` has Redis, DB, broker, change stream + run_command_handler side effects | Single class with 6+ responsibilities |
| LLM Scatter | openai/gemini/bedrock services independently called | Provider switch requires touching business code |
| A2A Leakage | `a2a-sdk` types used directly in business modules | Protocol version bump cascades everywhere |
| Hub Coupling | RelayService directly writes agents_collection | Hub logic and agent lifecycle tangled |
| Config Scatter | 30+ `os.getenv()` calls outside settings.py | Settings model incomplete; silent key mismatches |

### 2.2 Original Dependency Graph (Implicit)

```
api/* ──→ app_shell/* ──→ domain facades ──→ database/*
  │           │              │              │
  └───────────┼──────────────┼──────────────┘
              │              │         (all import settings, sse_manager, mongodb,
              └──────────────┘          legacy service singletons directly)
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
      │ sub-container injects Protocol
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
     │                      Adapter Layer (Anti-Corruption)                    │
     │                                                                        │
     │   ┌─────────────────────────┐    ┌─────────────────────────┐          │
     │   │  A2A Protocol Adapter   │    │      LLM Gateway        │          │
     │   │  AgentTransport         │    │  generate / embed       │          │
     │   │  AgentCardResolver      │    │  model registry         │          │
     │   │  internal DTO ↔ a2a-sdk │    │  routing / fallback     │          │
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
| 1 | **Common** | Protocols, DTOs, auth, config, utils, errors | `common/`, `models/` |
| 2 | **DAL** | Unified data access clients (split by concern) | `dal/`, `database/` (legacy migrations only) |
| 3 | **A2A Protocol Adapter** | Anti-corruption for a2a-sdk, internal model ↔ A2A types | `a2a_adapter/`, with `app_shell/a2a_runtime.py` as compatibility shim |
| 4 | **LLM Gateway** | Unified LLM invocation, provider routing, capability registry | `llm_gateway/`, `llm_gateway/providers/`, `llm_gateway/services/` |
| 5 | **Agent** | Agent lifecycle, health, matching, discovery | `app_shell/agent_*.py`, `api/agent.py`, `api/discovery.py` |
| 6 | **Room** | Room CRUD, membership, raw message persistence, message graph | `room/`, with `room/compat/runtime.py` as runtime compatibility owner and `app_shell/room_runtime.py` as shim |
| 7 | **Context & Memory** | Context assembly, compaction, search, user memory, ~~chat contexts~~ (legacy; source removed in Phase 0d/8) | `context_memory/`, with app-shell memory/context shims for compatibility |
| 8 | **Execution** | Run lifecycle, supervisor, debate, HITL, dispatch (NOT workflow) | `execution/`, `app_shell/hitl_service.py` |
| 9 | **Delivery** | SSE connections, event broker, dedup, domain→frontend event translation | `app_shell/delivery_runtime.py`, `delivery/` |
| 10 | **Platform** | Gateway API, rate limiting, file storage | `platform_module/`, `api/gateway.py`, `api/discovery.py`, `api/files.py` |
| 11 | **HubRuntimeBridge** | Hub connection, relay, liveness, offline queue, agent sync | `hub_runtime_bridge/`, `api/relay.py`, `api/hub.py`, `app_shell/relay_service.py` shim |
| 12 | **Jobs** | Background tasks with leader election | `jobs/`, DAL Redis runtime |

> **NOTE (Workflow decommission)**: The legacy `base_tasks` / `meta_tasks` / `task_sessions` data model
> (from the first version of chat room) is **deleted** in this refactor, NOT wrapped. The endpoints
> the old workflow and task route files are decommissioned in Phase 0d / follow-up
> workflow cleanup, not in Phase 7b. Phase 7b explicitly excludes active legacy workflow routes
> from the Execution extraction static gates except as documented out-of-scope manifest entries.
> See Phase 0d for frontend coordination and deprecation timeline.

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
Rule 10: a2a-sdk types are confined to the A2A Protocol Adapter in the target architecture
Rule 11: LLM provider SDK types NEVER appear outside LLM Gateway
```

> **NOTE (A1 fix)**: Rule 6 means Delivery is a **pure transport**. Historically,
> before the Phase 7a/Phase 6 separation, `sse_manager.send_processing_status()`
> combined frontend delivery with run lifecycle recording, which violated this
> rule. The accepted architecture requires that SSE no longer calls
> `run_command_handler`: Execution records through `RunLifecyclePort.record_processing_status()`
> **before** calling `EventPublisher.emit("processing_status", ...)`, and
> Delivery only translates and delivers. See §4.5 for the enforced call-site
> contract.

> **Accepted A2A SDK confinement:** Execution dispatch/orchestration files use
> explicit compatibility adapters for legacy A2A behavior. New Execution
> HITL/cancellation code must use string/domain-state ports, and A2A protocol
> adapter ownership remains in `a2a_adapter`.

> **Goal 7 acceptance state (2026-06-23):** The modular decoupling design is
> accepted. The remaining app-shell focus files `room_runtime.py`,
> `a2a_runtime.py`, `relay_service.py`, and `repository_store.py` are
> import-compatible shims only. Runtime behavior lives in
> `room.compat.runtime`, `a2a_adapter.runtime_service`,
> `hub_runtime_bridge.compat.relay_service`,
> `context_memory.compat.context_assembly`, and `dal.runtime_store`.
> `blocked_cleanup` and `app_shell_runtime_blockers` remain empty. Phase 9 gates
> reject new app-shell focus growth, route-global API gateway binding, Common
> upward imports, and direct domain dependencies on app-shell focus runtime
> modules.

### 3.4 Cross-Module Communication Rules

| From | To | Mechanism | Notes |
|------|----|-----------|-------|
| Execution → Agent | `AgentRegistry` Protocol | Sync call | |
| Execution → Room | `RoomRegistry` + `RoomMessageStore` | Sync call | |
| Execution → Context & Memory | `ContextAssembler` Protocol | Sync call | |
| Execution → Delivery | `EventPublisher` Protocol | Awaited best-effort event emit | Execution records side effects BEFORE emitting |
| Execution → A2A Adapter | `AgentTransport` Protocol | Async call | |
| Execution → HubRuntimeBridge | `HubDispatchPort` Protocol | Async call | |
| Context & Memory → Room | `RoomHistoryReader` Protocol | Sync call | |
| Context & Memory ← (domain events) | `MessageCommitted` | **In-process** on emitting worker + **Redis Pub/Sub** for other workers | See §4.5 |
| Room services → Context & Memory | `MemoryManager.delete_room_memory()` Protocol | Protocol-bound room deletion cleanup | Accepted room deletion cleanup; startup binds protocol only, never the concrete facade |
| HubRuntimeBridge → Agent | `AgentRegistryWriter` Protocol | Sync call | |
| HubRuntimeBridge → Execution | `HubAgentResponseInternal` via `EventPublisher.emit_internal` | Async internal event | Phase 8 owner-worker routing; Phase 7b only registers the response-handler seam |
| HubRuntimeBridge → Room | `RoomOwnershipReader` Protocol | Sync call | |
| Agent → HubRuntimeBridge | `HubLivenessReader` Protocol | Sync call | For agent hydration: `is_hub_online` |
| Agent ↔ Room | NO direct dependency | — | |

> **Phase 7b reverse-binding cleanup resolved (2026-06-21):** The former
> Execution-owned compatibility callables are no longer injected into legacy
> `RoomServices`. Pending-HITL checks, active-run gating, active-run route
> assembly, and sendMessage lifecycle/status emission now live behind
> `ExecutionFacade` or API-layer Execution ports. Room services may still perform
> room persistence and legacy route compatibility, but the dependency direction is
> `API / app shell -> ExecutionFacade -> Room port/services`; RoomServices does
> not call back into Execution-owned processing status, active-run reader, or
> recovery scheduler callables.

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
    """Agent selection — used by Execution."""

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
    """Room state lookup — used by Execution, HubRuntimeBridge."""

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
    dispatch_root_message_id: str | None = None
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


# common/protocols/repository_protocols.py

@runtime_checkable
class ContentStorageRepository(Protocol):
    """Conversation content storage owned by Context & Memory."""
    async def upsert_full_content(self, *, document_id: str, room_id: str, turn_id: str, content: str, content_type: str, content_hash: str, stored_at: datetime, expires_at: datetime | None = None, turn_notes: dict | None = None) -> str: ...
    async def get_content_by_document_id(self, document_id: str) -> dict | None: ...
    async def get_content_by_turn_id(self, room_id: str, turn_id: str) -> dict | None: ...
    async def delete_content_by_turn_id(self, room_id: str, turn_id: str) -> bool: ...
    async def delete_content_by_room_id(self, room_id: str) -> int: ...
    async def get_content_stats_for_room(self, room_id: str) -> dict: ...
    async def text_search(self, room_id: str, query: str, limit: int = 50) -> list[dict]: ...
    async def hydrate_turn_notes(self, room_id: str, turn_ids: list[str]) -> list[dict]: ...


@runtime_checkable
class MemoryProjector(Protocol):
    """Trigger projection from raw messages — used internally or by events."""

    async def project_message(self, room_id: str, message_id: str) -> None: ...
    async def run_compaction(self, room_id: str) -> CompactionResult: ...
```

Internal mutation and persistence APIs such as `create_room_memory()`,
`push_and_trim_conversation_turn()`, `update_turn_notes()`, and
`compact_turns_bulk()` belong to `MemoryRepository` in
`common/protocols/repository_protocols.py`. They are injected into the
Context & Memory facade and are not part of the public `MemoryManager`
boundary exposed to API/runtime consumers.

### 4.4 Execution Module Protocols

```python
# common/protocols/execution_protocols.py

@runtime_checkable
class ExecutionEngine(Protocol):
    """Execute agent interactions within a room — used by API layer, HubRuntimeBridge.

    IMPORTANT: /sendMessage keeps the current after-response scheduling boundary.
    execute() persists the user message and returns the saved message info.
    API routes schedule start_orchestration(...) through FastAPI BackgroundTasks;
    the facade tracks and awaits the orchestration task once that after-response
    callback runs, preserving the background task's lifetime/exception semantics.
    Agent responses arrive via EventPublisher → SSE stream.
    """

    async def execute(self, request: ExecutionRequest) -> ExecutionAck: ...
    async def start_orchestration(
        self,
        request: ExecutionRequest,
        ack: ExecutionAck,
    ) -> None: ...
    async def cancel(
        self,
        room_id: str,
        message_id: str,
        *,
        requested_by_user_id: str,
    ) -> bool:
        """requested_by_user_id preserves the existing Mongo cancellation audit field."""
        ...
    async def get_run(self, run_id: str) -> RunInfo | None: ...
    async def get_runs_for_room(self, room_id: str) -> list[RunInfo]: ...
    async def cancel_inflight_tasks(self) -> int:
        """Graceful shutdown: cancel tracked orchestration tasks and return count."""
        ...
    async def heal_diverged_runs(self, limit: int = 500) -> int:
        """Startup recovery: replay committed events whose head-row update was lost."""
        ...


@runtime_checkable
class HITLManager(Protocol):
    """Human-in-the-loop management — used by API layer."""

    async def create_hitl_request(
        self,
        room_id: str,
        user_message_id: str,
        prompt: str,
        source: Literal["agent", "supervisor"],
        source_step_id: str | None = None,
        agent_id: str | None = None,
        agent_name: str | None = None,
        a2a_task_id: str | None = None,
        a2a_context_id: str | None = None,
        continuation_message_id: str | None = None,
        display_message_id: str | None = None,
        prompt_type: Literal["text", "choice", "confirmation"] = "text",
        choices: list[str] | None = None,
        group_id: str | None = None,
        group_total: int | None = None,
        group_index: int | None = None,
    ) -> HITLRequest | None: ...

    # Phase 7b note: this public protocol intentionally mirrors the full
    # current HITL DTO/API metadata surface so pending-route response fields are
    # not lost while the runtime is still adapter-backed.

    async def resolve_hitl(
        self,
        room_id: str,
        request_id: str,
        response: str,
        responder_id: str,
    ) -> HITLResponse: ...

    async def get_pending_hitl(self, room_id: str) -> list[HITLRequest]: ...
    async def cancel_hitl(self, room_id: str, request_id: str) -> bool: ...


@runtime_checkable
class HubAgentResponseSink(Protocol):
    """Process hub agent responses for orchestration resume — used via internal event handler."""

    async def handle_hub_agent_response(self, event: "HubAgentResponseInternal") -> None: ...
```

**Key DTOs (A2 fix):**

```python
# common/dto/execution.py

class ExecutionRequest(BaseModel):
    """Matches current RoomCenterUserMessageRequest semantics."""
    room_id: str
    sender_id: str
    sender_name: str | None = None
    message: dict | None = None
    message_text: str | None = None
    attachments: list[dict] | None = None
    inline_file_ids: list[str] | None = None
    target_agent_ids: list[str] | None = None
    target_group: str | None = None
    target_group_id: str | None = None
    message_target_mode: str | None = None
    mentioned_agent_ids: list[str] | None = None
    parent_message_id: str | None = None
    client_request_id: str | None = None
    mode: Literal["direct", "supervisor", "debate"] = "direct"

class ExecutionAck(BaseModel):
    """Returned immediately after user message persistence.
    Matches current RoomCenterUserMessageResponse contract exactly.
    Orchestration happens asynchronously; results via SSE."""
    # Nullable fields mirror current error responses; do not coerce to "" or {}.
    room_id: str | None = None
    message_id: str | None = None
    dispatch_root_message_id: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    message: dict | None = None  # Full RoomUserMessage serialized when present
    message_list: list[dict] | None = None
    scope_resolution_error: dict | None = None
    success: bool = True
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
    seq: int = 0
    trigger_message_id: str | None = None
    parent_run_id: str | None = None
    error_code: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

class HITLRequest(BaseModel):
    request_id: str
    room_id: str
    user_message_id: str
    prompt: str
    source: Literal["agent", "supervisor"]
    message_id: str | None = None  # display_message_id or continuation_message_id or user_message_id
    prompt_type: Literal["text", "choice", "confirmation"] = "text"
    status: Literal["pending", "processing", "responded", "resolved", "expired", "canceled"] = "pending"
    source_step_id: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    a2a_task_id: str | None = None
    a2a_context_id: str | None = None
    continuation_message_id: str | None = None
    display_message_id: str | None = None
    choices: list[str] | None = None
    group_id: str | None = None
    group_total: int | None = None
    group_index: int | None = None
    expires_at: datetime | None = None
    created_at: datetime | None = None
    user_input: str | None = None
    responded_at: datetime | None = None
    responded_by_user_id: str | None = None

class HITLResponse(BaseModel):
    request_id: str
    status: str = "ok"
    response_text: str | None = None
    responder_id: str | None = None
    resolved_at: datetime | None = None
    reclaimed: bool | None = None
    error: str | None = None

class AgentEvent(BaseModel):
    """Deprecated Common compatibility DTO; keep current event_type/payload shape."""
    room_id: str
    agent_id: str
    message_id: str
    event_type: Literal["partial", "final", "status_update", "error", "input_required"]
    payload: dict = Field(default_factory=dict)
    hub_id: str | None = None
```

Phase 7b ownership note: the normalized runtime event consumed by
`AgentResponseHandler` is `execution.dispatch.agent_event.AgentEvent`, not the
Common DTO above. `common.dto.execution.AgentEvent` remains exported as a
deprecated compatibility DTO with its existing `event_type` / `payload` shape
until old imports and tests are migrated. It must not be used by Hub response
adapters, ExecutionFacade, or response-handler protocols. A follow-up Common
cleanup can remove it once old imports are gone.

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
    - EventPublisher is a pure delivery pipe — it NEVER calls back into business components.
    - EventPublisher/Delivery MUST preserve terminal `ProcessingStatusEvent`
      deduplication: duplicate `completed`, `failed`, `canceled`, `rejected`,
      `rate_limited`, or `error`
      processing-status frames for the same `(room_id, message_id)` are
      suppressed through the shared L1/Redis or equivalent dedup layer.
    """

    async def emit(self, event: DeliveryEvent) -> None: ...
    async def emit_internal(self, event: "InternalEvent") -> None: ...
        """Dispatch internal cross-module events. NOT delivered to SSE clients.
        Handler failures are logged + dead-lettered, never propagated."""
    def register_internal_handler(self, event_type: str, handler: Callable) -> None: ...
        """Register handler for internal events. Called during container assembly."""
    async def start(self) -> None: ...
        """Component hook. DeliveryFacade.start() owns app-shell startup."""
    async def stop(self) -> None: ...
        """Drain/cancel pending internal handler tasks. Facade owns bus shutdown."""


@runtime_checkable
class SSETransport(Protocol):
    """SSE connection management — used by API layer.

    RUNTIME CONSTRAINT (A6 fix):
    - Cancellation watcher runs in EVERY worker (not leader-elected).
    - Each worker independently monitors change stream on cancelled_messages collection.
    - This is required because cancellation must propagate to in-memory TTLCache per worker.
    """

    def connect(self, room_id: str, connection_id: str) -> AsyncIterator[dict]: ...
    async def disconnect(self, connection_id: str) -> None: ...
    def is_cancelled(self, message_id: str) -> bool: ...
    async def mark_cancelled(self, message_id: str) -> None: ...
    def set_draining(self, draining: bool) -> None: ...
    async def start_cancellation_watcher(self) -> None: ...
        """Start change-stream watcher for cancellation propagation. Runs per-worker."""
```

`SSETransport.connect()` is the narrow exception to the "cross-module methods are async"
rule: it is synchronous because it returns an async iterator directly, allowing callers to
write `async for frame in transport.connect(...): ...` without first awaiting a generator
factory. All other Delivery protocol methods remain async when they perform async work.

**DomainEvent Discriminated Union (B1 fix):**

```python
# common/dto/delivery.py

class DeliveryEnvelope(BaseModel):
    event_type: str
    room_id: str
    timestamp: datetime | None = None
    trace_id: str | None = None
    payload: dict = Field(default_factory=dict)

class DeliveryEventBase(BaseModel):
    room_id: str
    timestamp: datetime | None = None
    trace_id: str | None = None

class ProcessingStatusEvent(DeliveryEventBase):
    event_type: Literal["processing_status"] = "processing_status"
    message_id: str
    status: Literal[
        "queued",
        "processing",
        "awaiting_input",
        "completed",
        "failed",
        "canceled",
        "rejected",
        "rate_limited",
        "error",
    ]
    agent_id: str | None = None
    details: dict | None = None
    client_request_id: str | None = None
    agents: list[dict] | None = None

class RunEventNotification(DeliveryEventBase):
    event_type: Literal["run_event"] = "run_event"
    event_id: str
    run_id: str
    seq: int
    run_event_type: str
    payload: dict = Field(default_factory=dict)
    correlation_id: str | None = None

class AgentMessagePartial(DeliveryEventBase):
    event_type: Literal["agent_message_partial"] = "agent_message_partial"
    message_id: str
    agent_id: str
    content_delta: str

class AgentMessageFinal(DeliveryEventBase):
    event_type: Literal["agent_message_final"] = "agent_message_final"
    message_id: str
    agent_id: str
    content: dict

class CancellationEvent(DeliveryEventBase):
    event_type: Literal["cancellation"] = "cancellation"
    message_id: str
    reason: str | None = None

class HITLRequestEvent(DeliveryEventBase):
    event_type: Literal["hitl_request"] = "hitl_request"
    request_id: str
    message_id: str
    source: str
    prompt: str
    prompt_type: str
    choices: list[str] | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    client_request_id: str | None = None

class HITLResolvedEvent(DeliveryEventBase):
    event_type: Literal["hitl_resolved"] = "hitl_resolved"
    request_id: str
    message_id: str
    source: str
    status: str = "resolved"
    error_message: str | None = None
    client_request_id: str | None = None

class HubAgentEvent(DeliveryEventBase):
    """Frontend-visible: UI rendering of hub agent activity."""
    event_type: Literal["hub_agent_event"] = "hub_agent_event"
    hub_id: str
    agent_id: str
    message_id: str
    status: str  # "working" | "completed" | "error"
    partial: str | None = None  # Streaming partial content for UI

class DebateRoundEvent(DeliveryEventBase):
    event_type: Literal["debate_round"] = "debate_round"
    round_number: int
    agent_id: str
    message_id: str

# Discriminated union type
DeliveryEvent = Annotated[
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
- `emit()` is frontend-visible delivery only. Internal module-to-module dispatch is handled
  exclusively by `emit_internal()`.
- `RunEventNotification` SSE frames always include `correlation_id`, using `None` when no
  correlation id is available.
- Trace ids are preserved only when explicit: typed SSE frames use `frame["data"]["trace_id"]`,
  and Redis fan-out envelopes use top-level `envelope["trace_id"]`.

**Final Delivery/SSE seam:**
- `app_shell/delivery_runtime.py` is now the C3 migration adapter. Startup binds
  it to `DeliveryFacade`; before binding, public methods fail fast.
- Backend modules emit typed `DeliveryEvent` DTOs. Delivery translates every frontend-visible
  event into the room SSE frame shape `{type, timestamp, room_id, data}`.
- `delivery.translator` is the only DTO-to-wire translation point. Backend-internal
  `event_type` discriminators are not sent as the outer SSE protocol.
- `ProcessingStatusEvent` supports every final frontend status, and `details` is always
  `dict | None`.
- Terminal dedup still happens inside Delivery.
- Redis room subscriptions are bounded by `DeliveryConfig`: default
  `redis_room_subscription_production_limit=40`,
  `redis_subscription_reserved_connections=10`, and `redis_max_connections=50`. Deployments
  that need more active rooms per worker must raise the actual Redis Pub/Sub pool size and
  Delivery config together, or implement multiplexed Pub/Sub first.
- Delivery does not resolve `client_request_id` from the database, does not call
  `record_processing_status()`, and does not evaluate `run_event_sse_enabled()`.
- Execution callers preserve frontend-visible ordering by recording lifecycle state first,
  emitting optional `RunEventNotification`, then emitting `ProcessingStatusEvent`.

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

class HubAgentResponseInternal(InternalEventBase):
    """Emitted by HubRuntimeBridge when hub agent sends response (fix 2.1).
    Execution subscribes to resume orchestration. NOT sent to frontend —
    HubAgentEvent (frontend-visible) is emitted separately for UI."""
    event_type: Literal["hub_agent_response_internal"] = "hub_agent_response_internal"
    hub_id: str
    agent_id: str
    task_id: str
    room_id: str
    is_terminal: bool
    payload: dict  # A2A-normalized response; internal adapter requires kind plus message_id or continuation_message_id

InternalEvent = MessageCommitted | RunStateChanged | HubAgentResponseInternal
```

For accepted handler registration, `HubAgentResponseInternal.payload` is
normalized before Execution handles it. Required fields are `kind` and either
`message_id` or `continuation_message_id`; optional fields mirror the
`AgentEvent` normalized event fields (`text`, `state`, `parts`, `artifacts`,
`client_request_id`, `lifecycle_message_id`, etc.). If Hub only knows
`continuation_message_id`, the Execution adapter maps that to the
response-handler `message_id` deliberately and tests that mapping.
If Hub emits a `processing_status` payload with `lifecycle_message_id`, the
upstream Hub normalizer must first validate that id against the agent message's
turn/root user message id and set `lifecycle_message_id_verified=True`. Phase
7b rejects unverified Hub lifecycle ids to match the existing relay behavior,
which drops mismatched `user_message_id` values before lifecycle writes.

**Internal event delivery mechanism:**
- Same `EventPublisher.register_internal_handler()` mechanism as §7.3
- Internal events go through `EventPublisher.emit_internal(event: InternalEvent)` — a separate method that:
  - Dispatches to registered internal handlers (same worker)
  - Fan-out via Redis Pub/Sub to other workers' internal handlers
  - Does NOT deliver to SSE clients
- This keeps one bus implementation with two entry points (`emit` for frontend-visible, `emit_internal` for module-to-module)

**At-least-once + idempotent delivery for `HubAgentResponseInternal` target state:**

The durable ownership/idempotency behavior below is a Phase 8 HubRuntimeBridge
target, not Phase 7b scope. Phase 7b may register the Execution internal handler
seam, but it must not claim this durability is delivered until the Hub response
processing port and idempotency repository exist.

- `HubFacade.publish_from_hub()` first persists the response to `run_events` collection with idempotency key `(hub_id, task_id, response_seq)` before calling `emit_internal()`.
- Phase 8 Hub response owner maintains an owned-task map for tasks dispatched by this worker.
- Internal handler checks the Phase 8 owner map — only the owner-worker processes; other workers discard.
- Handler deduplicates via the idempotency key against already-processed events in the run's event log.
- If the owner-worker crashes: the response is durable in `run_events`. On next startup, `heal_diverged_runs()` replays unprocessed durable responses.
- Pub/Sub delivery is at-least-once (Redis may redeliver on reconnect); idempotency key ensures no double-processing.

### 4.6 HubRuntimeBridge Protocols

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
    async def start_heartbeat_monitor(self) -> None: ...
        """Start background heartbeat monitor for hub liveness detection."""
    async def stop(self) -> None: ...
        """Stop heartbeat monitor and clean up hub state."""


@runtime_checkable
class HubLivenessReader(Protocol):
    """Hub online status — used by Agent module for agent hydration (A7 fix)."""

    def is_hub_online(self, hub_id: str) -> bool: ...
    async def get_hub_owner_id(self, hub_id: str) -> str | None: ...
```

### 4.7 Platform Module Protocols

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

### 4.8 Adapter Layer Protocols

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
class LLMProviderAdapter(Protocol):
    """Provider adapter implemented only by llm_gateway/providers."""

    async def generate(
        self, messages: list[dict], model: str, **kwargs
    ) -> LLMResponse: ...

    async def generate_structured(
        self, messages: list[dict], schema: dict | None = None, model: str | None = None, **kwargs
    ) -> LLMStructuredResponse: ...

    async def embed(self, text: str, model: str) -> list[float]: ...
    async def embed_batch(self, texts: list[str], model: str) -> list[list[float]]: ...


@runtime_checkable
class LLMGateway(Protocol):
    """Provider-neutral gateway used by focused services and compatibility adapters."""

    async def generate(self, messages: list[dict], model: str | None = None, **kwargs) -> LLMResponse: ...
    async def generate_structured(self, messages: list[dict], schema: dict | None = None, model: str | None = None, **kwargs) -> LLMStructuredResponse: ...
    async def embed(self, text: str, model: str | None = None) -> list[float]: ...
    async def embed_batch(self, texts: list[str], model: str | None = None) -> list[list[float]]: ...
    def generate_stream(self, messages: list[dict], model: str | None = None, **kwargs) -> AsyncIterator[str]: ...


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

**Implemented LLM workflow DTOs:** `common/dto/llm_workflows.py` now carries
domain-neutral request shapes such as `AgentRoutingCandidate`,
`ParsedUserMessageRequest`, `RoomMessageSummary`,
`ChatContextGenerationInput`, and `RoomMemoryGenerationInput`. Focused gateway
services accept these DTOs or primitives rather than importing `models.*`.

### 4.9 DAL Protocols (B4 fix: split by concern)

#### 4.9.1 MongoDB — Two-Layer Design

The DAL exposes MongoDB at **two granularity levels**:

1. **`MongoDAL` + `MongoCollection`** — generic collection access for module-internal repositories
2. **Domain-scoped Repository Protocols** — typed, query-encapsulating Protocols per bounded context

Modules MUST use domain-scoped repositories for their primary data access. The generic `MongoDAL` is available for ad-hoc queries, migrations, and cross-cutting concerns only.

```python
# common/protocols/dal_protocols.py

@runtime_checkable
class MongoDAL(Protocol):
    """Low-level MongoDB access — used by domain Repository implementations internally.
    Business modules should prefer domain-scoped Repository Protocols."""

    def collection(self, name: str) -> MongoCollection: ...
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def ping(self) -> bool: ...


@runtime_checkable
class MongoChangeStream(Protocol):
    async def __aenter__(self) -> AsyncIterator[dict]: ...
    async def __aexit__(self, exc_type, exc, tb) -> bool | None: ...


@runtime_checkable
class MongoCollection(Protocol):
    """Single collection operations — implementation detail of Repository layer."""

    async def find_one(self, query: dict, **kwargs) -> dict | None: ...
    async def find(self, query: dict, **kwargs) -> list[dict]: ...
    async def find_one_and_update(self, query: dict, update: dict | list[dict], **kwargs) -> dict | None: ...
    async def insert_one(self, document: dict) -> str: ...
    async def insert_many(self, documents: list[dict]) -> list[str]: ...
    async def update_one(self, query: dict, update: dict | list[dict], **kwargs) -> bool: ...
    async def replace_one(self, query: dict, replacement: dict, **kwargs) -> bool: ...
    async def update_many(self, query: dict, update: dict) -> int: ...
    async def delete_one(self, query: dict) -> bool: ...
    async def delete_many(self, query: dict) -> int: ...
    async def count(self, query: dict) -> int: ...
    async def aggregate(self, pipeline: list[dict]) -> list[dict]: ...
    async def create_index(self, keys: list[tuple], **kwargs) -> str: ...
    async def create_indexes(self, indexes: list, **kwargs) -> list[str]: ...
    async def bulk_write(self, operations: list, **kwargs) -> Any: ...
    async def distinct(self, key: str, query: dict | None = None) -> list: ...
    async def find_one_by_stable_or_native_id(self, stable_id_field: str, id_value: str) -> dict | None: ...
    def watch(self, pipeline: list[dict] | None = None, **kwargs) -> MongoChangeStream: ...
```

`MongoCollection.watch()` models Motor's async-context-manager change stream shape. Delivery's
per-worker cancellation watcher uses `async with collection.watch(...) as stream:` so change
streams are cleaned up on reconnect, cancellation, and shutdown.

#### 4.9.2 Domain-Scoped Repository Protocols

Each module owns a Repository Protocol that encapsulates its queries. This prevents cross-module raw query coupling and makes the data schema ownership explicit.

```python
# common/protocols/repository_protocols.py

@runtime_checkable
class AgentRepository(Protocol):
    """Agent data access — owned by Agent module."""
    async def get_by_id(self, agent_id: str) -> dict | None: ...
    async def get_by_ids(self, agent_ids: list[str]) -> list[dict]: ...
    async def get_by_provider(self, provider_id: str) -> list[dict]: ...
    async def get_public(self, limit: int = 50) -> list[dict]: ...
    async def upsert(self, agent_id: str, data: dict) -> None: ...
    async def delete(self, agent_id: str) -> bool: ...
    async def update_health(self, agent_id: str, healthy: bool) -> None: ...
    async def mark_hub_agents_offline(self, hub_id: str) -> int: ...


@runtime_checkable
class RoomRepository(Protocol):
    """Room data access — owned by Room module."""
    async def get_by_id(self, room_id: str) -> dict | None: ...
    async def get_by_owner(self, owner_id: str) -> list[dict]: ...
    async def create(self, room: dict) -> str: ...
    async def update(self, room_id: str, updates: dict) -> bool: ...
    async def delete(self, room_id: str) -> bool: ...


@runtime_checkable
class MessageRepository(Protocol):
    """Message data access — owned by Room module."""
    async def save_user_message(self, message: dict) -> str: ...
    async def save_agent_message(self, message: dict) -> str: ...
    async def get_by_id(self, message_id: str) -> dict | None: ...
    async def get_by_ids(self, message_ids: list[str]) -> list[dict]: ...
    async def get_for_room(self, room_id: str, limit: int, before: datetime | None = None) -> list[dict]: ...
    async def get_thread(self, parent_message_id: str) -> list[dict]: ...
    async def update_status(self, message_id: str, status: str, **fields) -> bool: ...


@runtime_checkable
class RunRepository(Protocol):
    """Run data access — owned by Execution module."""
    async def create(self, run: dict) -> str: ...
    async def get_by_id(self, run_id: str) -> dict | None: ...
    async def get_for_room(self, room_id: str) -> list[dict]: ...
    async def update_state(self, run_id: str, state: str, **fields) -> bool: ...
    async def get_diverged(self, limit: int) -> list[dict]: ...


@runtime_checkable
class RunEventRepository(Protocol):
    """Run event data access — owned by Execution module."""
    async def append(self, run_id: str, event: dict) -> str: ...
    async def get_for_run(self, run_id: str) -> list[dict]: ...
    async def get_latest(self, run_id: str) -> dict | None: ...


@runtime_checkable
class HITLRepository(Protocol):
    """HITL request data access — owned by Execution module."""
    async def create(self, request: dict) -> str: ...
    async def get_by_id(self, request_id: str) -> dict | None: ...
    async def get_pending_for_room(self, room_id: str) -> list[dict]: ...
    async def resolve(self, request_id: str, response: dict) -> bool: ...


@runtime_checkable
class MemoryRepository(Protocol):
    """Memory data access — owned by Context & Memory module."""
    async def get_room_memory(self, room_id: str) -> dict | None: ...
    async def upsert_room_memory(self, room_id: str, memory: dict) -> None: ...
    async def get_user_memories(self, user_id: str) -> list[dict]: ...
    async def delete_room_memory(self, room_id: str) -> bool: ...
    async def create_room_memory(self, memory: dict) -> str: ...
    async def ensure_room_memory(self, room_id: str, defaults: dict) -> dict: ...
    async def get_room_memory_by_memory_id(self, memory_id: str) -> dict | None: ...
    async def update_room_memory_by_room_id(self, room_id: str, updates: dict) -> bool: ...
    async def update_room_memory_by_memory_id(self, memory_id: str, updates: dict) -> bool: ...
    async def delete_room_memory_by_memory_id(self, memory_id: str) -> bool: ...
    async def push_and_trim_conversation_turn(self, room_id: str, turn: dict, *, max_turns: int, summary_stub: str, max_summary_chars: int) -> tuple[bool, bool]: ...
    async def push_and_trim_conversation_turn_if_absent(self, room_id: str, turn: dict, *, turn_id: str, max_turns: int, summary_stub: str, max_summary_chars: int) -> tuple[bool, bool, bool]: ...
    async def update_turn_notes(self, room_id: str, turn_id: str, turn_notes: dict) -> bool: ...
    async def get_room_summary_projection(self, room_id: str) -> dict | None: ...
    async def update_room_summary_atomic(self, room_id: str, room_summary: dict, *, new_facts: list[dict] | None = None, max_facts: int = 50) -> bool: ...
    async def compact_turns_bulk(self, room_id: str, compacted_turns: list[dict]) -> bool: ...
    async def list_room_ids_with_memory(self, limit: int | None = None) -> list[str]: ...


@runtime_checkable
class ContentStorageRepository(Protocol):
    """Conversation content storage — owned by Context & Memory module."""
    async def upsert_full_content(self, *, document_id: str, room_id: str, turn_id: str, content: str, content_type: str, content_hash: str, stored_at: datetime, expires_at: datetime | None = None, turn_notes: dict | None = None) -> str: ...
    async def get_content_by_document_id(self, document_id: str) -> dict | None: ...
    async def get_content_by_turn_id(self, room_id: str, turn_id: str) -> dict | None: ...
    async def delete_content_by_turn_id(self, room_id: str, turn_id: str) -> bool: ...
    async def delete_content_by_room_id(self, room_id: str) -> int: ...
    async def get_content_stats_for_room(self, room_id: str) -> dict: ...
    async def text_search(self, room_id: str, query: str, limit: int = 50) -> list[dict]: ...
    async def hydrate_turn_notes(self, room_id: str, turn_ids: list[str]) -> list[dict]: ...


@runtime_checkable
class HubRepository(Protocol):
    """Hub data access — owned by HubRuntimeBridge module."""
    async def get_by_id(self, hub_id: str) -> dict | None: ...
    async def get_by_owner(self, owner_id: str) -> list[dict]: ...
    async def upsert(self, hub_id: str, data: dict) -> None: ...
    async def update_heartbeat(self, hub_id: str) -> None: ...
    async def get_stale(self, threshold: datetime) -> list[dict]: ...
```

**Relationship between layers:**
- `MongoDAL` → provides raw `MongoCollection` access
- Domain Repositories → implemented using `MongoDAL.collection(name)` internally
- Business facades → depend on Repository Protocols (not MongoDAL directly)
- One Repository implementation per module, living in `<module>/repository/`

**Why `dict` return types (fix 2.7):** Repository Protocols intentionally return `dict` (not typed
Document models) because:
1. The current MongoDB schema is implicit — introducing typed documents is a separate migration
2. Facades already validate/transform via DTOs; adding validation at Repository doubles the cost
3. Protocol consumers see only the facade's typed DTOs, never raw dicts

This is a deliberate tradeoff: type safety lives at the **facade boundary** (Repository → DTO
transform), not at the Repository wire. If schema is later formalized, Repository methods can be
updated to return typed models without changing Protocol consumers (facades absorb the change).


@runtime_checkable
class RedisKV(Protocol):
    """Redis key-value + atomic ops — general purpose cache/state."""

    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int | None = None) -> None: ...
    async def delete(self, key: str) -> bool: ...
    async def increment(self, key: str, amount: int = 1) -> int: ...
    async def setnx(self, key: str, value: str, ttl: int) -> bool: ...
    async def exists(self, key: str) -> bool: ...
    async def ping(self) -> bool: ...
    async def close(self) -> None: ...


@runtime_checkable
class RedisPubSub(Protocol):
    """Redis Pub/Sub — cross-instance event fan-out. Separate connection pool."""

    async def publish(self, channel: str, message: str) -> None: ...
    async def subscribe(self, channel: str) -> AsyncIterator[str]: ...
    async def ping(self) -> bool: ...
    async def close(self) -> None: ...


@runtime_checkable
class RedisStreams(Protocol):
    """Redis Streams — durable ordered messaging. Separate connection pool (blocking XREAD)."""

    async def xadd(self, stream: str, fields: dict, maxlen: int | None = None) -> str: ...
    async def xread(self, streams: dict, block: int = 0, count: int = 100) -> list[dict]: ...
    async def ping(self) -> bool: ...
    async def close(self) -> None: ...


@runtime_checkable
class VectorDAL(Protocol):
    """Vector search — used by Agent, Context & Memory."""

    async def search(
        self, index: str, vector: list[float], top_k: int, filter: dict | None = None
    ) -> list[VectorSearchResult]: ...

    async def upsert(self, index: str, records: list[VectorRecord]) -> None: ...
    async def delete(self, index: str, ids: list[str]) -> None: ...
    async def delete_by_filter(self, index: str, filter: dict) -> None: ...
    async def ping(self) -> bool: ...


@runtime_checkable
class ObjectStorageDAL(Protocol):
    """S3-compatible object storage."""

    async def put(self, key: str, data: bytes, content_type: str = "") -> str: ...
    async def put_file(
        self,
        key: str,
        file_data: Any,
        content_type: str = "",
        content_length: int | None = None,
    ) -> str: ...
    async def get_text(self, key: str) -> str | None: ...
    async def get_presigned_url(
        self, key: str, ttl: int = 3600, filename: str | None = None
    ) -> str: ...
    async def delete(self, key: str) -> bool: ...
    async def delete_prefix(self, prefix: str) -> int: ...
    def get_public_url(self, key: str) -> str: ...
    async def head(self, key: str) -> dict | None: ...


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

Redis DAL failure contract after Phase 6:
- Empty Redis URL remains graceful and keeps disabled/no-op behavior for local development.
- When Redis is configured, driver failures from `RedisKV.get()`, `set()`, `delete()`,
  `increment()`, `setnx()`, and `exists()` raise `TransientError`.
- Configured-driver failures from `RedisPubSub.publish()` and subscribe/listen setup surface to
  Delivery so the event bus can reconnect and health can report disconnected.
- Configured-driver failures from `RedisStreams.xadd()` and `xread()` raise `TransientError`.
- `ping()` remains a health boolean and `close()` remains best-effort cleanup.

`MongoCollection.find_one_by_stable_or_native_id(stable_id_field, id_value)` is the DAL-owned fallback for legacy compacted content pointers. BSON/ObjectId conversion stays inside `dal/mongo/client.py`; Common protocols and business modules do not construct provider-native `_id` queries. Vector index missing/unavailable states are reported through `common.errors.VectorIndexUnavailableError` so Context & Memory does not depend on Pinecone exception types.

---

## 5. Execution Module Internal Architecture

### 5.1 Internal Structure

```
execution/
├── __init__.py
├── facade.py                      # ExecutionFacade: implements ExecutionEngine + HITLManager + HubAgentResponseSink
├── ports.py                       # Internal Protocols (module-private)
│
├── orchestration/                 # Supervisor loop, debate, queue execution
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

> **Accepted Execution scope note (2026-06-23):** The structure above remains the
> target architecture. The accepted implementation delivers the Execution module
> boundary, compatibility shims, typed Delivery emits, and adapter-backed run/HITL
> persistence. The repository package and full lifecycle-command port
> (`create_run`, `start_run`, `complete_run`, `fail_run`, `pause_run`,
> `cancel_run`, `emit_event`) remain target interfaces for future expansion while
> current persistence is adapter-backed. The implementation also uses pragmatic
> adapter filenames such as
> `execution/run_lifecycle.py`, `execution/run_queries.py`,
> `execution/hitl/service.py`, `execution/hitl/detector.py`, and
> `execution/dispatch/transports/direct.py`; the tree above is the target layout,
> not a claim that those target paths are fully delivered by the accepted state.
> The accepted implementation still includes the existing watchdog-specific
> `append_run_timeout_failure(room_id, run_id, stale_minutes=...)` adapter so
> stale-run recovery keeps its current timeout event semantics.
> Until repositories land, `ExecutionEngine.get_run()` and `get_runs_for_room()`
> are served by an adapter-backed run read port that preserves the current
> active-run response fields such as `trigger_message_id`.
> The accepted `HITLCoordinator` seam starts with the currently used
> `request_input()` method plus `cancel_request()` for supervisor clarification
> cleanup; query helpers such as `is_hitl_pending()` and `get_active_hitl()`
> remain target-design methods until needed by runtime call sites. The accepted
> implementation also keeps adapter-oriented filenames
> (`execution/run_lifecycle.py`, `execution/hitl/service.py`,
> `execution/hitl/detector.py`, `execution/dispatch/agent_dispatcher.py`, and
> `execution/dispatch/transports/direct.py`) rather than fully matching this
> target tree; layout normalization is follow-up cleanup.

### 5.2 Internal Protocol Seams

```python
# execution/ports.py (module-private, NOT in common/)
from enum import Enum
from typing import Any

ProcessingStatusLike = str | Enum


class HITLCoordinator(Protocol):
    """Orchestrator → HITL: create/resolve/check interruptions."""

    # Mirrors current HITLService positional order; callers should use keywords.
    async def request_input(
        self, room_id: str, user_message_id: str, source: str, prompt: str, **kwargs
    ) -> HITLRequest | None: ...

    async def cancel_request(self, request_id: str, room_id: str) -> None: ...

    # Target query helpers, not initially required by Phase 7b runtime call sites.
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
        self,
        room_id: str,
        status: ProcessingStatusLike,
        message_id: str | None,
        *,
        client_request_id: str | None = None,
        details: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> dict | None: ...
    async def heal_diverged_runs(self, limit: int = 500) -> int: ...
    async def append_run_timeout_failure(
        self, room_id: str, run_id: str, *, stale_minutes: int
    ) -> dict | None: ...
```

Phase 7b keeps `record_processing_status()` narrow:
`message_id` may be `None` for lifecycle-only paths, typed frontend details stay
structured as `details: dict[str, Any] | None`, and failure text flows through
the separate `error_message` argument so implementers do not collapse lifecycle
failure text back into typed frontend `details`.
`ProcessingStatusLike` is the shared Phase 7b status input type for raw strings
and enum-like values such as `SSEProcessingStatus`; Execution normalizes it to a
plain string before calling lifecycle persistence or typed Delivery emitters.

### 5.3 What Does NOT Get a Protocol (Direct Import)

| Component | Why No Protocol |
|-----------|----------------|
| `run/reducer.py` | Pure function, no side effects |
| `run/metrics.py` | Derived value computation |
| `hitl/hitl_detector.py` | Pure heuristic (pattern matching) |
| `state/task_state_manager.py` | In-memory bookkeeping, only used by orchestrator |

Accepted response-handler seam: `dispatch/response_handler.py` is stateful and
performs DB, SSE, HITL continuation, and notification side effects. The
implementation provides an `AgentResponseHandlerPort` seam for facade
Hub-response handling and app-shell shared handler construction; it is not
treated as a direct no-protocol import.

### 5.4 Processing Status Call Flow (A1 Resolution)

```
Orchestrator: agent response arrives
    │
    ├─ 1. run_lifecycle.record_processing_status(room_id, "completed", message_id, ...)
    │      → writes to runs + run_events collections
    │      → returns last_run_event_payload (for run_event SSE)
    │
    ├─ 2. (if run_event_sse_enabled) event_publisher.emit(RunEventNotification(...))
    │      → Delivery translates to SSE frame → delivers to clients
    │
    └─ 3. event_publisher.emit(ProcessingStatusEvent(room_id, message_id, "completed", ...))
           → Delivery translates to SSE frame → delivers to clients

Delivery NEVER calls run_command_handler. It is a pure pipe.
```

Phase 7b preserves the frontend-visible ordering:
`record_processing_status()` → optional `RunEventNotification` →
`ProcessingStatusEvent`. Changing this order would require a separate
frontend-coordinated migration. `ProcessingStatusEvent` accepts all final
statuses (`queued`, `processing`, `awaiting_input`, `completed`, `failed`,
`canceled`, `rejected`, `rate_limited`, `error`). Callers pass structured
`details: dict | None`; human-readable failure text belongs in lifecycle
records or `error_message` inputs that are normalized before frontend delivery.

### 5.5 In-Flight Task Tracking (fix 2.10)

```python
# execution/facade.py

class ExecutionFacade:
    def __init__(self, ...):
        self._inflight: set[asyncio.Task] = set()

    def _spawn_orchestration(self, coro: Coroutine) -> asyncio.Task:
        """Spawn a tracked background orchestration task."""
        task = traced_create_task(coro, name=f"orchestrate-{uuid4().hex[:8]}")
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)
        return task

    async def cancel_inflight_tasks(self) -> int:
        """Cancel all in-flight orchestration tasks. Called during graceful shutdown."""
        tasks = set(self._inflight)
        count = len(tasks)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Orchestrator CancelledError handlers ensure each cancelled run
        # transitions to RunState.CANCELED before shutdown cancellation completes.
        return count
```

Phase 8 Hub response ownership target sketch:

```python
class HubResponseOwner:
    def __init__(self) -> None:
        self._owned_hub_tasks: dict[str, str] = {}  # task_id -> run_id / worker owner

    def track_hub_task(self, task_id: str, run_id: str) -> None: ...
    def owns_hub_task(self, task_id: str) -> bool: ...
```

Phase 7b registers only the Hub response handler seam; it does not implement
ownership maps, idempotency, duplicate detection, or durable replay.

All background orchestrations (supervisor loop, queue execution, debate dispatch) MUST be
created via `_spawn_orchestration`, never bare `asyncio.create_task`.

---

## 6. Application Shell & Lifespan

### 6.1 Lifespan Sequence (A4, A5 fixes)

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
    # MUST happen after indexes and after Execution deps are constructed,
    # BEFORE background services or serving traffic. Do not silently skip when
    # Execution wiring is expected to exist.
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

    # === Phase 1.8: Delivery startup ===
    # DeliveryFacade.start() is the only app-shell Delivery startup API. It starts
    # cancellation watcher readiness first, then Redis Pub/Sub subscriptions/health,
    # then EventPublisher's handler lifecycle hook.
    await delivery_facade.start()
    sse_manager.bind_facade(delivery_facade)  # C3 adapter binding
    app.state.delivery_facade = delivery_facade

    # === Phase 3: HubRuntimeBridge background ===
    await container.hub.hub_management.start_heartbeat_monitor()

    # === Serve ===
    yield

    # === Graceful shutdown (N10: cancel in-flight orchestration) ===
    # Cancel tracked background orchestration tasks before Delivery/SSE drain so
    # cancellation lifecycle/status frames can still be emitted.
    await container.execution.execution_engine.cancel_inflight_tasks()
    sse_manager.set_draining(True)
    await asyncio.sleep(delivery_config.shutdown_drain_seconds)
    await scheduler.stop()
    await container.hub.hub_management.stop()
    await delivery_facade.stop()  # closes SSE connections, unsubscribes rooms, stops bus/watcher
    sse_manager.unbind_facade()
    await container.dal.mongo.close()
    # Redis pools are Optional (None in single-worker no-redis mode)
    if container.dal.redis_kv:
        await container.dal.redis_kv.close()
    if container.dal.redis_pubsub:
        await container.dal.redis_pubsub.close()
    if container.dal.redis_streams:
        await container.dal.redis_streams.close()
```

Phase 6 implementation detail: the current repository does not yet have a single
`DALContainer`; `container.py` exposes focused helpers instead:
`create_mongo_dal()`, `create_vector_dal()`, `create_delivery_config()`,
`create_delivery_redis_clients()`, `create_delivery_cancellation_collection()`,
`create_delivery_facade()`, and `create_delivery_deps()`. `main.py` calls these helpers and
does not import concrete `delivery.*`, `dal.*`, or legacy SSE `RedisBroker` implementations.

Health and multi-worker safety now use explicit fields:
`delivery_pubsub_connected`, `delivery_kv_connected`, `redis_runtime_connected`,
`relay_streams_available`, `change_stream_connected`, and `redis_expected`. Deprecated
aliases (`broker_connected`, `broker_expected`, `redis_service_connected`) remain in
`/health` for backend compatibility.

### 6.2 Sub-Container Design

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


@dataclass(frozen=True)
class ExecutionDeps:
    execution_engine: ExecutionEngine
    hitl_manager: HITLManager
    hub_agent_response_sink: HubAgentResponseSink


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
    delivery: DeliveryDeps
    hub: HubDeps
    platform: PlatformDeps
```

### 6.3 Circular Dependency Resolution: Execution ⇆ Hub (N1 fix)

**Problem:** `ExecutionFacade` needs `HubDispatchPort` (send to hub), and `HubFacade` needs to resume execution (hub publish → orchestration continue). This is a real bi-directional dependency from current `relay_service.publish_from_hub → RoomMessageCenter.resume_queue_from_continuation`.

**Solution:** Hub does NOT hold a direct reference to Execution. Instead, Hub calls `EventPublisher.emit_internal(HubAgentResponseInternal(...))` for orchestration resume, and separately `emit(HubAgentEvent(...))` for frontend SSE. Execution subscribes to `HubAgentResponseInternal` via an internal handler registered post-construction. This breaks the construction-time cycle.

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
    # Hub publishes via EventPublisher.emit_internal (delivery.event_publisher),
    # NOT via direct reference to Execution. This breaks Execution ⇆ Hub cycle.
    hub = _create_hub(dal, adapters, agent.agent_registry_writer, delivery.event_publisher)

    # --- Phase E: Execution (depends on everything above, subscribes to HubAgentResponseInternal) ---
    execution = _create_execution(
        dal, adapters, agent.agent_registry, room.room_message_store,
        context_memory.context_assembler, delivery.event_publisher,
        hub.hub_dispatch,  # Execution → Hub (one-way, no cycle)
    )
    # Execution subscribes to internal hub response events
    delivery.event_publisher.register_internal_handler(
        "hub_agent_response_internal",
        execution.hub_agent_response_sink.handle_hub_agent_response,
    )

    # --- Phase F: Platform (depends on Agent, Execution, Delivery) ---
    platform = _create_platform(dal, adapters, agent.agent_registry, execution.execution_engine)

    return AppContainer(
        dal=dal, adapters=adapters, agent=agent, room=room,
        context_memory=context_memory, execution=execution,
        delivery=delivery, hub=hub, platform=platform,
    )
```

**Key insight:** `HubFacade.publish_from_hub()` emits **two events**: (1) `event_publisher.emit(HubAgentEvent(...))` for frontend SSE delivery (partial/status updates), and (2) `event_publisher.emit_internal(HubAgentResponseInternal(...))` for orchestration resume. Execution subscribes to `HubAgentResponseInternal` via an internal handler registered post-construction. This eliminates the bidirectional compile-time dependency while preserving the separation of frontend-visible events from orchestration signals.

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
        """Exception contract: emit() NEVER raises to callers.
        Business side effects are already committed before emit() is called (invariant 4).
        All delivery failures are best-effort: logged + dead-lettered, never propagated.
        Callers must not rely on emit() success for correctness.
        """
        # 1. Deliver to SSE clients (frontend) — best-effort
        try:
            await self._deliver_to_sse(event)
        except Exception as exc:
            logger.warning(
                "sse_delivery_failed: event_type=%s error=%s",
                event.event_type,
                exc,
            )
        # 2. Fan-out to other workers (Redis Pub/Sub) — best-effort
        try:
            await self._fanout_cross_instance(event)
        except Exception as exc:
            logger.error(
                "fanout_failed: event_type=%s error=%s",
                event.event_type,
                exc,
            )
            await self._dead_letter(event, exc)

    async def emit_internal(self, event: InternalEvent) -> None:
        """Dispatch module-to-module events only; no frontend SSE delivery."""
        try:
            await self._fanout_internal_cross_instance(event)
        except Exception as exc:
            logger.error(
                "internal_fanout_failed: event_type=%s error=%s",
                event.event_type,
                exc,
            )
            await self._dead_letter(event, exc)
        for handler in self._internal_handlers.get(event.event_type, []):
            try:
                await handler(event)
            except Exception as exc:
                logger.error(
                    "internal_handler_failed: event_type=%s error=%s",
                    event.event_type,
                    exc,
                )
                await self._dead_letter(event, exc)
```

---

## 7. Configuration Management (A8 fix)

### 7.1 Config Unification Strategy

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

### 7.2 Module-Scoped Config Access

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

## 8. Migration Strategy

### 8.1 Three-Layer Defense

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
| `GET /sse/subscribe` | Streaming; event ordering | Dual-emit to shadow connection |
| `POST /relay/hub/{id}/publish` | Hub → Cloud; multi-module | Shadow validation only |

Rules:
- Return OLD path result to client always
- New path runs parallel (read-only or isolated)
- Log diff if responses diverge
- No dual writes — shadow skips side effects
- Remove after 1 week stable with zero drift

### 8.2 Migration Phases (C1 fix: realistic timeline)

> **Total estimated: 18-22 weeks** (vs original 9 weeks)
> Strategy: "Facade wrap first, internal rewrite second" — each phase has a wrap sub-phase (fast, low risk) and a rewrite sub-phase (slower, needs golden tests).
>
> **Repository implementation:** Domain-scoped Repository Protocols (§4.9.2) are implemented as part of each business module phase. Phase 3 includes `AgentRepository` impl in `agent/repository/`; Phase 4 includes `RoomRepository` + `MessageRepository`; Phase 5 includes `MemoryRepository`; Phase 6+7 defines the target `RunRepository` + `RunEventRepository` + `HITLRepository`, while the accepted implementation uses adapter-backed persistence for those paths; Phase 8 includes `HubRepository`.

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

#### Phase 0d: Legacy Workflow Decommission (1 week)

- Coordinate with frontend to remove all UI dependencies on:
  - retired workflow run, retry, and summary endpoints
  - retired task query/session endpoints
- Remove the old workflow and task route shims
- Remove old workflow-center and task-service compatibility code
- 4-week deprecation window: endpoints return 410 Gone with deprecation message
- Drop `base_tasks`, `meta_tasks`, `task_sessions`, `chat_contexts` collections in Phase 8 cleanup

**Gate:** Frontend deployed without workflow endpoints. 410 responses confirmed for 4 weeks. No production traffic.

#### Phase 1: DAL (2 weeks)

- `dal/mongo/` wrapping existing Motor client → `MongoDAL` + `MongoCollection`
- `dal/redis/` split into `RedisKV` + `RedisPubSub` + `RedisStreams` (three pools)
- `dal/pinecone/` → `VectorDAL`
- `dal/s3/` → `ObjectStorageDAL`
- `dal/redis/lock.py` → `DistributedLock` (short-lived) + `LeaderElector` (long-lived)
- `IndexRegistry` implementation

**Complexity note:** mongodb.py is ~101KB with 200+ methods. This phase wraps the Motor client in MongoDAL/MongoCollection — it does NOT refactor queries. Individual modules will own their queries later.

**Gate:** Container instantiates full DAL. Old singletons delegate to new DAL. All tests pass.

**Implemented DAL convergence note (2026-06-05):** Production startup constructs
DAL adapters directly from the composition root. Agent, Room, Execution,
ContextMemory, Platform, HubRuntimeBridge, and Jobs use module-owned repositories
or protocols rather than `database.mongodb` or `app_shell.database_service`.
The final legacy runtime deletion removed `database/mongodb.py`,
`database/pinecone_db.py`, `database/repository.py`, and
`app_shell/database_service.py`; production code must not reintroduce them.

**Operational migration/script compatibility decision (2026-06-11):** One-off
scripts and historical migrations that imported `database.mongodb` were removed
with the final legacy runtime deletion. New migration scripts must be standalone
Motor/DAL scripts and must not restore `database/mongodb.py`,
`database/pinecone_db.py`, `database/repository.py`, or
`app_shell/database_service.py` for compatibility.

#### Phase 2: Adapter Layer (2.5 weeks)

- `a2a_adapter/` with all translators (InternalAgentMessage ↔ a2a.Message, etc.)
- `llm_gateway/` with OpenAI/Gemini/Bedrock providers + ModelRegistry + retry/fallback
- Verify: no business module imports `a2a`, `openai`, `google.genai`, `aioboto3`

**Implemented object-storage platform adapter note (2026-06-19):**
`platform_module.PlatformObjectStorage` provides the legacy-compatible upload,
presigned URL, metadata, public URL, text download, and prefix cleanup surface
over `ObjectStorageDAL` operations: `put`, `put_file`, `get_text`,
`get_presigned_url`, `delete`, `delete_prefix`, `get_public_url`, and `head`.
`platform_module.PlatformAgentAvatarManager` owns agent avatar upload/public URL
persistence through that adapter. Platform file/content/avatar services now use
DAL-shaped object storage dependencies; SDK ownership stays in `dal/s3/`.
Production startup passes `PlatformObjectStorage` directly into runtime
consumers that still need the legacy upload/presign methods through
object-storage-named injection points, while `app_shell/s3_service.py` is
retained only as an SDK-free compatibility shim for tests and legacy import
paths until the final S3 service removal phase.
The startup wiring also configures `a2a_adapter.artifact_storage` once with the
platform object-storage adapter, bucket name, and file-size limit. Execution
transports must call the shared A2A helper without rebinding artifact storage so
those startup settings remain intact during inline artifact conversion.
Tracked terminal A2A message/task responses also keep artifact conversion
best-effort: object-storage conversion failures are logged, but
`update_task_on_message()` still persists the terminal task state.
Boundary gates scan static and dynamic production imports, including `main.py`
and `app_shell/`, so new `app_shell.s3_service` dependencies fail outside the
shim file itself. AWS SDK imports are confined to `dal/s3/`, with a single
documented provider-specific exception for `llm_gateway/providers/bedrock_provider.py`
importing `aioboto3` until Bedrock SDK access is moved behind a dedicated
provider transport.

**Implemented LLM migration note (2026-06-25):** `LLMGatewayImpl` owns logical
model routing, retry/timeout behavior, structured JSON-object mode, and
streaming. Provider adapters own SDK translation only. The legacy
provider-named app-shell compatibility facades have been removed. Focused
services under `llm_gateway/services/` cover supervisor, embeddings, discovery,
summary, agent selection, message parsing, room memory, and debate workflows.
`container.py` constructs a single gateway and binds currently
production-consumed focused services into runtime consumers; debate remains a
focused capability/tested service rather than a provider-named app-shell facade.

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

- `context_memory/facade.py` implementing ContextAssembler, MemoryManager, MemoryProjector
- Token budgeting logic preserved exactly
- Compaction logic preserved
- Domain event listener for `MessageCommitted`

**Gate:** `assemble_context()` produces identical token-budget results for test fixtures.

#### Phase 6+7: Delivery + Execution (4 weeks, partially parallel — N9 fix)

> **Why interleaved:** Historically, Delivery could not be pure until Execution
> callers stopped relying on the old `send_processing_status()` record-and-send
> coupling. Phase 7a separated lifecycle recording from SSE delivery; Phase 7b
> assumes the post-Phase-6/7a prerequisite where SSE no longer calls
> `run_command_handler`.
>
> **Current status (2026-06-04):** The room SSE core path uses typed
> `DeliveryEvent` DTOs end to end. Execution emits optional
> `RunEventNotification` and `ProcessingStatusEvent` objects after lifecycle
> recording, and Delivery translates those DTOs to the final `{type, timestamp,
> room_id, data}` SSE frame.

**Phase 7a (week 1-2): Execution caller migration — "record-then-emit"**
- Modify all Execution callers to explicitly call `record_processing_status()` before typed Delivery emit.
- `RunLifecycleService.record_processing_status()` returns the optional run-event payload so callers can preserve `run_event` SSE ordering before processing-status delivery.
- Golden and manifest tests prove `record_processing_status()` -> typed `RunEventNotification` -> typed `ProcessingStatusEvent` ordering.
- This works against the post-Phase-7a SSE manager, which is delivery/dedup only;
  lifecycle writes live in `execution/run_command_handler.py`.

**Phase 6 (week 2-3): Delivery module extraction — implemented on branch `phase-6-delivery-module`**
- `delivery/facade.py` implementing EventPublisher, SSETransport
- DomainEvent → SSE frame translator
- Cross-instance pub/sub (Redis)
- Cancellation watcher (every worker, change stream)
- Deduplication (TTLCache per terminal status)
- `register_internal_handler()` + `emit_internal()` for internal events
- **No business logic** — verify by asserting no business module imports in delivery/
- `app_shell/delivery_runtime.py` is a fail-fast C3 adapter bound to `DeliveryFacade` during startup.
- `main.py` constructs Delivery through `container.py` helpers and does not import concrete `delivery.*`, concrete `dal.*`, or legacy SSE `RedisBroker`.
- Old `sse_manager.send_processing_status()` is transport-only: no `record_processing_status()`, no `run_event_sse_enabled()`, no DB fallback.
- At this point, the historical record-inside-send path is gone: callers already
  record separately and the C3 SSE adapter is delivery-only.

**Phase 7b (week 3-4): Execution internal rewrite**
- `execution/facade.py` with full orchestrator, HITL, dispatch
- Internal Protocol seams: HITLCoordinator, AgentDispatchPort, RunLifecyclePort
- Callers now emit via the Execution helper: optional `RunEventNotification`, then `ProcessingStatusEvent`
- `_heal_diverged_runs_on_startup` preserved
- Room-level locking preserved
- Shadow mode on high-risk endpoints (sendMessage, hitl/resolve)
- Detailed task breakdown: `docs/superpowers/plans/2026-05-17-phase-7-execution-module.md`

**Gate:** Full message flow end-to-end. HITL pause/resume. Shadow mode zero drift. No `run_command_handler` calls from Delivery.

#### Phase 8: HubRuntimeBridge (2 weeks)

- `hub_runtime_bridge/facade.py` implementing HubDispatchPort, HubManagement, HubLivenessReader
- Hub → Agent sync via AgentRegistryWriter
- Hub → Execution via internal event (HubAgentResponseInternal)
- Drop legacy workflow collections (`base_tasks`, `meta_tasks`, `task_sessions`, `chat_contexts`)

**Gate:** Hub relay works. Agent sync via Protocol. Legacy collections dropped.

#### Phase 9: Platform + API Layer + Cleanup (2 weeks)

- `platform/facade.py` implementing GatewayService, RateLimiter, FileStorage
- `api/` thin adapter layer (all routes extracted)
- Remove old compatibility package directories
- Remove singleton imports
- Full import linter enforcement
- Remove migration adapters

**Gate:** CI green. No old code. Import linter passes all contracts.

### 8.3 Migration Adapter Pattern (C3 fix)

During transition, old singletons delegate to new facades:

```python
# app_shell/agent_service.py (during Phase 3)

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

## 9. Feature Mapping

| Feature | Current Location | Target Module | Target Component |
|---------|-----------------|---------------|-----------------|
| **Agent lifecycle** | | | |
| Agent registration | `app_shell/agent_service.py` | Agent | `service/agent_crud.py` |
| Agent health checking | `app_shell/agent_health_service.py` | Agent + Jobs | `service/agent_health.py` |
| Agent matching (vector) | `app_shell/agent_selection_service.py` | Agent | `service/agent_matching.py` |
| Agent groups | `api/agent_group.py` | Agent | `repository/agent_group_repo.py` |
| Agent card fetching | `a2a_adapter/` via `app_shell/a2a_runtime.py` shim | A2A Adapter | `card_resolver.py` / `client_facade.py` |
| Agent inspection | `app_shell/inspection_runtime.py` | Agent | `service/agent_crud.py` |
| Discovery API (no visibility filter) | `api/discovery.py` | Agent | `facade.match_agents(respect_visibility=False)` |
| Listings (owner-scoped, masked) | `app_shell/agent_service.py` | Agent | `facade.match_agents(respect_visibility=True)` |
| is_directly_callable (hub 502) | implicit in gateway_service | Agent | `facade.is_directly_callable()` |
| Hub agent enrichment (is_hub_online) | Agent facade + `HubLivenessReader` | Agent + HubRuntimeBridge | Agent calls `HubLivenessReader` |
| **Room & messages** | | | |
| Room CRUD | `room/compat/runtime.py` owner, `app_shell/room_runtime.py` shim | Room | `room/facade.py` + `room/repository/` |
| Room membership (3 seed modes) | `room/compat/runtime.py` owner, `app_shell/room_runtime.py` shim | Room | `room/facade.py` compatibility methods |
| User message persistence | `room/compat/runtime.py` owner, `app_shell/room_runtime.py` shim | Room | `room/facade.py` + `MessageMongoRepository` |
| Agent message persistence | `room/compat/runtime.py` owner, `app_shell/room_runtime.py` shim | Room | `room/facade.py` + `MessageMongoRepository` |
| Message graph | `room/compat/runtime.py` owner, `app_shell/room_runtime.py` shim | Room | `room/repository/` message queries |
| **Context & Memory** | | | |
| Context assembly and legacy selection/metrics | `context_memory/compat/context_assembly.py` | Context & Memory | `context_memory/facade.py`, `context_memory/assembly.py`, `context_memory/legacy_assembly.py` |
| Memory compaction | `context_memory/compaction.py` and `ContextMemoryFacade` | Context & Memory | `context_memory/compaction.py`, `context_memory/protocols.py` |
| Memory search | `context_memory/search.py` and `context_memory/search_adapter.py` | Context & Memory | `context_memory/search.py`, `context_memory/search_adapter.py` |
| Room/chat memories | `context_memory/compat/runtime.py` and `ContextMemoryFacade` | Context & Memory | `context_memory/compat/runtime.py`, `context_memory/facade.py` |
| **Execution** | | | |
| Message dispatch | `execution/orchestration/room_message_center.py` | Execution | `orchestration/` + `dispatch/` |
| Supervisor loop | `execution/orchestration/supervisor_executor.py` | Execution | `orchestration/supervisor_executor.py` |
| Debate mode | `execution/orchestration/debate_dispatcher.py` | Execution | `orchestration/debate_dispatcher.py` |
| Queue execution | `execution/orchestration/queue_executor.py` | Execution | `orchestration/queue_executor.py` |
| Run lifecycle | `execution/run_lifecycle_service.py` | Execution | `run_lifecycle.py` in Phase 7b; target `run/lifecycle.py` |
| Run events | `execution/run_command_handler.py` | Execution | `run_lifecycle.py` adapter in Phase 7b; target `run/command_handler.py` |
| record_processing_status | `execution/run_command_handler.py` | Execution | `events.py` + `run_lifecycle.py` adapter in Phase 7b |
| HITL requests | `app_shell/hitl_service.py` | Execution | `hitl/service.py` in Phase 7b; target `hitl/hitl_service.py` |
| Room-level locking | `execution/orchestration/room_message_center.py` | Execution | `state/locking.py` |
| heal_diverged_runs_on_startup | `main.py` | Execution | `run/lifecycle.py` (exposed via Protocol) |
| A2A long-running tasks | `api/a2a_tasks.py` | Execution (API) | Accepted legacy API route location; future route split target `api/a2a_task_routes.py` |
| Webhooks | `api/webhooks.py` | Execution (API) | `api/webhook_routes.py` |
| **Legacy Workflow** (DECOMMISSIONED — see Phase 0d) | | | |
| Task decomposition / assignment / execution / CRUD | old workflow/task API surface | DELETED | Endpoints removed; collections dropped in Phase 8 cleanup |
| **Delivery** | | | |
| SSE broadcasting | `app_shell/delivery_runtime.py` | Delivery | `sse/manager.py` |
| SSE connections | `app_shell/delivery_runtime.py` | Delivery | `sse/connection.py` |
| Event dedup | `delivery/` | Delivery | `sse/deduplication.py` |
| Cross-instance pub/sub | `delivery/` | Delivery | `event_bus/cross_instance.py` |
| Cancellation watcher | `delivery/` | Delivery | `sse/cancellation_watcher.py` |
| Domain to SSE translation | `delivery/` | Delivery | `translator.py` |
| **Platform** | | | |
| Gateway API | `platform_module/` | Platform | `gateway/gateway_service.py` |
| Rate limiting | `platform_module/rate_limit.py` | Platform | `rate_limit/rate_limiter.py` |
| File uploads | `platform_module/` | Platform | `files/upload_service.py` |
| Content storage | `platform_module/` | Platform | `files/content_storage.py` |
| Agent avatar uploads | `platform_module/agent_avatar.py` | Platform over DAL | `api/agent.py` avatar route |
| Object storage compatibility | `platform_module/object_storage.py` | Platform over DAL | `object_storage.py` |
| **HubRuntimeBridge** | | | |
| Hub relay | `app_shell/relay_service.py` | HubRuntimeBridge | `service/hub_relay.py` |
| Hub liveness | `app_shell/relay_service.py` | HubRuntimeBridge | `service/hub_liveness.py` |
| Hub connection | `api/hub.py` | HubRuntimeBridge | `service/hub_connection.py` |
| Hub route lifecycle compatibility | `app_shell/relay_service.py` shim | HubRuntimeBridge | `adapters/legacy_lifecycle.py` |
| Hub publish intake | `hub_runtime_bridge/service/hub_publish.py` | HubRuntimeBridge | `service/hub_publish.py` |
| Offline queue | `hub_runtime_bridge/` | HubRuntimeBridge | `transport/offline_queue.py` |
| Redis Streams relay | `app_shell/redis_runtime.py` | HubRuntimeBridge | `transport/relay_transport.py` |
| Hub agent sync | `app_shell/relay_service.py` | HubRuntimeBridge -> Agent | via `AgentRegistryWriter` |
| **LLM Gateway** | | | |
| OpenAI SDK calls | deleted app-shell facade | LLM Gateway | `providers/openai_provider.py` |
| Gemini SDK calls | deleted app-shell facade | LLM Gateway | `providers/gemini_provider.py` |
| Bedrock SDK calls | deleted app-shell facade | LLM Gateway | `providers/bedrock_provider.py` |
| Embedding generation | agent, memory, and viewset adapters | LLM Gateway | `EmbeddingLLMService` + `gateway.py` |
| Supervisor, summary, parsing, memory prompts | focused service consumers | LLM Gateway services | `llm_gateway/services/` |
| **A2A Adapter** | | | |
| A2A message send/stream | `app_shell/a2a_runtime.py` shim | A2A Adapter | `a2a_adapter/client_facade.py` |
| A2A task tracking setup, tracked-send persistence, and HITL reply task persistence | `app_shell/a2a_runtime.py` shim | Execution | `execution/task_tracking.py` |
| A2A card resolution | `common/client/card_resolver.py` | A2A Adapter | `card_resolver.py` |
| A2A type mapping | scattered | A2A Adapter | `translators/` |
| Push notification auth | `common/utils/push_notification_auth.py` | A2A Adapter | `push_notification.py` |
| **DAL** | | | |
| MongoDB client | `database/mongodb.py` | DAL | `mongo/client.py` |
| Redis KV | `app_shell/redis_runtime.py` | DAL | `redis/kv.py` |
| Redis Pub/Sub | `delivery/` | DAL | `redis/pubsub.py` |
| Redis Streams | `app_shell/redis_runtime.py` | DAL | `redis/streams.py` |
| Pinecone | `database/pinecone_db.py` | DAL | `pinecone/client.py` |
| S3 SDK calls | `app_shell/s3_service.py` shim | DAL | `s3/client.py` |
| Leader election | `app_shell/redis_runtime.py` | DAL | `redis/leader.py` |
| **Jobs** | | | |
| Health check | `jobs/agent_health_service.py` | Jobs | `agent_health_job.py` |
| Compaction sweep | `jobs/compaction_sweep.py` | Jobs | `compaction_sweep_job.py` |
| Stale task checker | `jobs/stale_task_checker.py` | Jobs | `stale_task_checker_job.py` (conditional) |
| Orphaned upload | `jobs/cleanup_orphaned_uploads.py` | Jobs | `orphaned_upload_job.py` |

---

## 10. Invariants

1. **No module imports another module's internal code** — only Protocols and DTOs from `common/`
2. **`container.py` is the ONLY place that imports concrete implementations** across module boundaries
3. **Every cross-module method is async** — no blocking I/O, except `SSETransport.connect()` which synchronously returns an async iterator
4. **DTOs are immutable** (frozen Pydantic models)
5. **EventPublisher.emit() is best-effort and awaited; `emit_internal()` is fire-and-forget for subscribers** — delivery/fan-out steps are awaited in call order, internal handlers do not block emitters, and failures are logged/dead-lettered without propagating to the emitter
6. **Business side effects MUST complete before EventPublisher.emit()** — Delivery never calls back into business modules (A1)
7. **Room execution lock must be held** before supervisor/queue execution starts
8. **Settings are read-only after container creation**
9. **Background jobs run under LeaderElector** — exactly once per job
10. **Cancellation watcher runs in EVERY worker** — not leader-elected (A6)
11. **a2a-sdk types are confined to `a2a_adapter/` ownership** — exact legacy
    Execution dispatch/orchestration compatibility paths are documented and gated;
    new HITL/cancellation code must not use A2A SDK types.
12. **LLM provider SDK types never appear outside `llm_gateway/`**
13. **Room writes canonical messages; Context & Memory projects derived artifacts**
14. **HubRuntimeBridge never writes to agents_collection directly** — calls AgentRegistryWriter
15. **Execution internal Protocols** (HITLCoordinator, AgentDispatchPort, RunLifecyclePort) defined in `execution/ports.py`, not `common/`
16. **Run head must be healed from events on startup** before serving traffic (A5)
17. **Graceful shutdown cancels in-flight Execution tasks before `set_draining(True)`, then sleeps `delivery_config.shutdown_drain_seconds` before `DeliveryFacade.stop()` closes connections**
18. **Startup failure must NOT set_draining** — prevents singleton state poisoning on partial init
19. **Discovery API (X-API-Key) does NOT filter by visibility** — any indexed agent discoverable (B3)
21. **Graceful shutdown must cancel in-flight background orchestration tasks** — `asyncio.create_task`-spawned orchestrations must be tracked and cancelled during shutdown; cancelled runs transition to `RunState.CANCELED` (N10)
22. **`heal_diverged_runs(limit=500)` is best-effort at startup** — runs beyond the limit are caught by `StaleTaskCheckerJob._fail_stale_runs` (RUN_WATCHDOG_STALE_MINUTES=90) as a fallback (N10)

---

## 11. Error Handling and Propagation

### 11.1 Error Hierarchy

```python
# common/errors/base.py

class HybroError(Exception):
    """Base for all application errors. Every Protocol method raises ONLY subtypes of this."""
    def __init__(self, message: str, code: str, details: dict | None = None):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(message)

class NotFoundError(HybroError):
    """Entity does not exist (maps to HTTP 404)."""
    def __init__(self, entity_type: str, entity_id: str):
        super().__init__(f"{entity_type} {entity_id} not found", "NOT_FOUND",
                         {"entity_type": entity_type, "entity_id": entity_id})

class ConflictError(HybroError):
    """Optimistic concurrency failure or duplicate (maps to HTTP 409)."""

class ValidationError(HybroError):
    """Business rule violation (maps to HTTP 422)."""

class PermissionError(HybroError):
    """Caller lacks permission for this operation (maps to HTTP 403)."""

class TransientError(HybroError):
    """Retryable failure — downstream timeout, connection lost (maps to HTTP 503)."""
    def __init__(self, message: str, retry_after: int | None = None, **kwargs):
        super().__init__(message, "TRANSIENT", **kwargs)
        self.retry_after = retry_after

class UpstreamError(HybroError):
    """Non-retryable upstream failure — LLM refused, A2A agent errored (maps to HTTP 502)."""
```

### 11.2 Propagation Rules

| Layer | Catches | Raises / Propagates |
|-------|---------|---------------------|
| **DAL** | Driver exceptions (pymongo, redis-py) | Wraps as `TransientError` (connection) or re-raises as `HybroError` subtype |
| **Adapter** (A2A, LLM) | SDK exceptions, HTTP errors | Wraps as `UpstreamError` or `TransientError` (timeout) |
| **Business Module** | `HybroError` subtypes from dependencies | Raises its own `HybroError` subtypes; NEVER catches and swallows silently |
| **API Layer** | All `HybroError` subtypes | Maps to HTTP response via `error_handler` middleware |
| **EventPublisher** | Handler exceptions | Logs + emits to dead-letter; does NOT propagate to emitter |

Delivery dead-lettering publishes structured envelopes to the configured Redis dead-letter
channel when Pub/Sub is available; the in-memory deque is only fallback/test aid. Local SSE
connection failures are warning-only and do not suppress Redis fan-out. Translator, fan-out,
and internal handler failures never propagate to callers.

### 11.3 API Layer Error Mapping

```python
# api/error_handler.py

ERROR_STATUS_MAP: dict[str, int] = {
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "VALIDATION": 422,
    "PERMISSION": 403,
    "TRANSIENT": 503,
    "UPSTREAM": 502,
}

@app.exception_handler(HybroError)
async def hybro_error_handler(request: Request, exc: HybroError) -> JSONResponse:
    status = ERROR_STATUS_MAP.get(exc.code, 500)
    return JSONResponse(
        status_code=status,
        content={"error": exc.code, "message": exc.message, "details": exc.details},
    )
```

### 11.4 Cross-Module Error Contracts

- Protocol methods document which `HybroError` subtypes they may raise in docstrings
- A module MUST NOT catch errors from another module's Protocol to silently succeed — callers propagate or explicitly translate to their own error type
- `EventPublisher.emit()` and `emit_internal()` NEVER raise to callers — delivery/internal failures go to dead-letter topic + structured log
- Background jobs catch `TransientError` for retry, log `HybroError` as warning, and propagate unknown exceptions as fatal

---

## 12. Observability

### 12.1 Structured Logging

```python
# common/observability/logging.py

import structlog

def configure_logging(settings: "Settings"):
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
    )
```

**Conventions:**
- Every log line includes: `module`, `operation`, `room_id` (if applicable), `run_id` (if applicable), `trace_id`
- Log levels: `DEBUG` for internal state transitions, `INFO` for cross-module calls, `WARNING` for recoverable errors, `ERROR` for unrecoverable
- No string formatting in hot paths — use structlog lazy binding

### 12.2 Distributed Trace Context

```python
# common/observability/tracing.py

import asyncio
import contextvars
from collections.abc import Awaitable
from collections.abc import Iterator
from contextlib import contextmanager
from contextlib import nullcontext
from typing import Any, ContextManager, Protocol, runtime_checkable


_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "hybro_trace_id",
    default=None,
)


@runtime_checkable
class TracingProvider(Protocol):
    def start_span(
        self,
        name: str,
        attributes: dict[str, str] | None = None,
    ) -> ContextManager[None]: ...


class NoopTracingProvider:
    def start_span(
        self,
        name: str,
        attributes: dict[str, str] | None = None,
    ) -> ContextManager[None]:
        return nullcontext()


def traced_create_task(
    coro: Awaitable[Any],
    *,
    name: str | None = None,
) -> asyncio.Task:
    context = contextvars.copy_context()
    return asyncio.create_task(coro, name=name, context=context)


def get_current_trace_id() -> str | None:
    return _trace_id.get()


@contextmanager
def trace_id_context(trace_id: str | None) -> Iterator[None]:
    token = _trace_id.set(trace_id)
    try:
        yield
    finally:
        _trace_id.reset(token)
```

Phase 7b keeps tracing helper support dependency-free. If OpenTelemetry is added
in a later phase, it should be integrated behind `traced_create_task()` and the
facade tracing hooks without changing facade call sites.

**Span hierarchy per request:**

```
[API] POST /roomCenter/sendMessage
  ├── [Execution] execute()
  │     ├── [Room] save_user_message()
  │     └── [API] return ExecutionAck
  └── [FastAPI BackgroundTasks] start_orchestration(request, ack)
        └── [Execution] start_orchestration()
              └── [Execution] orchestrate() (tracked background — linked span)
                    ├── [Execution] create/update run lifecycle as today
                    ├── [ContextMemory] assemble_context()
                    ├── [A2A] send_message() / [Hub] send_to_hub()
                    ├── [Execution] record_processing_status()
                    ├── [Delivery] emit(RunEventNotification) (optional)
                    └── [Delivery] emit(ProcessingStatusEvent)
```

**Rules:**
- Each module facade method creates a span with `module.<method>` name
- Cross-module Protocol calls propagate trace context automatically (in-process)
- Background tasks MUST use `traced_create_task()` helper so context propagation and task naming remain centralized.
- Phase 6 intentionally implements contextvars/task-name propagation only; it does not import OpenTelemetry or synthesize trace ids. OpenTelemetry span links remain a future enhancement.
- **Invariant:** All `asyncio.create_task` calls within Delivery and Execution facades MUST use the injected task runner / `traced_create_task()`; bare task creation bypasses the facade tracing seam and is rejected by tests where enforceable.
- EventPublisher includes explicit `trace_id` values in typed SSE frame data and Redis envelopes for cross-worker correlation.
- DAL spans: `dal.mongo.<collection>.<operation>`, `dal.redis.<pool>.<command>`

### 12.3 Key Metrics

| Metric | Type | Labels | Source Module |
|--------|------|--------|---------------|
| `hybro_execution_run_duration_seconds` | Histogram | `room_id`, `mode`, `state` | Execution |
| `hybro_execution_runs_active` | Gauge | `worker_id` | Execution |
| `hybro_delivery_sse_connections` | Gauge | `worker_id` | Delivery |
| `hybro_delivery_events_emitted_total` | Counter | `event_type` | Delivery |
| `hybro_delivery_events_deduplicated_total` | Counter | `event_type` | Delivery |
| `hybro_llm_request_duration_seconds` | Histogram | `provider`, `model`, `operation` | LLM Gateway |
| `hybro_llm_tokens_total` | Counter | `provider`, `model`, `direction` | LLM Gateway |
| `hybro_a2a_request_duration_seconds` | Histogram | `agent_id`, `transport` | A2A Adapter |
| `hybro_hub_dispatch_duration_seconds` | Histogram | `hub_id` | HubRuntimeBridge |
| `hybro_hub_connections_active` | Gauge | — | HubRuntimeBridge |
| `hybro_dal_operation_duration_seconds` | Histogram | `backend`, `operation` | DAL |
| `hybro_hitl_pending_total` | Gauge | `room_id` | Execution |

### 12.4 Health Check Endpoint

```python
# api/health.py — /health (unauthenticated)

async def health_check(container: AppContainer) -> dict:
    return {
        "status": "healthy",
        "checks": {
            "mongo": await container.dal.mongo.ping(),
            "redis_kv": container.dal.redis_kv is not None and await container.dal.redis_kv.ping(),
            "redis_pubsub": container.dal.redis_pubsub is not None and await container.dal.redis_pubsub.ping(),
            "redis_streams": container.dal.redis_streams is not None and await container.dal.redis_streams.ping(),
            "vector": await container.dal.vector.ping(),
        },
    }
```

---

## 13. Import Enforcement

```toml
# pyproject.toml
[tool.import-linter]
root_packages = ["common", "dal", "a2a_adapter", "llm_gateway", "agent", "room", "context_memory", "execution", "delivery", "platform", "hub_runtime_bridge", "jobs", "api"]

[[tool.import-linter.contracts]]
name = "Common has no dependencies"
type = "forbidden"
source_modules = ["common"]
forbidden_modules = ["dal", "a2a_adapter", "llm_gateway", "agent", "room", "context_memory", "execution", "delivery", "platform", "hub_runtime_bridge", "jobs", "api"]

[[tool.import-linter.contracts]]
name = "DAL depends only on Common"
type = "layers"
layers = ["dal", "common"]

[[tool.import-linter.contracts]]
name = "Adapters depend only on DAL and Common"
type = "forbidden"
source_modules = ["a2a_adapter", "llm_gateway"]
forbidden_modules = ["agent", "room", "context_memory", "execution", "delivery", "platform", "hub_runtime_bridge", "jobs", "api"]

[[tool.import-linter.contracts]]
name = "Business modules never import each other's internals"
type = "independence"
modules = ["agent", "room", "context_memory", "execution", "delivery", "platform", "hub_runtime_bridge"]
ignore_imports = ["common", "dal", "a2a_adapter", "llm_gateway"]

[[tool.import-linter.contracts]]
name = "No module imports container"
type = "forbidden"
source_modules = ["common", "dal", "a2a_adapter", "llm_gateway", "agent", "room", "context_memory", "execution", "delivery", "platform", "hub_runtime_bridge", "jobs"]
forbidden_modules = ["container"]

[[tool.import-linter.contracts]]
name = "A2A SDK confined to adapter"
type = "forbidden"
source_modules = ["agent", "room", "context_memory", "execution", "delivery", "platform", "hub_runtime_bridge", "jobs", "api"]
forbidden_modules = ["a2a"]

[[tool.import-linter.contracts]]
name = "LLM SDKs confined to gateway"
type = "forbidden"
source_modules = ["agent", "room", "context_memory", "execution", "delivery", "platform", "hub_runtime_bridge", "jobs", "api"]
forbidden_modules = ["openai", "google.genai", "aioboto3"]
```

The A2A contract above is the target-state contract. Exact legacy Execution
compatibility files that still carry A2A protocol translation are documented and
gated; new HITL/cancellation code must not use those compatibility paths.

**AST scan (CI) — replaces the no-op import-linter contract (fix 2.8):**
```python
# scripts/check_no_getenv.py
# Fails CI if os.getenv() found outside common/config/settings.py
# import-linter cannot check function calls — only this AST scan enforces the rule.
```

---

## 14. Testing Strategy

### 14.1 Unit Tests (Per Module, Mocked Protocols)

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

### 14.2 Integration Tests

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

### 14.3 Golden Tests (Contract Freeze)

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

## 15. Key Design Decisions

| # | Decision | Rationale | Alternative |
|---|----------|-----------|-------------|
| 1 | Python `Protocol` over ABC | Structural typing; no inheritance coupling | ABC requires inheritance |
| 2 | Sub-container per module (frozen dataclass) | Physical isolation > discipline | Single container (no isolation) |
| 3 | Facade per module | Clear API surface; easy mock | Fine-grained exports |
| 4 | Thin API layer separate from modules | Zero HTTP knowledge in modules | Routes inside modules (bypass risk) |
| 5 | A2A Adapter independent module | a2a-sdk version isolation | Inline (leaks SDK types) |
| 6 | LLM Gateway with registry + routing + retry | Provider swap without business change | Direct provider calls (legacy state before 2026-06-05 migration) |
| 7 | HubRuntimeBridge doesn't own agent data | Single truth for agents | Hub owns its agents (split truth) |
| 8 | Context & Memory reads Room via Protocol | No dual canonical source | C&M owns turns (dual truth) |
| 9 | Mixed Protocol inside Execution | Core seams testable; helpers simple | All Protocol (forest) / all direct |
| 10 | Redis split: KV / PubSub / Streams | Blocking XREAD can't share pool | Single pool (blocking risk) |
| 11 | DistributedLock ≠ LeaderElector | Different TTL/semantics/lifecycle | Single lock abstraction |
| 12 | Three-layer migration defense | Proportional protection | Single strategy |
| 13 | Legacy Workflow deleted, not wrapped | Zero value wrapping dead code; saves ~1.5 weeks dev + ongoing maintenance | Wrap (cost with no benefit) |
| 14 | Execution owns record_processing_status | Delivery must be pure transport | Delivery calls back (violates Rule 6) |
| 15 | IndexRegistry centralized | Startup ordering constraint (heal needs indexes) | Per-module init (ordering unclear) |
| 16 | Config unification in Phase 0b | Silent breakage risk from env var mismatch | Split config sources (risk accumulates) |
| 17 | Domain-scoped Repository Protocols | Prevent cross-module raw query coupling; explicit schema ownership | Single generic MongoDAL (god interface) |
| 18 | HybroError hierarchy for cross-module errors | Consistent error propagation without catching/re-raising SDK exceptions | Untyped exceptions (no contract) |
| 19 | Trace context seam per facade method | Cross-module tracing is hard to retrofit; Phase 7b keeps no-dependency hooks that OpenTelemetry can wrap later | Ad hoc tracing later (lost context) |
| 20 | Hub event split: frontend HubAgentEvent + internal HubAgentResponseInternal | Different payloads for different consumers; breaks dual-purpose coupling | Single event (payload mismatch) |
| 21 | `traced_create_task()` mandatory for background orchestration | Centralizes context propagation and task naming without adding Phase 7b dependencies | Manual context passing (error-prone) |

---

## 16. Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Subtle behavior drift | Medium | High | Golden tests + targeted shadow |
| Protocol proliferation | Low | Medium | Only at module boundaries + Execution internal seams |
| Config migration breaks prod | Medium | High | Phase 0b AST scan + env validation test |
| Redis pool split breaks existing behavior | Medium | Medium | Shadow mode on SSE endpoint |
| MongoDB query ownership unclear (cross-collection) | Medium | Medium | Agent hydration uses HubLivenessReader Protocol |
| Frontend desync on feature flag changes | Medium | High | Phase 0c lockstep checklist |
| Frontend still using legacy workflow endpoints | Medium | High | Phase 0d frontend coordination + 4-week deprecation window (410 Gone) |
| Migration timeline slips | High | Medium | Facade-first strategy; each phase independently shippable |
| Testcontainers CI complexity | Low | Low | Docker-in-Docker CI setup documented |

---

## 17. Compatibility with Future Architecture

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

## 18. Potential Future Protocol Additions

> **Scope note:** These are Protocols that do NOT exist today and are NOT required for the
> current decoupling (which preserves all existing features). They are documented here because
> they represent the **hardest-to-retrofit seams** — the ones that require cross-module schema
> changes if added later without advance planning. Including them as an appendix lets us verify
> that the current module boundaries accommodate them without refactoring.
>
> **Rule:** Do NOT implement these now. Only add them when the feature they enable is actually built.
>
> **Terminology:** "Workflow" in this section refers to a future generic execution workflow engine (step graphs, parallel branches, conditional routing) — NOT the deleted legacy Workflow module (base_tasks/meta_tasks) removed in Phase 0d.

### 18.1 Events Extensions

```python
class WorkflowProgressEvent(DomainEventBase):
    """Step-level progress for workflow UI rendering."""
    event_type: Literal["workflow_progress"] = "workflow_progress"
    workflow_run_id: str
    step_id: str
    parent_step_id: str | None  # For parallel branch rendering (step tree)
    agent_id: str | None
    status: Literal["queued", "running", "completed", "failed", "skipped"]
    artifact_ref: str | None  # Reference to StepArtifactStore

class StepInfo(BaseModel):
    """Step tree model — steps carry parent for parallel branch visualization."""
    step_id: str
    parent_step_id: str | None
    workflow_run_id: str
    agent_id: str | None
    instruction: str
    status: str
    depends_on: list[str] | None = None
    artifacts: list[str] | None = None
```

**Accommodation check:** Adding `WorkflowProgressEvent` to the `DomainEvent` discriminated union is
additive (no existing events change). Step tree is internal to Execution module storage (future
step-based orchestration would live in Execution, not a separate Workflow module).

### 18.2 SSE/Delivery Extensions

```python
class SSETransportV2(Protocol):
    """Extended SSE with cross-room and topic-based subscriptions."""

    async def subscribe_user(self, user_id: str, connection_id: str) -> AsyncIterator[dict]: ...
        """Cross-room event stream for mission control / dashboard.
        Aggregates events from all rooms the user owns."""

    async def subscribe_topics(
        self, topics: list[str], connection_id: str
    ) -> AsyncIterator[dict]: ...
        """Selective subscription for dashboard widgets.
        Topics: 'runs:active', 'agents:health', 'hub:status', etc."""
```

**Accommodation check:** `SSETransport` Protocol is currently room-scoped. User-scoped and
topic-scoped subscriptions require a routing layer in Delivery, but do NOT change any
business module — Delivery already receives all events via `EventPublisher`.

### 18.3 Artifacts

```python
@runtime_checkable
class StepArtifactStore(Protocol):
    """Query diffs, logs, reasoning traces by step_id.
    Unspecified in current DAL — needs object storage + metadata index."""

    async def store(self, step_id: str, artifact_type: str, data: bytes, metadata: dict) -> str: ...
    async def get(self, artifact_id: str) -> ArtifactInfo | None: ...
    async def list_for_step(self, step_id: str) -> list[ArtifactInfo]: ...
    async def list_for_run(self, workflow_run_id: str) -> list[ArtifactInfo]: ...

class ArtifactInfo(BaseModel):
    artifact_id: str
    step_id: str
    workflow_run_id: str
    artifact_type: str  # "reasoning_trace", "diff", "log", "output"
    size_bytes: int
    url: str  # Presigned URL from ObjectStorageDAL
    created_at: datetime
```

**Accommodation check:** Lives in DAL layer (uses `ObjectStorageDAL` + metadata collection).
Business modules reference artifacts by ID only.

### 18.4 Cross-Cutting Queries

```python
@runtime_checkable
class DashboardQueryService(Protocol):
    """Cross-cutting read queries for operational dashboards.
    Reads across module boundaries — lives in API layer, not a business module."""

    async def get_active_runs_for_user(self, user_id: str) -> list[RunSummary]: ...
    async def get_failed_runs(self, since: datetime, limit: int = 50) -> list[RunSummary]: ...
    async def get_agent_health_summary(self) -> list[AgentHealthSummary]: ...
    async def get_hub_status_overview(self) -> list[HubStatusSummary]: ...
```

**Accommodation check:** This is a read-only aggregation service that calls multiple module
Protocols. It lives in the API layer (or a dedicated `dashboard/` module) and does NOT
violate module independence — it consumes Protocol interfaces, not internal implementations.

### 18.5 Future Workflow Engine

> **Clarification:** This is a future generic execution workflow engine (step graphs, parallel
> branches, conditional routing, mid-execution override). It has NO relation to the deleted
> legacy Workflow module (`base_tasks` / `meta_tasks` / `task_sessions` / `chat_contexts`)
> removed in Phase 0d.

```python
@runtime_checkable
class WorkflowEngine(Protocol):
    """Step-graph orchestration — future replacement for linear queue execution."""

    async def start_workflow(self, run_id: str, plan: "WorkflowPlan") -> None: ...
    async def resume_from_step(
        self, run_id: str, step_id: str, overrides: dict | None = None
    ) -> None: ...
        """Enables step retry and 'edit instruction → partial rerun'."""
    async def get_step_tree(self, run_id: str) -> "StepTree": ...
    async def cancel_workflow(self, run_id: str) -> None: ...


@runtime_checkable
class ExecutionEngineV2(Protocol):
    """Extended ExecutionEngine with mid-execution override support."""

    async def modify_plan(self, run_id: str, instruction: str) -> None: ...
        """Continuous chat mid-execution override: user sends new instruction
        while workflow is running; engine re-plans remaining steps."""
```

**Accommodation check:** `WorkflowEngine` lives inside the Execution module as a new
orchestration strategy (alongside supervisor/debate/queue). `modify_plan` extends the
existing `ExecutionEngine` Protocol — additive, no breaking change. Step state stored in
`run_events` collection (same event-sourcing model as current runs).

### 18.6 Plugin System

```python
@runtime_checkable
class WorkflowTypeRegistry(Protocol):
    """Pluggable workflow mechanism registration.
    Enables third-party workflow types beyond supervisor/debate/queue."""

    def register(self, workflow_type: str, factory: "WorkflowStepFactory") -> None: ...
    def get_factory(self, workflow_type: str) -> "WorkflowStepFactory | None": ...
    def list_types(self) -> list[str]: ...


@runtime_checkable
class WorkflowStep(Protocol):
    """Interface for third-party step implementations."""

    async def execute(self, context: "StepContext") -> "StepResult": ...
    async def rollback(self, context: "StepContext") -> None: ...
    def supports_retry(self) -> bool: ...
```

**Accommodation check:** Plugin registration happens during container assembly. The
`WorkflowTypeRegistry` would live in the Execution module and the `mode` field in `ExecutionRequest`
naturally routes to registered types. No cross-module changes needed.

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
| 2026-05-04 | ~~Execution stays one module, Workflow separates~~ | SUPERSEDED: Workflow deleted (see 2026-05-05) |
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
| 2026-05-05 | ~~WorkflowEngine expanded to 11 methods~~ | SUPERSEDED: Workflow deleted |
| 2026-05-05 | ~~WorkflowResult → OrchestrationResponse mapping~~ | SUPERSEDED: Workflow deleted |
| 2026-05-05 | Multi-worker guard checks all 3 Redis subsystems | Single missing pool causes silent runtime failure (N5) |
| 2026-05-05 | ~~ChatContextManager dual-path~~ | SUPERSEDED: Merged into MemoryManager (summarize is impl detail) |
| 2026-05-05 | FileStorage.upload requires room_id explicitly | S3 key + orphan cleanup both need room_id (N7) |
| 2026-05-05 | Internal domain events (MessageCommitted) via emit_internal | Separate from frontend-visible DomainEvent; shared bus impl (N8) |
| 2026-05-05 | Phase 6+7 interleaved (record-then-emit first) | Cannot extract pure Delivery until callers stop embedding record (N9) |
| 2026-05-05 | Shutdown cancels in-flight orchestration; heal fallback documented | Invariants 21+22 (N10) |
| 2026-05-05 | Added §12 Error Handling: HybroError hierarchy + propagation rules | Protocol implementors need consistent error contract |
| 2026-05-05 | Added §13 Observability: structlog + trace context seam + metrics table | Cross-module tracing is impossible to retrofit without upfront span design |
| 2026-05-05 | MongoDAL split into domain-scoped Repository Protocols (§4.9.2) | Prevents cross-module raw query coupling; explicit schema ownership |
| 2026-05-05 | Legacy Workflow DELETED (not wrapped) | Dead code; zero value in wrapping; saves 1.5 weeks + maintenance |
| 2026-05-05 | HubAgentEvent split: frontend (status) + HubAgentResponseInternal (orchestration) | Different payloads for different consumers; fixes dual-purpose conflict (fix 2.1) |
| 2026-05-05 | EventPublisher.emit() catches handler exceptions (dead-letter) | Invariant 5 compliance; never propagate to caller (fix 2.2) |
| 2026-05-05 | cancel_inflight_tasks() added to ExecutionEngine Protocol | Shutdown invariant requires Protocol method (fix 2.3) |
| 2026-05-05 | emit_internal + register_internal_handler added to EventPublisher Protocol | Internal events need Protocol-visible methods (fix 2.4) |
| 2026-05-05 | ping() added to all Redis + Vector Protocols | Health check requires real connectivity validation (fix 2.5/2.6) |
| 2026-05-05 | Repository Protocols return dict (intentional) | Type safety at facade boundary; avoids double-validation cost (fix 2.7) |
| 2026-05-05 | No-op import-linter os.getenv contract removed | Contract was always-pass; only AST scan enforces (fix 2.8) |
| 2026-05-05 | traced_create_task() mandatory helper | Centralized background task context propagation without requiring new Phase 7b dependencies (fix 2.9) |
| 2026-05-05 | ExecutionFacade._inflight set + _spawn_orchestration() | In-flight task tracking for graceful shutdown (fix 2.10) |
| 2026-05-05 | Repository impl noted per migration phase | Prevents ambiguity about when Repositories are built (fix 2.11) |
| 2026-05-05 | §15 Key Design Decisions expanded to 21 entries | Was stale at 16; now includes all major decisions (fix 2.12) |
| 2026-05-05 | Added §19 Potential Future Protocol Additions | Documents hardest-to-retrofit seams; validates module boundaries accommodate them |
| 2026-05-17 | Phase 6 Delivery extracted behind C3 `sse_manager` adapter | Delivery owns SSE transport, Redis fan-out, cancellation, dedup, translation, and internal event dispatch; app shell binds facade during startup |
| 2026-05-17 | Phase 6 tracing uses contextvars/task names, not OpenTelemetry links | Implemented helper preserves explicit trace ids without synthesizing ids; OTel span links remain future work |
| 2026-06-04 | Raw-frame isolation removed from active Delivery architecture | Room SSE production emitters use typed `DeliveryEvent` DTOs translated by Delivery |
| 2026-05-17 | Main app shell no longer owns concrete DAL or legacy SSE broker construction | `container.py` owns concrete DAL/Delivery wiring; health uses explicit Delivery KV/PubSub and legacy RedisService fields |
| 2026-06-15 | API route owner protocols moved out of `app_shell.bound` | Route inventory now records common protocols for reusable contracts and module-owned `agent.protocols`, `room.protocols`, and `context_memory.protocols` compatibility contracts for legacy model-shaped routes; `app_shell.bound.py` was removed |
