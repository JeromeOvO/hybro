# Persistence Unification Design

**Date**: March 2026  
**Status**: Proposal  
**Author**: Kevin Lu  
**Prerequisites**: `RECOMMENDED_ARCHITECTURE.md`, `HORIZONTAL_SCALING_DESIGN.md`, `EVENT_PIPELINE_DESIGN.md`  
**Implements**: Persistence gaps identified in `RECOMMENDED_ARCHITECTURE.md §Module Architecture`

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Root Cause Analysis](#2-root-cause-analysis)
3. [Design Principles](#3-design-principles)
4. [The Three-Layer Persistence Model](#4-the-three-layer-persistence-model)
5. [Layer 1: Execution Truth → DBOS / Postgres](#5-layer-1-execution-truth--dbos--postgres)
6. [Layer 2: Streaming Accumulation → Redis](#6-layer-2-streaming-accumulation--redis)
7. [Layer 3: Conversation Record → MongoDB](#7-layer-3-conversation-record--mongodb)
8. [Unified Write Flow End-to-End](#8-unified-write-flow-end-to-end)
9. [Context Memory Integration](#9-context-memory-integration)
10. [Behavioral Decisions Required Before Phase 3](#10-behavioral-decisions-required-before-phase-3)
11. [Migration Path](#11-migration-path)
12. [Risk Analysis](#12-risk-analysis)
13. [Decision Summary](#13-decision-summary)

---

## 1. Problem Statement

The current persistence layer has three distinct problems that compound each other:

### Problem 1: Dual Artifact Persistence Path

`HORIZONTAL_SCALING_DESIGN.md §4.10` ("Persistence path unification") was marked **COMPLETED (narrowed scope)**, meaning partially resolved. The remaining gap:

- `DirectTransport` uses `tsm.persist_message()` — full-document replace per streaming chunk
- `RelayTransport` / `WebhookTransport` route through `AgentResponseHandler` → `accumulate_artifact_on_message()` — atomic `$push` per chunk

`EVENT_PIPELINE_DESIGN.md` also documents that **10 direct `sse_manager` calls** remain in `DirectTransport` that bypass the handler entirely. "Consolidation of DirectTransport's 10 direct `sse_manager` calls — **not yet implemented**."

There is no single path through which all streaming artifacts are persisted.

### Problem 2: Execution State Embedded in Conversation Documents

`room_agent_messages` stores three conceptually distinct things in one document:

```
room_agent_messages (current schema):
  ┌─────────────────────────────────────────┐
  │  Conversation record    ← frontend reads │
  │    message_text, artifacts, role, parts  │
  ├─────────────────────────────────────────┤
  │  Execution metadata     ← runtime reads  │
  │    extend_info, supervisor_trajectory    │
  │    pending_continuation, resume blobs    │
  ├─────────────────────────────────────────┤
  │  Streaming accumulator  ← written live   │
  │    artifact chunks appended per-token    │
  └─────────────────────────────────────────┘
```

These three parts have incompatible write patterns, incompatible retention requirements, and incompatible read access patterns. Writing execution state into conversation documents means:

- Schema migrations on `room_agent_messages` affect execution logic
- Execution metadata persists in conversation history forever, consuming space
- Multi-instance deployments risk last-writer-wins corruption on concurrent writes to the same document
- Resuming a workflow requires reading a conversation document, not an execution record

### Problem 3: MongoDB Write Amplification During Streaming

Every token/chunk produced by an agent triggers a MongoDB write today:

- `DirectTransport`: full-document replace — rewrites the entire document per chunk
- Handler path: `$push` per chunk — atomic but high-frequency

A 5-minute Ollama response at 30 tokens/second generates ~9,000 MongoDB writes for a single agent invocation. At 10 concurrent agents: ~90,000 writes/minute from streaming alone.

---

## 2. Root Cause Analysis

All three problems share a single root cause: **the persistence model was designed when there was one execution path and one instance**. MongoDB was the only persistent store, so everything — execution state, streaming accumulators, and conversation records — went into MongoDB documents.

The introduction of DBOS (durable execution substrate), Redis (real-time delivery), and multi-instance deployment creates the opportunity to assign each concern to the storage primitive that is actually best suited to it.

---

## 3. Design Principles

1. **One owner per concern.** Each category of data has exactly one store that owns it. No store holds state that semantically belongs in another.
2. **MongoDB holds only finalized data.** MongoDB is the permanent conversation record. It never receives partial, in-flight, or ephemeral data.
3. **DBOS owns in-flight execution.** Anything that answers "what is happening right now in a workflow" lives in DBOS/Postgres.
4. **Redis owns ephemeral real-time state.** Streaming buffers, SSE delivery, hub relay, and operational keys all live in Redis with appropriate TTLs.
5. **Persistence is decoupled from delivery.** The AG-UI event stream to the browser and the write to MongoDB are independent operations. Delivery does not wait for persistence; persistence does not require delivery.
6. **No MongoDB writes during streaming.** MongoDB only receives a single atomic write per agent invocation — when the invocation completes.

---

## 4. The Three-Layer Persistence Model

```
┌──────────────────────────────────────────────────────────────────────┐
│  Layer 3: MongoDB — permanent conversation record                    │
│  messages, artifacts, room_memories, agents, rooms                   │
│  Written: ONCE per message, on invocation completion                 │
│  Read: frontend, context assembly, search                            │
└──────────────────────────────────────────────────────────────────────┘
                              ▲ single finalized write
┌──────────────────────────────────────────────────────────────────────┐
│  Layer 2: Redis — ephemeral real-time state                          │
│  stream:{run_id}:{invocation_id}  ← in-flight token accumulation     │
│  room:{room_id}:events            ← SSE fan-out                      │
│  hub:{hub_id}:relay               ← hub relay streams                │
│  cancel:*, terminal:*, leader:*   ← operational keys                 │
│  TTL: 30–60 minutes for buffers; varies for operational keys         │
└──────────────────────────────────────────────────────────────────────┘
                              ▲ assembled on step completion
┌──────────────────────────────────────────────────────────────────────┐
│  Layer 1: DBOS / Postgres — execution durability                     │
│  DBOS internal tables: workflow_status, operation_outputs,           │
│                         notifications, scheduler                     │
│  Domain table: agent_invocations                                     │
│  Written: continuously during workflow execution                     │
│  Read: DBOS runtime for crash recovery and resume                    │
└──────────────────────────────────────────────────────────────────────┘
```

Data flows **downward** (execution produces it) and is **promoted upward** (finalized into MongoDB) at step boundaries. Nothing flows sideways between layers during a run.

---

## 5. Layer 1: Execution Truth → DBOS / Postgres

### What DBOS Owns

DBOS manages its own internal Postgres tables automatically. The workflow executor reads and writes them:

| DBOS Internal Table | Purpose |
|---|---|
| `dbos.workflow_status` | Workflow identity, status, input, output |
| `dbos.operation_outputs` | Step outputs (completed steps are not re-executed on replay) |
| `dbos.notifications` | Durable send/recv messages (HITL continuation payloads) |
| `dbos.scheduler` | Cron job ownership and last-execution tracking |

### The Domain-Level Invocation Table

DBOS internal tables track execution mechanics. The `agent_invocations` table captures business-level agent invocation facts:

```python
class AgentInvocation:
    invocation_id: str          # PK, generated per invoke_agent() call
    run_id: str                 # FK → DBOS workflow_id
    parent_invocation_id: str | None  # for nested sub-agent calls
    agent_id: str
    agent_name: str
    scope: str                  # "user:{id}" | "org:{id}"
    status: InvocationStatus    # pending | running | completed | failed | cancelled
    step_number: int
    started_at: datetime
    completed_at: datetime | None
    error: str | None
```

This table is written by `@DBOS.step() invoke_agent()` and serves as the audit log for all agent calls — independent of DBOS internals, readable without DBOS tooling.

### What Leaves MongoDB

Once DBOS is the execution substrate, these fields are removed from `room_agent_messages`:

| Removed Field | Moved To |
|---|---|
| `extend_info.supervisor_trajectory` | DBOS `operation_outputs` (implicit in step outputs) |
| `pending_continuation` | DBOS `notifications` (`DBOS.send/recv`) |
| `extend_info.resume_blob` | DBOS `notifications` |
| `processing_status` (runtime field) | DBOS `workflow_status` |

**MongoDB stops being a scratch pad for in-flight execution state.**

---

## 6. Layer 2: Streaming Accumulation → Redis

### The Problem with Per-Chunk MongoDB Writes

Writing to MongoDB per token produces:

- **Write amplification**: thousands of DB writes per agent invocation
- **Dual-path complexity**: DirectTransport (full-doc replace) vs. handler (`$push`) — two different write patterns for the same logical operation
- **Persistence-delivery coupling**: SSE delivery waits for (or runs in parallel with) DB writes

### The Redis Accumulation Buffer

During streaming, all chunks are accumulated in Redis:

```
Key:   stream:{run_id}:{invocation_id}
Type:  Redis List
Write: RPUSH per chunk/token (in invoke_agent step)
Read:  LRANGE 0 -1 on step completion (assemble full content)
TTL:   60 minutes (garbage-collected if step never completes)
```

The buffer is per-invocation and per-run — it is scoped so that concurrent agents do not interfere with each other.

### The Unified Streaming Path

> **Idempotency requirement**: `invocation_id` must be generated by the **caller** (the workflow) and passed into the step as a parameter. DBOS retries a failed step by re-executing the function from the beginning, so any value generated inside the step changes between retry attempts. Passing `invocation_id` in means the buffer key and MongoDB `message_id` are stable across retries, enabling the idempotency guard on insert.

```python
# In the workflow — invocation_id is assigned BEFORE the step is called:
@DBOS.workflow()
async def supervisor_run(run_id: str, request: OrchestrationRequest):
    for agent_id in action.targets:
        # ID generated here, outside the step, so it's stable across retries
        invocation_id = f"{run_id}:{agent_id}:{step_number}"   # deterministic
        await invoke_agent(run_id, agent_id, invocation_id, request)

# The step receives invocation_id as a parameter:
@DBOS.step(retries_allowed=3)   # for cloud agents; see retries_allowed note below
async def invoke_agent(
    run_id: str,
    agent_id: str,
    invocation_id: str,           # ← passed in, not generated here
    request: OrchestrationRequest,
):
    buffer_key = f"stream:{run_id}:{invocation_id}"

    async for chunk in a2a_protocol.stream(agent_id, request):
        # 1. Deliver to browser immediately via AG-UI
        await interaction_adapter.emit(
            TEXT_MESSAGE_CONTENT(content=chunk.text, message_id=invocation_id)
        )
        # 2. Accumulate in Redis — no MongoDB write
        await redis.rpush(buffer_key, chunk.text)

    # Set TTL once after streaming completes (not per-token)
    await redis.expire(buffer_key, 3600)

    # 3. On completion: single atomic MongoDB write
    raw_chunks = await redis.lrange(buffer_key, 0, -1)
    full_content, parts = assemble_content_and_parts(raw_chunks)
    # assemble_content_and_parts:
    #   - joins all text chunk strings → full_content (str)
    #   - wraps as [TextPart(text=full_content)] for text-only responses
    #   - for multi-modal responses (image, file, data), the caller must pass
    #     typed chunk markers (e.g. FilePart stubs) through the Redis buffer
    #     as serialized JSON entries, not raw strings; assemble_content_and_parts
    #     detects the entry type and builds the correct MessagePart subtype.
    #     This is a Phase 2 extension — initial implementation handles text only.
    await mongo.messages.insert_one(
        {
            "message_id":    invocation_id,
            "thread_id":     request.thread_id,
            "role":          "agent",
            "content":       full_content,
            "parts":         parts,
            "invocation_id": invocation_id,
            "created_at":    utcnow(),
        },
        # Idempotency: if DBOS replays and the insert already succeeded,
        # this upsert is a no-op rather than a duplicate.
        upsert_filter={"message_id": invocation_id},
    )

    # 4. Clean up buffer
    await redis.delete(buffer_key)

    return AgentInvocationResult(invocation_id=invocation_id, content=full_content)
```

> **Note on `retries_allowed` for hub agents**: Hub (relay) agents must use `retries_allowed=0` because a `RELAY_DISPATCHED` result is not a transient failure — the hub is offline and the workflow should durably wait, not retry. Cloud agent invocations may use `retries_allowed=3` for transient network failures. The `invoke_agent` step should be split into `invoke_cloud_agent` and `invoke_hub_agent` with different retry policies, or the retry policy should be passed as a parameter. See `RECOMMENDED_ARCHITECTURE.md §Gap 2`.

### What This Eliminates

| Eliminated | Replaced By |
|---|---|
| `tsm.persist_message()` full-doc replace per chunk | Single insert on completion |
| `accumulate_artifact_on_message()` `$push` per chunk | Redis RPUSH per chunk |
| `skip_persist=True` flag on `AgentEvent` | Removed — no dual-path |
| DirectTransport's 10 direct `sse_manager` calls | All delivery via `interaction_adapter.emit()` |
| `tsm.MessageStreamingState` for content assembly | Redis List serves this role during streaming |
| `s3_converted` flag to prevent double S3 conversion | No double-write possible — single finalization path |

### Handling Artifacts (Files, Images, Code Blocks)

Non-text artifacts follow the same pattern:

```
During streaming:
  Redis List: stream:{run_id}:{invocation_id}:artifacts
  RPUSH JSON-serialized artifact metadata per artifact chunk

On completion:
  Assemble full artifacts from list
  Trigger S3 upload for binary content
  Insert into artifacts collection (one document per artifact)
  Link to message via message_id FK
```

### Failure Semantics

If the backend crashes during streaming:

1. The Redis buffer is abandoned (TTL expires within 60 minutes)
2. The DBOS step did not complete — DBOS retries `invoke_agent` from the beginning
3. The browser reconnects via SSE; the AG-UI client receives a new `RUN_STARTED`
4. No orphan partial documents in MongoDB — the insert never happened
5. The `StaleTaskChecker` detects the orphaned run and either retries or marks it failed

This is strictly better than the current behavior where partial artifact chunks may be written to MongoDB and then not cleaned up correctly.

---

## 7. Layer 3: Conversation Record → MongoDB

### New Schema: `messages` Collection

Replaces `room_agent_messages`. Every document is a finalized, complete message:

```python
class Message:
    message_id:     str
    thread_id:      str                  # replaces room_id
    role:           Literal["user", "agent", "system"]
    content:        str                  # full assembled text (never partial)
    parts:          list[MessagePart]    # AG-UI compatible (TextPart, FilePart, DataPart)
    invocation_id:  str | None           # soft FK → agent_invocations (agent messages only)
    created_at:     datetime
    # All the following are REMOVED from this collection:
    #   extend_info, pending_continuation, processing_status
    #   supervisor_trajectory, resume_blob, artifact chunks
```

### New Schema: `artifacts` Collection

Extracted from embedded `message.artifacts` array into its own collection for independent access:

```python
class Artifact:
    artifact_id:    str
    message_id:     str         # FK → messages
    thread_id:      str         # denormalized for efficient thread-scoped queries
    artifact_type:  str         # "file" | "code" | "image" | "data" | "text"
    content:        str | None  # inline content (text, code — small artifacts)
    s3_url:         str | None  # external reference (binary files, large content)
    mime_type:      str
    filename:       str | None
    created_at:     datetime
```

Extracting artifacts to their own collection enables:
- Artifact-level search and retrieval without loading full message content
- Independent S3 lifecycle management
- Artifact type filtering (show only files, show only code blocks)

### Retained Collections (Unchanged)

| Collection | Role | Changed? |
|---|---|---|
| `room_memories` | Compacted context for LLM context window assembly | Schema unchanged; reads from `messages` instead of `room_agent_messages` |
| `agents` | Agent catalog, capabilities, health | Unchanged |
| `rooms` | Room configuration, membership | Loses `extend_info.use_supervisor` flag (moves to run request parameter) |
| `hitl_requests` | HITL request history | **Removed** — replaced by DBOS `notifications` for state; a thin `hitl_history` view can be derived from `agent_invocations` if needed for audit |

---

## 8. Unified Write Flow End-to-End

```
User sends message
  ↓
POST /api/v1/threads/{thread_id}/messages
  → db.messages.insert_one({role: "user", content: ..., created_at: now})
  → DBOS.start_workflow(supervisor_run, run_id={uuid}, request={...})
  → Return 202 Accepted; SSE stream delivers progress

@DBOS.workflow() supervisor_run(run_id, request):
  while not done:
    action = await decide_next(request, trajectory)
    if action is Delegate:
      results = await asyncio.gather(*[
          invoke_agent(run_id, agent_id, request)  ← DBOS step (durable)
          for agent_id in action.targets
      ])
    elif action is Clarify:
      # HITL — workflow pauses durably
      await interaction_adapter.emit(RUN_FINISHED(outcome="interrupt", ...))
      user_response = await DBOS.recv(f"hitl:{run_id}", timeout=3600)
      await interaction_adapter.emit(RUN_STARTED(...))

@DBOS.step() invoke_agent(run_id, agent_id, request):
  # invocation_id is passed in from the workflow (deterministic, stable across retries)
  buffer_key = f"stream:{run_id}:{invocation_id}"

  async for chunk in a2a_protocol.stream(agent_id, request):
    ├─→ AG-UI: TEXT_MESSAGE_CONTENT event → browser (immediate)
    └─→ Redis: RPUSH buffer_key chunk (accumulate, no MongoDB write)

  await redis.expire(buffer_key, 3600)       # set TTL once, after streaming

  # Step completion: promote to MongoDB
  content, parts = assemble(await redis.lrange(buffer_key, 0, -1))
  await mongo.messages.insert_one({...finalized message...}, upsert_filter={"message_id": invocation_id})
  await mongo.artifacts.insert_many([...finalized artifacts...])
  await redis.delete(buffer_key)

  # Record invocation for audit in Postgres (via SQLAlchemy / psycopg — NOT MongoDB)
  await postgres.execute(
      "UPDATE agent_invocations SET status='completed', completed_at=:now WHERE invocation_id=:id",
      {"id": invocation_id, "now": utcnow()},
  )
  return AgentInvocationResult(...)

Workflow completes:
  → AG-UI: RUN_FINISHED event → browser
  → MongoDB has N clean message documents, one per agent invocation
  → DBOS marks workflow_status = completed
  → Redis buffers already cleaned up
```

**Single truth at each moment:**

| Phase | Source of truth |
|---|---|
| During run | DBOS Postgres (execution state) + Redis (streaming buffers) |
| After run | MongoDB (conversation record) + DBOS (execution audit) |
| Between instances | Redis (SSE fan-out, hub relay, cancellation) |

---

## 9. Context Memory Integration

`context_memory/` currently reads `room_agent_messages.conversation_history` for context assembly, compaction, and vector search. With the new schema, it reads from the `messages` and `artifacts` collections instead.

The change is internal to the `context_memory/` module — no other module is affected because `ContextMemory` is already a facade:

```python
# context_memory/context_assembly.py — one internal change
# BEFORE:
messages = await db.room_agent_messages.find(
    {"room_id": room_id}, {"message_text": 1, "artifacts": 1}
)

# AFTER:
messages = await db.messages.find(
    {"thread_id": thread_id, "role": {"$in": ["user", "agent"]}},
    {"content": 1, "parts": 1, "invocation_id": 1}
)
```

**Compaction** (`compaction_service.py`): compaction writes compacted summaries to `room_memories`. This collection is unchanged. The compaction trigger conditions (currently in `room_services.py`) are ported to `context_memory/compaction/`. The trigger logic reads `messages` collection length instead of `room_agent_messages.conversation_history` array length.

---

## 10. Behavioral Decisions Required Before Phase 3

Before building the new schema, these behaviors from the old system need explicit decisions. They are documented in `MIGRATION_STRATEGY_ANALYSIS.md Appendix` and must be in `BEHAVIORAL_DECISIONS.md` before code is written.

| Behavior | Current Location | Required Decision |
|---|---|---|
| Text artifact backfill: synthesize `TextPart` from `message_text` when `artifacts` is empty | `room_services.py` | **Keep**: replicate as post-processing in `invoke_agent` step finalization |
| Terminal status dedup via `_terminal_status_sent` TTLCache | `sse_services.py` | **Keep**: already in Redis (cross-instance), no change |
| Supervisor V2 three distinct resume paths | `RoomMessageCenter._resume_supervisor_v2` | **Replace**: DBOS `recv()` is a single resume model; three-path complexity disappears |
| HITL state with artifact and task state preservation | `hitl_service.py`, `SupervisorExecutor.py` | **Replace**: DBOS `send/recv` carries the full payload; `hitl_requests` collection removed |
| Compaction trigger conditions and context window heuristics | `compaction_service.py`, `room_services.py` | **Keep**: port to `context_memory/compaction/`, reading from `messages` collection |
| SSE event ordering: `task_submitted` before `artifact_update` before `task_update(completed)` | `direct.py`, `AgentResponseHandler` | **Keep**: ordering is now encoded in the `invoke_agent` step — `RUN_STARTED` before content events before `RUN_FINISHED` |
| Continuation blob storage variants | `SupervisorExecutor.py`, `RoomMessageCenter.py` | **Drop**: DBOS replaces all continuation storage; schema variants disappear |
| `room.extend_info.use_supervisor` flag | `room_services.py` | **Improve**: move to run-time request parameter, not stored configuration. ⚠️ **Frontend change required**: the frontend currently sets this flag at room-creation time; it must instead pass a `workflow_type` parameter per run request. |

---

## 11. Migration Path

These phases map to the phases in `RECOMMENDED_ARCHITECTURE.md`. Phase numbers here intentionally match that document so work can be tracked together.

### Persistence work in Phase 1 (DBOS Introduction)

*Prerequisite: DBOS is running and `supervisor_run` workflow is implemented.*

1. Stop writing `extend_info.supervisor_trajectory` to `room_agent_messages` — DBOS `operation_outputs` holds this
2. Stop writing `pending_continuation` — DBOS `notifications` holds this
3. Stop writing `processing_status` as a persistent field — it is now runtime state in DBOS `workflow_status`
4. Introduce `agent_invocations` Postgres table (written by `invoke_agent` step)
5. `room_agent_messages` still used for streaming accumulation and conversation history (unchanged in this phase)

### Persistence work in Phase 2 (AG-UI Streaming Unification)

*Prerequisite: `invoke_agent` is a DBOS step; AG-UI InteractionAdapter is in place.*

1. Introduce Redis accumulation buffer in `invoke_agent` step
2. Replace all DirectTransport `sse_manager` direct calls with `interaction_adapter.emit()` (completing EVENT_PIPELINE_DESIGN goal G1)
3. Remove `tsm.persist_message()` per-chunk writes from DirectTransport
4. Remove `accumulate_artifact_on_message()` per-chunk writes from handler path
5. New runs write finalized messages to **new** `messages` collection on step completion
6. Old rooms remain readable from `room_agent_messages` via a compatibility read layer in `context_memory/`

```python
# context_memory/context_assembly.py — compatibility read during migration
async def get_thread_messages(thread_id: str) -> list[Message]:
    # New runs: read from messages collection
    new_messages = await db.messages.find({"thread_id": thread_id})
    # Old runs: read from room_agent_messages
    # Note: old docs use "room_id" and have a "conversation_history" subdoc array,
    # not a top-level "messages" field — filter on "room_id" presence only.
    old_messages = await db.room_agent_messages.find({"room_id": thread_id})
    return merge_and_sort(new_messages, map_old_schema(old_messages))
```

### Persistence work in Phase 3 (Schema Cutover)

*Prerequisite: All active users are on new-schema threads; no active old-schema runs.*

1. Background migration job converts `room_agent_messages` → `messages` + `artifacts`

```python
# Migration job (runs once as @DBOS.workflow())
async def migrate_room_agent_messages():
    cursor = db.room_agent_messages.find({})
    async for old_doc in cursor:
        new_message = map_old_to_new_schema(old_doc)
        await db.messages.insert_one(new_message)
        if old_doc.get("artifacts"):
            await db.artifacts.insert_many(
                [map_artifact(a, new_message["message_id"]) for a in old_doc["artifacts"]]
            )
    await db.room_agent_messages.rename("room_agent_messages_archive")
```

2. Remove compatibility read layer from `context_memory/`
3. Drop `extend_info`, `pending_continuation` fields from any remaining documents
4. Archive or drop `hitl_requests` collection (history readable from `agent_invocations`)
5. Rename `room_agent_messages_archive` → drop after 30-day validation period

---

## 12. Risk Analysis

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| Redis buffer loss during streaming | Agent response not persisted | Low | **Cloud agents** (`retries_allowed=3`): DBOS retries step; agent re-invoked; Redis buffer repopulated; idempotency upsert on `message_id` prevents duplicate MongoDB write. **Hub agents** (`retries_allowed=0`): step does not auto-retry; parent workflow catches the exception and emits `RUN_FINISHED` with error outcome; browser shows error state; no orphan in MongoDB |
| Long-running agent overflows Redis buffer TTL | Buffer expires before step completes | Very Low | Set TTL = 2× max expected agent duration (e.g., 4 hours for agents with 2h limit); monitor `LLEN` |
| MongoDB insert fails on step completion | Step fails; DBOS retries invoke_agent | Low | Step retried from beginning; Redis buffer repopulated on retry; idempotency key on `message_id` prevents duplicate inserts |
| Context memory reads wrong collection during migration | Compaction operates on incomplete history | Medium | Compatibility read layer merges both collections during Phase 3; tested with integration tests before Phase 4 |
| Old `room_agent_messages` reads break during cutover | Frontend shows incomplete history | Medium | Phase 4 migration job runs as background sweep before any schema removal; rollback = re-enable compatibility layer |
| `agent_invocations` Postgres table grows unbounded | Storage cost | Low | Add retention policy: delete invocations older than 90 days (conversation history stays in MongoDB) |
| Redis List data structure inefficiency for large messages | Memory usage for concurrent long-running agents | Very Low | Each buffer is bounded by agent response size; 100 concurrent agents × 200KB average = 20MB Redis usage |

---

## 13. Decision Summary

| Decision | Choice | Rationale |
|---|---|---|
| Execution state storage | DBOS / Postgres | Replaces embedded MongoDB fields; crash-safe; replay-capable |
| Streaming accumulation | Redis List per invocation | Ephemeral by design; no MongoDB write amplification during streaming |
| Conversation record | MongoDB `messages` + `artifacts` collections | Clean schema; single write per message; separation of concerns |
| MongoDB write timing | On step completion only | Eliminates dual-path; removes per-chunk writes |
| Artifact persistence | Separate `artifacts` collection | Independent retrieval; S3 lifecycle independence |
| HITL state | DBOS `notifications` (send/recv) | Removes `hitl_requests` collection; cleaner resume model |
| Context memory | Internal module change only | `context_memory/` facade absorbs schema change; no cascade |
| Old data migration | Background sweep in Phase 4 | Low-risk; compatibility layer provides continuity during transition |
| `room_agent_messages` fate | Archived → dropped after 30-day validation | Preserves rollback option during cutover |

---

*Related documents: `RECOMMENDED_ARCHITECTURE.md` · `HORIZONTAL_SCALING_DESIGN.md` · `EVENT_PIPELINE_DESIGN.md` · `MIGRATION_STRATEGY_ANALYSIS.md` · `CONTEXT_MEMORY_SYSTEM_DESIGN.md`*
