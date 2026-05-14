# Phase 5 Context & Memory Module Implementation Plan

> **For agentic workers:** REQUIRED: Use `superpowers:subagent-driven-development` if subagents are available, or `superpowers:executing-plans` to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the Context & Memory business module so `context_memory/` owns room memory projection, token-budgeted context assembly, lossless compaction, content storage, and memory search through Common protocols while preserving existing context and compaction behavior exactly.

**Architecture:** Add a `context_memory/` package with `ContextMemoryFacade` implementing `ContextAssembler`, `MemoryManager`, and `MemoryProjector`. The facade depends only on Common DTOs/protocols, `RoomHistoryReader`, `MemoryRepository`, `ContentStorageRepository`, `VectorDAL`, `LLMProvider`, and injected configuration; legacy `services/context_assembly_service.py`, `services/compaction_service.py`, `services/memory_search_service.py`, `services/memory_service.py`, and `services/content_storage_service.py` become C3 migration wrappers that delegate through `bind_facade()` until Phase 9 cleanup.

**Tech Stack:** Python 3.11+, FastAPI, MongoDAL, VectorDAL/Pinecone adapter, LLMProvider embeddings, Pydantic DTOs in `common.dto`, Room protocols from Phase 4, pytest, pytest-asyncio, AST import-boundary tests, golden equivalence fixtures.

---

## Scope

Include:
- Create the `context_memory/` package on `phase-5-context-memory-module`.
- Implement `ContextMemoryFacade` as the concrete implementation for `ContextAssembler`, `MemoryManager`, and `MemoryProjector` in `common/protocols/context_memory_protocols.py`.
- Implement `MemoryMongoRepository` against `MongoDAL` for `room_memories` plus explicit `user_memories` reads required by `MemoryManager.get_user_memories()`, extending `MemoryRepository` only for missing Context & Memory atomic operations.
- Implement `ContentStorageMongoRepository` against `MongoDAL` for `conversation_content`; add a separate `ContentStorageRepository` protocol instead of overloading `MemoryRepository`.
- Port token-budget, stable-prefix, dynamic-suffix, truncation, char-cap, and context metric logic from `services/context_assembly_service.py` without semantic changes.
- Port lossless pointer compaction from `services/compaction_service.py`, including preserve-recent behavior, idempotent content storage, compact pointer rendering, expansion, and compaction stats.
- Port hybrid memory search from `services/memory_search_service.py`, including vector search through `VectorDAL`, Mongo text search, weighted merge, temporal decay, MMR diversity, hydration, and vector deletion.
- Port room-memory lifecycle and projection behavior from `RoomMemoryService`, including user turns, agent response turns, synthesis turns, turn-note enrichment, room summary updates, and room fact deduplication.
- Use `RoomHistoryReader` from Phase 4 for raw message reads in `MemoryProjector.project_message()` and only as a fallback in `ContextAssembler.assemble_context()` when the requested message has not yet been projected into room memory.
- Add a Context & Memory event handler for `MessageCommitted` that calls `project_message()` and then `run_compaction()` where appropriate.
- Keep the current direct compaction call path during Phase 5 while adding the event-handler seam. Do not introduce a new in-process event bus in Phase 5.
- Keep `jobs/compaction_sweep.py` outside `context_memory/` as application-shell scheduling and leader-election infrastructure; bind it to the facade or to a delegated legacy compaction service.
- Add C3 migration adapter bindings for existing service singletons so current callers continue to call legacy module paths.
- Add unit, golden, compatibility, migration adapter, and import-boundary tests.

Exclude:
- Moving `ChatMemoryService` from `services/memory_service.py`. It handles legacy `chat_contexts` by session ID, not room memory; keep it in legacy `services.memory_service` and `modules/MemoryCenter.py` until a separate legacy chat cleanup phase.
- Extracting Room raw message persistence. Phase 4 owns `room/`, and Context & Memory must read through `RoomHistoryReader` only.
- Changing hub liveness. Phase 5 has no hub liveness dependency and must not alter `HubLivenessReader`.
- Creating a new Delivery/EventPublisher implementation. The Common `EventPublisher` protocol already has `emit_internal()` and `register_internal_handler()`, but runtime `MessageCommitted` delivery is not wired. Phase 5 adds a handler seam and keeps direct calls for compatibility.
- Moving `jobs/compaction_sweep.py` into `context_memory/`; the job owns periodic scheduling, active-run skip checks, and leader election, while Context & Memory owns the compaction operation.
- Rewriting API routes or frontend response models beyond legacy adapter conversions.
- Removing `services/`, `modules/`, or legacy singleton imports globally; removal is Phase 9.
- Importing `database.mongodb`, `database.pinecone_db`, `services.openai_service`, `models.*`, `modules.*`, `api.*`, `main`, or `container` from inside `context_memory/**`.

## Current Repo Check

The requested branch starts from current `main` after Phase 4 has landed:
- Branch setup for this plan: `git switch main`, then `git switch -c phase-5-context-memory-module`.
- Current `main` includes Phase 4 artifacts: `room/facade.py`, `RoomHistoryReader`, `RoomDeps`, `create_room_deps()`, and C3 Room bindings.
- Recent history on the checkout used for this plan includes `Address phase 4 room review feedback` and `Merge branch 'phase-4'`.

IMPORTANT: Phase 5 depends on `RoomHistoryReader`. If an implementation checkout does not contain Phase 4, do not import legacy room services from `context_memory/`; instead, add the `RoomHistoryReader` protocol definition and build the new module against fakes until Phase 4 is merged.

IMPORTANT: The prompt names `ContextAssembler.assemble_context(room_id, message_id, token_budget, agent_id=None) -> AssembledContext`, but the current legacy service has `build_supervisor_context()` and `build_agent_execution_context()` returning `ContextAssemblyResult`. Phase 5 should keep the Common protocol unchanged and handle this gap with non-protocol facade compatibility helpers plus legacy adapter conversion. Do not force supervisor/agent-specific fields into the minimal protocol.

IMPORTANT: The existing `MemoryRepository` protocol is too small for the current `room_memories` behavior. Extend it with domain-specific atomic methods only. Add a separate `ContentStorageRepository` for `conversation_content`; do not add content-storage methods to `MemoryRepository`.

IMPORTANT: `MessageCommitted` exists as a DTO and `EventPublisher` has internal-handler methods, but Room does not currently emit runtime `MessageCommitted` events. The current production trigger is a direct call from `modules/RoomMessageCenter.py` to `compaction_service.compact_if_needed(room_id)`. Phase 5 should add the handler seam and keep the direct call path until Delivery/Execution phases wire actual internal delivery.

IMPORTANT: `ChatMemoryService` is excluded from this extraction. Keep `MemoryCenter` and legacy chat-context APIs using `ChatMemoryService` directly.

Branch used for implementation: create `phase-5-context-memory-module` from current `main` after Phase 4 is present. If the branch already exists, verify it is based on current `main` before implementation.

## File Inventory

Create:
- `context_memory/__init__.py`: exports `ContextMemoryFacade`, `MemoryMongoRepository`, and `ContentStorageMongoRepository`.
- `context_memory/facade.py`: concrete implementation of `ContextAssembler`, `MemoryManager`, and `MemoryProjector`; owns orchestration between assembly, projection, compaction, content storage, and search.
- `context_memory/config.py`: token budget, compaction, and memory search configuration using `common.config.settings` and injected overrides for tests.
- `context_memory/models.py`: internal dataclasses/enums for `AssemblyResult`, `TruncationReason`, normalized room memory, conversation turns, content references, and search ranking records. These are not Pydantic legacy `models.*`.
- `context_memory/translators.py`: pure dict/DTO conversion helpers for room memory docs, raw room messages, turns, summaries, facts, content references, and Common DTOs.
- `context_memory/assembly.py`: port of stable-prefix/dynamic-suffix builders, token budget selection, truncation, and char-limit logic.
- `context_memory/projection.py`: raw `RoomMessageInfo` to conversation-turn projection plus legacy user/agent/synthesis turn helpers.
- `context_memory/summary.py`: room summary extraction prompt construction, LLM JSON parsing, fact deduplication, and summary update payloads.
- `context_memory/compaction.py`: lossless compaction helpers and compaction result translation.
- `context_memory/search.py`: hybrid vector+keyword memory search, score merge, temporal decay, MMR, hydration, indexing, and delete-index helpers.
- `context_memory/content_storage.py`: content hash, content-reference expansion, idempotent content storage helpers, and content stats.
- `context_memory/events.py`: `ContextMemoryEventHandler` with `handle_message_committed(event: MessageCommitted)`.
- `context_memory/repository/__init__.py`: exports repository implementations.
- `context_memory/repository/mongo.py`: `MemoryRepository` and `ContentStorageRepository` implementations using `MongoDAL`.
- `common/mongo_ids.py`: Common helper for safe ObjectId-string fallback queries used by content storage repository legacy lookup.
- `tests/test_context_memory_protocols.py`: runtime protocol conformance, exports, package list, container assembly, and import-boundary tests.
- `tests/test_context_memory_repository.py`: repository tests against fake `MongoCollection` instances.
- `tests/test_context_memory_projection.py`: raw message projection and legacy turn helper tests.
- `tests/test_context_memory_assembly.py`: direct assembly unit tests ported from `tests/test_context_assembly_service.py`.
- `tests/test_context_memory_assembly_golden.py`: golden equivalence tests comparing legacy service outputs to new facade/assembly outputs.
- `tests/test_context_memory_compaction.py`: compaction, expansion, stats, and content storage tests.
- `tests/test_context_memory_search.py`: vector, keyword, merge, temporal decay, MMR, hydration, indexing, and deletion tests.
- `tests/test_context_memory_facade.py`: facade behavior with fake repositories, fake `RoomHistoryReader`, fake `VectorDAL`, and fake `LLMProvider`.
- `tests/test_context_memory_events.py`: `MessageCommitted` handler behavior and idempotency tests.
- `tests/test_context_memory_adapters.py`: C3 fail-fast and delegation tests for legacy service wrappers.

Delete if porting from another branch:
- Any `context_memory/**` scaffold that imports `agent`, `room`, `services`, `modules`, `api`, `database`, `models`, `main`, `container`, `config`, `llm_gateway`, or Pinecone/OpenAI concrete clients.
- Any temporary event bus implementation under `context_memory/`; Phase 5 should not create a competing internal bus.

Modify:
- `common/protocols/repository_protocols.py`: extend `MemoryRepository` with domain-specific room memory operations needed by projection, compaction, and summary updates.
- `common/protocols/context_memory_protocols.py`: keep `ContextAssembler`, `MemoryManager`, and `MemoryProjector` signatures unchanged unless Task 2 proves a missing method is unavoidable; prefer non-protocol facade helpers for legacy compatibility.
- `common/protocols/dal_protocols.py`: add a small Common write-operation DTO/protocol method only if exact ordered compaction updates cannot be expressed through existing `MongoCollection.update_one(..., **kwargs)`.
- `common/protocols/__init__.py`: export `ContentStorageRepository` and any new Common protocol names.
- `common/dto/context_memory.py`: add fields only if golden tests show current DTOs cannot carry legacy metadata without unsafe `metadata` overloading.
- `common/dto/__init__.py`: export any added DTOs.
- `pyproject.toml`: add `context_memory` and `context_memory.repository` to `[tool.setuptools].packages`.
- `container.py`: add `ContextMemoryDeps` and `create_context_memory_deps()` alongside `AgentDeps` and `RoomDeps`.
- `main.py`: build ContextMemory deps after Room deps and bind legacy service adapters before background work can run.
- `services/context_assembly_service.py`: convert public methods to C3 facade delegation while retaining legacy dataclasses and response shape.
- `services/compaction_service.py`: convert public methods to C3 facade delegation while retaining legacy `CompactionResult` conversion.
- `services/memory_search_service.py`: convert public methods to C3 facade delegation while retaining legacy `MemorySearchResponse` conversion.
- `services/memory_service.py`: add facade binding to `RoomMemoryService` only; leave `ChatMemoryService` unchanged.
- `services/content_storage_service.py`: convert public content-storage methods to C3 facade/repository delegation where needed by tests and legacy callers.
- `modules/RoomMessageCenter.py`: keep direct `_trigger_compaction_safe()` call, but route through the bound legacy `compaction_service` wrapper; optionally bind the ContextMemory facade for future event-handler registration if a local pattern exists.
- `modules/QueueExecutor.py`: no direct import of `context_memory`; continue calling `room_memory_service`, which delegates after bind.
- `modules/SupervisorExecutor.py`: no direct import of `context_memory`; continue calling `room_memory_service`, which delegates after bind.
- `services/room_services.py`: keep existing calls to legacy context/memory service singletons; they delegate after bind.
- `jobs/compaction_sweep.py`: stay outside `context_memory/`; either keep importing `services.compaction_service` after it delegates or add a `bind_projector()` seam for `MemoryProjector`.
- Existing Context & Memory tests: update to bind fake facades where they construct migrated legacy services directly.

