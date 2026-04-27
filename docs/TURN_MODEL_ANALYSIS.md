# Analysis: Turn as a Data Primitive — Scope, Limits, and Long-Term Design

> **Status:** Position paper / architectural analysis (**implementation update applied**)
> **Date:** 2026-04-17 (updated 2026-04-25)
> **Scope:** hybro-frontend + multi-agents-backend (+ their boundary contracts)
> **Related:**
> - `hybro-frontend/PLAN-turn-store-single-writer.md` — stabilization plan (ship first, this doc depends on it)
> - `hybro-frontend/docs/ROOM_TIMELINE_DESIGN.md` — current UI-layer turn model
>
> **Line-number disclaimer.** All file:line citations in this document were
> captured on 2026-04-17 and are indicative, not authoritative. Trust the
> symbol name over the line number; re-run the cited `rg` queries before
> acting on a specific reference.

---

## Implementation Update (2026-04-25)

The cleanup described in this document has now been applied for the turn-event backend/frontend path:

- Frontend no longer consumes `turn_event` SSE for room rendering; turn UI is derived from `message-store`.
- Backend turn-event runtime wiring has been removed from the active room execution path.
- Turns API has been removed (`api/turns.py` + router include).
- Turn-event persistence primitives have been removed from active database service/mongo index setup paths.

Remaining references in this document that mention `turn_events`/`turn_event` should be read as historical analysis context unless explicitly marked as current-state behavior.

---

## TL;DR

1. **Turn is the right concept for UI grouping, but the wrong concept for a data / protocol primitive** given Hybro's trajectory (multi-agent rooms, A2A delegation, proactive agents, long-horizon tasks).

2. **Every modern agent framework converged on `Run` + a message graph, not Turn.** OpenAI Assistants, AG-UI, LangGraph, Anthropic Messages, Google Gemini — none treat "turn" as a first-class stored entity. Hybro is the outlier.

3. **Turn has already sprawled too far in this codebase**, in three specific ways:
   - **A name collision.** `turn_id` means two different things in two subsystems (chat lane vs. memory lane), and they are not joinable.
   - **A persisted storage invariant.** `RoomAgentMessage.turn_id` bakes "every agent message has a root user prompt" into the data model, which breaks for A2A and proactive agents.
   - **Orchestration-layer leakage.** `QueueExecutor`, `SupervisorExecutor`, and `WorkflowCenter` thread `turn_id` through dispatch paths when they should be speaking `run_id` + `parent_message_id`.

4. **Immediate action:** ship the stabilization plan first, then adopt a "don't expand Turn" architecture rule. A Run-model migration is a multi-quarter effort — the next big refactor after stabilization, not something to rush.

5. **Endgame:** three primitives, two stored and one derived:
   - `Run` (execution lifecycle, stored)
   - `Message` with `parent_message_id` (graph-shaped conversation, stored)
   - `TurnViewModel` (UI grouping, derived at render time, **not** stored)

   Delete `turn_events` as a persistence primitive. Keep `components/turn/*` and `TurnViewModel` forever.

---

## 1. Context

The duplication bug that started this investigation (agent messages appearing twice until page reload) was traced in `hybro-frontend/PLAN-turn-store-single-writer.md` to a deeper architectural issue: Hybro's "Turn" concept — originally a convenient UI grouping — had been promoted to a first-class data primitive with its own event stream, its own frontend store, its own MongoDB collection, and its own lifecycle. That promotion introduced structural problems the stabilization plan now has to work around with feature flags, buffering, and hydration fallbacks.

This document asks the broader question the fix surfaced: **is Turn the right shape at all?**

Conclusion: **not for Hybro's trajectory.** This doc explains why (§3), what evidence exists that it's already biting (§4), where it has spread too far (§6), and what the right shape looks like (§7).

## 2. What "Turn" means in the codebase today

### 2.1 Intent

A "Turn" was introduced to mean: **one user message and everything that happens in response to it.**

Materially, this manifests as:

