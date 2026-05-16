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
- `tests/test_context_memory_protocols.py`: runtime protocol conformance, exports, package list, container assembly, and import-boundary tests.
- `tests/test_context_memory_repository.py`: repository tests against fake `MongoCollection` instances.
- `tests/test_context_memory_projection.py`: raw message projection and legacy turn helper tests.
- `tests/test_context_memory_assembly.py`: direct assembly unit tests ported from `tests/test_context_assembly_service.py`.
- `tests/test_context_memory_assembly_golden.py`: golden equivalence tests comparing legacy service outputs to pure compatibility assembly helpers; protocol `assemble_context()` gets separate contract tests because its Common signature cannot receive every legacy-only context input.
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
- `common/protocols/dal_protocols.py`: allow Mongo pipeline updates in `MongoCollection` write signatures, add a DAL-owned native-id fallback lookup method on `MongoCollection`, and add `VectorDAL.delete_by_filter()` for room-level vector cleanup. Do not add `VectorDAL.ping_index()` in Phase 5.
- `common/errors/base.py` and `common/errors/__init__.py`: add `VectorIndexUnavailableError` so DAL implementations can report memory-index availability without leaking Pinecone exceptions into `context_memory/**`.
- `common/protocols/__init__.py`: export `ContentStorageRepository` and any new Common protocol names.
- `common/dto/context_memory.py`: add fields only if golden tests show current DTOs cannot carry legacy metadata without unsafe `metadata` overloading.
- `common/dto/__init__.py`: export any added DTOs.
- `pyproject.toml`: add `context_memory` and `context_memory.repository` to `[tool.setuptools].packages`.
- `container.py`: add protocol-only `ContextMemoryDeps`, startup-local `create_context_memory_facade()`, and `create_context_memory_deps(facade)` alongside `AgentDeps` and `RoomDeps`.
- `main.py`: build ContextMemory deps after Room deps and bind legacy service adapters before background work can run.
- `docs/MODULAR_DECOUPLING_DESIGN.md`: update Phase 5 protocol/repository/DAL changes in the same tasks that introduce them; do not defer known design deviations to final cleanup.
- `database/mongodb.py`: update `create_context_memory_indexes()` with the new `conversation_content.document_id` and expanded text indexes; this remains the production Phase 5 index owner.
- `services/context_assembly_service.py`: convert public methods to C3 facade delegation while retaining legacy dataclasses and response shape.
- `services/compaction_service.py`: convert public methods to C3 facade delegation while retaining legacy `CompactionResult` conversion.
- `services/memory_search_service.py`: convert public methods to C3 facade delegation while retaining legacy `MemorySearchResponse` conversion.
- `services/memory_service.py`: add facade binding to `RoomMemoryService` only; leave `ChatMemoryService` unchanged.
- `services/content_storage_service.py`: convert public content-storage methods to C3 facade/repository delegation where needed by tests and legacy callers.
- `modules/RoomMessageCenter.py`: keep direct `_trigger_compaction_safe()` call, but route through the bound legacy `compaction_service` wrapper.
- `modules/QueueExecutor.py`: no direct import of `context_memory`; continue calling `room_memory_service`, which delegates after bind.
- `modules/SupervisorExecutor.py`: no direct import of `context_memory`; continue calling `room_memory_service`, which delegates after bind.
- `services/room_services.py`: keep existing calls to legacy context/memory service singletons; add temporary C3 cleanup binding that accepts the `MemoryManager` protocol, not the concrete facade.
- `jobs/compaction_sweep.py`: stay outside `context_memory/`; either keep importing `services.compaction_service` after it delegates or add a `bind_projector()` seam for `MemoryProjector`.
- Existing Context & Memory tests: update to bind fake facades where they construct migrated legacy services directly.

Reference-only:
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
- `common/utils/context_utils.py`: ONLY the pure subset is allowed from `context_memory/**`: `estimate_tokens()`, `extract_turn_notes()`, `MAX_CONTEXT_CHARS`, `CHARS_PER_TOKEN_ESTIMATE`, `MAX_HISTORY_TURNS`, `MAX_SUMMARY_CHARS`, `LLM_TURN_NOTES_THRESHOLD`, `clean_mention_format()`. Functions that have deferred imports from `models.*` or `services.*` (`add_turn_to_history`, `extract_turn_notes_llm`, turn-rendering helpers) must NOT be called from `context_memory/**`. `build_turn_content()` is in `services/room_services.py`, not this file, and must be ported as a pure equivalent in `context_memory/projection.py` if needed.
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
- Only `container.py`, `main.py`, `context_memory/**`, intended legacy service adapters (`services/context_assembly_service.py`, `services/compaction_service.py`, `services/memory_search_service.py`, `services/memory_service.py`, `services/content_storage_service.py`), and tests may import `context_memory` during migration. `modules/**` and `jobs/**` must not import `context_memory` directly; they keep using service wrappers or Common protocols.

## Interface Definitions

### ContextMemoryFacade Constructor

Use explicit dependency injection. Do not construct singletons inside `context_memory/`.

```python
from collections.abc import Awaitable, Callable
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
        llm_config: ContextMemoryLLMConfig | None = None,
        background_task_runner: Callable[[Awaitable[Any]], None] | None = None,
        tracer: Any | None = None,
    ) -> None: ...
```

`background_task_runner` defaults to a small sync wrapper around `asyncio.create_task` in production. Tests inject a sync recording runner and then explicitly await the recorded coroutines; the runner itself must not be typed as async or hide awaits inside production-only control flow.

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
- `legacy_create_room_memory(memory_doc: dict) -> dict | None`
- `legacy_get_room_memory_by_room_id(room_id: str) -> dict | None`
- `legacy_get_room_memory_by_memory_id(memory_id: str) -> dict | None`
- `legacy_update_room_memory_by_room_id(room_id: str, memory_doc: dict) -> bool`
- `legacy_get_room_memory_for_update_by_memory_id(memory_id: str) -> dict | None`
- `legacy_delete_room_memory_by_room_id(room_id: str) -> bool`
- `legacy_delete_room_memory_by_memory_id(memory_id: str) -> bool`
- `project_message_for_event(room_id: str, message_id: str) -> dict`
- `initialize_or_update_room_memory(room_id: str, *, memory_content: str | None, room_agent_set: dict | None, user_id: str | None, attachments: list | None = None) -> dict | None`
- `add_agent_response_to_memory(room_id: str, agent_id: str, agent_name: str, response_text: str, was_successful: bool = True) -> tuple[bool, bool]`
- `add_synthesis_to_history(room_id: str, synthesis_text: str, trajectory: Any | None = None) -> str | None`
- `update_room_summary(room_id: str, synthesis_text: str, synthesis_turn_id: str | None = None) -> bool`
- `legacy_search(query: str, room_id: str, user_id: str | None = None, limit: int = 10) -> dict`
- `should_compact(room_id: str) -> bool`
- `compact_if_needed(room_id: str) -> CompactionResult | None`
- `compact_room_memory(room_id: str, room_memory_doc: dict | None = None) -> CompactionResult`
- `expand_turn_content(room_id: str, turn_id: str) -> str | None`
- `expand_turn_content_from_turn(turn_doc: dict) -> str`
- `fetch_turn_content(turn_id: str, room_id: str) -> str`
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

Signature convention: `assemble_supervisor_context_from_memory()` and `assemble_agent_execution_context_from_memory()` are synchronous pure helpers. Every other non-protocol facade helper in this list is `async def` because it may touch repositories, vector search, LLM calls, content storage, or legacy async service adapters. Synchronous legacy adapters must not call those async helpers unless their public method is already async and can `await`.

All non-protocol compatibility helpers listed above are temporary C3 migration APIs. Add an AST call-boundary test that builds its helper-name set from this list and fails if any helper is called outside its intended legacy adapter file or tests. Allowed call sites:
- `services/context_assembly_service.py`: `assemble_supervisor_context_from_memory()`, `assemble_agent_execution_context_from_memory()`.
- `services/memory_service.py`: `legacy_create_room_memory()`, `legacy_get_room_memory_by_room_id()`, `legacy_get_room_memory_by_memory_id()`, `legacy_update_room_memory_by_room_id()`, `legacy_get_room_memory_for_update_by_memory_id()`, `legacy_delete_room_memory_by_room_id()`, `legacy_delete_room_memory_by_memory_id()`, `initialize_or_update_room_memory()`, `add_agent_response_to_memory()`, `add_synthesis_to_history()`, `update_room_summary()`.
- `services/memory_search_service.py`: `legacy_search()`, `index_turn_for_search()`, `delete_room_index()`.
- `services/compaction_service.py`: `should_compact()`, `compact_if_needed()`, `compact_room_memory()`, `expand_turn_content()`, `expand_turn_content_from_turn()`, `fetch_turn_content()`, `get_compaction_stats()`.
- `services/content_storage_service.py`: every `content_*` helper.
- `context_memory/events.py`: `project_message_for_event()` only.
- `tests/**`: all helpers for adapter, facade, and boundary tests.

No Room, Execution, API, module, job, or new service code may call these helpers directly. The synchronous assembly helpers are a narrower temporary exception to the design invariant that cross-module methods are async/protocol-based; they exist only because the legacy `ContextAssemblyService` public API is synchronous until callers migrate to async protocol methods.

Content-storage compatibility helpers convert repository dicts to legacy service return shapes. `content_get_content_by_document_id()` and `content_get_content_by_turn_id()` return `doc["content"]` or `None`, never the raw repository dict. `content_expand_mongodb_reference()` accepts a primitive dict form of the legacy `ContentReference`, supports only `storage_type="mongodb"`, raises the Context & Memory `ContentExpiredError` when missing, and leaves S3/URL behavior to the legacy service adapter.

Room-memory CRUD and lifecycle compatibility helpers accept and return primitive full room-memory dicts. `initialize_or_update_room_memory()` returns the full updated room-memory document so `RoomMemoryService` can reconstruct `RoomCenterMemoryResponse.memory`; `RoomMemoryInfo` remains only for the Common `MemoryManager.get_room_memory()` protocol. `add_agent_response_to_memory()` returns `(modified, matched)` so the adapter can preserve legacy `404` for missing memory and `500` for failed update. `services.memory_service.RoomMemoryService` owns conversion between `models.memory.RoomMemory` and dicts so `context_memory/**` does not import `models.*`. Adapter tests must assert `RoomCenterMemoryResponse.memory` remains a legacy `RoomMemory` instance for create/get/update/initialize paths and `None` for delete paths.

Search compatibility helpers return a primitive response state, not only result rows. `legacy_search()` preserves the current public signature order `search(query, room_id, user_id=None)`; Phase 5 does not use `user_id` for ranking but must accept and pass it through adapter tests. The response dict must include `query`, `room_id`, `results`, `total_matches`, `search_time_ms`, `searched_at`, and response-level flags: `vector_search_used`, `keyword_search_used`, `temporal_decay_applied`, and `mmr_applied`. `services.memory_search_service.MemorySearchService` converts that dict into legacy `models.search.MemorySearchResponse`.