Reference-only:
- `docs/MODULAR_DECOUPLING_DESIGN.md`: Phase 5 description, Context & Memory protocols, `MemoryRepository`, `ContextMemoryDeps`, internal event design, and import rules.
- `docs/CONTEXT_MEMORY_SYSTEM_DESIGN.md`: context assembly, compaction, search, turn notes, content storage, and summary design.
- `docs/superpowers/plans/2026-05-11-phase-4-room-module.md`: exact plan structure and C3 migration style.
- `common/protocols/context_memory_protocols.py`: target facade protocols.
- `common/dto/context_memory.py`: target facade DTOs.
- `common/protocols/repository_protocols.py`: existing `MemoryRepository`.
- `common/protocols/room_protocols.py`: `RoomHistoryReader`.
- `common/protocols/dal_protocols.py`: `MongoDAL`, `MongoCollection`, `VectorDAL`.
- `common/protocols/llm_protocols.py`: `LLMProvider`.
- `common/protocols/delivery_protocols.py`: existing `EventPublisher.emit_internal()` and handler registration.
- `common/dto/internal_events.py`: `MessageCommitted`.
- `common/utils/context_utils.py`: ONLY the pure subset is allowed from `context_memory/**`: `estimate_tokens()`, `extract_turn_notes()`, `MAX_CONTEXT_CHARS`, `CHARS_PER_TOKEN_ESTIMATE`, `MAX_HISTORY_TURNS`, `MAX_SUMMARY_CHARS`, `LLM_TURN_NOTES_THRESHOLD`, `clean_mention_format()`. Functions that have deferred imports from `models.*` or `services.*` (`add_turn_to_history`, `extract_turn_notes_llm`, `build_turn_content`, turn-rendering helpers) must NOT be called from `context_memory/**`. Re-implement needed logic as pure equivalents in `context_memory/projection.py`.
- `common/config/settings.py`: current config defaults for token budget, compaction, and memory search.
- `services/context_assembly_service.py`: exact token budget and stable/dynamic context behavior.
- `services/compaction_service.py`: lossless compaction behavior and pointer semantics.
- `services/memory_search_service.py`: hybrid search behavior and ranking.
- `services/memory_service.py`: room memory lifecycle, turn persistence, summary update, and ChatMemoryService exclusion.
- `services/content_storage_service.py`: content storage behavior.
- `modules/RoomMessageCenter.py`, `modules/QueueExecutor.py`, `modules/SupervisorExecutor.py`, `services/room_services.py`: migration callers to preserve.
- `jobs/compaction_sweep.py`: application-shell sweep job.
- `agent/facade.py`, `room/facade.py`, `container.py`, `services/agent_service.py`, `services/room_services.py`, `main.py`: Phase 3/4 facade and adapter patterns.
- Existing tests: `tests/test_context_assembly_service.py`, `tests/test_compaction_service.py`, `tests/test_memory_search_service.py`, `tests/test_context_memory_bugfixes.py`, `tests/test_phase5_supervisor_integration.py`.

## Dependency Diagram

```text
modules.RoomMessageCenter / QueueExecutor / SupervisorExecutor / services.room_services
  -> legacy service singletons                         migration adapters
    -> context_memory.facade.ContextMemoryFacade       protocol implementation
      -> common.dto.*
      -> common.protocols.RoomHistoryReader            raw message reads only
      -> common.protocols.MemoryRepository             room_memories
      -> common.protocols.ContentStorageRepository     conversation_content
      -> common.protocols.VectorDAL                    vector search/upsert/delete
      -> common.protocols.LLMProvider                  embeddings + summary/notes enrichment
      -> context_memory.assembly
      -> context_memory.projection
      -> context_memory.compaction
      -> context_memory.search
      -> context_memory.summary

Room (Phase 4)
  -> RoomHistoryReader                                 read-only protocol consumed by C&M
  -> MessageCommitted                                  future internal event, not fully wired today

Delivery (Phase 6/7)
  -> EventPublisher.emit_internal(MessageCommitted)    future runtime delivery
  -> ContextMemoryEventHandler.handle_message_committed

jobs.compaction_sweep
  -> services.compaction_service or MemoryProjector    scheduling/leader election only
```

## Forbidden/Allowed Imports

Forbidden from `context_memory/**`:
- `agent`
- `room`
- `services`
- `modules`
- `api`
- `database`
- `models`
- `main`
- `container`
- `config`
- `llm_gateway`
- `a2a_adapter`
- `infrastructure`
- `pinecone`
- `openai`
- `pymongo`
- `motor`

Allowed in `context_memory/**`:
- stdlib
- `common.*`
- relative imports inside `context_memory`

Additional import-boundary detail:
- `context_memory/repository/mongo.py` may reference `common.protocols.MongoDAL`, `MongoCollection`, `MemoryRepository`, and `ContentStorageRepository` in constructor signatures.
- `context_memory/**` must not import the concrete `dal.mongo.client.MongoDALImpl`; `container.py` owns concrete construction.
- `context_memory/**` must not import `room/**`; all room reads go through `RoomHistoryReader`.
- `context_memory/**` must not import `services.openai_service`; embedding and structured generation go through `LLMProvider`.
- `context_memory/**` must not import `database.pinecone_db`; vector calls go through `VectorDAL`.
- Legacy adapters in `services/**`, `modules/**`, `jobs/**`, and `main.py` may import `context_memory` during migration, but `context_memory` must never import them.

## Interface Definitions

### ContextMemoryFacade Constructor

Use explicit dependency injection. Do not construct singletons inside `context_memory/`.

```python
from collections.abc import Callable
from datetime import datetime
from typing import Any

from common.protocols import (
    ContentStorageRepository,
    LLMProvider,
    MemoryRepository,
    RoomHistoryReader,
    VectorDAL,
)

class ContextMemoryFacade:
    def __init__(
        self,
        *,
        memory_repository: MemoryRepository,
        content_repository: ContentStorageRepository,
        room_history_reader: RoomHistoryReader,
        vector: VectorDAL,
        llm_provider: LLMProvider,
        id_factory: Callable[[], str],
        now: Callable[[], datetime],
        token_budget: TokenBudgetConfig | None = None,
        compaction_config: CompactionConfig | None = None,
        search_config: MemorySearchConfig | None = None,
        tracer: Any | None = None,
    ) -> None: ...
```

Do not include old delegation parameters such as `database_service`, `mongodb`, `pinecone_db`, `openai_service`, `room_services`, `room_message_center`, or `compaction_service`. The facade owns Context & Memory behavior directly.

Protocol methods implemented exactly:
- `assemble_context(room_id: str, message_id: str, token_budget: int, agent_id: str | None = None) -> AssembledContext`
- `get_room_memory(room_id: str) -> RoomMemoryInfo | None`
- `search_memory(room_id: str, query: str, limit: int = 10) -> list[MemorySearchResult]`
- `get_user_memories(user_id: str) -> list[UserMemory]`
- `delete_room_memory(room_id: str) -> bool`
- `project_message(room_id: str, message_id: str) -> None`
- `run_compaction(room_id: str) -> CompactionResult`

Non-protocol compatibility helpers allowed on `ContextMemoryFacade`:
- `assemble_supervisor_context_from_memory(room_memory_doc: dict, current_task: str, *, agent_registry: list[dict] | None = None, max_turns: int = 5, memory_search_results: list | None = None) -> AssembledContext`
- `assemble_agent_execution_context_from_memory(room_memory_doc: dict, current_task: str, *, agent_id: str | None = None, agent_name: str | None = None, room_awareness: str | None = None, quoted_text: str | None = None, include_system_instruction: bool = True) -> AssembledContext`
- `initialize_or_update_room_memory(room_id: str, *, memory_content: str | None, room_agent_set: dict | None, user_id: str | None, attachments: list | None = None) -> RoomMemoryInfo`
- `add_agent_response_to_memory(room_id: str, agent_id: str, agent_name: str, response_text: str, was_successful: bool = True) -> bool`
- `add_synthesis_to_history(room_id: str, synthesis_text: str, trajectory: Any | None = None) -> str | None`
- `update_room_summary(room_id: str, synthesis_text: str, synthesis_turn_id: str | None = None) -> bool`
- `compact_if_needed(room_id: str) -> CompactionResult | None`
- `expand_turn_content(room_id: str, turn_id: str) -> str | None`
- `fetch_turn_content(room_id: str, turn_id: str) -> str`
- `get_compaction_stats(room_id: str) -> dict`
- `index_turn_for_search(room_id: str, turn_doc: dict) -> bool`
- `delete_room_index(room_id: str) -> bool`
- `content_upsert_full_content(room_id: str, turn_id: str, content: str, content_type: str, turn_notes: dict | None = None) -> str`
- `content_get_content_by_document_id(document_id: str) -> str | None`
- `content_get_content_by_turn_id(room_id: str, turn_id: str) -> str | None`
- `content_expand_mongodb_reference(content_ref: dict, turn_id: str) -> str`
- `content_delete_content_by_turn_id(room_id: str, turn_id: str) -> bool`
- `content_delete_content_by_room_id(room_id: str) -> int`
- `content_get_content_stats_for_room(room_id: str) -> dict`

These helpers exist only to keep legacy callers stable. New cross-module consumers should use the three Common protocols.

Content-storage compatibility helpers convert repository dicts to legacy service return shapes. `content_get_content_by_document_id()` and `content_get_content_by_turn_id()` return `doc["content"]` or `None`, never the raw repository dict. `content_expand_mongodb_reference()` accepts a primitive dict form of the legacy `ContentReference`, supports only `storage_type="mongodb"`, raises the Context & Memory `ContentExpiredError` when missing, and leaves S3/URL behavior to the legacy service adapter.

Assembly compatibility helpers must be synchronous and pure. They accept an already-loaded room memory document and optional precomputed memory search results; they must not call `MemoryRepository`, `RoomHistoryReader`, `LLMProvider`, or search helpers. Legacy `build_supervisor_context()` and `build_agent_execution_context()` are synchronous and current callers do not await them, so repository-backed loading belongs only in async protocol methods such as `assemble_context()`.

### Context Assembly Output Mapping

`AssembledContext` does not have the legacy `context` field. Preserve both the new DTO shape and legacy behavior with ordered blocks plus metadata:

```python
AssembledContext(
    room_id=room_id,
    blocks=[
        ContextBlock(
            block_id="stable_prefix",
            room_id=room_id,
            content=stable_prefix,
            token_count=stable_prefix_tokens,
            block_type="stable_prefix",
        ),
        ContextBlock(
            block_id="dynamic_suffix",
            room_id=room_id,
            content=dynamic_suffix,
            token_count=dynamic_suffix_tokens,
            block_type="dynamic_suffix",
        ),
    ],
    total_tokens=total_tokens,
    metadata={
        "context": assembled_context_string,
        "occupancy_pct": occupancy_pct,
        "was_truncated": was_truncated,
        "truncation_reason": truncation_reason,
        "turns_included": turns_included,
        "turns_truncated": turns_truncated,
        "stable_prefix_tokens": stable_prefix_tokens,
        "dynamic_suffix_tokens": dynamic_suffix_tokens,
        "mode": "supervisor" | "agent",
    },
)
```

Legacy `services.context_assembly_service.ContextAssemblyResult` stays in the service module. The adapter converts from `AssembledContext.metadata` back to the legacy dataclass.

### MemoryRepository Additions

Start from the existing protocol:

```python
class MemoryRepository(Protocol):
    async def get_room_memory(self, room_id: str) -> dict | None: ...
    async def upsert_room_memory(self, room_id: str, memory: dict) -> None: ...
    async def get_user_memories(self, user_id: str) -> list[dict]: ...
    async def delete_room_memory(self, room_id: str) -> bool: ...
```

Verify completeness in Task 2. If missing, add only these domain-specific methods:

```python
async def create_room_memory(self, memory: dict) -> str: ...
async def get_room_memory_by_memory_id(self, memory_id: str) -> dict | None: ...
async def update_room_memory_by_room_id(self, room_id: str, updates: dict) -> bool: ...
async def update_room_memory_by_memory_id(self, memory_id: str, updates: dict) -> bool: ...
async def delete_room_memory_by_memory_id(self, memory_id: str) -> bool: ...

async def push_and_trim_conversation_turn(
    self,
    room_id: str,
    turn: dict,
    *,
    max_turns: int,
    summary_stub: str,
    max_summary_chars: int,
) -> tuple[bool, bool]: ...

async def update_turn_notes(self, room_id: str, turn_id: str, turn_notes: dict) -> bool: ...
async def get_room_summary_projection(self, room_id: str) -> dict | None: ...
async def update_room_summary_atomic(
    self,
    room_id: str,
    room_summary: dict,
    *,
    new_facts: list[dict] | None = None,
    max_facts: int = 50,
) -> bool: ...

async def compact_turns_bulk(self, room_id: str, compacted_turns: list[dict]) -> bool: ...
async def list_room_ids_with_memory(self, limit: int = 1000) -> list[str]: ...
```