| Role | Expression in code |
|---|---|
| Identity | `turn_id = user_message.message_id` |
| Persistence | **Removed** from active architecture (historically `turn_events`) |
| Protocol | Runtime uses `processing_status` / `task_*` / `agent_response`; `turn_event` kept only as compatibility type |
| Frontend store | `stores/turn-event-store/*` — event log + projections |
| UI grouping | `TurnViewModel`, rendered by `components/turn/*` |
| Per-message annotation | `RoomAgentMessage.turn_id` (`models/room.py:140`) |

### 2.2 Two incompatible definitions (the landmine)

The **same field name `turn_id`** is used in the memory subsystem with a **different semantic meaning**:

```101:117:/Users/kflu/Projects/multi-agents-backend/models/memory.py
class ConversationTurn(BaseModel):
    """
    A single turn in the conversation. Supports full and compact representations.
    ...
    """
    # Core identification
    turn_id: str = Field(default_factory=lambda: str(uuid4()))
    role: TurnRole
    agent_id: str | None = None  # Only for agent/supervisor messages
    agent_name: str | None = None  # Only for agent/supervisor messages
    user_id: str | None = None  # Only for user messages
    timestamp: datetime = Field(default_factory=utcnow)
```

In `ConversationTurn`, `turn_id` identifies a **single message-role pair** in the conversation history — i.e., each agent response is its *own* "turn" here. In the chat lane, each agent response is *part of* a turn.

These are two different concepts wearing the same name:

| | Chat `turn_id` | Memory `turn_id` |
|---|---|---|
| Value | `user_message.id` | `uuid4()` (fresh) |
| Cardinality | 1 per user prompt | 1 per message in the context window (user or agent) |
| Lifetime | Tied to the user message | Independent |
| Semantics | "A user request and its responses" | "A single history item / context-window entry" |
| Defined in | `models/room.py:140` (historically also `models/turn_event.py`) | `models/memory.py:112` |

Any future code that tries to join on `turn_id` across the chat/memory boundary will be subtly wrong. **This is the single most dangerous finding in this analysis** — it is already a bug, it is just not yet weaponized.

## 3. Why Turn is the wrong long-term data primitive

### 3.1 The principle

A data primitive should be **the narrowest shape that captures the invariant the system actually needs.** Turn violates this principle: it bundles two independent concerns into one entity.

1. **Conversation grouping** — "these messages belong together for rendering."
2. **Execution tracking** — "this is a unit of work with a lifecycle, status, events, cancel / retry semantics."

Every modern agent framework has concluded these two concerns should *not* share a primitive. The split is:

- Grouping → a graph property on messages (`parent_message_id`). No primitive needed.
- Execution → a first-class entity: `Run`.

### 3.2 Concrete failure modes where Turn breaks down

| Scenario | Turn's failure | Run + Message-graph's behavior |
|---|---|---|
| **Multi-agent response to one prompt** | One Turn contains N agent executions; a single `status` field has to compress N lifecycles into one | N Runs, each with its own status; UI aggregates at render |
| **A2A delegation** | Agent→agent work has no root user message → no natural `turn_id` | Child Run points to parent Run via `parent_run_id`; no user message required |
| **Proactive agents** (Hermes chimes in unprompted) | No user prompt → requires a "synthetic system turn" workaround | Run with `parent_message_id: null`, `trigger: scheduled\|proactive` |
| **Long-horizon tasks** (4-hour research agent with check-ins) | Doesn't fit "one turn"; forces artificial turn-splitting | Run with long duration; optional `Task` grouping across Runs |
| **HITL scoping** (one agent waits, others keep running) | HITL conceptually blocks the whole Turn | HITL blocks one Run; sibling Runs are unaffected |
| **Retry granularity** | Retry a Turn = re-run all agents | Retry one Run, leave others untouched |
| **Cancel granularity** | Cancel a Turn = cancel everything | Cancel one Run or a subtree |

### 3.3 Industry alignment

| System | Execution primitive | Conversation primitive | "Turn" as a stored entity? |
|---|---|---|---|
| OpenAI Assistants | `Run` | `Thread` + `Message` | No |
| AG-UI | `Run` (RunStarted / RunFinished events) | `messages[]` | No |
| LangGraph | `Run` | Graph state + messages | No |
| Anthropic Messages | — (request-scoped) | `messages[]` array | No |
| Google Gemini | `Request` (stateless) | `contents[]` | No |
| **Hybro (today)** | **Room-level processing + per-message task state (no persisted Turn primitive)** | **`RoomMessage`** | **No (removed from active architecture)** |