Compaction compatibility helpers preserve the existing service signatures. `compact_room_memory()` accepts an optional primitive room-memory dict converted from legacy `RoomMemory`; `expand_turn_content_from_turn()` accepts a primitive turn dict converted from legacy `ConversationTurn` and preserves legacy exceptions for compact turns missing content refs; `fetch_turn_content()` keeps the legacy argument order `(turn_id, room_id)` and returns legacy error strings instead of raising. `compact_if_needed()` maps skipped/below-threshold `CompactionResult` from protocol `run_compaction()` back to legacy `None`. `services.compaction_service.CompactionService` owns legacy model conversion before calling these helpers.

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
async def ensure_room_memory(self, room_id: str, defaults: dict) -> dict: ...
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

async def push_and_trim_conversation_turn_if_absent(
    self,
    room_id: str,
    turn: dict,
    *,
    turn_id: str,
    max_turns: int,
    summary_stub: str,
    max_summary_chars: int,
) -> tuple[bool, bool, bool]: ...

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
async def list_room_ids_with_memory(self, limit: int | None = None) -> list[str]: ...
```

`create_room_memory()` stores and returns the legacy `memory_id` field from the supplied primitive document. If the adapter did not provide one, the facade/helper generates `memory_id` with the injected `id_factory()` before calling the repository. Do not substitute the Mongo insert `_id` for `memory_id` in legacy responses.

`ensure_room_memory(room_id, defaults)` is the only repository API used for race-safe first-message projection. It must perform `$setOnInsert` upsert semantics keyed by `room_id`, then return the stored room-memory document. It must not overwrite an existing room memory with `defaults`. Tests must cover two concurrent callers receiving the same stored memory document and only one inserted room-memory row.

Keep repository inputs and outputs as dicts. The repository must not return `models.memory.RoomMemory`, `ConversationTurn`, or other legacy models.

Projected raw-message idempotency must use a defined persisted field, not arbitrary extra fields that legacy `ConversationTurn` would drop. For turns created by `project_message()`, set `turn_id = f"message:{message_id}"` and use that deterministic id to detect duplicates. Direct legacy compatibility helpers may keep legacy UUID turn ids.

`push_and_trim_conversation_turn()` returns `(modified, matched)`. Implement it with `MongoCollection.find_one_and_update(..., upsert=False, return_document=True)` and a minimal projection, not `MongoCollection.update_one()`, because the Common `update_one()` contract currently returns only `bool` and does not expose `matched_count`. Treat `matched = returned_doc is not None`; treat `modified = matched` because the pipeline always pushes a new turn and updates memory metadata when a room memory exists. Write failures should raise. Test fakes must cover both `None` return for missing room and returned document for successful mutation.

`push_and_trim_conversation_turn_if_absent()` is the atomic projection path for `MessageCommitted` retries. It must use a query that only matches when no existing conversation turn has the deterministic projected `turn_id` in either supported history shape:

```python
{
    "room_id": room_id,
    "memory_content.conversation_history.turn_id": {"$ne": turn_id},
    "conversation_history.turn_id": {"$ne": turn_id},
}
```

Apply the same push/trim pipeline only when that conditional query matches. If `find_one_and_update()` returns no document, perform a follow-up `get_room_memory(room_id)` read to distinguish missing room from duplicate turn:
- no room document: `(modified=False, matched=False, already_exists=False)`
- room exists and either history shape already contains `turn_id`: `(modified=False, matched=True, already_exists=True)`
- room exists but `turn_id` is still absent: retry the conditional update once, then raise/write-fail if it still cannot determine a state

This preserves atomic duplicate prevention while still giving the facade enough information to keep missing-room and duplicate-event behavior distinct.

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
- `get_content_by_document_id(document_id)` must call a DAL-owned stable-or-native-id lookup, not construct provider-native `_id` queries in Common or `context_memory/**`.
- For legacy compacted turns whose `content_ref.document_id` is a Mongo ObjectId string and whose stored document lacks `document_id`, add `MongoCollection.find_one_by_stable_or_native_id(stable_id_field: str, id_value: str) -> dict | None` to the Common DAL protocol and implement the optional BSON/ObjectId conversion inside `dal/mongo/client.py`. Common defines only the protocol shape; it must not import `bson`, mention `ObjectId` in helper code, or expose provider-specific query builders.
- Repository tests must cover both stable string `document_id` lookup and legacy `_id`/ObjectId-string fallback.
- Add a `conversation_content.document_id` index. Preferred: unique partial index on `document_id` for documents where the field exists, so legacy documents without `document_id` remain valid while new stable ids are protected.

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

`render_user_memory_content(doc)` must use this exact stable format, omitting empty sections and joining remaining lines with `"\n"`:
- `Communication Style: {communication_style}`
- `Preferences: {key1}={value1}; {key2}={value2}` with preference keys sorted lexicographically and each value rendered by stable JSON serialization (`json.dumps(value, sort_keys=True, separators=(",", ":"))`) for dict/list values
- `Preferred Agents: {agent1}, {agent2}` in stored order
- `Facts: {fact_content_1}; {fact_content_2}` in stored order, reading each fact's `content`

If all sections are empty, return `""`. Add DTO mapping tests before implementation. Do not change the Common DTO unless these tests prove the existing shape is unusable.

`RoomMemoryInfo.content` must use this exact stable format, omitting empty sections and joining remaining lines with `"\n"`:
- `Summary: {room_summary.summary or room_summary.current_goal}` if present
- `Recent Turns: {one_liner_1}; {one_liner_2}; {one_liner_3}` from the three most recent turn notes with `one_liner`
- `Facts: {fact_content_1}; {fact_content_2}; ...` sorted by `content`

If all sections are empty, use `""`.

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

`ContextMemoryDeps` stays protocol-only to match the design doc. The application shell may keep a startup-local `context_memory_facade` variable for C3 adapter binding before narrowing the same object into `ContextMemoryDeps`, but the concrete facade must not be exposed through the deps object or passed to new module consumers.

### Event Handler

Add the handler now, but do not create a new bus:

```python
from collections.abc import Awaitable, Callable

class ContextMemoryEventHandler:
    def __init__(self, projector: MemoryProjector, project_for_event: Callable[[str, str], Awaitable[dict]]) -> None:
        self._projector = projector
        self._project_for_event = project_for_event

    async def handle_message_committed(self, event: MessageCommitted) -> None:
        try:
            status = await self._project_for_event(event.room_id, event.message_id)
            if status.get("projected") or status.get("reason") == "duplicate":
                await self._projector.run_compaction(event.room_id)
        except Exception:
            logger.exception("Context & Memory projection failed", extra={"room_id": event.room_id, "message_id": event.message_id})
            raise
```

`MemoryProjector.run_compaction()` is threshold-gated in Phase 5: it must check compaction eligibility and return a zero-count `CompactionResult` when compaction is not needed. The event handler must not force compaction after every message.

Failure behavior for the registered `EventPublisher` handler is log-and-re-raise. EventPublisher owns retry/dead-letter visibility, so the handler must not swallow exceptions once registered. If Phase 5 adds a direct best-effort wrapper for tests or transitional manual calls, that wrapper may catch/log and suppress failures, but it must not be the callable registered with `EventPublisher`.

If a concrete implementation of the Common `EventPublisher` protocol is available during startup, register the handler:

```python
event_publisher.register_internal_handler(
    "message_committed",
    context_memory_event_handler.handle_message_committed,
)
```

This registration is future-facing only until Room/Execution actually emit `MessageCommitted`. Direct legacy calls remain the live path in Phase 5. Do not register this handler against the current `infrastructure.event_broker.EventBroker`; that broker uses `set_handler` and is not the Common internal `EventPublisher`.

## Implementation Order

Parallelization note: Tasks 2 and 3 are contract prerequisites for the implementation slices. Complete or stabilize repository protocols, DAL protocol decisions, DTO/model translators, and fake contracts before workers implement Tasks 4-7. After those contracts are stable, Tasks 4, 5, and 6 can run in parallel only if they keep writes to disjoint helper modules and tests. Task 7 depends on the Task 6 `store_full_content()` helper contract; do not start Task 7 implementation until that helper signature and fake behavior are fixed. `context_memory/facade.py` is owned by Task 8 as the integration point; earlier workers should not edit it. Do not parallelize edits to the same legacy service file. The golden assembly tests in Task 4 should not be updated in parallel with context assembly code unless ownership is split between fixture generation and implementation.

Task granularity note: each numbered task is a work package, and the checkboxes below are the authoritative execution units. When a checkbox is broad, its "Cover", "Rules", "Implementation notes", and "Required TDD micro-steps" bullets define the smaller 2-5 minute TDD actions: write or adjust the smallest failing test, make the minimal production change, run the task-local command, then continue. Do not replace these steps with a separate high-level plan, and do not treat a broad checkbox such as "Implement compaction preparation" as one coding action. Subagents must report which listed checkbox or required micro-step they completed in their final handoff before code review.

Legacy test timeline note: before Task 9, existing legacy service tests are regression oracles and should stay unchanged except for fixture/oracle capture. Tasks 4, 5, 6, and 7 may run those tests to detect drift, but migrated adapter expectations are added only in Task 9 after `bind_facade()` exists.

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
if git cat-file -e HEAD:context_memory/facade.py 2>/dev/null; then
  git show HEAD:context_memory/facade.py | sed -n '1,220p'
fi
if git cat-file -e HEAD:context_memory/repository/mongo.py 2>/dev/null; then
  git show HEAD:context_memory/repository/mongo.py | sed -n '1,220p'
fi
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

Also add a repo-wide non-protocol facade helper call-boundary test:
- Build the helper-name set from the "Non-protocol compatibility helpers allowed on `ContextMemoryFacade`" list in this plan, or keep a test constant with the exact same names and fail if the lists drift.
- Scan AST calls across Python files and reject any call to those helper names outside the allowed adapter paths listed in "Interface Definitions".
- Allow `project_message_for_event()` only from `context_memory/events.py` and tests.
- Fail if Room, Execution, API, module, job, or new service code calls helpers such as `legacy_search()`, `compact_if_needed()`, `index_turn_for_search()`, or `content_*()` directly; those paths must go through Common protocols or legacy service adapters.

Also add a repo-wide inbound import-boundary test for `context_memory`:
- Allowed importers: `context_memory/**`, `container.py`, `main.py`, tests, and the intended legacy service adapter files only (`services/context_assembly_service.py`, `services/compaction_service.py`, `services/memory_search_service.py`, `services/memory_service.py`, `services/content_storage_service.py`).
- Fail if `modules/**`, `jobs/**`, `api/**`, Room, Execution, or unrelated services import `context_memory` directly.

- [ ] **Step 5: Run and verify failure**

```bash
uv run python -m pytest tests/test_context_memory_protocols.py -q
```

Expected before implementation: FAIL because `context_memory` package, repositories, or `ContextMemoryDeps` are missing.

### Task 2: Extend and Implement Memory and Content Repositories

**Files:**
- Modify: `common/protocols/repository_protocols.py`
- Modify: `common/protocols/dal_protocols.py`
- Modify: `dal/mongo/client.py`
- Modify: `common/errors/base.py`
- Modify: `common/errors/__init__.py`
- Modify: `common/protocols/__init__.py`
- Modify: `docs/MODULAR_DECOUPLING_DESIGN.md`
- Create/modify: `context_memory/repository/mongo.py`
- Create/modify: `context_memory/repository/__init__.py`
- Create: `tests/test_context_memory_repository.py`
- Modify: `tests/test_common_foundation.py`
- Modify: `tests/test_dal_unit.py`

- [ ] **Step 1: Write memory repository contract tests**

Cover:
- `MemoryMongoRepository(mongo=fake_mongo)` calls `mongo.collection("room_memories")` and `mongo.collection("user_memories")`.
- `get_room_memory(room_id)` queries `{"room_id": room_id}` and returns a raw dict.
- `get_room_memory_by_memory_id(memory_id)` queries `{"memory_id": memory_id}`.
- `get_user_memories(user_id)` queries `user_memories` by `{"user_id": user_id}` and returns raw dicts for the Common `MemoryManager.get_user_memories()` protocol.
- `create_room_memory(memory)` inserts the supplied dict and returns the stored legacy `memory_id` field, not the Mongo inserted `_id`. Tests must fail if the repository returns `MongoCollection.insert_one()` directly.
- `ensure_room_memory(room_id, defaults)` uses `$setOnInsert` upsert semantics, returns the stored document, and does not overwrite existing memory fields.
- `upsert_room_memory(room_id, memory)` writes with upsert semantics.
- `update_room_memory_by_room_id()` applies `$set` by `room_id` after stripping `_id` and any other immutable fields from the update document.
- `update_room_memory_by_memory_id()` applies `$set` by `memory_id` only for repository-level coverage after stripping `_id` and immutable fields; the legacy adapter behavior is specified separately in Task 9 because the current public method is effectively read-only.
- `delete_room_memory(room_id)` deletes by `room_id`.
- `delete_room_memory_by_memory_id(memory_id)` deletes by `memory_id`.
- Repository outputs stay raw dicts, not `models.memory.RoomMemory`.

- [ ] **Step 2: Write atomic room-memory mutation tests**

Cover:
- `push_and_trim_conversation_turn()` preserves the existing pipeline update shape from `database/mongodb.py`.
- The method returns `(modified, matched)` and distinguishes missing room from write failure.
- The method uses `find_one_and_update(..., upsert=False, return_document=True)` rather than `update_one()`, deriving `matched` from whether a document is returned and `modified` from `matched`.
- `push_and_trim_conversation_turn_if_absent()` atomically skips duplicate deterministic projected `turn_id` values across both `memory_content.conversation_history` and direct `conversation_history`, then performs the follow-up read described in "MemoryRepository Additions" to return `(modified, matched, already_exists)` without conflating missing room and duplicate turn.
- `update_turn_notes()` uses positional `$` update for `memory_content.conversation_history.$.turn_notes`.
- `get_room_summary_projection()` fetches only `room_summary` and `room_facts`.
- `update_room_summary_atomic()` sets `room_summary`, optionally pushes new facts, and slices to `max_facts`.
- `compact_turns_bulk()` marks only matching full turns compact, clears `content`, sets `content_ref`, sets `estimated_tokens_compact`, increments `total_compactions`, and updates `last_activity_at`.
- If `compact_turns_bulk()` uses ordered `update_one(..., array_filters=[...])` instead of Common bulk operations, preserve legacy ordered `bulk_write` semantics as closely as the Common DAL allows: attempt turn updates in order, then attempt the final `total_compactions`/`last_activity_at` update even if earlier array-filter updates matched no turn. Raise/return `False` only on actual write exceptions. This means missing turn ids can produce fewer modified turn elements while still incrementing the compaction counter, matching legacy behavior.
- `list_room_ids_with_memory(limit=None)` returns all room ids from `room_memories` without loading full documents and without relying on a single uncapped `MongoCollection.find()` call. Because `MongoCollectionAdapter.find()` currently caps unbounded results at 1000, implement this method with explicit pagination in batches, or first change the DAL contract and tests so `find(..., limit=None)` truly means unbounded. Tests must seed more than 1000 ids and prove none are skipped.

- [ ] **Step 3: Write content storage repository tests**

Cover:
- `ContentStorageMongoRepository(mongo=fake_mongo)` calls `mongo.collection("conversation_content")`.
- `upsert_full_content()` is idempotent for `(room_id, turn_id)`, stores a stable string `document_id` field, and returns the existing `document_id` on repeat.
- `upsert_full_content()` backfills `document_id` and returns the stable id when it finds an existing legacy `(room_id, turn_id)` document missing `document_id`.
- `get_content_by_document_id()` first queries `{"document_id": document_id}` for new documents.
- `get_content_by_document_id()` falls back through `MongoCollection.find_one_by_stable_or_native_id("document_id", document_id)` for existing compacted content that only has a provider-native `_id` string in `content_ref.document_id`; `context_memory/**` and Common helper modules must not import `bson` or construct native `_id` queries.
- `get_content_by_turn_id()` queries `{"room_id": room_id, "turn_id": turn_id}`.
- `delete_content_by_turn_id()` deletes by room and turn.
- `delete_content_by_room_id()` deletes all stored content for a room and returns count.
- `get_content_stats_for_room()` mirrors current aggregate output.
- `text_search()` performs Mongo `$text` query with score projection, sorts by Mongo `textScore` descending before limiting, and searches over `content`, `turn_notes.keywords`, `turn_notes.entities`, and `turn_notes.one_liner`.
- `hydrate_turn_notes()` fetches `turn_id` and `turn_notes` for a set of turn ids.

- [ ] **Step 4: Extend repository protocols only as needed**

Use the additions listed in "MemoryRepository Additions" and "ContentStorageRepository Protocol". Keep protocols domain-scoped; do not expose generic `find(query)`, raw collection access, or direct Motor/PyMongo result objects.

Also update `tests/test_common_foundation.py` in the same task:
- Add any accepted `MemoryRepository` additions to the exact expected method set.
- Add `ContentStorageRepository` to the expected repository protocol method-set assertions if the protocol is exported from `common.protocols`.
- Keep this test in Task 2 so protocol drift fails with the repository work, not late in final gates.

Also update `MongoCollection` in `common/protocols/dal_protocols.py` and fakes:
- `find_one_and_update(query: dict, update: dict | list[dict], **kwargs) -> dict | None`
- `update_one(query: dict, update: dict | list[dict], **kwargs) -> bool`
- `find_one_by_stable_or_native_id(stable_id_field: str, id_value: str) -> dict | None`
- Test fakes must accept pipeline-list updates because the legacy room-memory push/trim operation uses MongoDB aggregation pipeline updates.
- `dal/mongo/client.py` owns provider-native id fallback. Its implementation may use `bson.ObjectId` internally after first querying `{stable_id_field: id_value}`; no Common module may import or expose BSON.

Also update `docs/MODULAR_DECOUPLING_DESIGN.md` in Task 2:
- Add the accepted `MemoryRepository` additions.
- Add `ContentStorageRepository`.
- Add the DAL-owned stable-or-native-id lookup method and state that BSON/ObjectId conversion stays inside the Mongo DAL implementation, not Common or business modules.

Also add `VectorIndexUnavailableError` to `common.errors`:
- DAL implementations catch provider-specific missing-index/unavailable-index exceptions and raise `VectorIndexUnavailableError(index_name, operation)`.
- `context_memory/search.py` catches only this Common error for unavailable-index fallback metadata; it must not inspect Pinecone exception classes or strings.

- [ ] **Step 5: Implement `MemoryMongoRepository`**

Implementation notes:
- Constructor accepts `mongo: MongoDAL`, optional `collection_name: str = "room_memories"`, and optional `user_collection_name: str = "user_memories"`.
- `MemoryMongoRepository` does not own production index creation in Phase 5. If a future branch moves all indexes into `IndexRegistry`, add an optional `index_registry` here and register the full room/user/agent memory index set in the same task.
- Store `self._memories = mongo.collection(collection_name)`.
- Store `self._user_memories = mongo.collection(user_collection_name)`.
- Use `MongoCollection.find_one`, `find`, `insert_one`, `update_one`, `find_one_and_update`, `delete_one`, and `aggregate`.
- `create_room_memory()` calls `insert_one()` but returns `memory["memory_id"]`; if absent, raise a clear error because id generation belongs in the facade/helper.
- `ensure_room_memory()` uses `find_one_and_update({"room_id": room_id}, {"$setOnInsert": defaults_with_room_id}, upsert=True, return_document=True)` or an equivalent DAL-supported pattern, then returns the stored document.
- `update_room_memory_by_room_id()` and repository-level `update_room_memory_by_memory_id()` sanitize update dicts by removing `_id` and immutable identity fields before `$set`.
- For `push_and_trim_conversation_turn()`, use `find_one_and_update()` to preserve matched/missing-room semantics; do not rely on `update_one()` because it returns only `bool`.
- For `push_and_trim_conversation_turn_if_absent()`, use the conditional update plus follow-up read described above; fake collections must support both nested history paths.
- Do not import `database.mongodb`, `pymongo`, `motor`, or `models.memory`.
- For `compact_turns_bulk()`, prefer Common protocol support for ordered update operations if added; otherwise use ordered `update_one(..., array_filters=[...])` calls and document the equivalence to the current ordered `bulk_write`.

Required TDD micro-steps for this checkbox:
1. Add/green constructor collection-selection test.
2. Add/green read method tests: `get_room_memory()`, `get_room_memory_by_memory_id()`, `get_user_memories()`.
3. Add/green create/ensure tests: `create_room_memory()` returns `memory_id`; `ensure_room_memory()` uses `$setOnInsert`.
4. Add/green sanitized update tests for room id and memory id.
5. Add/green delete tests for room id and memory id.
6. Add/green `push_and_trim_conversation_turn()` matched/modified tests.
7. Add/green `push_and_trim_conversation_turn_if_absent()` duplicate/missing-room tests across both history shapes.
8. Add/green summary projection/update tests.
9. Add/green `compact_turns_bulk()` ordered update tests.
10. Add/green paginated `list_room_ids_with_memory()` test with more than 1000 ids.

NOTE: `MongoCollection.update_one(query, update, **kwargs)` passes `array_filters` through `**kwargs`. Fake `MongoCollection` implementations in tests MUST support `array_filters` kwarg to test compaction updates. If this implicit contract is insufficient, extend the `MongoCollection` protocol with an explicit `array_filters: list[dict] | None = None` parameter in Phase 5. Document the decision in Task 2 Step 4.

- [ ] **Step 6: Implement `ContentStorageMongoRepository`**

Implementation notes:
- Constructor accepts `mongo: MongoDAL`, optional `collection_name: str = "conversation_content"`, and optional `index_registry` only for tests/future registry migration. Production Phase 5 index creation remains in `database/mongodb.py`.
- Preserve unique `(room_id, turn_id)` upsert semantics.
- Preserve current `content_hash`, `stored_at`, `expires_at`, and `turn_notes` fields.
- Store and return a stable string `document_id` field for new documents. If the collection already has legacy docs without `document_id`, support provider-native id fallback only through `MongoCollection.find_one_by_stable_or_native_id("document_id", document_id)`.
- For an existing legacy document matched by `(room_id, turn_id)` but missing `document_id`, backfill the stable `document_id` in the upsert path and return that stable id. Use `find_one_and_update()` with `$set` for `document_id` plus `$setOnInsert` for content fields, or an equivalent two-step operation covered by tests.
- Do not import `services.content_storage_service`, `database.mongodb`, `bson`, `pymongo`, or `models.compaction` inside `context_memory/**`.
- Ensure the production index path creates a unique partial index for `document_id` in addition to the existing unique `(room_id, turn_id)` and text indexes.
- Text index creation: The `conversation_content` collection requires a MongoDB text index on full content and compact turn notes for BM25 keyword search: `content`, `turn_notes.keywords`, `turn_notes.entities`, and `turn_notes.one_liner`.
- Required Phase 5 production path: keep `database.mongodb.create_context_memory_indexes()` as the authoritative startup index owner. Update that function with the `document_id` unique partial index and expanded text index while preserving the existing room memory, user memory, agent memory, and TTL indexes.
- `IndexRegistry` is test/future scaffolding only in Phase 5. Do not construct `IndexRegistryImpl` or call `index_registry.ensure_all()` from `main.py` for this phase unless the same implementation task moves the full legacy index set into registry registrations.
- Test fakes simulate text search over both `content` and the three `turn_notes` fields without a real index.

- [ ] **Step 7: Run repository tests**

```bash
uv run python -m pytest tests/test_context_memory_repository.py tests/test_context_memory_protocols.py tests/test_common_foundation.py tests/test_dal_unit.py -k "repository or package or protocol_methods or stable_or_native_id" -q
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
- `CompactionConfig.concurrency` exists even though legacy stores this as module-level `COMPACTION_CONCURRENCY`; default it from the same environment/settings source and test that it matches the current legacy default.
- `ContextMemoryLLMConfig` exposes explicit `turn_notes_model` and `summary_model` defaults of `"context_memory_legacy_json_model"`, a logical model registered to concrete OpenAI model id `"gpt-4o-mini"`, so legacy structured-call model selection does not drift through unrelated logical-model defaults.
- Tests can inject explicit config objects without monkeypatching global settings.

- [ ] **Step 2: Implement `context_memory/config.py`**

Rules:
- Import `common.config.settings`, not `config.settings`.
- Keep property names aligned with legacy `models.context_config`.
- Allow dataclass overrides for golden tests.
- Do not mutate global settings.
- Keep config objects injectable through `ContextMemoryFacade` and `create_context_memory_facade()`; do not read global settings inside assembly, compaction, search, or LLM helper functions after construction.

- [ ] **Step 3: Audit `common/utils/context_utils.py` pure subset**

Identify which functions from `common/utils/context_utils.py` can be safely called from `context_memory/**`:
- SAFE (no deferred model/service imports): `estimate_tokens()`, `extract_turn_notes()`, `MAX_CONTEXT_CHARS`, `CHARS_PER_TOKEN_ESTIMATE`, `MAX_HISTORY_TURNS`, `MAX_SUMMARY_CHARS`, `LLM_TURN_NOTES_THRESHOLD`, `clean_mention_format()`
- UNSAFE (deferred `from models.memory import ...` or `from services.*`): `add_turn_to_history()`, `extract_turn_notes_llm()`, and any function importing `TurnRole`, `TurnType`, `ConversationTurn`, or `ContentType`
- `build_turn_content()` is not in `common/utils/context_utils.py`; it lives in `services/room_services.py` and must be ported as a pure projection helper if needed.

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
- Do not modify `tests/test_context_assembly_service.py` in Task 4 except to capture fixed golden fixture output in a test-only oracle file; adapter binding updates belong to Task 9.
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
- first three recent agent contributions under `Recent Agent Work`
- first three important constraints under `Constraints`
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

- [ ] **Step 6: Design `assemble_context()` algorithm**

This step documents the algorithm as a design spec for Task 8 Step 5. No test targeting `ContextMemoryFacade.assemble_context()` should be added in Task 4 - Task 4 tests cover only the pure synchronous assembly helper functions directly.

Mapping:
- Load room memory via `MemoryRepository.get_room_memory(room_id)` - this is the PRIMARY data source for context assembly (conversation history, summary, facts).
- Extract current task text from a projected turn whose `turn_id == f"message:{message_id}"`, OR use `RoomHistoryReader.get_messages_by_ids([message_id])` as fallback if the turn has not yet been projected. Do not try to match legacy direct UUID turn ids to `message_id`.
- If `agent_id is None`, call supervisor helper with no agent registry and no search snippets unless those inputs are made available through a future protocol.
- If `agent_id` is provided, call agent helper and pass only `agent_id`; `agent_name`, `room_awareness`, `quoted_text`, and system-instruction controls are not part of the Common protocol and remain exact only through compatibility helpers.
- Per-call budget formula: `available_for_content = max(0, token_budget - fixed_reserve_tokens)`, where `fixed_reserve_tokens` preserves the legacy budget reserves from `TokenBudgetConfig`. Apply the same dynamic allocation percentages to that available content. If `token_budget <= fixed_reserve_tokens`, stable prefix may exceed available content; retain legacy behavior by logging/metadata rather than raising.
- Store `message_id` and `agent_id` in `AssembledContext.metadata`.

NOTE: `RoomHistoryReader` is used primarily by `project_message()` (MemoryProjector), NOT by the assembly step. Context assembly works on PROJECTED conversation turns stored in room_memories, not raw room messages.

- [ ] **Step 7: Add golden equivalence fixtures**

Before Task 9 wraps `ContextAssemblyService`, capture fixed legacy golden outputs from the pre-wrapper implementation into committed fixture data, or preserve a test-only legacy oracle that is not affected by the C3 adapter. This Task 4 step is the required capture point; Task 12 verifies and expands these fixtures after adapters exist but must not create its first oracle after wrapping. After Task 9, golden tests must compare new output to those fixed outputs/oracle, not to the wrapped `ContextAssemblyService` singleton.

For each fixture, compare fixed legacy output to new synchronous assembly helper output. Do not require protocol `assemble_context()` to match fixtures that depend on legacy-only inputs such as agent registry, search snippets, agent display name, room awareness, quoted text, or system-instruction controls.
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

Expected: PASS with exact legacy token-budget results for synchronous compatibility assembly helpers.

### Task 5: Port Projection, Room Memory Lifecycle, and Summary Updates

**Files:**
- Create: `context_memory/projection.py`
- Create: `context_memory/summary.py`
- Create/modify: `tests/test_context_memory_projection.py`
- Reference/run `tests/test_phase5_supervisor_integration.py`; do not add adapter binding assertions there until Task 9/12.
Do not modify `context_memory/facade.py` in this task; Task 8 owns facade integration.

- [ ] **Step 1: Write failing user-message projection tests**

Cover:
- `project_message(room_id, message_id)` reads raw message through `RoomHistoryReader.get_messages_by_ids()`.
- It verifies the fetched message `room_id` equals the supplied `room_id`; mismatches are logged and treated as no-op.
- User raw message creates a room memory if missing.
- Concurrent first-message projections for the same room do not create duplicate room-memory documents: creation uses repository room-id upsert / `$setOnInsert` semantics, reloads the room memory, then calls `push_and_trim_conversation_turn_if_absent()`. Do not catch `pymongo.errors.DuplicateKeyError` inside `context_memory/**`; PyMongo details stay behind the DAL/repository.
- Direct `initialize_or_update_room_memory()` compatibility cleans `@mention` UUIDs the same way legacy code does when `room_agent_set` is supplied.
- Event projection is best-effort in Phase 5 because current `RoomHistoryReader` does not provide `room_agent_set`; do not claim exact mention cleanup for `MessageCommitted` projection. Event projection may apply only safe generic cleanup. Direct `initialize_or_update_room_memory()` compatibility remains the exact legacy path for user-message writes.
- Attachments from `RoomMessageInfo.content` are represented by the same pure `build_turn_content()` output as legacy adapter conversion.
- Turn contains `role="user"`, `user_id`, content, token estimate, turn notes, timestamp, and default representation `full`.
- Agent event projection returns no-op with a logged warning when room memory is missing, matching legacy 404 semantics rather than creating memory.
- Agent message projection is best-effort for `agent_name` because current `RoomHistoryReader` does not populate agent-name metadata; preserve `agent_id`, use `sender_name` if present, otherwise document fallback and keep direct `add_agent_response_to_memory()` compatibility tests exact.
- Agent message response text extraction from `RoomMessageInfo.content` is deterministic: if content is a string, use it; if it is a dict, prefer `message_text`, then `response_text`, then `response`, then `content`, then `message`, then `text`; otherwise render a stable JSON string with sorted keys. Missing/empty text logs and no-ops.
- Projected raw-message turns use deterministic `turn_id = f"message:{message_id}"` so projection idempotency survives legacy `RoomMemory` reconstruction.
- `total_messages` and `last_activity_at` update through repository atomic push-if-absent mutation.

- [ ] **Step 2: Implement raw message to turn projection**

Rules:
- Consume `RoomMessageInfo.content` and `RoomMessageInfo.metadata`; attachments and message body come from `content`, while optional sender/agent hints come from `metadata`.
- Do not import `models.room` or `services.room_services`.
- Port a pure equivalent of `services.room_services.build_turn_content()` into `context_memory/projection.py` and prove equivalence with dict attachments and `UserAttachment`-like attachment objects.
- Do not fetch room membership directly from Room, database, or services for `room_agent_set`; `MessageCommitted` projection is best-effort until a Common reader metadata path exists.
- Do not rely on arbitrary extra turn fields such as `source_message_id`; legacy `ConversationTurn` drops unknown fields. Use deterministic `turn_id` for source idempotency.
- Missing raw message should be logged and treated as no-op.
- Missing-room creation in event projection must be race-safe: call `MemoryRepository.ensure_room_memory(room_id, defaults)` with `$setOnInsert` defaults including a generated legacy-form `memory_id`, then continue through `push_and_trim_conversation_turn_if_absent()`. Do not catch PyMongo duplicate-key classes in `context_memory/**`.

- [ ] **Step 3: Write failing legacy room memory lifecycle tests**

Cover:
- `initialize_or_update_room_memory()` creates memory on first user message.
- Existing memory gets a pushed and trimmed user turn.
- `_track_user_interaction()` behavior stays in the legacy adapter in Phase 5. Record the required adapter assertion here, but add that runnable adapter test in Task 9 after `RoomMemoryService.bind_facade()` exists.
- Return mapping from lifecycle compatibility helpers preserves the full primitive room memory document for adapter reconstruction.

- [ ] **Step 4: Implement lifecycle compatibility helpers**

Use `MemoryRepository` atomic methods. Keep user/agent side-effect counter writes in the legacy adapter in Phase 5: `_track_user_interaction()` and agent call stats remain outside `context_memory/**` until a later user/agent-memory extraction. Add runnable adapter tests proving those legacy side effects still run in Task 9, not in Task 5.

- [ ] **Step 5: Write failing agent response and synthesis tests**

Cover:
- `add_agent_response_to_memory()` creates an `agent` turn with `agent_id`, `agent_name`, `was_successful`, token estimate, and turn notes.
- Missing room memory returns `(modified=False, matched=False)` so the adapter can return legacy `404`.
- Successful long agent response schedules LLM turn-note enrichment through injected `LLMProvider`.
- `add_synthesis_to_history()` creates a `supervisor` turn and enriches content with trajectory contributions exactly like legacy.
- The synthesis method returns the new `turn_id`.
- Summary stubs match legacy strings.

- [ ] **Step 6: Implement agent and synthesis helpers**

Rules:
- Keep the same `MAX_HISTORY_TURNS`, `MAX_SUMMARY_CHARS`, `LLM_TURN_NOTES_THRESHOLD`, `estimate_tokens`, and `extract_turn_notes` behavior from `common.utils.context_utils`.
- Reimplement `extract_turn_notes_llm()` behavior exactly: truncate content to 3000 chars, use the legacy prompt content, call `LLMProvider.generate_structured(messages, schema=TURN_NOTES_SCHEMA, model=llm_config.turn_notes_model)`, read `response.data`, slice keywords/entities/tags to the same limits, and fall back to heuristic `extract_turn_notes()` on failure. `TURN_NOTES_SCHEMA` is a plain JSON-schema `dict`, not a Pydantic class.
- Define `TURN_NOTES_SCHEMA` in `context_memory/projection.py` next to the prompt builder and test that the fake `LLMProvider` receives that dict unchanged.
- Fire-and-forget background tasks must go through the injected `background_task_runner`. Tests inject a deterministic sync runner that records the coroutine, then the test awaits the recorded coroutine explicitly; helper tests must not depend on global `asyncio.create_task()`.

- [ ] **Step 7: Write failing room summary update tests**

Port coverage from `tests/test_phase5_supervisor_integration.py`:
- Builds the same extraction prompt.
- Calls LLM structured JSON through `LLMProvider.generate_structured(messages, schema=ROOM_SUMMARY_EXTRACTION_SCHEMA, model=llm_config.summary_model)` and reads `response.data`. `ROOM_SUMMARY_EXTRACTION_SCHEMA` is a plain JSON-schema `dict`.
- Define `ROOM_SUMMARY_EXTRACTION_SCHEMA` in `context_memory/summary.py` next to the prompt builder and test that the fake `LLMProvider` receives that dict unchanged.
- Both `TURN_NOTES_SCHEMA` and `ROOM_SUMMARY_EXTRACTION_SCHEMA` must be strict OpenAI-compatible JSON schema dicts: top-level `type: "object"`, explicit `properties`, complete `required` list for every property the model may return, and `additionalProperties: False` at every object level.
- Loads summary projection only.
- Merges missing fields with existing summary.
- Deduplicates new facts case-insensitively.
- New fact primitive shape: `{"fact_id": id_factory(), "content": str, "confidence": 1.0, "created_at": now(), "source_turn_id": synthesis_turn_id}` unless the extracted fact explicitly includes a legacy-compatible confidence value.
- Writes `updated_after_turn_id`.
- Returns false on LLM failure or missing memory.

- [ ] **Step 8: Implement `context_memory/summary.py`**

Use injected `LLMProvider.generate_structured()` with explicit JSON-schema dicts for summary extraction and room facts. Do not import `services.openai_service`.

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
- Reference/run `tests/test_memory_search_service.py` and `tests/test_context_memory_bugfixes.py`; adapter expectation updates belong to Task 9/12.
- Modify: `common/protocols/dal_protocols.py`
- Modify: `dal/pinecone/client.py`
- Modify: `docs/MODULAR_DECOUPLING_DESIGN.md`
- Modify: `tests/test_common_foundation.py`
- Modify: `tests/test_dal_protocols.py`
- Modify: `tests/test_dal_unit.py`
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
- Keyword search preserves legacy Mongo behavior by sorting text matches by textScore before applying the limit.
- Hydration uses `ContentStorageRepository.hydrate_turn_notes()`.
- Search output maps to Common `MemorySearchResult`; top-level `MemorySearchResult.score` is the final combined score after vector/keyword merge, temporal decay, and MMR selection. Raw component scores live only in `metadata`.

- [ ] **Step 4: Implement vector and keyword search**

Rules:
- No Pinecone imports.
- No `database.pinecone_db`.
- No `services.openai_service`.
- Preserve vector failure fallback and keyword failure fallback.
- Store enough legacy reconstruction data in Common `MemorySearchResult.metadata`: `turn_id`, `source_type`, `content_preview`, `vector_score`, `keyword_score`, `combined_score`, `temporal_decay_factor`, `timestamp`, `role`, `agent_name`, `is_compact`, `can_expand`, and any disabled/failure flags needed by `legacy_search()`.
- `legacy_search()` returns a full primitive legacy response dict, not only the Common result list, so adapters can preserve response-level fields such as vector/keyword usage and failure flags.

- [ ] **Step 5: Write failing indexing and delete tests**

Cover:
- `index_turn_for_search()` skips empty content.
- Embedding uses `LLMProvider.embed(content)`.
- Upsert uses `VectorDAL.upsert(index, [VectorRecord(...)])`.
- Metadata includes `room_id`, `turn_id`, `role`, `agent_name`, and timestamp.
- `delete_room_index()` deletes all room vectors through `VectorDAL.delete_by_filter(index, {"room_id": {"$eq": room_id}})`.
- Memory vector availability is inferred from operations against the configured memory search index name, not from `VectorDAL.ping()` because that method has no index parameter. Preserve legacy "index unavailable" response metadata when `search(memory_index, ...)`, `upsert(memory_index, ...)`, or `delete_by_filter(memory_index, ...)` raises Common `VectorIndexUnavailableError`. Do not add `VectorDAL.ping_index()` in Phase 5 unless a later review explicitly requires preflight checks.

- [ ] **Step 6: Extend `VectorDAL` with delete-by-filter**

Current `VectorDAL.delete(index, ids)` cannot express Pinecone delete-by-filter used by legacy `delete_room_index()`. Phase 5 chooses the protocol extension path, not vector-id tracking.

Required changes:
- Add `delete_by_filter(index: str, filter: dict) -> None` to `VectorDAL`.
- Implement it in `dal/pinecone/client.py`.
- Add `VectorIndexUnavailableError` handling in `dal/pinecone/client.py` for search, upsert, and delete-by-filter index-unavailable failures.
- Update `docs/MODULAR_DECOUPLING_DESIGN.md` in this same step so the VectorDAL protocol shown there includes `delete_by_filter()` and the Common error contract for unavailable vector indexes.
- Update `tests/test_common_foundation.py` expected `VectorDAL` method set.
- Update `tests/test_dal_protocols.py` runtime conformance for `VectorDALImpl`.
- Update `tests/test_dal_unit.py` so concrete delete tests cover both id-based delete and filter delete.

- [ ] **Step 7: Write failing content storage helper tests**

Port coverage from `tests/test_compaction_service.py`:
- `hash_content()` deterministic SHA-256.
- `store_full_content()` computes deterministic `document_id`, `content_hash`, `stored_at`, and `expires_at`, calls `ContentStorageRepository.upsert_full_content()` with the full protocol argument set, and returns the stable `document_id`.
- Idempotent upsert returns the stable string `document_id`.
- Existing legacy content doc missing `document_id` is backfilled by repository upsert and expands by the returned stable id.
- Expand by stable `document_id`.
- Expand by legacy provider-native `_id` string through the repository/DAL stable-or-native-id lookup; no Common ID helper exists.
- Expand by turn id.
- Missing content raises a Context & Memory local `ContentExpiredError`.
- Delete by turn and by room.
- Content stats match legacy shape.
- Pure helpers and repository-backed content storage functions return internal dicts or primitive storage values only; facade compatibility return-shape conversion is owned by Task 8.

- [ ] **Step 8: Implement content storage helpers**

Rules:
- `ContentExpiredError` lives in `context_memory/content_storage.py`.
- `services.content_storage_service` must import and re-export this exact class object (`from context_memory.content_storage import ContentExpiredError`) after migration. Do not keep a separate service-local exception class, or callers catching `services.content_storage_service.ContentExpiredError` will miss exceptions raised by `context_memory/**`.
- `store_full_content()` is the only helper compaction should call for full-content storage; it computes deterministic `document_id` and storage metadata before calling the repository.
- Pointer string rendering must preserve the legacy `ContentReference.to_compact_string()` format for MongoDB references. For newly compacted turns, the `document_id` segment comes from the stable `document_id` field and may differ from legacy Mongo `_id` values.
- `context_memory/**` supports MongoDB content expansion only. Do not import `services.s3_service`. S3 expansion pass-through remains in the legacy `ContentStorageService` adapter, and URL expansion remains `NotImplementedError`.

- [ ] **Step 9: Run search and content storage tests**

```bash
uv run python -m pytest tests/test_context_memory_search.py tests/test_memory_search_service.py tests/test_context_memory_bugfixes.py tests/test_common_foundation.py tests/test_dal_protocols.py tests/test_dal_unit.py -k "search or hydration or content or dal_implementations or vector" -q
```

Expected: PASS.

### Task 7: Port Lossless Compaction

**Files:**
- Create: `context_memory/compaction.py`
- Create/modify: `tests/test_context_memory_compaction.py`
- Reference/run `tests/test_compaction_service.py`; adapter expectation updates belong to Task 9/12.
- Modify: `tests/test_context_memory_bugfixes.py`
Do not modify `context_memory/facade.py` in this task; Task 8 owns facade integration.

Compaction helpers must receive indexing as an injected callback. Do not import `context_memory.facade`, legacy services, Pinecone, or OpenAI from `context_memory/compaction.py`. Suggested dependency shape:

```python
IndexTurnCallback = Callable[[str, dict], Awaitable[bool]]  # (room_id, turn_doc)
```

The facade wires this callback to the search/indexing helper in Task 8.

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
- Direct compatibility `compact_room_memory(room_id, room_memory_doc=None)` preserves legacy force behavior and compacts eligible older turns even when `should_compact()` would return false because thresholds are below limit.
- Protocol `run_compaction(room_id)` remains threshold-gated and returns skipped metadata below threshold.
- Older full turns are selected; recent full turns are preserved.
- Already compact turns are skipped.
- Turns with missing content are skipped.
- Content is persisted before memory pointers are written.
- Vector indexing is attempted through the injected callback after content storage and before pointer update.
- Vector indexing failure is logged and does not block compaction.
- Repository compact update only marks successfully prepared entries.
- `tokens_saved` is sum of `estimated_tokens_full - estimated_tokens_compact`.
- Errors are included in metadata or local result state without failing the entire operation.

- [ ] **Step 5: Implement compaction preparation**

Algorithm:
1. Compute turns to compact using legacy preserve-recent and threshold rules.
2. Prepare turns with bounded concurrency matching legacy `COMPACTION_CONCURRENCY`: use `asyncio.Semaphore(compaction_config.concurrency)` around content storage and indexing work. Preserve ordered output by collecting prepared entries in the original selected-turn order before calling the repository update.
3. For each turn, call `context_memory.content_storage.store_full_content()` with `room_id`, `turn_id`, `content`, `content_type`, and `turn_notes`. That helper computes `document_id`, `content_hash`, `stored_at`, and `expires_at`, then calls `ContentStorageRepository.upsert_full_content()` with the full protocol argument set.
4. Call the injected `IndexTurnCallback(room_id, turn_doc)` for that turn. If no callback is supplied in a unit test, skip indexing explicitly and record that in metadata.
5. Build `content_ref` dict with `storage_type="mongodb"`, `collection="conversation_content"`, `document_id`, `content_hash`, and created timestamp.
6. Build compacted entries for repository update.

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
- `expand_turn_content_from_turn()` returns `turn["content"] or ""` for full turns, raises `ValueError(f"Compact turn {turn_id} missing content reference")` for compact turns without `content_ref`, and raises `ContentExpiredError` when stored content is missing.
- `fetch_turn_content(turn_id, room_id)` preserves legacy exact error strings for missing room, missing turn, expired content, unsupported storage, and `ValueError`.
- `get_compaction_stats()` returns `{"error": f"Room {room_id} not found"}` for missing room; otherwise it returns total turns, full turns, compact turns, tokens saved, total compactions, and content storage stats.

- [ ] **Step 8: Implement expansion and stats**

Use `ContentStorageRepository` and normalized content references. Do not import legacy content storage service.

- [ ] **Step 9: Run compaction tests**

```bash
uv run python -m pytest tests/test_context_memory_compaction.py tests/test_compaction_service.py tests/test_context_memory_bugfixes.py -k "compaction or content" -q
```

Expected: PASS.

### Task 8: Implement ContextMemoryFacade and Event Handler

**Files:**
- Create/modify: `context_memory/__init__.py`
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
- `get_room_memory()` maps repository docs to `RoomMemoryInfo` with `content` rendered as a stable compact string from `room_summary`, recent conversation one-liners, and sorted room facts. The full structured document remains available only through `legacy_get_room_memory_*()` helpers.
- `search_memory()` delegates to search helper and returns Common DTO list.
- `get_user_memories()` maps repository user-memory docs to Common `UserMemory` using the explicit synthetic `memory_id`, stable rendered `content`, and metadata mapping from "UserMemory DTO Mapping".
- `delete_room_memory()` pre-reads `MemoryRepository.get_room_memory(room_id)` before deletion so it can distinguish missing memory from failed deletion while the repository delete method still returns `bool`. Missing memory is a successful no-op for the memory-delete category. If memory existed and `delete_room_memory(room_id)` returns false, treat that as an actual cleanup failure. Content delete count `0` and vector not-found/no-vector states are successful no-ops; repository/vector exceptions are failures. Callers that delete the Room record decide whether a false cleanup result fails the user-visible room delete.
- `legacy_*` room-memory CRUD helpers delegate to `MemoryRepository` and return primitive dict/bool results for `RoomMemoryService` adapter conversion.

- [ ] **Step 4: Implement MemoryProjector methods**

Cover:
- `project_message()` uses `RoomHistoryReader`.
- Add non-protocol `project_message_for_event(room_id, message_id) -> dict` for `ContextMemoryEventHandler`. It returns a primitive status such as `{"projected": True, "reason": "created"}` or `{"projected": False, "reason": "missing_message" | "room_mismatch" | "duplicate"}`. Protocol `project_message()` may still return `None`.
- `run_compaction()` delegates to the threshold-gated compaction helper and returns a zero-count result when below threshold.
- Idempotency: projecting the same raw message twice does not create duplicate turns if the repository already has a turn whose deterministic `turn_id` is `f"message:{message_id}"`. Do not rely on unknown source metadata fields that legacy `ConversationTurn` reconstruction would drop.

- [ ] **Step 5: Implement ContextAssembler method**

Cover:
- `assemble_context()` loads projected room memory via `MemoryRepository` first, then uses `RoomHistoryReader` only to recover current message text if the matching projected turn is missing.
- Fakes can drive exact token-budget output in tests.
- Missing message raises `ValueError(f"Message {message_id} not found in room {room_id}")`. Tests assert this exact behavior so implementations do not diverge between empty-context and exception semantics.

- [ ] **Step 6: Add facade content compatibility helper tests**

Cover:
- `content_upsert_full_content()` delegates to content storage helper and returns the stable document id string.
- `content_get_content_by_document_id()` converts repository dicts to legacy `str | None`.
- `content_get_content_by_turn_id()` converts repository dicts to legacy `str | None`.
- `content_expand_mongodb_reference()` accepts primitive content reference dicts, expands MongoDB content, and raises Context & Memory `ContentExpiredError` when missing.
- `content_delete_content_by_turn_id()`, `content_delete_content_by_room_id()`, and `content_get_content_stats_for_room()` return legacy `bool`, `int`, and stats-dict shapes.
- These tests live with `tests/test_context_memory_facade.py`; do not add facade assertions to Task 6 content storage tests.

- [ ] **Step 7: Implement facade content compatibility helpers**

Rules:
- Implement the `content_*` helpers listed in "Interface Definitions".
- Convert raw repository documents to legacy return shapes at the facade boundary.
- Support MongoDB content expansion only; S3 and URL behavior remain in `services.content_storage_service`.
- Wire compaction helpers with an `IndexTurnCallback` that calls the search/indexing helper; `context_memory/compaction.py` must not import the facade or search service singletons.

- [ ] **Step 8: Add `ContextMemoryEventHandler` tests**

Cover:
- `handle_message_committed()` calls `project_message_for_event()` on the concrete facade/helper when available, or a supplied projection-status callable in tests.
- It calls threshold-gated `run_compaction()` when projection status says a message was projected or the event was a duplicate. Duplicate retries may be recovering from a prior compaction failure, and `run_compaction()` is already below-threshold safe. It skips compaction only for missing-message and room-mismatch no-op statuses.
- The registered handler catches projection or compaction exceptions only to log room/message identifiers, then re-raises so `EventPublisher` owns retry/dead-letter visibility.
- If a separate direct-call best-effort wrapper is added, tests must prove that wrapper is not registered with `EventPublisher`.
- It is safe for user and agent message types.

- [ ] **Step 9: Implement event handler**

Do not import Delivery implementation. Accept the concrete `ContextMemoryFacade` or an injected projection-status callable plus `MemoryProjector`; do not rely on bare `MemoryProjector.project_message()` for event handling because the Common protocol returns no no-op status.

- [ ] **Step 10: Run facade and event tests**

```bash
uv run python -m pytest tests/test_context_memory_facade.py tests/test_context_memory_events.py tests/test_context_memory_protocols.py -k "not container and not deps" -q
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
- Adapter updates service-local metrics such as `_truncation_count` from `AssembledContext.metadata` so existing truncation counters and log behavior do not drift.

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
- `should_compact(room_id)` delegates to facade helper and returns `bool`.
- `compact_room_memory(room_id, room_memory=None)` converts optional legacy `RoomMemory` to a primitive dict and delegates to `compact_room_memory(room_id, room_memory_doc=...)`.
- `expand_turn_content(turn)` converts legacy `ConversationTurn` to a primitive dict and delegates to `expand_turn_content_from_turn()`.
- After bind, methods call facade compatibility helpers with legacy-compatible argument conversion.
- Result conversion to legacy `models.compaction.CompactionResult` preserves fields.

- [ ] **Step 4: Add `CompactionService.bind_facade()`**

Keep singleton name `compaction_service`. Remove direct construction of `database_service`, content storage, and memory search from active paths after bind.

- [ ] **Step 5: Write fail-fast binding tests for `MemorySearchService`**

Cover:
- `search()`, `index_turn_for_search()`, and `delete_room_index()` fail before bind.
- After bind, `search()` calls `legacy_search()` and returns legacy `MemorySearchResponse`, including response-level disabled/failure flags.
- After bind, indexing and delete delegate to facade helpers.

- [ ] **Step 6: Add `MemorySearchService.bind_facade()`**

Keep pure static helpers only if legacy tests import them directly; otherwise route through `context_memory.search`.

- [ ] **Step 7: Write fail-fast binding tests for `RoomMemoryService` only**

Cover:
- `RoomMemoryService` room-based methods fail before bind.
- `ChatMemoryService` remains unchanged and does not require facade binding.
- `create_room_memory()`, `get_room_memory_by_room_id()`, `get_room_memory_by_memory_id()`, `update_room_memory_by_room_id()`, and `delete_room_memory_by_memory_id()` delegate through the `legacy_*` facade helpers after bind.
- `update_room_memory_by_memory_id()` preserves the current legacy behavior: it reads by `memory_id` and returns the existing memory without applying `request.memory`. The facade helper returns the existing primitive doc or `None`; do not introduce a new write on this public path unless a separate behavior-change task updates legacy tests.
- The adapter converts legacy `RoomMemory` request/response models to primitive dicts before calling the facade, then reconstructs `models.memory.RoomMemory` for `RoomCenterMemoryResponse.memory`.
- `initialize_or_update_room_memory()` delegates after bind and preserves mention cleanup, attachment rendering, and `RoomCenterMemoryResponse` fields.
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
- Before bind, migrated MongoDB-backed methods raise `RuntimeError("ContentStorageService.bind_facade() not called - startup incomplete")`: `upsert_full_content()`, `get_content_by_document_id()`, `get_content_by_turn_id()`, MongoDB `expand_content_reference()`, `delete_content_by_turn_id()`, `delete_content_by_room_id()`, and `get_content_stats_for_room()`.
- Before bind, S3 and URL `expand_content_reference()` behavior remains legacy-local: S3 uses `services.s3_service`, and URL raises `NotImplementedError`.
- `ContentStorageService.upsert_full_content()` delegates after bind and returns the stable document id string.
- `ContentStorageService.get_content_by_document_id()` delegates after bind and returns the full content string or `None`, not the repository dict.
- `ContentStorageService.get_content_by_turn_id()` delegates after bind and returns the full content string or `None`, not the repository dict.
- `ContentStorageService.expand_content_reference()` delegates MongoDB references after bind and preserves `ContentExpiredError` behavior for missing MongoDB content.
- `ContentStorageService.expand_content_reference()` preserves existing S3 pass-through behavior locally: call `services.s3_service.s3_service.download_text(s3_key)`, return the downloaded string, and raise `ContentExpiredError(turn_id, s3_key)` when it returns `None`.
- `ContentStorageService.expand_content_reference()` continues to raise `NotImplementedError` for URL references.
- `ContentStorageService.delete_content_by_turn_id()`, `delete_content_by_room_id()`, and `get_content_stats_for_room()` delegate after bind with the same `bool`, `int`, and stats-dict return shapes.
- `services.content_storage_service.ContentExpiredError is context_memory.content_storage.ContentExpiredError` after migration, so old imports catch new Context & Memory expansion failures.
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
- Modify: `llm_gateway/model_registry.py`
- Modify: startup-related tests
- Modify: `tests/test_context_memory_protocols.py`

- [ ] **Step 1: Add container assembly tests**

Create tests that instantiate the container with fakes and assert:
- `create_context_memory_facade(...)` returns the concrete `ContextMemoryFacade` instance used only by application-shell adapter binding.
- `create_context_memory_deps(context_memory_facade)` returns protocol fields only and does not expose a `.facade` attribute.
- `ContextMemoryDeps.context_assembler` is a `ContextAssembler`.
- `ContextMemoryDeps.memory_manager` is a `MemoryManager`.
- `ContextMemoryDeps.memory_projector` is a `MemoryProjector`.
- All three protocol fields are the same object as the startup-local facade passed into `create_context_memory_deps(...)`.
- `create_context_memory_facade()` accepts `room_history_reader` from `RoomDeps`.
- `create_context_memory_facade()` accepts optional `memory_repository` and `content_repository` overrides for tests; when omitted, it constructs concrete repositories from `mongo`.
- `create_context_memory_facade()` may accept optional `index_registry` for repository unit tests/future refactor only; production startup in Phase 5 passes `None` and uses `database.mongodb.create_context_memory_indexes()`.
- `create_context_memory_facade()` accepts optional `token_budget`, `compaction_config`, `search_config`, `llm_config`, and `background_task_runner` overrides and passes them to the facade.
- Default `id_factory` produces `str(uuid4())` with hyphens to preserve legacy `memory_id`, turn id, and fact id format; do not use `uuid4().hex`.

- [ ] **Step 2: Implement `container.py` ContextMemoryDeps assembly**

Target:

```python
def create_context_memory_facade(
    *,
    mongo: MongoDAL,
    vector: VectorDAL,
    llm_provider: LLMProvider,
    room_history_reader: RoomHistoryReader,
    memory_repository: MemoryRepository | None = None,
    content_repository: ContentStorageRepository | None = None,
    index_registry: IndexRegistry | None = None,  # tests/future only; production passes None in Phase 5
    token_budget: TokenBudgetConfig | None = None,
    compaction_config: CompactionConfig | None = None,
    search_config: MemorySearchConfig | None = None,
    llm_config: ContextMemoryLLMConfig | None = None,
    background_task_runner: Callable[[Awaitable[Any]], None] | None = None,
) -> ContextMemoryFacade:
    memory_repository = memory_repository or MemoryMongoRepository(mongo=mongo)
    content_repository = content_repository or ContentStorageMongoRepository(mongo=mongo, index_registry=index_registry)
    facade = ContextMemoryFacade(
        memory_repository=memory_repository,
        content_repository=content_repository,
        room_history_reader=room_history_reader,
        vector=vector,
        llm_provider=llm_provider,
        id_factory=lambda: str(uuid4()),
        now=utcnow,
        token_budget=token_budget,
        compaction_config=compaction_config,
        search_config=search_config,
        llm_config=llm_config,
        background_task_runner=background_task_runner,
    )
    return facade


def create_context_memory_deps(facade: ContextMemoryFacade) -> ContextMemoryDeps:
    return ContextMemoryDeps(
        context_assembler=facade,
        memory_manager=facade,
        memory_projector=facade,
    )
```

- [ ] **Step 3: Instantiate ContextMemoryDeps during lifespan startup**

In `main.py`, after Mongo is available and before adapter binding:
- Reuse `mongo_dal`.
- Call updated `mongodb.create_context_memory_indexes()` inside the Mongo-available branch before building/binding Context & Memory adapters. Do not construct `IndexRegistryImpl` or call `index_registry.ensure_all()` in `main.py` for Phase 5.
- Extend `ModelRegistryImpl._register_defaults()` with `context_memory_legacy_json_model`, `model_id="gpt-4o-mini"`, provider `"openai"`, and `json_schema` capability.
- Instantiate `model_registry = ModelRegistryImpl()` in startup after that registration exists.
- Hoist `vector_dal = VectorDALImpl()` and `llm_provider = LLMGatewayImpl(model_registry=model_registry)` before constructing `AgentDeps`, then pass those same variables to both `create_agent_deps()` and `create_context_memory_facade()`.
- Build `RoomDeps`, then pass `_room_deps.room_history_reader` to `create_context_memory_facade()`.
- Pass `ContextMemoryLLMConfig(turn_notes_model="context_memory_legacy_json_model", summary_model="context_memory_legacy_json_model")`. Tests must assert `OpenAIProvider.generate_structured()` receives effective model id `gpt-4o-mini`.
- Build startup-local `context_memory_facade = create_context_memory_facade(...)`; this concrete object is application-shell binding state only and must not be stored on `ContextMemoryDeps`.
- Build `_context_memory_deps = create_context_memory_deps(context_memory_facade)` to expose only `ContextAssembler`, `MemoryManager`, and `MemoryProjector` protocol fields to module consumers.
- Bind `context_assembly_service.bind_facade(context_memory_facade)`.
- Bind `compaction_service.bind_facade(context_memory_facade)`.
- Bind `memory_search_service.bind_facade(context_memory_facade)`.
- Bind `room_memory_service.bind_facade(context_memory_facade)`.
- Bind `content_storage_service.bind_facade(context_memory_facade)`.
- Bind `room_services.bind_context_memory(_context_memory_deps.memory_manager)` for temporary room deletion cleanup. This binding accepts only the `MemoryManager` protocol; do not pass the concrete facade into Room services, do not introduce an alternate binding name in Phase 5, and do not overload the existing Room facade binding.
- Register `ContextMemoryEventHandler` only if a concrete Common `EventPublisher` already exists in startup. Do not register it against `_event_broker` / `infrastructure.event_broker.EventBroker`; if only EventBroker exists, leave registration documented and covered with fakes.
- Keep current Redis/SSE/relay/leader-election startup order unchanged. Context & Memory adapters must be bound before traffic can run, and compaction sweep must not start until both adapter binding and the existing leader-election dependencies are ready.

- [ ] **Step 4: Add startup fail-fast tests**

Cover:
- If Mongo is unavailable, startup logs a warning and does not partially bind Context & Memory services.
- If Mongo is unavailable, `mongodb.create_context_memory_indexes()` is not called.
- `ModelRegistryImpl` resolves `context_memory_legacy_json_model` to concrete model id `gpt-4o-mini` with `json_schema` capability.
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
- Modify: `docs/MODULAR_DECOUPLING_DESIGN.md`
- Modify: existing module tests

- [ ] **Step 1: Add caller preservation tests**

Cover:
- `modules/RoomMessageCenter.py` still calls `room_memory_service.add_agent_response_to_memory()`, `add_synthesis_to_history()`, `update_room_summary()`, and `compaction_service.compact_if_needed()` through legacy imports.
- `modules/QueueExecutor.py` still calls `room_memory_service.add_agent_response_to_memory()`.
- `modules/SupervisorExecutor.py` still calls `room_memory_service.add_agent_response_to_memory()`.
- `services/room_services.py` still calls `context_assembly_service.build_supervisor_context()` and `build_agent_execution_context()`.
- Room deletion cleanup in `services/room_services.py` routes Context & Memory cleanup through a temporary bound `MemoryManager` protocol, not direct `room_memories` or `conversation_content` collection deletes and not the concrete `ContextMemoryFacade`.
- Room deletion tests assert memory deletion also attempts content storage cleanup and vector index cleanup through `MemoryManager.delete_room_memory()`.
- Room deletion tests assert existing S3 prefix cleanup and `file_uploads_collection` cleanup remain in the Room/application-shell deletion flow, because those resources are not owned by Context & Memory.
- Room deletion tests assert any stale or unreachable direct-delete block for `room_memories` / `conversation_content` is removed or updated so there is only one Context & Memory cleanup path.
- User-message write paths in `services/room_services.py` still call `room_memory_service.initialize_or_update_room_memory()` through the legacy service.
- `jobs/compaction_sweep.py` still runs sweep and compacts idle rooms.

- [ ] **Step 2: Route room deletion Context & Memory cleanup through adapter**

Replace direct cleanup of `room_memories` and `conversation_content` in room deletion paths with `MemoryManager.delete_room_memory(room_id)` through `room_services.bind_context_memory(memory_manager)`. Keep Room-owned raw message, membership, S3/file-upload cleanup, and other application-shell cleanup in Room. This is a documented temporary C3 dependency from Room services to the Context & Memory protocol during migration; do not import `context_memory`, bind the concrete facade, or call non-protocol helpers from Room. This prevents room deletion from bypassing Context & Memory vector index cleanup while preserving existing non-memory resource cleanup.

Failure semantics:
- Context & Memory cleanup is best-effort after the Room record deletion is confirmed, matching current transitional cleanup behavior.
- If content or vector cleanup has an actual failure or returns `False`, log a warning with `room_id` and keep the user-visible room delete successful. No-op cleanup because memory/content/vectors did not exist is success and should not log as a failure.
- Tests must cover vector-index-unavailable cleanup returning `False` without failing room deletion.

- [ ] **Step 3: Document temporary Room cleanup dependency in design doc**

Update `docs/MODULAR_DECOUPLING_DESIGN.md` cross-module communication rules with a Phase 5 migration-only exception:
- `Room services -> Context & Memory` may call `MemoryManager.delete_room_memory(room_id)` only for room deletion cleanup while legacy Room deletion owns orchestration.
- The dependency is protocol-only, startup-bound by the application shell, and must not expose the concrete `ContextMemoryFacade` to Room.
- The exception is removed when deletion cleanup orchestration moves to the application shell or a later lifecycle owner.

- [ ] **Step 4: Keep direct compaction call in RoomMessageCenter**

Do not replace `_trigger_compaction_safe()` with a new event bus. Keep:
- Inline await while the per-room lock is held.
- Exception swallowing/logging behavior.
- Call through `services.compaction_service.compaction_service`, which delegates after bind.

- [ ] **Step 5: Add optional future event registration only in startup**

If a concrete Common `EventPublisher` exists in the implementation branch, register `ContextMemoryEventHandler` there. Do not register against `_event_broker` / `infrastructure.event_broker.EventBroker`, and do not import Delivery implementation into `context_memory/`.

- [ ] **Step 6: Keep compaction sweep outside the module**

Chosen Phase 5 path: leave `jobs/compaction_sweep.py` importing `services.compaction_service`; the service delegates to facade after bind. Do not add `CompactionSweep.bind_projector()` in Phase 5.

The job stays in `jobs/` because leader election, active-run skip checks, sleep loops, and worker pools are application-shell concerns.

If the existing sweep keeps a direct read of `database.mongodb.room_memories_collection` to discover candidate room ids, document it as an application-shell exception in the job: the job may identify room ids and enforce active-run skipping, but the actual compaction operation must go through the bound compaction service/facade so Context & Memory still owns memory mutation, content storage, and vector indexing.

Because the chosen sweep path keeps the existing job-owned room-id scan, `list_room_ids_with_memory()` is not used by the production sweep in Phase 5; it still must be implemented without the 1000-row DAL cap for future cleaner wiring and tests.

- [ ] **Step 7: Run caller compatibility tests**

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

Use the fixed expected context assembly outputs captured in Task 4 before wrapping `services.context_assembly_service.ContextAssemblyService`, or use the test-only copy/oracle created there. Do not capture the first oracle in Task 12, because Task 9 has already converted the service to a wrapper by then. Store fixed expected values in fixture data so post-adapter tests cannot compare the new implementation to itself.

Fixture families:
- Supervisor, small memory.
- Supervisor, large memory requiring turn truncation.
- Supervisor, memory search snippets.
- Agent, facts and full history.
- Agent, quoted text and room awareness.
- Compact turn pointers.
- Direct `conversation_history` and legacy `memory_content.conversation_history`.

Assert synchronous compatibility helpers produce identical token-budget results to fixed legacy fixture output for the same data. Protocol `assemble_context()` has separate contract tests: it must be deterministic and token-budget-correct for the Common inputs it can receive, but it is not required to match legacy fixtures that depend on supervisor agent registry, optional search snippets, agent display name, room awareness, quoted text, or system-instruction controls unless those values are supplied through compatibility helpers.

- [ ] **Step 2: Add golden projection tests**

Assert:
- Direct user-message compatibility helper output matches legacy `initialize_or_update_room_memory()`, including mention cleanup and attachments. Event-based `project_message()` is explicitly best-effort in Phase 5 because the current Common reader does not expose `room_agent_set`; tests should assert deterministic projection, room-id safety, attachment rendering from `RoomMessageInfo.content`, and no duplicate turns, not exact mention-cleanup parity.
- Agent response compatibility helper produces the same turn fields as legacy `add_agent_response_to_memory()`.
- Synthesis helper produces the same enriched content and summary stub as legacy.
- Golden projection comparisons normalize known intentional identifiers and clocks: deterministic event `turn_id = "message:{message_id}"`, generated legacy UUID turn ids, generated `memory_id`, and timestamps are compared by format/presence rather than exact value unless injected factories make them deterministic.

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
- Same disabled/failure metadata, including response-level flags exposed by `legacy_search()`.

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
- Modify: `database/mongodb.py` for the required `create_context_memory_indexes()` updates

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
uv run python -m pytest tests/test_common_foundation.py tests/test_dal_protocols.py tests/test_agent_protocols.py tests/test_agent_repository.py tests/test_agent_facade.py tests/test_service_agent.py tests/test_room_protocols.py tests/test_room_repository.py tests/test_room_facade.py tests/test_room_golden.py -q
```

Expected: PASS.

- [ ] **Step 5: Run import-boundary tests**

```bash
uv run python -m pytest tests/test_context_memory_protocols.py -k import_boundary -q
```

Expected: PASS and no forbidden imports from `context_memory/**`.

- [ ] **Step 6: Run broad regression suite**

```bash
uv run python -m pytest -q
```

Expected: PASS. This suite is required for Phase 5 unless the user explicitly accepts a skip because of time or environment limits; if skipped, record the targeted commands above and the concrete skip reason in the final handoff.

- [ ] **Step 7: Commit Phase 5**

```bash
git status --short
git add <exact files changed for Phase 5, as shown by git status --short>
git commit -m "feat: extract context memory module facade"
```

Expected: one final integration commit only if earlier task commits were not made. Do not run broad directory `git add` in a shared worktree; stage exact changed files so unrelated user edits are not included.

- [ ] **Step 8: Re-run final Context & Memory gate after commit**

```bash
uv run python -m pytest tests/test_context_memory_protocols.py tests/test_context_memory_assembly_golden.py tests/test_context_memory_facade.py tests/test_context_memory_compaction.py tests/test_context_memory_search.py tests/test_dal_protocols.py tests/test_context_assembly_service.py tests/test_compaction_service.py tests/test_memory_search_service.py -q
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
- `services.room_services.room_services` through `bind_context_memory(memory_manager: MemoryManager)` for temporary deletion cleanup

Services not bound:
- `services.memory_service.chat_memory_service`

Recommended binding order during startup:
1. Connect Mongo and initialize DAL.
2. Run updated `mongodb.create_context_memory_indexes()` inside the Mongo-available branch.
3. Build `AgentDeps` exactly as Phase 3 does.
4. Build `RoomDeps` exactly as Phase 4 does.
5. Build startup-local `context_memory_facade` with `room_history_reader=_room_deps.room_history_reader`.
6. Build `_context_memory_deps = create_context_memory_deps(context_memory_facade)` so module consumers receive protocol fields only.
7. Bind `context_assembly_service`.
8. Bind `memory_search_service`.
9. Bind `content_storage_service`.
10. Bind `compaction_service`.
11. Bind `room_memory_service`.
12. Bind `room_services.bind_context_memory(_context_memory_deps.memory_manager)` for the temporary deletion cleanup path.
13. Initialize Redis/SSE/event broker and leader-election dependencies exactly as current startup does.
14. Register `ContextMemoryEventHandler` with a concrete Common `EventPublisher` if one exists; do not use `_event_broker` / `EventBroker`. Otherwise leave direct calls as the active runtime path.
15. Start compaction sweep and other background work only after adapters are bound and leader-election dependencies are ready.
16. Serve traffic only after Agent, Room, and Context & Memory adapters are bound.

Avoid circular imports:
- `container.py` can import concrete implementations.
- `main.py` can import `container.py`.
- `context_memory/**` must never import `container.py` or `main.py`.
- `context_memory/**` must never import `room/**`; use `RoomHistoryReader`.
- `context_memory/**` must never import `services/**`; services are wrappers.
- Only `container.py`, `main.py`, `context_memory/**`, the intended legacy service adapter files, and tests may import `context_memory` during migration. `modules/**` and `jobs/**` must continue using service wrappers or Common protocols and must not import concrete Context & Memory implementation code.

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
- `tests/test_context_memory_assembly_golden.py`: exact legacy-vs-new context string and token-budget equality for synchronous compatibility helpers; protocol `assemble_context()` gets Common-input contract tests.
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
uv run python -m pytest tests/test_common_foundation.py tests/test_dal_protocols.py tests/test_agent_protocols.py tests/test_agent_repository.py tests/test_agent_facade.py tests/test_service_agent.py tests/test_room_protocols.py tests/test_room_repository.py tests/test_room_facade.py tests/test_room_golden.py -q
```

## Gate Criteria Checklist

- [ ] `context_memory/` package exists and is listed in `pyproject.toml`.
- [ ] `ContextMemoryFacade` satisfies `ContextAssembler`, `MemoryManager`, and `MemoryProjector` at runtime.
- [ ] `MemoryMongoRepository` satisfies `MemoryRepository` at runtime.
- [ ] `ContentStorageMongoRepository` satisfies `ContentStorageRepository` at runtime.
- [ ] `ContextMemoryDeps` exists in `container.py` alongside `AgentDeps` and `RoomDeps`.
- [ ] `create_context_memory_facade()` creates the startup-local concrete facade, and `create_context_memory_deps(facade)` exposes only protocol fields with no concrete `.facade` field.
- [ ] `context_memory/**` import-boundary test passes.
- [ ] No `context_memory/**` imports from `agent`, `room`, `services`, `modules`, `api`, `database`, `models`, `main`, `container`, `config`, `llm_gateway`, `pinecone`, `openai`, `pymongo`, or `motor`.
- [ ] No `modules/**`, `jobs/**`, `api/**`, Room, Execution, or unrelated services import `context_memory` directly; concrete imports stay limited to `container.py`, `main.py`, `context_memory/**`, intended legacy service adapters, and tests.
- [ ] No Common module imports `bson`; legacy native-id lookup for compacted content is implemented behind `MongoCollection` / `dal/mongo/client.py`.
- [ ] `assemble_context()` uses projected `room_memories` as its primary context source and uses `RoomHistoryReader` only as a fallback to recover current message text when projection has not completed.
- [ ] Legacy supervisor and agent context helpers are available only as non-protocol compatibility helpers or service adapter methods.
- [ ] Every non-protocol `ContextMemoryFacade` compatibility helper is covered by the call-boundary test and is called only from its intended legacy adapter file or tests; `project_message_for_event()` is additionally allowed from `context_memory/events.py`.
- [ ] Synchronous supervisor and agent compatibility helpers produce identical token-budget results for golden fixtures.
- [ ] Protocol `assemble_context()` produces deterministic Common-input output and does not claim equality for legacy-only inputs it cannot receive.
- [ ] Stable prefix and dynamic suffix strings match legacy output exactly in compatibility-helper golden tests.
- [ ] Token counts, stable prefix tokens, dynamic suffix tokens, truncation reason, and turn counts match legacy output exactly in compatibility-helper golden tests.
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
- [ ] `services/room_services.py` binds only the `MemoryManager` protocol for temporary Context & Memory room deletion cleanup; it does not import `context_memory` or receive the concrete facade.
- [ ] `RoomMemoryService` room-based methods use `bind_facade()` and raise `RuntimeError` before bind for migrated methods.
- [ ] `docs/MODULAR_DECOUPLING_DESIGN.md` is updated in Task 2/6 for accepted protocol/DAL changes and in Task 11 for the documented Room cleanup migration-only exception.
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
- Extend `VectorDAL` with `delete_by_filter(index, filter)` in Phase 5.
- Implement in `dal/pinecone` and test with fakes.
- Do not add vector-id tracking as a fallback in this phase.

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
- Register handler only when a Common `EventPublisher` exists, not against the current `_event_broker` / `EventBroker`, and do not depend on runtime events for current behavior.
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
- Store source identity for projected raw messages in the defined `turn_id` field, using `turn_id = f"message:{message_id}"`.
- Before projecting, check existing room memory for that deterministic projected turn id.
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

Commit checkpoint rule: after each numbered task's verification command passes, run `git status --short`, stage only the exact files changed for that task, and commit with a task-scoped message. The final commit step is only for any remaining integration changes.

Do not start by editing `modules/RoomMessageCenter.py`. The safest path is to make the new facade match legacy behavior behind the existing service singletons, then run existing caller tests unchanged. Keep direct compaction calls and the compaction sweep job in place until Delivery/Execution phases wire runtime `MessageCommitted` events.