Keep repository inputs and outputs as dicts. The repository must not return `models.memory.RoomMemory`, `ConversationTurn`, or other legacy models.

`push_and_trim_conversation_turn()` returns `(modified, matched)`. Implement it with `MongoCollection.find_one_and_update(..., upsert=False, return_document=True)` and a minimal projection, not `MongoCollection.update_one()`, because the Common `update_one()` contract currently returns only `bool` and does not expose `matched_count`. Treat `matched = returned_doc is not None`; treat `modified = matched` because the pipeline always pushes a new turn and updates memory metadata when a room memory exists. Write failures should raise. Test fakes must cover both `None` return for missing room and returned document for successful mutation.

### ContentStorageRepository Protocol

Add this protocol to `common/protocols/repository_protocols.py` or a new Common protocol file, then export it from `common.protocols`:

```python
@runtime_checkable
class ContentStorageRepository(Protocol):
    async def upsert_full_content(
        self,
        *,
        document_id: str,
        room_id: str,
        turn_id: str,
        content: str,
        content_type: str,
        content_hash: str,
        stored_at: datetime,
        expires_at: datetime | None = None,
        turn_notes: dict | None = None,
    ) -> str: ...

    async def get_content_by_document_id(self, document_id: str) -> dict | None: ...
    async def get_content_by_turn_id(self, room_id: str, turn_id: str) -> dict | None: ...
    async def delete_content_by_turn_id(self, room_id: str, turn_id: str) -> bool: ...
    async def delete_content_by_room_id(self, room_id: str) -> int: ...
    async def get_content_stats_for_room(self, room_id: str) -> dict: ...
    async def text_search(self, room_id: str, query: str, limit: int = 50) -> list[dict]: ...
    async def hydrate_turn_notes(self, room_id: str, turn_ids: list[str]) -> list[dict]: ...
```

`ContentStorageMongoRepository` owns the `conversation_content` collection. It may use deterministic `find_one_and_update(..., upsert=True, return_document=True)` through the `MongoCollection` protocol to preserve idempotent upsert semantics without exposing Motor or PyMongo to `context_memory/**`.

`text_search()` must preserve legacy BM25 coverage over full stored `content` plus `turn_notes.keywords`, `turn_notes.entities`, and `turn_notes.one_liner`.

The repository method intentionally requires `document_id`, `content_hash`, `stored_at`, and `expires_at`. Do not call it directly from compaction with only the raw turn fields. Add a pure `context_memory.content_storage.store_full_content(...)` helper that computes `document_id = f"conversation_content:{room_id}:{turn_id}"`, `content_hash = hash_content(content)`, `stored_at = now()`, and `expires_at` from compaction config, then calls `ContentStorageRepository.upsert_full_content(...)` with the full argument set.

Document ID strategy:
- New `conversation_content` documents MUST store a stable string `document_id` field. The returned document id and `content_ref.document_id` should use this string field, not rely solely on Mongo `_id`.
- This intentionally means new compact pointer id values may differ from legacy pointers that used Mongo-returned `_id` strings. Preserve the legacy pointer format and prove expandability; do not require exact pointer id equality for newly compacted turns.
- If `upsert_full_content()` matches an existing legacy document by `(room_id, turn_id)` that lacks `document_id`, it MUST backfill the stable `document_id` on that existing document and return the stable id. Do not return the legacy Mongo `_id` in this case, because new compact pointers will use the stable id and must expand through `get_content_by_document_id()`.
- `get_content_by_document_id(document_id)` must query `{"document_id": document_id}` first.
- For legacy compacted turns whose `content_ref.document_id` is a Mongo ObjectId string and whose stored document lacks `document_id`, add `common/mongo_ids.py` with `object_id_query(document_id: str) -> dict | None`. That Common helper may contain the optional `bson.ObjectId` import. `context_memory/**` may import the Common helper but must not import `bson` directly.
- Repository tests must cover both stable string `document_id` lookup and legacy `_id`/ObjectId-string fallback.

### UserMemory DTO Mapping

Common `UserMemory` requires `memory_id` and `content`, but the legacy `models.memory.UserMemory` document currently has neither. Map legacy docs deterministically:

```python
UserMemory(
    user_id=doc["user_id"],
    memory_id=doc.get("memory_id") or f"user_memory:{doc['user_id']}",
    content=render_user_memory_content(doc),
    created_at=doc.get("created_at"),
    metadata={
        "preferences": doc.get("preferences", {}),
        "preferred_agents": doc.get("preferred_agents", []),
        "communication_style": doc.get("communication_style"),
        "user_facts": doc.get("user_facts", []),
        "last_active_at": doc.get("last_active_at"),
        "total_interactions": doc.get("total_interactions", 0),
    },
)
```

`render_user_memory_content(doc)` should produce a stable human-readable string from `communication_style`, sorted preferences, preferred agents, and user fact contents. If all fields are empty, return `""`. Add DTO mapping tests before implementation. Do not change the Common DTO unless these tests prove the existing shape is unusable.

### ContextMemoryDeps Sub-Container

Extend `container.py` rather than creating a parallel container:

```python
from dataclasses import dataclass

from common.protocols import ContextAssembler, MemoryManager, MemoryProjector

@dataclass(frozen=True)
class ContextMemoryDeps:
    context_assembler: ContextAssembler
    memory_manager: MemoryManager
    memory_projector: MemoryProjector
```

Because one `ContextMemoryFacade` implements all three protocols, the initial assembly can bind all fields to the same instance.

### Event Handler

Add the handler now, but do not create a new bus:

```python
class ContextMemoryEventHandler:
    def __init__(self, projector: MemoryProjector) -> None:
        self._projector = projector

    async def handle_message_committed(self, event: MessageCommitted) -> None:
        await self._projector.project_message(event.room_id, event.message_id)
        await self._projector.run_compaction(event.room_id)
```

`MemoryProjector.run_compaction()` is threshold-gated in Phase 5: it must check compaction eligibility and return a zero-count `CompactionResult` when compaction is not needed. The event handler must not force compaction after every message.

If `EventPublisher` is available during startup, register the handler:

```python
event_publisher.register_internal_handler(
    "message_committed",
    context_memory_event_handler.handle_message_committed,
)
```

This registration is future-facing only until Room/Execution actually emit `MessageCommitted`. Direct legacy calls remain the live path in Phase 5.

## Implementation Order

Parallelization note: Tasks 2, 3, 4, 5, 6, and 7 can run in parallel only if they keep writes to helper modules and tests. `context_memory/facade.py` is owned by Task 8 as the integration point; earlier workers should not edit it. Do not parallelize edits to the same legacy service file. The golden assembly tests in Task 4 should not be updated in parallel with context assembly code unless ownership is split between fixture generation and implementation.

### Task 0: Branch, Baseline, and Context Memory Inventory Reconciliation

**Files:**
- Maybe create: `context_memory/**`
- Maybe modify: `container.py`
- No behavior changes yet

- [ ] **Step 1: Verify branch starts from current main**

```bash
git status --short --branch
git log --oneline --decorate -5
```

Expected: branch is `phase-5-context-memory-module` created from current `main`; worktree is clean except planned changes.

- [ ] **Step 2: Verify Phase 4 artifacts exist**

```bash
test -f room/facade.py
test -f room/repository/mongo.py
test -f container.py
rg -n "class RoomHistoryReader|def create_room_deps|class RoomDeps" common/protocols/room_protocols.py container.py
```

Expected: all commands exit 0. If any fail, do not start Phase 5; build against protocol fakes only or first reconcile Phase 4 into the branch.

- [ ] **Step 3: Verify Context & Memory Common contracts exist**

```bash
test -f common/protocols/context_memory_protocols.py
test -f common/dto/context_memory.py
rg -n "class ContextAssembler|class MemoryManager|class MemoryProjector" common/protocols/context_memory_protocols.py
rg -n "class AssembledContext|class CompactionResult|class ContextBlock" common/dto/context_memory.py
```

Expected: current Common contracts exist and match the design doc.

- [ ] **Step 4: Confirm no existing `context_memory/` scaffold**

```bash
git ls-tree -r --name-only HEAD -- context_memory
```

Expected: either no `context_memory/` files exist, or any existing scaffold is inspected before use.

- [ ] **Step 5: If scaffold exists, inspect before porting**

```bash
git show HEAD:context_memory/facade.py | sed -n '1,220p'
git show HEAD:context_memory/repository/mongo.py | sed -n '1,220p'
```

Expected: confirm whether the scaffold delegates to legacy services. Do not keep any `context_memory/**` import from `services`, `modules`, `api`, `database`, `models`, `room`, `agent`, `main`, `container`, `config`, `llm_gateway`, `pinecone`, or `openai`.

- [ ] **Step 6: Check referenced test-file availability**

```bash
for path in \
  tests/test_context_assembly_service.py \
  tests/test_compaction_service.py \
  tests/test_memory_search_service.py \
  tests/test_context_memory_bugfixes.py \
  tests/test_phase5_supervisor_integration.py \
  tests/test_module_queue_executor.py \
  tests/test_module_supervisor_executor.py \
  tests/test_module_room_message_center.py
do
  test -f "$path" && printf "exists %s\n" "$path" || printf "missing %s\n" "$path"
done
```

Expected: record which files already exist. For missing files, create focused replacements in the task that first references them rather than silently dropping coverage.

- [ ] **Step 7: Run baseline tests for completed phases and existing Context & Memory behavior**

```bash
uv run python -m pytest tests/test_common_foundation.py tests/test_room_protocols.py tests/test_room_facade.py tests/test_context_assembly_service.py tests/test_compaction_service.py tests/test_memory_search_service.py tests/test_context_memory_bugfixes.py -q
```

Expected: PASS before Phase 5 changes, or document existing failures before editing Context & Memory code.

### Task 1: Add Failing Context Memory Protocol, Packaging, and Boundary Tests

**Files:**
- Create: `tests/test_context_memory_protocols.py`
- Modify: `pyproject.toml`
- Modify: `container.py`

- [ ] **Step 1: Add runtime protocol conformance test**

Assert:
- `ContextMemoryFacade(...)` is a `ContextAssembler`.
- `ContextMemoryFacade(...)` is a `MemoryManager`.
- `ContextMemoryFacade(...)` is a `MemoryProjector`.
- `MemoryMongoRepository(mongo=fake_mongo)` is a `MemoryRepository`.
- `ContentStorageMongoRepository(mongo=fake_mongo)` is a `ContentStorageRepository`.

- [ ] **Step 2: Add top-level export test**

Assert:
- `from context_memory import ContextMemoryFacade` works.
- `from context_memory import MemoryMongoRepository` works.
- `from context_memory import ContentStorageMongoRepository` works.
- `from context_memory.repository import MemoryMongoRepository, ContentStorageMongoRepository` works.
- `context_memory.__all__` is explicit and stable.

- [ ] **Step 3: Add package-list test**

Assert `pyproject.toml` includes:
- `context_memory`
- `context_memory.repository`

- [ ] **Step 4: Add import-boundary AST test**

Use the helper style from `tests/test_agent_protocols.py` or `tests/test_room_protocols.py`. Allowed roots:
- `__future__`
- stdlib roots from `sys.stdlib_module_names`
- `common`
- `context_memory`

Forbidden roots:
- `a2a_adapter`
- `agent`
- `api`
- `config`
- `container`
- `database`
- `infrastructure`
- `llm_gateway`
- `main`
- `models`
- `modules`
- `openai`
- `pinecone`
- `pymongo`
- `room`
- `services`

- [ ] **Step 5: Add ContextMemoryDeps container test**

Assert:
- `create_context_memory_deps(...)` returns one facade instance bound to all three protocol fields.
- `ContextMemoryDeps.context_assembler is ContextMemoryDeps.memory_manager`.
- `ContextMemoryDeps.context_assembler is ContextMemoryDeps.memory_projector`.
- The constructor accepts fakes for `RoomHistoryReader`, `MemoryRepository`, `ContentStorageRepository`, `VectorDAL`, and `LLMProvider`.

- [ ] **Step 6: Run and verify failure**

```bash
uv run python -m pytest tests/test_context_memory_protocols.py -q
```

Expected before implementation: FAIL because `context_memory` package, repositories, or `ContextMemoryDeps` are missing.

### Task 2: Extend and Implement Memory and Content Repositories