Historically Hybro treated Turn as a stored primitive. That path has now been removed from active frontend/backend runtime architecture.

## 4. Evidence the model is already biting

Four concrete symptoms visible in the current codebase:

### 4.1 The "synthetic system turn" rule (UI)

`hybro-frontend/docs/ROOM_TIMELINE_DESIGN.md §4.3` contains this rule:

> "Agent messages before the first user message → synthetic system turn."

This exists because the Turn model assumes a user message triggers every agent response. Proactive / system-authored agent messages don't fit, so the UI invents a synthetic turn. It's a canary: when the data model doesn't fit reality, code invents placeholders.

### 4.2 The mutable `turn_id` / duplication bug (the original issue)

In the current design, a Turn must exist from the moment the user hits send — but the real `turn_id` (= `user_message.message_id`) isn't known until the server responds. Result: `turnId` transitions through `clientRequestId` → `tempMessageId` → `realMessageId`. The race between SSE arrival and the POST response causes duplicate rendering.

This is recorded in `hybro-frontend/PLAN-turn-store-single-writer.md:20-22`:

> "A mutable turn id — `turnId` is transiently `clientRequestId`, then `tempMessageId`, then `realMessageId`. Every rename is a potential inconsistency point."

In a Run model, the Run starts server-side when the agent starts running. There is no "before id exists" window, because the client doesn't own the identity.

### 4.3 Two frontend stores bridged by reconciliation glue

`hybro-frontend/PLAN-turn-store-single-writer.md:17-30` identifies:

- `useMessageStore` — normalized messages
- `useTurnEventStore` — turn events
- `useMessageStoreSync` — a bridge translating message-store diffs into turn events
- An "optimistic merge" block + a `cleanupOrphanOptimisticTurns` sweep to clean up stragglers

These are two representations of the same state, tied together by a bridge that is itself the root cause of ordering bugs. In a Run + Message-graph model, messages and runs are *independent entities*, and `TurnViewModel` is a pure function of both — no bridge, no merge, no sweep.

### 4.4 `resolve_turn_id()` walks the message graph

A utility exists to compute `turn_id` when it isn't already set on an agent message:

```15:20:/Users/kflu/Projects/multi-agents-backend/common/utils/turn_id.py
async def resolve_turn_id(msg: RoomAgentMessage, db_service) -> str:
    if msg.turn_id:
        return msg.turn_id
    ...
```

Its existence reveals the underlying truth: **the system already needs a "parent message" join to recover what it claims `turn_id` means.** Under a `parent_message_id` graph, this function is just `msg.parent_message_id` — no traversal, no fallback logic.

## 5. The audit: where Turn lives now

### 5.1 Frontend

| Layer | Files | Role | Verdict |
|---|---|---|---|
| **UI components** | `components/turn/*` (11 files — `TurnList`, `OrchestraTurn`, `UserInputBlock`, `AgentContentBlock`, `SummaryContentBlock`, `HitlRecordBlock`, `ContentSlotRenderer`, `OrchestrationRail`, `expand-collapse-context`), plus `components/conversation-turn.tsx` | Rendering-only, derive view models | ✅ Fine as UI grouping |
| **Render hooks** | `hooks/turn/useTurnProjection`, `hooks/turn/useTurnScroll` | Build view state for render | ✅ Fine |
| **Data layer (event-sourced)** | `stores/turn-event-store/*` (index, types, event-log, projections/content-slots, projections/rail, projections/composer) | In-memory event-sourced state | ⚠ Deepens Turn-as-primitive |
| **Bridge / adapter** | `hooks/turn/useMessageStoreSync`, `hooks/turn/useTurnHydration` | Derive turn projection from message-store | ⚠ Transitional while turn-event-store still exists |
| **Protocol** | `lib/types/sse.ts:10` (`turn_event` kind retained for compatibility) | `turn_event` is ignored in runtime | ✅ No longer a write-path dependency |
| **API client** | — | Removed (`lib/api/turns.ts` deleted) | ✅ Turn is no longer an HTTP resource |
| **Timeline builder** | `lib/room-timeline/build-turns.ts`, `lib/room-timeline/types.ts`, `lib/room-timeline/event-log.ts` | Pure function: messages → TurnViewModel | ✅ **The desired shape.** More code should look like this |
| **Message store** | `stores/message-store/*` | **Zero `turnId` references** | ✅ Clean — messages don't carry Turn |