**Files:**
- Create: `common/mongo_ids.py`
- Modify: `common/protocols/repository_protocols.py`
- Modify: `common/protocols/dal_protocols.py` only if exact ordered compact updates require a Common operation DTO
- Modify: `common/protocols/__init__.py`
- Create/modify: `context_memory/repository/mongo.py`
- Create/modify: `context_memory/repository/__init__.py`
- Create: `tests/test_context_memory_repository.py`

- [ ] **Step 1: Write memory repository contract tests**

Cover:
- `MemoryMongoRepository(mongo=fake_mongo)` calls `mongo.collection("room_memories")` and `mongo.collection("user_memories")`.
- `get_room_memory(room_id)` queries `{"room_id": room_id}` and returns a raw dict.
- `get_room_memory_by_memory_id(memory_id)` queries `{"memory_id": memory_id}`.
- `get_user_memories(user_id)` queries `user_memories` by `{"user_id": user_id}` and returns raw dicts for the Common `MemoryManager.get_user_memories()` protocol.
- `create_room_memory(memory)` inserts the supplied dict and returns the stored insert id.
- `upsert_room_memory(room_id, memory)` writes with upsert semantics.
- `update_room_memory_by_room_id()` applies `$set` by `room_id`.
- `update_room_memory_by_memory_id()` applies `$set` by `memory_id`.
- `delete_room_memory(room_id)` deletes by `room_id`.
- `delete_room_memory_by_memory_id(memory_id)` deletes by `memory_id`.
- Repository outputs stay raw dicts, not `models.memory.RoomMemory`.

- [ ] **Step 2: Write atomic room-memory mutation tests**

Cover:
- `push_and_trim_conversation_turn()` preserves the existing pipeline update shape from `database/mongodb.py`.
- The method returns `(modified, matched)` and distinguishes missing room from write failure.
- The method uses `find_one_and_update(..., upsert=False, return_document=True)` rather than `update_one()`, deriving `matched` from whether a document is returned and `modified` from `matched`.
- `update_turn_notes()` uses positional `$` update for `memory_content.conversation_history.$.turn_notes`.
- `get_room_summary_projection()` fetches only `room_summary` and `room_facts`.
- `update_room_summary_atomic()` sets `room_summary`, optionally pushes new facts, and slices to `max_facts`.
- `compact_turns_bulk()` marks only matching full turns compact, clears `content`, sets `content_ref`, sets `estimated_tokens_compact`, increments `total_compactions`, and updates `last_activity_at`.
- `list_room_ids_with_memory()` returns room ids from `room_memories` without loading full documents.

- [ ] **Step 3: Write content storage repository tests**

Cover:
- `ContentStorageMongoRepository(mongo=fake_mongo)` calls `mongo.collection("conversation_content")`.
- `upsert_full_content()` is idempotent for `(room_id, turn_id)`, stores a stable string `document_id` field, and returns the existing `document_id` on repeat.
- `upsert_full_content()` backfills `document_id` and returns the stable id when it finds an existing legacy `(room_id, turn_id)` document missing `document_id`.
- `get_content_by_document_id()` first queries `{"document_id": document_id}` for new documents.
- `get_content_by_document_id()` falls back to a legacy `_id` lookup using `common.mongo_ids.object_id_query(document_id)` for existing compacted content that only has an ObjectId string in `content_ref.document_id`.
- `get_content_by_turn_id()` queries `{"room_id": room_id, "turn_id": turn_id}`.
- `delete_content_by_turn_id()` deletes by room and turn.
- `delete_content_by_room_id()` deletes all stored content for a room and returns count.
- `get_content_stats_for_room()` mirrors current aggregate output.
- `text_search()` performs Mongo `$text` query with score projection and limit over `content`, `turn_notes.keywords`, `turn_notes.entities`, and `turn_notes.one_liner`.
- `hydrate_turn_notes()` fetches `turn_id` and `turn_notes` for a set of turn ids.

- [ ] **Step 4: Extend repository protocols only as needed**

Use the additions listed in "MemoryRepository Additions" and "ContentStorageRepository Protocol". Keep protocols domain-scoped; do not expose generic `find(query)`, raw collection access, or direct Motor/PyMongo result objects.

- [ ] **Step 5: Implement `MemoryMongoRepository`**

Implementation notes:
- Constructor accepts `mongo: MongoDAL`, optional `collection_name: str = "room_memories"`, and optional `user_collection_name: str = "user_memories"`.
- Store `self._memories = mongo.collection(collection_name)`.
- Store `self._user_memories = mongo.collection(user_collection_name)`.
- Use `MongoCollection.find_one`, `find`, `insert_one`, `update_one`, `find_one_and_update`, `delete_one`, and `aggregate`.
- For `push_and_trim_conversation_turn()`, use `find_one_and_update()` to preserve matched/missing-room semantics; do not rely on `update_one()` because it returns only `bool`.
- Do not import `database.mongodb`, `pymongo`, `motor`, or `models.memory`.
- For `compact_turns_bulk()`, prefer Common protocol support for ordered update operations if added; otherwise use ordered `update_one(..., array_filters=[...])` calls and document the equivalence to the current ordered `bulk_write`.

NOTE: `MongoCollection.update_one(query, update, **kwargs)` passes `array_filters` through `**kwargs`. Fake `MongoCollection` implementations in tests MUST support `array_filters` kwarg to test compaction updates. If this implicit contract is insufficient, extend the `MongoCollection` protocol with an explicit `array_filters: list[dict] | None = None` parameter in Phase 5. Document the decision in Task 2 Step 4.

- [ ] **Step 6: Implement `ContentStorageMongoRepository`**

Implementation notes:
- Constructor accepts `mongo: MongoDAL` and optional `collection_name: str = "conversation_content"`.
- Preserve unique `(room_id, turn_id)` upsert semantics.
- Preserve current `content_hash`, `stored_at`, `expires_at`, and `turn_notes` fields.
- Store and return a stable string `document_id` field for new documents. If the collection already has legacy docs without `document_id`, support ObjectId-string fallback through `common.mongo_ids.object_id_query()`.
- For an existing legacy document matched by `(room_id, turn_id)` but missing `document_id`, backfill the stable `document_id` in the upsert path and return that stable id. Use `find_one_and_update()` with `$set` for `document_id` plus `$setOnInsert` for content fields, or an equivalent two-step operation covered by tests.
- Do not import `services.content_storage_service`, `database.mongodb`, `bson`, `pymongo`, or `models.compaction` inside `context_memory/**`.
- Text index creation: The `conversation_content` collection requires a MongoDB text index on full content and compact turn notes for BM25 keyword search: `content`, `turn_notes.keywords`, `turn_notes.entities`, and `turn_notes.one_liner`. Use one of:
  - Preferred: Accept an optional `IndexRegistry` in the repository constructor and register the index spec. Application shell calls `index_registry.ensure_all()` during startup.
  - Alternative: Document that the index is pre-existing in production and test fakes simulate text search over both `content` and the three `turn_notes` fields without a real index.
  Document the chosen approach in tests.

- [ ] **Step 7: Run repository tests**

```bash
uv run python -m pytest tests/test_context_memory_repository.py tests/test_context_memory_protocols.py -k "repository or package" -q
```

Expected: repository tests PASS; facade conformance may still fail until Task 8.

### Task 3: Add Internal Config, Models, and Translators

**Files:**
- Create: `context_memory/config.py`
- Create: `context_memory/models.py`
- Create: `context_memory/translators.py`
- Modify: `common/dto/context_memory.py` only if required
- Create: `tests/test_context_memory_projection.py`
- Modify: `tests/test_context_memory_assembly.py` utility sections if useful

- [ ] **Step 1: Write config preservation tests**

Cover:
- `TokenBudgetConfig` exposes the same properties as legacy `models.context_config.TokenBudget`.
- Defaults match `common.config.settings`: `context_model_window`, fixed reserves, allocation percentages.
- `available_for_content`, `room_context_tokens`, `conversation_history_tokens`, and `current_task_tokens` calculate exactly like legacy.
- `CompactionConfig` and `MemorySearchConfig` expose the same default fields as legacy.
- Tests can inject explicit config objects without monkeypatching global settings.

- [ ] **Step 2: Implement `context_memory/config.py`**

Rules:
- Import `common.config.settings`, not `config.settings`.
- Keep property names aligned with legacy `models.context_config`.
- Allow dataclass overrides for golden tests.
- Do not mutate global settings.

- [ ] **Step 3: Audit `common/utils/context_utils.py` pure subset**

Identify which functions from `common/utils/context_utils.py` can be safely called from `context_memory/**`:
- SAFE (no deferred model/service imports): `estimate_tokens()`, `extract_turn_notes()`, `MAX_CONTEXT_CHARS`, `CHARS_PER_TOKEN_ESTIMATE`, `MAX_HISTORY_TURNS`, `MAX_SUMMARY_CHARS`, `LLM_TURN_NOTES_THRESHOLD`, `clean_mention_format()`
- UNSAFE (deferred `from models.memory import ...` or `from services.*`): `add_turn_to_history()`, `extract_turn_notes_llm()`, `build_turn_content()`, and any function importing `TurnRole`, `TurnType`, `ConversationTurn`, or `ContentType`

For each UNSAFE function needed by context assembly or projection:
- Port a pure equivalent into `context_memory/projection.py` or `context_memory/assembly.py` using only primitive types and internal models
- Add a golden test proving the re-implementation matches legacy output for the same inputs
- Do NOT import the legacy function from `context_memory/**`

- [ ] **Step 4: Write internal model and translator tests**

Cover:
- Raw `room_memories` dict to normalized `RoomMemoryState`.
- Legacy nested `memory_content.conversation_history` and direct `conversation_history` both read correctly.
- `room_summary`, `room_facts`, `total_messages`, `total_compactions`, and `memory_id` preserve field names.
- Conversation turn dicts handle `role`, `representation`, `content_ref`, `content_type`, `turn_type`, `turn_notes`, `was_successful`, and token estimates.
- Compact turns render the same pointer format as legacy `ContentReference.to_compact_string()` and expand successfully; new deterministic `document_id` values may differ from legacy Mongo `_id` strings.
- `AssembledContext` conversion stores stable/dynamic blocks and legacy metadata.
- Common `MemorySearchResult` conversion preserves `turn_id` in metadata or `source_message_id` as appropriate.
- Common `CompactionResult` conversion preserves `room_id`, `compacted_count`, `tokens_saved`, errors, and compacted timestamp in metadata.

- [ ] **Step 5: Implement `context_memory/models.py`**

Suggested internal shapes:
- `TruncationReason(str, Enum)`
- `ContentReferenceData`
- `ConversationTurnData`
- `RoomSummaryData`
- `RoomMemoryState`
- `AssemblyResult`
- `SearchRankingRecord`

Keep these focused and plain. Do not recreate the full legacy Pydantic `models.memory` tree inside `context_memory/`.

- [ ] **Step 6: Implement `context_memory/translators.py`**

Rules:
- Accept and return dicts/Common DTOs only.
- Preserve legacy enum string values: `user`, `agent`, `supervisor`, `full`, `compact`, `text`, `tool_result`, `agent_response`, `message`.
- Preserve `turn_id` values when projecting existing turns.
- Generate ids through injected `id_factory`, not direct `uuid4()` in translators unless explicitly passed.
- Preserve legacy `memory_content.summary` fallback behavior.

- [ ] **Step 7: Run config and translator tests**

```bash
uv run python -m pytest tests/test_context_memory_projection.py tests/test_context_memory_assembly.py -k "config or translator or normalized" -q
```

Expected: PASS.

### Task 4: Port Context Assembly Logic with Golden Equivalence

**Files:**
- Create: `context_memory/assembly.py`
- Create/modify: `tests/test_context_memory_assembly.py`
- Create: `tests/test_context_memory_assembly_golden.py`
- Modify: `tests/test_context_assembly_service.py` only for adapter binding updates
Do not modify `context_memory/facade.py` in this task; Task 8 owns facade integration.

- [ ] **Step 1: Write failing direct assembly tests**

Port coverage from `tests/test_context_assembly_service.py`:
- Available-for-content calculation.
- Dynamic allocation percentages.
- Supervisor context returns result with context, tokens, occupancy, stable/dynamic token split, and max-turn enforcement.
- Supervisor context includes agent registry and memory search snippets.
- Agent execution context includes agent name, room awareness, quoted text, room facts, and system instruction.
- Over-budget turns truncate oldest first.
- Stable prefix over-budget edge logs but continues.
- `MAX_CONTEXT_CHARS` hard cap truncates and updates token count.
- Compact turns render legacy-compatible pointer strings and count as compact turns.

- [ ] **Step 2: Implement stable prefix builder**

Port `_build_stable_prefix()` exactly:
- `[Room Context]`
- `Current Goal`
- first three key decisions
- first three open questions
- agent roster under `[Available Agents]`
- room facts under the current labels
- memory search snippets under the current labels

Do not rename headings or punctuation unless golden tests prove the legacy code already differs.

- [ ] **Step 3: Implement dynamic suffix builders**

Port exactly:
- Supervisor `_build_dynamic_suffix()`.
- Agent `_build_agent_dynamic_suffix()`.
- `_select_turns_within_budget()`.
- Turn rendering behavior, summary fallback, quoted text section, and system instruction text.

- [ ] **Step 4: Implement synchronous supervisor assembly helper path**

Algorithm:
1. Accept an already-loaded `room_memory_doc` or normalized `RoomMemoryState`.
2. Normalize room memory through translators if needed.
3. Accept precomputed `memory_search_results`; do not run async search from this helper.
4. Build stable prefix and dynamic suffix with the exact legacy algorithm.
5. Return `AssembledContext` with ordered blocks and legacy metadata.

- [ ] **Step 5: Implement synchronous agent execution assembly helper path**

Algorithm:
1. Accept an already-loaded `room_memory_doc` or normalized `RoomMemoryState`.
2. Normalize room memory if needed.
3. Build room facts stable prefix and agent dynamic suffix with exact legacy budget rules.
4. Return `AssembledContext` with ordered blocks and legacy metadata.

These helpers are the only path used by synchronous legacy `ContextAssemblyService` adapter methods. They must remain sync and must not perform repository reads, RoomHistoryReader calls, LLM calls, or memory search.

- [ ] **Step 6: Implement protocol `assemble_context()`**

Mapping:
- Load room memory via `MemoryRepository.get_room_memory(room_id)` - this is the PRIMARY data source for context assembly (conversation history, summary, facts).
- Extract current task text from the most recent user turn in the room memory matching `message_id`, OR use `RoomHistoryReader.get_messages_by_ids([message_id])` as fallback if the turn has not yet been projected.
- If `agent_id is None`, call supervisor helper.
- If `agent_id` is provided, call agent helper and pass `agent_id`.
- Override token budget by constructing a per-call budget object whose available content is compatible with `token_budget` while retaining fixed reserves in metadata.
- Store `message_id` and `agent_id` in `AssembledContext.metadata`.

NOTE: `RoomHistoryReader` is used primarily by `project_message()` (MemoryProjector), NOT by the assembly step. Context assembly works on PROJECTED conversation turns stored in room_memories, not raw room messages.

- [ ] **Step 7: Add golden equivalence fixtures**

Before Task 9 wraps `ContextAssemblyService`, capture fixed legacy golden outputs from the pre-wrapper implementation into committed fixture data, or preserve a test-only legacy oracle that is not affected by the C3 adapter. After Task 9, golden tests must compare new output to those fixed outputs/oracle, not to the wrapped `ContextAssemblyService` singleton.

For each fixture, compare fixed legacy output to new synchronous assembly helper output:
- Small supervisor context.
- Supervisor with agent registry and memory search snippets.
- Supervisor over budget with turn truncation.
- Agent execution with room facts.
- Agent execution with quoted text and room awareness.
- Compact turn rendering.
- Char cap truncation.
- Legacy `memory_content.conversation_history` fallback.
- Direct `conversation_history` fallback.

Assert exact equality for:
- final context string
- `total_tokens`
- `stable_prefix_tokens`
- `dynamic_suffix_tokens`
- `turns_included`
- `turns_truncated`
- `was_truncated`
- `truncation_reason`

- [ ] **Step 8: Run assembly tests**

```bash
uv run python -m pytest tests/test_context_memory_assembly.py tests/test_context_memory_assembly_golden.py tests/test_context_assembly_service.py -q
```

Expected: PASS with exact legacy token-budget results.

### Task 5: Port Projection, Room Memory Lifecycle, and Summary Updates

**Files:**
- Create: `context_memory/projection.py`
- Create: `context_memory/summary.py`
- Create/modify: `tests/test_context_memory_projection.py`
- Modify: `tests/test_phase5_supervisor_integration.py`
Do not modify `context_memory/facade.py` in this task; Task 8 owns facade integration.

- [ ] **Step 1: Write failing user-message projection tests**

Cover:
- `project_message(room_id, message_id)` reads raw message through `RoomHistoryReader.get_messages_by_ids()`.
- User raw message creates a room memory if missing.
- User text is cleaned of `@mention` UUIDs the same way legacy code does.
- Attachments are represented by the same `build_turn_content()` output as legacy adapter conversion.
- Turn contains `role="user"`, `user_id`, content, token estimate, turn notes, timestamp, and default representation `full`.
- `total_messages` and `last_activity_at` update through repository atomic mutation.

- [ ] **Step 2: Implement raw message to turn projection**

Rules:
- Consume `RoomMessageInfo` only.
- Do not import `models.room` or `services.room_services`.
- If a legacy helper such as `build_turn_content()` is needed, port a pure equivalent into `context_memory/projection.py` and prove equivalence with tests.
- Missing raw message should be logged and treated as no-op.

- [ ] **Step 3: Write failing legacy room memory lifecycle tests**

Cover:
- `initialize_or_update_room_memory()` creates memory on first user message.
- Existing memory gets a pushed and trimmed user turn.
- `_track_user_interaction()` behavior is preserved or explicitly stays in legacy if out of scope.
- Return mapping to `RoomMemoryInfo` preserves `room_id`, `memory_id`, `content`, timestamps, and token count where available.

- [ ] **Step 4: Implement lifecycle compatibility helpers**

Use `MemoryRepository` atomic methods. If the explicit Phase 5 collection ownership list excludes `user_memories` or `agent_memories` on the implementation branch, keep user/agent counter writes in the legacy adapter and document that deferral in adapter tests.

- [ ] **Step 5: Write failing agent response and synthesis tests**

Cover:
- `add_agent_response_to_memory()` creates an `agent` turn with `agent_id`, `agent_name`, `was_successful`, token estimate, and turn notes.
- Successful long agent response schedules LLM turn-note enrichment through injected `LLMProvider`.
- `add_synthesis_to_history()` creates a `supervisor` turn and enriches content with trajectory contributions exactly like legacy.
- The synthesis method returns the new `turn_id`.
- Summary stubs match legacy strings.

- [ ] **Step 6: Implement agent and synthesis helpers**

Rules:
- Keep the same `MAX_HISTORY_TURNS`, `MAX_SUMMARY_CHARS`, `LLM_TURN_NOTES_THRESHOLD`, `estimate_tokens`, and `extract_turn_notes` behavior from `common.utils.context_utils`.
- Use injected `LLMProvider.generate_structured()` for enrichment if replacing `extract_turn_notes_llm()`; do not import `services.openai_service`.
- Fire-and-forget background tasks should be isolated so tests can await or disable them deterministically.

- [ ] **Step 7: Write failing room summary update tests**

Port coverage from `tests/test_phase5_supervisor_integration.py`:
- Builds the same extraction prompt.
- Calls LLM structured JSON through `LLMProvider`.
- Loads summary projection only.
- Merges missing fields with existing summary.
- Deduplicates new facts case-insensitively.
- Writes `updated_after_turn_id`.
- Returns false on LLM failure or missing memory.

- [ ] **Step 8: Implement `context_memory/summary.py`**

Use injected `LLMProvider.generate_structured()` or a small adapter method that can map to the current OpenAI service from `llm_gateway` in `container.py`. Do not import `services.openai_service`.

- [ ] **Step 9: Run projection and summary tests**

```bash
uv run python -m pytest tests/test_context_memory_projection.py tests/test_phase5_supervisor_integration.py -k "memory or synthesis or summary or projection" -q
```

Expected: PASS.

### Task 6: Port Memory Search and Content Storage

**Files:**
- Create: `context_memory/search.py`
- Create: `context_memory/content_storage.py`
- Create/modify: `tests/test_context_memory_search.py`
- Modify: `tests/test_memory_search_service.py`
- Modify: `tests/test_context_memory_bugfixes.py`
Do not modify `context_memory/facade.py` in this task; Task 8 owns facade integration.

- [ ] **Step 1: Write failing search unit tests**

Port coverage from `tests/test_memory_search_service.py`:
- `_cosine_similarity()` behavior.
- Weighted merge for disjoint, overlapping, vector-only, keyword-only, and empty result sets.
- Score normalization.
- Sort by combined score.
- Temporal decay with configured half-life.
- Unknown timestamp penalty.
- MMR diversity selection.
- Disabled search returns no results and marks search mechanisms unused.
- Graceful degradation when vector or keyword search fails.

- [ ] **Step 2: Implement pure search ranking helpers**

Keep helpers pure and local to `context_memory/search.py`. Do not import Pinecone classes or legacy search models.

- [ ] **Step 3: Write failing vector and keyword search tests**

Cover:
- Query embedding uses `LLMProvider.embed(query)`.
- Vector search uses `VectorDAL.search(index, vector, top_k, filter={"room_id": {"$eq": room_id}})`.
- Vector results map `VectorSearchResult.id`, score, and metadata to internal ranking records.
- Keyword search uses `ContentStorageRepository.text_search()`.
- Hydration uses `ContentStorageRepository.hydrate_turn_notes()`.
- Search output maps to Common `MemorySearchResult`.

- [ ] **Step 4: Implement vector and keyword search**

Rules:
- No Pinecone imports.
- No `database.pinecone_db`.
- No `services.openai_service`.
- Preserve vector failure fallback and keyword failure fallback.
- Store search metadata in Common DTO `metadata`.

- [ ] **Step 5: Write failing indexing and delete tests**

Cover:
- `index_turn_for_search()` skips empty content.
- Embedding uses `LLMProvider.embed(content)`.
- Upsert uses `VectorDAL.upsert(index, [VectorRecord(...)])`.
- Metadata includes `room_id`, `turn_id`, `role`, `agent_name`, and timestamp.
- `delete_room_index()` deletes all room vectors using `VectorDAL.delete()` if only ids are available, or document the need to extend `VectorDAL` with `delete_by_filter()`.

- [ ] **Step 6: Resolve `VectorDAL.delete` filter gap**

Current `VectorDAL.delete(index, ids)` cannot express Pinecone delete-by-filter used by legacy `delete_room_index()`. Choose one:
- Preferred: extend `VectorDAL` with `delete_by_filter(index: str, filter: dict) -> None` and implement it in `dal/pinecone`.
- Temporary: repository tracks vector ids per room and deletes by ids, with golden tests proving current behavior. Use this only if extending `VectorDAL` is blocked.

Document the chosen option in tests and in the final handoff.

- [ ] **Step 7: Write failing content storage helper tests**

Port coverage from `tests/test_compaction_service.py`:
- `hash_content()` deterministic SHA-256.
- `store_full_content()` computes deterministic `document_id`, `content_hash`, `stored_at`, and `expires_at`, calls `ContentStorageRepository.upsert_full_content()` with the full protocol argument set, and returns the stable `document_id`.
- Idempotent upsert returns the stable string `document_id`.
- Existing legacy content doc missing `document_id` is backfilled by repository upsert and expands by the returned stable id.
- Expand by stable `document_id`.
- Expand by legacy ObjectId-string document id via `common.mongo_ids.object_id_query()`.
- Expand by turn id.
- Missing content raises a Context & Memory local `ContentExpiredError`.
- Delete by turn and by room.
- Content stats match legacy shape.
- Facade compatibility helpers return legacy string/boolean/integer/dict shapes, not raw repository documents.

- [ ] **Step 8: Implement content storage helpers**

Rules:
- `ContentExpiredError` lives in `context_memory/content_storage.py`.
- `store_full_content()` is the only helper compaction should call for full-content storage; it computes deterministic `document_id` and storage metadata before calling the repository.
- Pointer string rendering must preserve the legacy `ContentReference.to_compact_string()` format for MongoDB references. For newly compacted turns, the `document_id` segment comes from the stable `document_id` field and may differ from legacy Mongo `_id` values.
- `context_memory/**` supports MongoDB content expansion only. Do not import `services.s3_service`. S3 expansion pass-through remains in the legacy `ContentStorageService` adapter, and URL expansion remains `NotImplementedError`.

- [ ] **Step 9: Run search and content storage tests**

```bash
uv run python -m pytest tests/test_context_memory_search.py tests/test_memory_search_service.py tests/test_context_memory_bugfixes.py -k "search or hydration or content" -q
```

Expected: PASS.

### Task 7: Port Lossless Compaction

**Files:**
- Create: `context_memory/compaction.py`
- Create/modify: `tests/test_context_memory_compaction.py`
- Modify: `tests/test_compaction_service.py`
- Modify: `tests/test_context_memory_bugfixes.py`
Do not modify `context_memory/facade.py` in this task; Task 8 owns facade integration.