### 5.2 Backend

| Layer | Files | Role | Verdict |
|---|---|---|---|
| **Chat event lane** | — | Removed from active architecture | ✅ Completed cleanup |
| **Turn-id utility** | `common/utils/turn_id.py` | Resolve root user-msg for an agent msg | ⚠ Exists because the concept is derivable, not primary |
| **Persisted on agent messages** | `models/room.py:140` — `RoomAgentMessage.turn_id` | "Root user message_id that triggered this processing chain" | ❌ Too far — should be `parent_message_id` |
| **Chat service propagation** | `services/hitl_service.py`, `services/room_services.py` | Residual `turn_id` usage in non-turn-event flows | ⚠ Reduced surface; continue migration to parent-message/run model |
| **Module propagation** | `modules/RoomMessageCenter.py`, `modules/SupervisorExecutor.py`, `modules/WorkflowCenter.py`, `modules/QueueExecutor.py`, `modules/agent_event.py`, `modules/agent_response_handler.py`, `modules/transports/direct.py` | Residual `turn_id` threading in execution path | ❌ Still too far — execution layer should speak `run_id` |
| **Memory subsystem (NAME COLLISION)** | `models/memory.py`, `models/search.py`, `models/compaction.py`, `services/memory_service.py` (12), `services/compaction_service.py` (16), `services/content_storage_service.py` (27), `services/memory_search_service.py` (18) | Different concept, same field name | ❌ Too far — needs rename |
| **A2A adapter** | `a2a-adapter/` | **Zero `turn_id` references** | ✅ Clean — external boundary uncorrupted |
| **Hub / webhooks / task-notification** | `hub/`, `api/webhooks.py`, `services/task_notification_service.py` | **Zero `turn_id` references** | ✅ Clean |

## 6. Three zones where Turn has gone too far

### 6.1 Zone 1: Memory subsystem — name collision (worst)

The memory lane uses `turn_id` to mean a UUID-keyed **single message in context window**, not a chat turn.

Evidence already in the model:

```220:246:/Users/kflu/Projects/multi-agents-backend/models/memory.py
    # Metadata
    last_updated_at: datetime | None = None
    updated_after_turn_id: str | None = None  # Which turn triggered the last update


class RoomFact(BaseModel):
    """
    A durable fact extracted from room conversations.
    """

    fact_id: str = Field(default_factory=lambda: str(uuid4()))
    content: str  # The fact statement
    source_turn_id: str | None = None  # Which turn this was extracted from
    confidence: float = 1.0  # Confidence score (0-1)
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime | None = None  # Optional expiry for time-sensitive facts
```

And:

```34:54:/Users/kflu/Projects/multi-agents-backend/models/search.py
class MemorySearchResult(BaseModel):
    """
    A single memory search result.
    """
    # Source identification
    turn_id: str | None = None  # If from conversation history
    fact_id: str | None = None  # If from room_facts
    room_id: str
    source_type: MemorySourceType
    ...
```

These `turn_id`s are UUIDs that have nothing structurally in common with chat-lane `turn_id`s. Any future feature that joins memory records to chat turns will silently mismatch.

**Fix:** rename memory-lane `turn_id` → `message_id` / `history_item_id`. Isolated blast radius; the memory subsystem doesn't need the Turn concept at all.

### 6.2 Zone 2: `RoomAgentMessage.turn_id` as a persisted storage field

```121:141:/Users/kflu/Projects/multi-agents-backend/models/room.py
class RoomAgentMessage(RoomMessage):
    message_type: str = "agent"
    extend_info: Any | None = None
    # Task tracking fields (consolidated from a2a_tasks collection)
    webhook_token_hash: str | None = None
    pending_continuation: dict | None = None
    last_notified_state: str | None = None
    agent_url: str | None = None
    task_created_at: datetime | None = None
    task_updated_at: datetime | None = None
    task_content: str | None = None
    has_task_tracking: bool = False
    turn_id: str | None = None  # Root user message_id that triggered this processing chain
```

This field embeds the assumption **"every agent message has a root user prompt"** as a hard storage invariant. Implications:

- **A2A delegated work** has no root user prompt → `turn_id` is wrong or null.
- **Proactive agent messages** have no root user prompt → synthetic turn required.
- **Messages that span multiple user prompts** (long-horizon tasks, debates resuming across prompts) → `turn_id` becomes ambiguous.

**Fix:** rename to `parent_message_id: str | None`. Points at any message (user prompt, another agent's message, or null). Strictly more general; handles A2A / proactive / long-horizon naturally.

### 6.3 Zone 3: Orchestration modules know about Turn

`turn_id` is threaded through the agent-dispatch path:

- `modules/QueueExecutor.py` — 11 refs
- `modules/SupervisorExecutor.py` — 7 refs
- `modules/WorkflowCenter.py` — 3 refs
- `modules/RoomMessageCenter.py` — 9 refs
- `modules/agent_event.py` — 1 ref
- `modules/transports/direct.py` — 2 refs

These orchestration modules shouldn't care about chat turns. Their job — "dispatch work to an agent, collect result, emit events" — is a Run-shaped job. They care about `run_id` (this execution's identity) and `parent_message_id` (context for correlation). They do not care about user-message grouping for rendering.

**Fix:** introduce `run_id` as the orchestration-layer identity. Keep `turn_id` only in the chat API boundary (the layer that is *about* chat rendering). Refactor `QueueExecutor.run(...)` signature to accept `run_id`, `parent_message_id` instead of `turn_id`.

## 7. The right design

### 7.1 Three primitives — two stored, one derived

```
Stored:
  Message {
    id, room_id, parent_message_id?, role, content,
    created_at, author_agent_id?, ...
  }

  Run {
    id, room_id, parent_message_id?, parent_run_id?,
    agent_id, status, started_at, completed_at?,
    events: [...],
    ...
  }

Derived at render time:
  TurnViewModel {
    user_message: Message,
    child_messages: Message[],
    runs: Run[],
    aggregated_status,
    ...
  }
```

### 7.2 What each primitive owns

| Concern | Owned by | Why |
|---|---|---|
| Message content (text, attachments) | `Message` | It's the content |
| Conversation structure | `Message.parent_message_id` (graph) | Graphs express reply-chains, threads, A2A delegation, and proactive interjections in one shape |
| Agent execution lifecycle | `Run` | One unit of work, one lifecycle, one status |
| Run status (`running`, `completed`, `failed`, `canceled`, `paused_hitl`) | `Run` | Coherent, closed state machine |
| Run events (tool calls, HITL, phase changes) | `Run.events` | Events live under the thing whose lifecycle they describe |
| Cancel / retry / pause semantics | `Run` | Right granularity |
| UI grouping ("one chat round") | `TurnViewModel` — derived | Rendering concern; not stored |

### 7.3 How familiar patterns re-home

| Scenario | Current model | New model |
|---|---|---|
| Multi-agent response to one prompt | 1 Turn with N slots | N Runs, each with `parent_message_id = user_msg.id`; UI groups them |
| A2A delegation | No natural home | Child Run with `parent_run_id = caller_run.id`, no user message required |
| Proactive agent | Synthetic system turn | Run with `parent_message_id: null`, `trigger: scheduled \| proactive` |
| HITL | Pauses the Turn | Pauses one Run; sibling Runs keep going |
| Retry the failed part | Retry whole Turn (re-runs all agents) | `POST /runs/:id/retry` |
| Cancel one agent | Cancel whole Turn | `POST /runs/:id/cancel` |
| Long-horizon task | Doesn't fit | Run with long duration (or group via optional `Task` primitive) |

### 7.4 What stays the same

- **UI grouping.** Users still see "user prompt + grouped responses." That grouping is now a pure function of messages + runs, not a stored unit.
- **`components/turn/*`** stays. The name is UI-appropriate. Rename someday if desired, not urgently.
- **`lib/room-timeline/build-turns.ts`** is already shaped right — a pure function from messages into `TurnViewModel`. This is the template.
- **Event shapes** (`slot_opened`, `slot_delta`, `hitl_requested`, `phase_changed`) can be preserved as Run events — same payloads, just re-homed under `Run` instead of `Turn`.