- [ ] **Step 1: Write failing compaction eligibility tests**

Port coverage:
- Disabled compaction returns false/no-op.
- Missing memory returns false/no-op.
- Full turn count above max triggers compaction.
- Full token total above max triggers compaction.
- Compact turns are ignored for full-token trigger.
- Preserve recent turns count is honored.

- [ ] **Step 2: Implement `should_compact()` and `compact_if_needed()`**

Use `MemoryRepository.get_room_memory()` and normalized room memory state. Preserve current short-circuit behavior.

- [ ] **Step 3: Define `run_compaction()` threshold behavior**

`MemoryProjector.run_compaction(room_id)` must be threshold-gated in Phase 5. It should call the same eligibility check as `compact_if_needed()` and return `CompactionResult(room_id=room_id, compacted_count=0, tokens_saved=0, metadata={"skipped": True, "reason": "below_threshold"})` when compaction is not needed. Do not introduce a force-compaction path unless a separate internal helper is explicitly named and tested.

- [ ] **Step 4: Write failing compaction operation tests**

Cover:
- Older full turns are selected; recent full turns are preserved.
- Already compact turns are skipped.
- Turns with missing content are skipped.
- Content is persisted before memory pointers are written.
- Vector indexing is attempted after content storage and before pointer update.
- Vector indexing failure is logged and does not block compaction.
- Repository compact update only marks successfully prepared entries.
- `tokens_saved` is sum of `estimated_tokens_full - estimated_tokens_compact`.
- Errors are included in metadata or local result state without failing the entire operation.

- [ ] **Step 5: Implement compaction preparation**

Algorithm:
1. Compute turns to compact using legacy preserve-recent and threshold rules.
2. For each turn, call `context_memory.content_storage.store_full_content()` with `room_id`, `turn_id`, `content`, `content_type`, and `turn_notes`. That helper computes `document_id`, `content_hash`, `stored_at`, and `expires_at`, then calls `ContentStorageRepository.upsert_full_content()` with the full protocol argument set.
3. Call search indexing for that turn.
4. Build `content_ref` dict with `storage_type="mongodb"`, `collection="conversation_content"`, `document_id`, `content_hash`, and created timestamp.
5. Build compacted entries for repository update.

- [ ] **Step 6: Implement `run_compaction()`**

Return Common `CompactionResult`:
- `room_id`
- `compacted_count`
- `tokens_saved`
- `memory_id`
- `metadata.errors`
- `metadata.compacted_at`

Legacy `services.compaction_service` converts this to legacy `models.compaction.CompactionResult` for old tests and callers.

- [ ] **Step 7: Write expansion and stats tests**

Cover:
- `expand_turn_content()` returns full content for compact turn.
- Missing `content_ref` raises or returns legacy-compatible error.
- `fetch_turn_content()` returns full content or compact pointer fallback according to current behavior.
- `get_compaction_stats()` returns total turns, full turns, compact turns, tokens saved, total compactions, and content storage stats.

- [ ] **Step 8: Implement expansion and stats**

Use `ContentStorageRepository` and normalized content references. Do not import legacy content storage service.

- [ ] **Step 9: Run compaction tests**

```bash
uv run python -m pytest tests/test_context_memory_compaction.py tests/test_compaction_service.py tests/test_context_memory_bugfixes.py -k "compaction or content" -q
```

Expected: PASS.

### Task 8: Implement ContextMemoryFacade and Event Handler

**Files:**
- Create/modify: `context_memory/facade.py`
- Create: `context_memory/events.py`
- Modify: `tests/test_context_memory_facade.py`
- Create/modify: `tests/test_context_memory_events.py`
- Modify: `tests/test_context_memory_protocols.py`

- [ ] **Step 1: Write facade protocol tests**

Cover:
- Runtime `isinstance(facade, ContextAssembler)`.
- Runtime `isinstance(facade, MemoryManager)`.
- Runtime `isinstance(facade, MemoryProjector)`.
- All protocol methods delegate to the appropriate internal helpers.

- [ ] **Step 2: Implement facade constructor and fields**

Store dependencies and configs. Use `NoopTracingProvider` if a tracer is not provided, matching Phase 3/4 style.

- [ ] **Step 3: Implement MemoryManager methods**

Cover:
- `get_room_memory()` maps repository docs to `RoomMemoryInfo`.
- `search_memory()` delegates to search helper and returns Common DTO list.
- `get_user_memories()` maps repository user-memory docs to Common `UserMemory` using the explicit synthetic `memory_id`, stable rendered `content`, and metadata mapping from "UserMemory DTO Mapping".
- `delete_room_memory()` deletes room memory, content storage documents, and vector index entries owned by Context & Memory.

- [ ] **Step 4: Implement MemoryProjector methods**

Cover:
- `project_message()` uses `RoomHistoryReader`.
- `run_compaction()` delegates to the threshold-gated compaction helper and returns a zero-count result when below threshold.
- Idempotency: projecting the same raw message twice does not create duplicate turns if the repository already has a turn with matching source metadata. If legacy schema lacks source metadata, add it in projected turns and preserve existing legacy turns.

- [ ] **Step 5: Implement ContextAssembler method**

Cover:
- `assemble_context()` loads projected room memory via `MemoryRepository` first, then uses `RoomHistoryReader` only to recover current message text if the matching projected turn is missing.
- Fakes can drive exact token-budget output in tests.
- Missing message returns empty context with metadata error or raises a clear `ValueError`; choose behavior and test it.

- [ ] **Step 6: Add `ContextMemoryEventHandler` tests**

Cover:
- `handle_message_committed()` calls `project_message()` with event room/message.
- It calls threshold-gated `run_compaction()` after projection and does not force compaction after every message.
- It handles projection failure according to the existing EventPublisher contract: log and let the caller/bus dead-letter if appropriate. If the handler catches exceptions, tests must assert logging and no re-raise.
- It is safe for user and agent message types.

- [ ] **Step 7: Implement event handler**

Do not import Delivery implementation. Accept only `MemoryProjector` and `MessageCommitted`.

- [ ] **Step 8: Run facade and event tests**

```bash
uv run python -m pytest tests/test_context_memory_facade.py tests/test_context_memory_events.py tests/test_context_memory_protocols.py -q
```

Expected: PASS.

### Task 9: Add C3 Migration Adapters for Legacy Services

**Files:**
- Modify: `services/context_assembly_service.py`
- Modify: `services/compaction_service.py`
- Modify: `services/memory_search_service.py`
- Modify: `services/memory_service.py`
- Modify: `services/content_storage_service.py`
- Create/modify: `tests/test_context_memory_adapters.py`
- Modify: existing legacy tests that instantiate these services directly

- [ ] **Step 1: Write fail-fast binding tests for `ContextAssemblyService`**

Cover:
- New `ContextAssemblyService()` has `_bound is False`.
- Calling `build_supervisor_context()` before `bind_facade()` raises `RuntimeError("ContextAssemblyService.bind_facade() not called - startup incomplete")`.
- Calling `build_agent_execution_context()` before bind raises the same error.
- After bind, methods call synchronous pure assembly compatibility helpers only; they must not call async repository-backed facade methods.
- Returned object is the legacy `ContextAssemblyResult` dataclass.

- [ ] **Step 2: Add `ContextAssemblyService.bind_facade()`**

Target shape:

```python
class ContextAssemblyService:
    def __init__(self) -> None:
        self._facade = None
        self._bound = False

    def bind_facade(self, facade) -> None:
        self._facade = facade
        self._bound = True

    def _require_facade(self):
        if not self._bound or self._facade is None:
            raise RuntimeError(
                "ContextAssemblyService.bind_facade() not called - startup incomplete"
            )
        return self._facade
```

Keep `ContextAssemblyResult`, `ContextMetrics`, and `TruncationReason` exports in this file for compatibility.

The adapter methods stay synchronous. They convert the supplied legacy `RoomMemory` object to a primitive dict/state, pass through any already-computed `memory_search_results`, call the sync assembly helper, and convert `AssembledContext.metadata` back to `ContextAssemblyResult`. They must not perform repository reads, memory search, or `await` internally.

- [ ] **Step 3: Write fail-fast binding tests for `CompactionService`**

Cover:
- `should_compact()`, `compact_if_needed()`, `compact_room_memory()`, `expand_turn_content()`, `fetch_turn_content()`, and `get_compaction_stats()` fail before bind.
- After bind, they call facade compatibility helpers.
- Result conversion to legacy `models.compaction.CompactionResult` preserves fields.

- [ ] **Step 4: Add `CompactionService.bind_facade()`**

Keep singleton name `compaction_service`. Remove direct construction of `database_service`, content storage, and memory search from active paths after bind.

- [ ] **Step 5: Write fail-fast binding tests for `MemorySearchService`**

Cover:
- `search()`, `index_turn_for_search()`, and `delete_room_index()` fail before bind.
- After bind, `search()` returns legacy `MemorySearchResponse`.
- After bind, indexing and delete delegate to facade helpers.

- [ ] **Step 6: Add `MemorySearchService.bind_facade()`**

Keep pure static helpers only if legacy tests import them directly; otherwise route through `context_memory.search`.

- [ ] **Step 7: Write fail-fast binding tests for `RoomMemoryService` only**

Cover:
- `RoomMemoryService` room-based methods fail before bind.
- `ChatMemoryService` remains unchanged and does not require facade binding.
- `room_memory_service.add_agent_response_to_memory()` delegates to facade after bind.
- `add_synthesis_to_history()` and `update_room_summary()` delegate after bind.
- CRUD-style room memory methods return `RoomCenterMemoryResponse` with legacy fields.

- [ ] **Step 8: Add `RoomMemoryService.bind_facade()`**

Do not move or bind `ChatMemoryService`. Keep singleton exports:

```python
chat_memory_service = ChatMemoryService()
room_memory_service = RoomMemoryService()
```

- [ ] **Step 9: Write content storage adapter tests**

Cover:
- `ContentStorageService.upsert_full_content()` delegates after bind and returns the stable document id string.
- `ContentStorageService.get_content_by_document_id()` delegates after bind and returns the full content string or `None`, not the repository dict.
- `ContentStorageService.get_content_by_turn_id()` delegates after bind and returns the full content string or `None`, not the repository dict.
- `ContentStorageService.expand_content_reference()` delegates MongoDB references after bind and preserves `ContentExpiredError` behavior for missing MongoDB content.
- `ContentStorageService.expand_content_reference()` preserves existing S3 pass-through behavior locally: call `services.s3_service.s3_service.download_text(s3_key)`, return the downloaded string, and raise `ContentExpiredError(turn_id, s3_key)` when it returns `None`.
- `ContentStorageService.expand_content_reference()` continues to raise `NotImplementedError` for URL references.
- `ContentStorageService.delete_content_by_turn_id()`, `delete_content_by_room_id()`, and `get_content_stats_for_room()` delegate after bind with the same `bool`, `int`, and stats-dict return shapes.
- `ContentExpiredError` remains import-compatible from `services.content_storage_service`.
- `hash_content()` remains import-compatible and deterministic.

- [ ] **Step 10: Add `ContentStorageService.bind_facade()`**

Keep compatibility imports stable for existing compaction tests. The adapter may import `services.s3_service` only inside the S3 branch to preserve legacy behavior; `context_memory/**` must never import it.

- [ ] **Step 11: Run adapter and legacy tests**

```bash
uv run python -m pytest tests/test_context_memory_adapters.py tests/test_context_assembly_service.py tests/test_compaction_service.py tests/test_memory_search_service.py tests/test_phase5_supervisor_integration.py -q
```

Expected: PASS with legacy response/dataclass shapes unchanged.

### Task 10: Wire ContextMemoryDeps in Container and Startup

**Files:**
- Modify: `container.py`
- Modify: `main.py`
- Modify: startup-related tests
- Modify: `tests/test_context_memory_protocols.py`

- [ ] **Step 1: Add container assembly tests**

Create tests that instantiate the container with fakes and assert:
- `ContextMemoryDeps.context_assembler` is a `ContextAssembler`.
- `ContextMemoryDeps.memory_manager` is a `MemoryManager`.
- `ContextMemoryDeps.memory_projector` is a `MemoryProjector`.
- All three fields are the same `ContextMemoryFacade` instance.
- `create_context_memory_deps()` accepts `room_history_reader` from `RoomDeps`.

- [ ] **Step 2: Implement `container.py` ContextMemoryDeps assembly**

Target:

```python
def create_context_memory_deps(
    *,
    mongo: MongoDAL,
    vector: VectorDAL,
    llm_provider: LLMProvider,
    room_history_reader: RoomHistoryReader,
) -> ContextMemoryDeps:
    memory_repository = MemoryMongoRepository(mongo=mongo)
    content_repository = ContentStorageMongoRepository(mongo=mongo)
    facade = ContextMemoryFacade(
        memory_repository=memory_repository,
        content_repository=content_repository,
        room_history_reader=room_history_reader,
        vector=vector,
        llm_provider=llm_provider,
        id_factory=lambda: uuid4().hex,
        now=utcnow,
    )
    return ContextMemoryDeps(
        context_assembler=facade,
        memory_manager=facade,
        memory_projector=facade,
    )
```

- [ ] **Step 3: Instantiate ContextMemoryDeps during lifespan startup**

In `main.py`, after `RoomDeps` is ready:
- Reuse `mongo_dal`.
- Reuse `VectorDALImpl()`.
- Reuse `LLMGatewayImpl()`.
- Pass `_room_deps.room_history_reader`.
- Build `ContextMemoryDeps`.
- Bind `context_assembly_service.bind_facade(context_memory_facade)`.
- Bind `compaction_service.bind_facade(context_memory_facade)`.
- Bind `memory_search_service.bind_facade(context_memory_facade)`.
- Bind `room_memory_service.bind_facade(context_memory_facade)`.
- Bind `content_storage_service.bind_facade(context_memory_facade)`.
- Register `ContextMemoryEventHandler` with `EventPublisher` only if a concrete publisher already exists in startup. If not, leave registration documented and covered with fakes.
- Keep Redis/SSE/relay startup order unchanged except that adapters must be bound before traffic/background jobs can run.

- [ ] **Step 4: Add startup fail-fast tests**

Cover:
- If Mongo is unavailable, startup logs a warning and does not partially bind Context & Memory services.
- If `ContextMemoryDeps` is built, all legacy Context & Memory adapters are bound before compaction sweep or agent health background work starts.
- ContextMemory binding does not require Redis, relay, or hub liveness.
- Existing Phase 3/4 startup tests still pass.

- [ ] **Step 5: Run startup-related tests**

```bash
uv run python -m pytest tests/test_context_memory_protocols.py tests/test_multi_worker_safety.py tests/test_heartbeat_fixes.py tests/test_room_protocols.py -q
```

Expected: PASS.

### Task 11: Preserve Callers Through C3 Adapters

**Files:**
- Modify: `modules/RoomMessageCenter.py`
- Modify: `modules/QueueExecutor.py` only if constructor tests require explicit bind setup
- Modify: `modules/SupervisorExecutor.py` only if constructor tests require explicit bind setup
- Modify: `services/room_services.py`
- Modify: `jobs/compaction_sweep.py`
- Modify: existing module tests

- [ ] **Step 1: Add caller preservation tests**

Cover:
- `modules/RoomMessageCenter.py` still calls `room_memory_service.add_agent_response_to_memory()`, `add_synthesis_to_history()`, `update_room_summary()`, and `compaction_service.compact_if_needed()` through legacy imports.
- `modules/QueueExecutor.py` still calls `room_memory_service.add_agent_response_to_memory()`.
- `modules/SupervisorExecutor.py` still calls `room_memory_service.add_agent_response_to_memory()`.
- `services/room_services.py` still calls `context_assembly_service.build_supervisor_context()` and `build_agent_execution_context()`.
- `jobs/compaction_sweep.py` still runs sweep and compacts idle rooms.

- [ ] **Step 2: Keep direct compaction call in RoomMessageCenter**

Do not replace `_trigger_compaction_safe()` with a new event bus. Keep:
- Inline await while the per-room lock is held.
- Exception swallowing/logging behavior.
- Call through `services.compaction_service.compaction_service`, which delegates after bind.

- [ ] **Step 3: Add optional future event registration only in startup**

If a concrete `EventPublisher` exists in the implementation branch, register `ContextMemoryEventHandler` there. Do not import Delivery implementation into `context_memory/`.

- [ ] **Step 4: Keep compaction sweep outside the module**

Preferred options:
- Minimal: leave `jobs/compaction_sweep.py` importing `services.compaction_service`; the service delegates to facade after bind.
- Cleaner: add `CompactionSweep.bind_projector(memory_projector: MemoryProjector)` and use it inside `sweep()`.

Either way, the job stays in `jobs/` because leader election, active-run skip checks, sleep loops, and worker pools are application-shell concerns.

- [ ] **Step 5: Run caller compatibility tests**

```bash
uv run python -m pytest tests/test_module_room_message_center.py tests/test_module_queue_executor.py tests/test_module_supervisor_executor.py tests/test_service_room.py tests/test_context_memory_bugfixes.py -q
```

Expected: PASS. Call sites remain behavior-compatible.

### Task 12: Golden Tests and End-to-End Compatibility

**Files:**
- Create/modify: `tests/test_context_memory_assembly_golden.py`
- Modify: `tests/test_context_assembly_service.py`
- Modify: `tests/test_compaction_service.py`
- Modify: `tests/test_memory_search_service.py`
- Modify: `tests/test_phase5_supervisor_integration.py`
- Modify: `tests/test_context_memory_bugfixes.py`

- [ ] **Step 1: Add fixed golden context assembly fixtures**

Capture expected context assembly outputs before wrapping `services.context_assembly_service.ContextAssemblyService`, or preserve a test-only copy/oracle of the legacy implementation that does not delegate to the new facade. Store fixed expected values in fixture data so post-adapter tests cannot compare the new implementation to itself.

Fixture families:
- Supervisor, small memory.
- Supervisor, large memory requiring turn truncation.
- Supervisor, memory search snippets.
- Agent, facts and full history.
- Agent, quoted text and room awareness.
- Compact turn pointers.
- Direct `conversation_history` and legacy `memory_content.conversation_history`.

Assert `assemble_context()` and compatibility helpers produce identical token-budget results to fixed legacy fixture output for the same data.

- [ ] **Step 2: Add golden projection tests**

Assert:
- User raw message projection produces the same turn fields as legacy `initialize_or_update_room_memory()`.
- Agent response compatibility helper produces the same turn fields as legacy `add_agent_response_to_memory()`.
- Synthesis helper produces the same enriched content and summary stub as legacy.

- [ ] **Step 3: Add golden compaction tests**

Assert:
- Same turns selected for compaction.
- Same compacted count.
- Same token savings.
- Same content reference field set, storage type, collection, and content hash.
- Same compact pointer format and successful expansion. Exact pointer id equality is required only for captured legacy ObjectId fixtures, not for newly compacted turns that use deterministic `document_id`.
- Same content storage document field set, including deterministic `document_id`, `content_hash`, `stored_at`, and `expires_at` rules.
- Same non-blocking behavior when vector indexing fails.

- [ ] **Step 4: Add golden memory search tests**

Assert:
- Same weighted merge ordering.
- Same temporal decay ordering.
- Same MMR ordering for hand-crafted score profiles.
- Same hydration behavior for empty vector-only content.
- Same disabled/failure metadata.

- [ ] **Step 5: Add adapter response tests**

Assert:
- Legacy `ContextAssemblyResult` dataclass fields remain identical.
- Legacy `RoomCenterMemoryResponse` fields remain identical.
- Legacy `MemorySearchResponse` fields remain identical.
- Legacy `CompactionResult` fields remain identical.

- [ ] **Step 6: Run golden compatibility tests**

```bash
uv run python -m pytest tests/test_context_memory_assembly_golden.py tests/test_context_assembly_service.py tests/test_compaction_service.py tests/test_memory_search_service.py tests/test_phase5_supervisor_integration.py tests/test_context_memory_bugfixes.py -q
```

Expected: PASS with no golden token-budget drift.

### Task 13: Final Import Boundary and Full Gate

**Files:**
- Modify: `tests/test_context_memory_protocols.py`
- Maybe modify: `docs/MODULAR_DECOUPLING_DESIGN.md` only if documenting actual Phase 5 deviations

- [ ] **Step 1: Run Context & Memory module tests**

```bash
uv run python -m pytest tests/test_context_memory_protocols.py tests/test_context_memory_repository.py tests/test_context_memory_projection.py tests/test_context_memory_assembly.py tests/test_context_memory_assembly_golden.py tests/test_context_memory_compaction.py tests/test_context_memory_search.py tests/test_context_memory_facade.py tests/test_context_memory_events.py -q
```

Expected: PASS.

- [ ] **Step 2: Run legacy Context & Memory compatibility tests**

```bash
uv run python -m pytest tests/test_context_assembly_service.py tests/test_compaction_service.py tests/test_memory_search_service.py tests/test_context_memory_bugfixes.py tests/test_phase5_supervisor_integration.py tests/test_context_memory_adapters.py -q
```

Expected: PASS.

- [ ] **Step 3: Run caller compatibility tests**

```bash
uv run python -m pytest tests/test_module_room_message_center.py tests/test_module_queue_executor.py tests/test_module_supervisor_executor.py tests/test_service_room.py -q
```

Expected: PASS.

- [ ] **Step 4: Run completed phase tests**

```bash
uv run python -m pytest tests/test_common_foundation.py tests/test_agent_protocols.py tests/test_agent_repository.py tests/test_agent_facade.py tests/test_service_agent.py tests/test_room_protocols.py tests/test_room_repository.py tests/test_room_facade.py tests/test_room_golden.py -q
```

Expected: PASS.

- [ ] **Step 5: Run import-boundary tests**

```bash
uv run python -m pytest tests/test_context_memory_protocols.py -k import_boundary -q
```

Expected: PASS and no forbidden imports from `context_memory/**`.

- [ ] **Step 6: Run broad regression suite if time allows**

```bash
uv run python -m pytest -q
```

Expected: PASS. If too slow, record the targeted commands above and any skipped broad-suite reason.

- [ ] **Step 7: Commit Phase 5**

```bash
git status --short
git add context_memory common/protocols common/dto pyproject.toml container.py main.py services modules jobs tests
git commit -m "feat: extract context memory module facade"
```

Expected: one focused Phase 5 implementation commit, or several commits matching task boundaries if using subagents.

- [ ] **Step 8: Re-run final Context & Memory gate after commit**

```bash
uv run python -m pytest tests/test_context_memory_protocols.py tests/test_context_memory_assembly_golden.py tests/test_context_memory_facade.py tests/test_context_memory_compaction.py tests/test_context_memory_search.py tests/test_context_assembly_service.py tests/test_compaction_service.py tests/test_memory_search_service.py -q
```

Expected: PASS.

## Migration Adapter Wiring

The C3 pattern is mandatory for room-memory and context-memory legacy service singletons:
- No import-time construction of new Context & Memory business dependencies.
- No fallback to legacy Context & Memory logic before bind for migrated methods.
- Before bind, raise `RuntimeError`.
- After bind, migrated public methods delegate to the new facade.
- Legacy `ChatMemoryService` remains unchanged and is explicitly out of scope.

Services to bind:
- `services.context_assembly_service.context_assembly_service`
- `services.compaction_service.compaction_service`
- `services.memory_search_service.memory_search_service`
- `services.memory_service.room_memory_service`
- `services.content_storage_service.content_storage_service`

Services not bound:
- `services.memory_service.chat_memory_service`

Recommended binding order during startup:
1. Connect Mongo and initialize DAL.
2. Build `AgentDeps` exactly as Phase 3 does.
3. Build `RoomDeps` exactly as Phase 4 does.
4. Build `ContextMemoryDeps` with `room_history_reader=_room_deps.room_history_reader`.
5. Bind `context_assembly_service`.
6. Bind `memory_search_service`.
7. Bind `content_storage_service`.
8. Bind `compaction_service`.
9. Bind `room_memory_service`.
10. Register `ContextMemoryEventHandler` with `EventPublisher` if a concrete publisher exists; otherwise leave direct calls as the active runtime path.
11. Start compaction sweep and other background work only after adapters are bound.
12. Initialize Redis/SSE/event broker exactly as current startup does.
13. Serve traffic only after Agent, Room, and Context & Memory adapters are bound.

Avoid circular imports:
- `container.py` can import concrete implementations.
- `main.py` can import `container.py`.
- `context_memory/**` must never import `container.py` or `main.py`.
- `context_memory/**` must never import `room/**`; use `RoomHistoryReader`.
- `context_memory/**` must never import `services/**`; services are wrappers.
- Legacy `services/**`, `modules/**`, and `jobs/**` may import `context_memory` during migration because they are wrappers, but `context_memory` must not import them.

Current direct-call decision:
- Keep `RoomMessageCenter._trigger_compaction_safe()` calling `compaction_service.compact_if_needed(room_id)` in Phase 5.
- Add `ContextMemoryEventHandler.handle_message_committed()` and tests now.
- Do not add a new in-process bus; Delivery already owns the future event publisher.
- When Room/Execution later emit `MessageCommitted`, the handler can be registered without changing the Context & Memory module.

## Test Plan