### 7.5 The ASCII picture

```
Today:
  ┌──────────────────────────────────────────┐
  │  turn_events (stored, per user msg)      │
  │   ├─ turn_started                        │
  │   ├─ slot_opened (agent A)               │
  │   ├─ slot_delta (agent A) × N            │
  │   ├─ slot_opened (agent B)               │
  │   ├─ slot_terminated (agent B)           │
  │   ├─ hitl_requested (for agent A)        │
  │   └─ turn_completed                      │
  └──────────────────────────────────────────┘
  ↓ render
  TurnView  (1 Turn → N slots)


Endgame:
  ┌─────────────┐   ┌─────────────┐
  │  Message    │   │  Message    │   (graph — parent_message_id)
  │  user_1     │◄──┤  agent_A_1  │
  └─────────────┘   └─────────────┘
        ▲                 ▲
        │                 │
  ┌─────┴───────┐   ┌─────┴───────┐
  │  Run R_A    │   │  Run R_B    │   (execution — independent)
  │  agent A    │   │  agent B    │
  │  status=..  │   │  status=..  │
  │  events[]   │   │  events[]   │
  └─────────────┘   └─────────────┘
  ↓ derived at render
  TurnViewModel { user_msg, [R_A, R_B], aggregated_status }
```

## 8. Migration strategy

**Multi-quarter, five phases. Do not start Phase 2+ until the stabilization plan is in production.**

### Phase 0 — Stabilize (now)

Ship `hybro-frontend/PLAN-turn-store-single-writer.md`. Fixes the duplication bug, gives a stable `turn_id`, a single writer, and a backend-authoritative event stream. **This is required regardless of destination** — a Run migration is much safer to execute on top of a stable current system than on top of a racy one.

### Phase 1 — Contain (this quarter, after Phase 0 lands)

- Adopt the "don't expand Turn" architecture rule (§9).
- **Rename memory-lane `turn_id` → `message_id`** (in `ConversationTurn`, `MemorySearchResult`, `StoredContent`, `RoomSummary.updated_after_*`, `RoomFact.source_*`, and all service call sites). Isolated subsystem, medium-effort, high-value (kills the name collision).
- Audit new PRs against the architecture rule to prevent further sprawl.

### Phase 2 — RFC (next quarter)

Write a Run-model RFC. Define:
- `Run` schema (id, parent_run_id, parent_message_id, agent_id, status, events, timestamps)
- `run_events` replacement for `turn_events`
- Frontend `TurnViewModel` as a pure function of `runs[]` + `messages[]`
- Migration mapping for every existing flow (single-agent, multi-agent, supervisor, debate, workflow, HITL)

Prototype against A2A and proactive use cases to validate the model before committing.

### Phase 3 — Dual-write (quarter after RFC)

Backend dual-writes `runs` + `run_events` alongside `turn_events`. Frontend can read from either source (flag-gated). No user-visible change. Parity-check suite runs for weeks.

### Phase 4 — Flip

Frontend switches primary source to runs + messages. `TurnViewModel` becomes derived. `turn_events` becomes legacy read-only.

### Phase 5 — Delete

After a month of production stability: drop `turn_events`, `RoomAgentMessage.turn_id`, `common/utils/turn_id.py`, and related plumbing. Keep `TurnViewModel`. Keep `components/turn/*`.

## 9. Guardrails for today

Adopt and enforce this rule in review (copy into `docs/ARCHITECTURE.md` or equivalent):

> ### `turn_id` is a chat-UI grouping identifier. It is not a universal join key.
>
> **✅ `turn_id` may appear in:**
>
> Frontend:
> - `components/turn/*` and `lib/room-timeline/*`
> - `stores/turn-event-store/*` (internal to chat UI; not for cross-feature use)
> - `hooks/turn/*`
>
> Backend:
> - `models/turn_event.py`, `services/turn_event_service.py`, `api/turns.py`
> - `services/hitl_service.py` (HITL lifecycle is chat-scoped today; re-homes under Run later)
>
> **🚫 `turn_id` must not appear in:**
>
> - Any memory / search / compaction / summarization code → use `message_id`
> - `a2a-adapter`, `hub`, webhook, or task-notification code → use `message_id` or `run_id`
> - Any new orchestration / execution path → use `run_id` + `parent_message_id`
> - Any external agent protocol
>
> **Known tracked violations (do not compound):**
>
> | Violation | Tracked for |
> |---|---|
> | Memory subsystem uses `turn_id` with a different meaning | Phase 1 rename |
> | `RoomAgentMessage.turn_id` persisted on every agent message | Phase 2+ rename to `parent_message_id` |
> | `QueueExecutor` / `SupervisorExecutor` / `WorkflowCenter` thread `turn_id` | Phase 2+ introduce `run_id` |

This rule, enforced in review, prevents the sprawl from getting worse while stabilization lands.

## 10. FAQ / counter-arguments

**Q: Isn't adopting AG-UI's wire format the easier path than inventing Run?**

A: AG-UI is a good target at the *shape* level — their Run / event / message model is close to the endgame described in §7. But adopting AG-UI's wire format *today* is a distraction because you'd still have to migrate the data model, and you'd be doing it under the constraint of a third-party spec that doesn't yet cover Hybro's multi-agent + HITL + A2A semantics. Fix the internal data model first; evaluate AG-UI protocol conformance separately, likely around Phase 3. See `AG-UI/CLAUDE.md` for their event model as a reference.

**Q: Doesn't Run introduce more complexity than Turn?**

A: The opposite. A Run has one agent, one lifecycle, one status, one events list. A Turn has N agents, aggregated status, and lifecycle composed of N sub-lifecycles, plus bridging logic on the frontend. The fact that `hybro-frontend/PLAN-turn-store-single-writer.md` is 1,195 lines to keep Turn coherent is evidence that Turn is the complex primitive. Run is strictly simpler per-entity; the only added cost is having "two primitives" (Run + Message) instead of one (Turn). That cost is paid off immediately by deleting the bridge, the sweep logic, the mutable id, and the synthetic-system-turn workaround.

**Q: What about existing Turn users in the UI?**

A: They stay. `components/turn/*` stays. `TurnViewModel` stays. Users see no change. The refactor is under the hood: the UI stops being driven by an event-sourced Turn store, and starts being driven by a pure function of messages + runs. This is exactly what `lib/room-timeline/build-turns.ts` already does for its slice; more code moves in that direction.

**Q: Can we keep `turn_events` and just add `runs` alongside forever?**

A: Phase 3 does exactly this as a dual-write transition. But *ending* with two sources of truth is worse than ending with one — it's the current problem generalized. The endgame is delete, not coexist.

**Q: We don't have major A2A / proactive / long-horizon features shipping today. Isn't this premature?**

A: Three answers:
1. They're on-deck: `a2a-adapter/`, `hybro-a2a-agents/`, `hermes-agent/` all exist in this monorepo and are actively developed.
2. Phase 1 (rename memory-lane `turn_id`, adopt architecture rule) is low-cost regardless of feature timing.
3. The current duplication bug is *itself* a symptom of the Turn model's unsuitability. It won't be the last bug the design produces.

**Q: Won't renaming the memory `turn_id` break existing data?**

A: It's a migration, not a breaking change. A one-time script reads `turn_id` on existing docs and rewrites it as `message_id` (or `history_item_id`). Zero semantic change — just a name change. The riskier rename is `RoomAgentMessage.turn_id` → `parent_message_id` because the semantic *does* change (root user msg → any parent message); that one is Phase 2+ and lives behind the RFC.

**Q: What does this mean for `hybro-frontend/docs/ROOM_TIMELINE_DESIGN.md`?**

A: It stays structurally valid as a *presentation-layer* design doc — `TurnViewModel` is exactly what that doc describes, and it's the correct shape for UI. Two minor hardenings are recommended for future-proofing:
1. Add a note that the underlying data primitive will eventually be Run + Message graph; `TurnViewModel` is a derived function.
2. Loosen the "synthetic system turn" rule into a more general "standalone event grouping" concept, so proactive / A2A / scheduled agent messages have a non-synthetic home.

Those are additive edits; the doc doesn't need a rewrite.