Unit tests:
- `tests/test_context_memory_repository.py`: Mongo repository query/update/content behavior against fakes.
- `tests/test_context_memory_projection.py`: raw message projection, turn creation, summary stubs, and translator behavior.
- `tests/test_context_memory_assembly.py`: token budget allocation, stable/dynamic builders, truncation, char cap, compact turns.
- `tests/test_context_memory_compaction.py`: eligibility, content storage, pointer compaction, expansion, stats.
- `tests/test_context_memory_search.py`: vector, keyword, merge, temporal decay, MMR, hydration, indexing, delete.
- `tests/test_context_memory_facade.py`: facade behavior with fake repositories, fake RoomHistoryReader, fake VectorDAL, and fake LLMProvider.
- `tests/test_context_memory_events.py`: MessageCommitted handler.
- `tests/test_context_memory_protocols.py`: runtime protocol conformance, exports, packaging, container assembly, and import boundaries.

Golden integration tests:
- `tests/test_context_memory_assembly_golden.py`: exact legacy-vs-new context string and token-budget equality.
- Existing legacy tests: `tests/test_context_assembly_service.py`, `tests/test_compaction_service.py`, `tests/test_memory_search_service.py`, `tests/test_context_memory_bugfixes.py`, `tests/test_phase5_supervisor_integration.py`.

Migration adapter tests:
- `tests/test_context_memory_adapters.py`: fail-fast before bind and exact legacy response/dataclass compatibility after bind.
- Existing caller tests: `tests/test_module_room_message_center.py`, `tests/test_module_queue_executor.py`, `tests/test_module_supervisor_executor.py`, `tests/test_service_room.py`.

Import boundary tests:
- `context_memory/**` imports only stdlib, `common`, and `context_memory`.
- `context_memory/**` does not import `agent`, `room`, `services`, `modules`, `api`, `database`, `models`, `main`, `container`, `config`, `llm_gateway`, `pinecone`, `openai`, `pymongo`, or `motor`.
- Existing Agent, Room, adapter, and DAL boundary tests continue to pass.

Verification commands:

```bash
uv run python -m pytest tests/test_context_memory_protocols.py tests/test_context_memory_repository.py tests/test_context_memory_projection.py tests/test_context_memory_assembly.py tests/test_context_memory_assembly_golden.py tests/test_context_memory_compaction.py tests/test_context_memory_search.py tests/test_context_memory_facade.py tests/test_context_memory_events.py -q
uv run python -m pytest tests/test_context_assembly_service.py tests/test_compaction_service.py tests/test_memory_search_service.py tests/test_context_memory_bugfixes.py tests/test_phase5_supervisor_integration.py tests/test_context_memory_adapters.py -q
uv run python -m pytest tests/test_module_room_message_center.py tests/test_module_queue_executor.py tests/test_module_supervisor_executor.py tests/test_service_room.py -q
uv run python -m pytest tests/test_common_foundation.py tests/test_agent_protocols.py tests/test_agent_repository.py tests/test_agent_facade.py tests/test_service_agent.py tests/test_room_protocols.py tests/test_room_repository.py tests/test_room_facade.py tests/test_room_golden.py -q
```

## Gate Criteria Checklist

- [ ] `context_memory/` package exists and is listed in `pyproject.toml`.
- [ ] `ContextMemoryFacade` satisfies `ContextAssembler`, `MemoryManager`, and `MemoryProjector` at runtime.
- [ ] `MemoryMongoRepository` satisfies `MemoryRepository` at runtime.
- [ ] `ContentStorageMongoRepository` satisfies `ContentStorageRepository` at runtime.
- [ ] `ContextMemoryDeps` exists in `container.py` alongside `AgentDeps` and `RoomDeps`.
- [ ] `create_context_memory_deps()` binds one `ContextMemoryFacade` to all three Context & Memory protocol fields.
- [ ] `context_memory/**` import-boundary test passes.
- [ ] No `context_memory/**` imports from `agent`, `room`, `services`, `modules`, `api`, `database`, `models`, `main`, `container`, `config`, `llm_gateway`, `pinecone`, `openai`, `pymongo`, or `motor`.
- [ ] `assemble_context()` uses projected `room_memories` as its primary context source and uses `RoomHistoryReader` only as a fallback to recover current message text when projection has not completed.
- [ ] Legacy supervisor and agent context helpers are available only as non-protocol compatibility helpers or service adapter methods.
- [ ] `assemble_context()` produces identical token-budget results for golden fixtures.
- [ ] Stable prefix and dynamic suffix strings match legacy output exactly.
- [ ] Token counts, stable prefix tokens, dynamic suffix tokens, truncation reason, and turn counts match legacy output exactly.
- [ ] Lossless compaction stores full content in `conversation_content` before writing compact pointers.
- [ ] Compaction pointer strings preserve legacy format and expand successfully; deterministic new `document_id` values may differ from legacy Mongo-returned ids.
- [ ] Compaction preserves recent turns exactly as legacy.
- [ ] Vector indexing at compaction time uses `VectorDAL` and `LLMProvider`, not concrete Pinecone or OpenAI services.
- [ ] Memory search preserves hybrid vector+BM25 behavior, temporal decay, and MMR ordering.
- [ ] `MemoryManager.get_user_memories()` has explicit `user_memories` repository coverage and DTO mapping tests.
- [ ] Content storage remains owned by Context & Memory, not Room.
- [ ] `MessageCommitted` handler exists and calls projection/compaction.
- [ ] Direct `compaction_service.compact_if_needed(room_id)` path remains live in Phase 5.
- [ ] `jobs/compaction_sweep.py` remains outside `context_memory/` and still uses leader election.
- [ ] `ChatMemoryService` remains legacy and is not moved into `context_memory/`.
- [ ] `RoomMemoryService` room-based methods use `bind_facade()` and raise `RuntimeError` before bind for migrated methods.
- [ ] Existing RoomMessageCenter, QueueExecutor, SupervisorExecutor, and RoomServices callers continue to pass compatibility tests.
- [ ] Existing Phase 0-4 tests still pass.

## Risk Assessment

### Risk: Phase 4 RoomHistoryReader is unavailable

Impact: Context & Memory may be tempted to import legacy Room services or raw Mongo room message collections, violating the Phase 5 boundary.

Mitigation:
- Start with Task 0 Phase 4 artifact checks.
- If Phase 4 is unavailable, copy only the Common protocol definition or build against fakes; do not import legacy Room code from `context_memory/**`.
- Keep `RoomHistoryReader` fakes in facade and projection tests.

Verification:
- `rg -n "class RoomHistoryReader" common/protocols/room_protocols.py`
- `uv run python -m pytest tests/test_context_memory_protocols.py -k import_boundary -q`

### Risk: Token budget output drifts

Impact: The Phase 5 gate fails, and supervisor/agent behavior may change subtly.

Mitigation:
- Port context assembly in small pure functions.
- Add golden equivalence tests before implementation.
- Compare final context string and all token metrics, not just total tokens.
- Freeze config values in fixtures.

Verification:
- `uv run python -m pytest tests/test_context_memory_assembly_golden.py tests/test_context_assembly_service.py -q`

### Risk: Common `AssembledContext` cannot represent legacy `ContextAssemblyResult`

Impact: Existing callers need `context`, `was_truncated`, and token split fields that are not top-level fields on the Common DTO.

Mitigation:
- Use ordered `ContextBlock` entries for stable prefix and dynamic suffix.
- Store legacy fields in `metadata`.
- Keep `ContextAssemblyResult` conversion in `services/context_assembly_service.py`.
- Avoid protocol churn unless tests prove metadata is insufficient.

Verification:
- Adapter tests assert legacy dataclass fields.
- Golden tests assert `AssembledContext.metadata` is complete.

### Risk: MemoryRepository becomes a generic Mongo escape hatch

Impact: Context & Memory leaks raw database coupling and repeats the old service-layer problem.

Mitigation:
- Extend `MemoryRepository` with domain-specific methods only.
- Add `ContentStorageRepository` for `conversation_content`.
- Do not expose raw collection access, generic `find`, generic `aggregate`, or Motor/PyMongo result objects.

Verification:
- Repository protocol review.
- Import-boundary tests.
- Repository tests inspect exact query/update shapes.

### Risk: Compaction update semantics change

Impact: Compaction could lose content, compact recent turns, or become non-idempotent after crashes.

Mitigation:
- Store content before compact pointer updates.
- Preserve idempotent `(room_id, turn_id)` content upsert.
- Preserve ordered compact updates or explicitly add Common DAL support for ordered update operations.
- Re-run compaction on already compact turns as no-op.

Verification:
- `uv run python -m pytest tests/test_context_memory_compaction.py tests/test_compaction_service.py -k compaction -q`

### Risk: VectorDAL cannot delete by Pinecone filter

Impact: `delete_room_index(room_id)` cannot preserve legacy behavior with only `delete(index, ids)`.

Mitigation:
- Prefer extending `VectorDAL` with `delete_by_filter(index, filter)`.
- Implement in `dal/pinecone` and test with fakes.
- If deferred, document and test a vector-id tracking fallback.

Verification:
- `uv run python -m pytest tests/test_context_memory_search.py -k delete_room_index -q`

### Risk: LLM dependency leaks concrete OpenAI service

Impact: `context_memory/**` violates import boundaries and becomes hard to test.

Mitigation:
- Use `LLMProvider.embed()` and `LLMProvider.generate_structured()`.
- Build any adapter from `services.openai_service` or `llm_gateway` in `container.py` or legacy services only.
- Add import-boundary tests before porting search/summary.

Verification:
- `uv run python -m pytest tests/test_context_memory_protocols.py -k import_boundary -q`

### Risk: `MessageCommitted` runtime delivery is incomplete

Impact: Projection/compaction may not run if direct legacy calls are removed too early.

Mitigation:
- Keep direct calls in Phase 5.
- Add `ContextMemoryEventHandler` and tests.
- Register handler only when an EventPublisher exists, but do not depend on runtime events for current behavior.
- Document that actual Room emission is a later Delivery/Execution integration step.

Verification:
- Caller tests still assert direct compaction path.
- Event handler tests pass with fakes.

### Risk: ChatMemoryService gets moved accidentally

Impact: Session-based legacy chat context code may pollute Context & Memory or force unrelated API migration.

Mitigation:
- Exclude `ChatMemoryService` explicitly.
- Keep `modules/MemoryCenter.py` unchanged unless tests require import updates.
- Add adapter tests proving `ChatMemoryService` does not require `bind_facade()`.

Verification:
- `uv run python -m pytest tests/test_context_memory_adapters.py -k chat -q`

### Risk: Compaction sweep gets pulled into business module

Impact: Context & Memory would own leader election, active-run checks, and scheduling infrastructure.

Mitigation:
- Keep `jobs/compaction_sweep.py` in `jobs/`.
- Let it call a bound `MemoryProjector` or delegated `compaction_service`.
- Keep `LeaderElection` imports outside `context_memory/`.

Verification:
- Import-boundary tests.
- `uv run python -m pytest tests/test_context_memory_bugfixes.py -k CompactionSweep -q`

### Risk: Legacy service fail-fast breaks direct unit tests

Impact: Existing tests that instantiate services without startup wiring fail broadly.

Mitigation:
- Update tests to bind fake facades explicitly.
- Keep pure helper tests pointed at `context_memory.*` modules.
- Use clear error text for unbound service methods.

Verification:
- `uv run python -m pytest tests/test_context_memory_adapters.py tests/test_context_assembly_service.py tests/test_compaction_service.py tests/test_memory_search_service.py -q`

### Risk: Projection duplicates turns

Impact: MessageCommitted retries or direct calls could duplicate room memory history.

Mitigation:
- Store source metadata on projected turns, for example `source_message_id`.
- Before projecting, check existing room memory for that source id.
- Keep legacy helpers idempotent where feasible; document any legacy non-idempotent path.

Verification:
- `uv run python -m pytest tests/test_context_memory_projection.py -k idempotent -q`

## Final Handoff Notes

Implement Phase 5 in small commits:
1. Branch/scaffold and failing tests.
2. Memory and content repositories.
3. Internal config, models, and translators.
4. Context assembly and golden token-budget equivalence.
5. Projection, room memory lifecycle, and summary updates.
6. Memory search and content storage.
7. Lossless compaction.
8. ContextMemoryFacade and MessageCommitted handler.
9. Legacy migration adapters.
10. Container/startup wiring.
11. Caller compatibility and compaction sweep boundary.
12. Final golden tests and boundary gates.

Do not start by editing `modules/RoomMessageCenter.py`. The safest path is to make the new facade match legacy behavior behind the existing service singletons, then run existing caller tests unchanged. Keep direct compaction calls and the compaction sweep job in place until Delivery/Execution phases wire runtime `MessageCommitted` events.