## 11. Summary table — where Turn should live

| Layer | Today | Endgame |
|---|---|---|
| UI components (`components/turn/*`) | Turn-shaped | Unchanged — Turn is a UI grouping |
| Timeline builder (`lib/room-timeline/*`) | Derives TurnView | Unchanged — derived function |
| Frontend store (`stores/turn-event-store/*`) | Event-sourced Turn primitive | Replaced by selectors over `runs[]` + `messages[]` |
| Bridge (`useMessageStoreSync`) | Reconciles two stores | **Deleted** |
| SSE protocol | `turn_event` as primary | `run_event` as primary; Turn-grouping is a UI concept |
| Backend chat lane (`models/turn_event.py`, `api/turns.py`) | Primary persistence | **Deleted** (Phase 5) |
| Backend orchestration (`QueueExecutor`, etc.) | Knows `turn_id` | Knows `run_id` + `parent_message_id` |
| Backend messages (`RoomAgentMessage.turn_id`) | Stored annotation | Renamed to `parent_message_id` |
| Backend memory (`ConversationTurn.turn_id`, etc.) | Overloaded name | Renamed to `message_id` (Phase 1) |
| External protocols (a2a-adapter, hub, webhooks) | Clean | Stay clean |

---

## Appendix A — Files cited

| Path | Role |
|---|---|
| `/Users/kflu/Projects/multi-agents-backend/models/memory.py:101` | `ConversationTurn` definition (memory-lane `turn_id`) |
| `/Users/kflu/Projects/multi-agents-backend/models/memory.py:230` | `RoomSummary.updated_after_turn_id` |
| `/Users/kflu/Projects/multi-agents-backend/models/memory.py:243` | `RoomFact.source_turn_id` |
| `/Users/kflu/Projects/multi-agents-backend/models/search.py:42` | `MemorySearchResult.turn_id` |
| `/Users/kflu/Projects/multi-agents-backend/models/compaction.py:85` | `StoredContent.turn_id` |
| `/Users/kflu/Projects/multi-agents-backend/models/room.py:140` | `RoomAgentMessage.turn_id` |
| `/Users/kflu/Projects/multi-agents-backend/models/turn_event.py` | **Removed** (turn-event model deleted) |
| `/Users/kflu/Projects/multi-agents-backend/common/utils/turn_id.py:15` | `resolve_turn_id()` fallback utility |
| `/Users/kflu/Projects/multi-agents-backend/api/turns.py` | **Removed** (turns API deleted) |
| `/Users/kflu/Projects/multi-agents-backend/services/hitl_service.py` | 8× `turn_id=user_message_id` sites |
| `/Users/kflu/Projects/hybro-frontend/src/lib/types/sse.ts:67` | Frontend `turn_event` SSE type |
| `/Users/kflu/Projects/hybro-frontend/src/stores/turn-event-store/types.ts` | Frontend turn-event envelope + projections |
| `/Users/kflu/Projects/hybro-frontend/src/lib/room-timeline/build-turns.ts` | Good-shape example: pure messages → TurnViewModel |
| `/Users/kflu/Projects/hybro-frontend/docs/ROOM_TIMELINE_DESIGN.md` | Current UI-layer turn model |
| `/Users/kflu/Projects/hybro-frontend/PLAN-turn-store-single-writer.md` | Stabilization plan (Phase 0) |

## Appendix B — Audit queries

To regenerate the audit tables at any time:

```bash
# Find all turn_id / turn_event references in backend
rg -n '\b(turn_id|turn_event|TurnEvent|TurnEventAppender|TurnEventLog|TurnStatus|start_turn|end_turn|append_turn)' multi-agents-backend --type py

# Find all turn references in frontend
rg -n '\b(turn_id|turnId|turn_event|TurnEvent)\b' hybro-frontend/src

# Confirm external boundaries are clean
rg -n 'turn_id|turnId|turn_event' a2a-adapter
rg -n 'turn_id|turn_event' multi-agents-backend/api/webhooks.py multi-agents-backend/services/task_notification_service.py
```

External boundaries (a2a-adapter, hub, webhooks, task-notification) should stay at **zero matches**. Any new match there is a boundary violation and should be rejected in review.

---

*End of document.*
