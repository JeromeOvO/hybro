# Quote Reply — Text Selection Quote System

> **Status: Partially implemented (interim)** — Room UI, capture, and inline `extend_info.quoted_text` work on the **primary** dispatch path. **Phase 0** (queue resume, `extend_info` merge) may still be open—verify before assuming parity. **Target design:** persisted `QuotedSnippet` + turn `quote_id` + unified prompt assembly (§5–§7). See **§4.4** (wire contract), **§18** (implementation contracts), §12 (migration), **§17** (pre-ship checklist). Critical behavioral fix: §5.2.1 (`[Current request]` must use user message text, not supervisor task).

---

## 1. Overview

Users select text in an agent response, click **Quote**, and send a follow-up. The highlight becomes an **immutable snapshot** stored as its own record. The **user turn** references that record; every agent dispatched for the turn receives the same quote and user message in a fixed prompt shape—regardless of chat mode (Ultimate / Fast / debate).

**Core design goals:**

1. **Quote is a first-class persisted object**, not only a blob inside `extend_info`.
2. **Turn context** references the quote (`quote_id`); orchestration loads it once per turn (including resume).
3. **Prompt assembly** is mode-agnostic: one builder combines quote snapshot + user message + mode-specific agent task.
4. **Planners may paraphrase tasks; executors must not** be the only carrier of verbatim quoted text.

This is intentionally **more explicit** than ChatGPT-style inline quoting (see §9), because Hybro has multi-agent orchestration, webhook resume, and supervisor-written `task_content` that can diverge from the user’s selection.

---

## 2. User flow

1. User selects text in a quotable region (`data-quote-message-id`, `data-quote-agent-name`).
2. Floating **Quote** button (`useTextSelectionQuote` + `getSelectionPlainText`).
3. Quote preview in `RoomChatInput` (sender + clipped text).
4. User types a follow-up and sends.
5. Backend creates a **QuotedSnippet** (if any), then a **user message** with `quote_id`.
6. All agents for that turn load the snippet by id when building prompts.
7. UI shows the quote on the user bubble and in the agent detail pane (separate from `task_content`).

**Dismiss:** Global `mousedown` outside the quote button; button uses `mousedown` + `preventDefault` so selection survives the click.

---

## 3. Chat modes (unchanged by quote design)

| UI mode | `use_supervisor` | `debateMode` | Orchestration |
|---------|------------------|--------------|---------------|
| Ultimate | true | false | Supervisor V2 |
| Fast | false | false | QueueExecutor → coordinator summary |
| Ultimate debate | true | true | Supervisor debate fast-path |
| Fast debate | false | true | QueueExecutor + `inject_short_debate` |

The client does **not** branch on mode for quote. **Coordinator / HYBRO synthesis** does not require the quote for correctness—only **dispatched agents** do.

---

## 4. Target data model

### 4.1 `QuotedSnippet` (persisted)

A dedicated record—same idea as **attachments** (`file_id` on the message, bytes in storage): quote text lives in its own collection, not duplicated across continuations or agent messages.

```text
quote_id              (PK, e.g. UUID)
room_id
created_by_user_id
created_at

text                  # resolved snapshot at quote time (verbatim)
format                # plain | markdown  (v1: plain)

# provenance (for UI and debugging; dispatch uses text, not re-resolution)
source_message_id     # agent message, synthesis message, or turn id
source_kind           # agent | synthesis | user_turn | unknown
source_agent_id       # optional
sender_display_name   # optional, for UI

# optional future
source_range          # block id or char offsets
content_hash          # dedupe / integrity
max_length_policy     # which cap was applied at insert
```

**Rules:**

- **Persist resolved `text` at send time.** Do not re-read `source_message_id` at dispatch (streaming edits, compaction, and debate rewrites make re-resolution unreliable).
- **Validate size at insert** (max length, reject or warn on client). Avoid silent truncation in context assembly without policy (see §8.3).
- **Cascade delete** quotes when a room is deleted.

Proposed backend module/collection: `room_quotes` / `QuotedSnippet` in `multi-agents-backend` (exact name TBD).

### 4.2 Turn context (reference)

A **turn** is anchored by the root **user message** (`message_id` = `turn_id` on agent messages). Turn context is the logical bundle orchestration loads once:

```text
TurnContext
  user_message_id     (PK for the turn)
  room_id
  client_request_id   (frontend correlation)
  message_text        # user's follow-up prompt, e.g. "Get details about this"
  quote_id            # optional → QuotedSnippet
  attachment_file_ids # existing pattern
  # mode flags live in extend_info (supervisor_v2, dispatch_strategy, …)
```

**Storage (v1):** `RoomUserMessage.quote_id` field or `extend_info.quote_id`. Prefer a **top-level field** when schema allows; `extend_info.quote_id` is acceptable for minimal migration.

**Do not overload `related_message_id` for quote provenance.** Today `quoteData.messageId` is sent as `related_message_id`; that conflates graph linkage with quote source. Provenance belongs on `QuotedSnippet`; the user message only holds `quote_id`. **Keep `related_message_id` for graph semantics** (mentions, reply chains) when those exist—quote provenance is separate.

**Source id typing:** `FinalAnswerSurface` uses `data-quote-message-id={turnId}` (user turn id), not an agent `messageId`. `source_kind` must distinguish agent vs synthesis vs turn-level surfaces.

### 4.3 API / wire format

**Target send payload** — aligned with existing `RoomUserMessage` / `MessageContent` (inline on `sendMessage`; dedicated `POST /quotes` optional later):

```json
{
  "room_id": "…",
  "message": {
    "message_content": {
      "message_text": "Get details about this",
      "attachments": []
    },
    "quote_id": null,
    "quote": {
      "text": "verbatim selection…",
      "source_message_id": "agent-msg-uuid",
      "source_kind": "agent",
      "sender_display_name": "Files Parse Agent",
      "source_agent_id": "optional"
    },
    "extend_info": {}
  }
}
```

**Interim (today):** quote fields live under `extend_info.quoted_text` / `quoted_sender_name`; `related_message_id` may carry `quoteData.messageId`.

**Target inquiry/hydration response** — user message includes at least one of:

| Field | Purpose |
|-------|---------|
| `quote_id` | Stable reference |
| `quoted_text` / `quoted_sender_name` | Denormalized for UI (optional; avoids extra fetch) |

Client store: persist `quoteId` + `quotedText`; on replay, prefer API denormalized fields, else resolve `quote_id` if a fetch endpoint exists later.

**Server flow:**

```text
if quote payload present:
  validate source_message_id ∈ room (§8.8)
  quote_id = insert QuotedSnippet(snapshot)   # same transaction as user message when possible
user_message.quote_id = quote_id
persist user_message
```

**Read path:** `load_turn_context(user_message)` → fetch `QuotedSnippet` by `quote_id` if set.

**Send validation:** Require non-empty `message_text` **or** attachments (same as today). Quote-only send (empty prompt) is **not** supported in v1 unless product changes attachment rules.

### 4.4 Canonical wire contract (`RoomCenterUserMessageRequest`)

Backend request type: `models/request.py` → `RoomCenterUserMessageRequest` with **both** `user_input` (string) and optional nested `message: RoomUserMessage`. Today the frontend duplicates the prompt in both places; the server must treat them as one logical text.

**Canonical rule (implementers):**

| Field | Role |
|-------|------|
| `message.message_content.message_text` | **Source of truth** for `turn.message_text` after persist |
| `user_input` | Legacy duplicate; must **match** `message_text` when both sent (server may normalize: prefer `message.message_content.message_text`, fall back to `user_input`) |
| `message.quote` | **Only** place for quote create payload (target); not top-level on request |
| `message.quote_id` | Set by server on response; omit on client send |
| `message.extend_info.quoted_*` | Interim only; dual-write copy during migration (§12) |
| `related_message_id` | Graph only (mentions, chains)—**not** quote provenance after Phase 5.4 |

**Target client send** (`src/lib/api/room.ts`):

```json
{
  "room_id": "…",
  "user_id": "…",
  "user_name": "…",
  "user_input": "Get details about this",
  "client_request_id": "…",
  "message": {
    "room_id": "…",
    "message_type": "user",
    "message_content": {
      "message_text": "Get details about this",
      "attachments": []
    },
    "quote": {
      "text": "…",
      "source_message_id": "…",
      "source_kind": "agent",
      "sender_display_name": "…",
      "source_agent_id": null
    }
  }
}
```

Do **not** send quote only via `extend_info` once Phase 5.1 lands. During dual-write, server may still copy `quote.text` → `extend_info.quoted_text` for old clients.

**Server read on ingest:** `room_services` / `RoomMessageCenter` extracts quote from `request.message.quote` first, then legacy `request.message.extend_info`.

### 4.5 Interim (shipped / in flight today)

Until the persisted entity lands:

| Layer | Today |
|-------|--------|
| Frontend | `QuoteData` in `src/lib/types/quote.ts`; `extend_info.quoted_text` + `quoted_sender_name` |
| Backend | `RoomMessageCenter` reads `extend_info.quoted_text`; threads `quoted_text` param to dispatch |
| Resume gap | Queue `resume_from_continuation` does not pass `quoted_text` into `process_queue` |

`load_turn_context()` should read **legacy** inline fields when `quote_id` is absent so old messages keep working.

### 4.6 Frontend `QuoteData` (interim → target)

**Today** (`src/lib/types/quote.ts`):

```typescript
interface QuoteData {
  messageId: string      // → related_message_id (interim only)
  content: string
  senderName: string
}
```

**Target** (Phase 5.3)—extend client type (§4.6); server defaults missing fields to `source_kind: "unknown"`:

```typescript
interface QuoteData {
  text: string                    // was content
  sourceMessageId: string         // was messageId
  sourceKind: 'agent' | 'synthesis' | 'user_turn' | 'unknown'
  senderDisplayName: string       // was senderName
  sourceAgentId?: string
}
```

Map DOM → `sourceKind`: `AgentContentBlock` / detail pane → `agent`; `SynthesisContent` → `synthesis`; `FinalAnswerSurface` (`turnId`) → `user_turn`.

---

## 5. Prompt assembly (mode-agnostic)

Single choke point—**all** orchestration paths call the same builder before A2A dispatch:

```text
build_agent_execution_envelope(turn: TurnContext, agent_task: str, …) → str
```

### 5.1 Layers (do not merge roles)

| Layer | Source | Purpose |
|-------|--------|---------|
| **Quoted context** | `QuotedSnippet.text` from DB | Verbatim highlight |
| **Current request** | `turn.message_text` | User’s follow-up question |
| **Task** | Mode-specific | What this agent should do (supervisor delegate, queue step, debate prompt) |

**Associated prompt** means: the user’s question lives in **Current request**, not inside the quote row. The quote entity is **context only**.

### 5.2 Envelope shape (contract)

```text
[Quoted context]
The user highlighted the following from {sender_display_name}
(source: {source_kind}, message {source_message_id}):
---
{quote.text}
---

[Current request]
User: {turn.message_text}

[Task]
{agent_task}
```

**Contract tests:** If `turn.quote_id` is set, assembled output **must** contain `quote.text` verbatim in `[Quoted context]`. `agent_task` must not be the only place quote content appears.

### 5.2.1 Delta from today (must change in Phase 3)

**Today** (`room_services.process_agent_message`), `ContextAssemblyService.build_agent_execution_context` receives `current_task` from the **agent message body** (supervisor/queue `task_content`), not from the user message:

```text
original_text = agent_message.parts[0].text   # supervisor paraphrase / queue step
[Current request]
User: {original_text}                         # ← wrong layer today

[Quoted context]
… quoted_text param …                         # ← quote only here
```

The user’s short follow-up (`turn.message_text`, e.g. “Get details about this”) may appear only inside the supervisor task string, if at all.

**Target:** Phase 3 must pass:

| Envelope section | Source |
|------------------|--------|
| `[Quoted context]` | `turn.quote.text` |
| `[Current request]` | **`turn.message_text`** |
| `[Task]` | `agent_task` (supervisor delegate, queue step, debate injection) |

**SSE/UI:** `task_content` on agent messages remains the supervisor/queue wording for the detail pane. **Assembled** text sent to the agent (after envelope) is a different artifact—do not assume they are identical.

### 5.3 Planners vs executors

| Role | Sees quote | Verbatim in output |
|------|------------|-------------------|
| Supervisor `decide_next` | Summary + `message_text` | No (planning only) |
| `parse_user_message` / decomposer | Should see quote summary (Fast mode gap today) | No |
| Debate template | Reference only | No — choke point owns verbatim block |
| `process_agent_message` | Full snippet via `turn` | **Yes** |

Supervisor delegate tasks should **not** re-embed a paraphrased “conversation snippet” as the only copy of the quote (reduces attribution drift and UI confusion).

### 5.4 End-to-end orchestration

```text
sendMessage
  → create QuotedSnippet? (transaction)
  → create RoomUserMessage(quote_id)
  → orchestration entry
       turn = load_turn_context(user_message)   # joins quote by quote_id
       ├─ supervisor_v2
       │    decide_next(turn)                   # planning
       │    dispatch → build_agent_execution_envelope(turn, agent_task)
       └─ queue
            process_queue(turn)                 # same turn on resume
                 → build_agent_execution_envelope(turn, agent_task)
```

**Resume:** Reload `turn` from `user_message_id` → `quote_id` → DB. **Do not** store quote text in queue continuation JSON.

### 5.5 Envelope builder vs `ContextAssemblyService`

**Single owner:** `build_agent_execution_envelope` is the only place that adds `[Quoted context]`, `[Current request]`, and `[Task]` in the target shape.

**Delegation:**

```text
build_agent_execution_envelope(turn, agent_task, room_memory, …)
  → ContextAssemblyService.build_agent_execution_context(
        current_task = turn.message_text,      # NOT agent_message.parts[0].text
        agent_task = agent_task,                 # new param; becomes [Task] section
        quoted_text = turn.quote.text if set,
        …
     )
  → merge sections in fixed order; apply truncation policy (§8.3)
```

**Rules:**

- Do **not** add `[Quoted context]` in both envelope builder and `process_agent_message` legacy branches.
- **Legacy string `room_memory` path** in `process_agent_message` must call the same envelope builder (or be removed)—no second quote assembly path.
- **Debate `inject_short_debate`:** runs on `agent_task` before queue dispatch; envelope runs **after** injection so quote is never dropped.

### 5.6 Phase 3 touch map (not a single file)

`ContextAssemblyService.build_agent_execution_context` has **no** `agent_task` param today. Phase 3 must update **all** of:

| Layer | File(s) | Change |
|-------|---------|--------|
| Envelope | `services/prompt_envelope.py` (new) | Owns section order; calls CAS |
| CAS wrapper | `services/context_assembly_service.py` | Add `agent_task`; `current_task` = user message text |
| Facade | `context_memory/facade.py` | Pass through `agent_task` |
| Assembly | `context_memory/assembly.py` | Add `[Task]` block; **remove** 500-char quote truncation (§8.3); stop embedding quote only inside task string |
| Dispatch | `services/room_services.py` `process_agent_message` | Call envelope builder; drop duplicate legacy quote blocks |
| Orchestration | `execution/orchestration/room_message_center.py`, `queue_executor.py`, `supervisor_executor.py` | Pass `TurnContext`; resume via `load_turn_context` |
| Processor | `execution/dispatch/agent_message_processor.py` | Accept `turn` instead of bare `quoted_text` |

**Regression (Phase 3):** Agents today see supervisor/queue wording under `[Current request]`. After the fix they see the **user’s short prompt** there and the task under `[Task]`. Plan a **prompt-quality pass** (sample rooms, delegate + Fast queue) before calling Phase 3 done—not a rollback of §5.2.1, but validate agent outputs.

**Audit:** Hub/relay paths must reach the same `process_agent_message` + envelope builder (§13). Any bypass needs an explicit matrix row before merge.

---

## 6. Frontend architecture

| Piece | Location | Role |
|-------|----------|------|
| `QuoteData` | `src/lib/types/quote.ts` | Composer state |
| Selection | `src/lib/selection-plain-text.ts` | DOM-aware capture |
| Quote UI | `src/hooks/useTextSelectionQuote.ts`, `room-page-shell.tsx` | Select + button |
| Preview / send | `room-chat-input.tsx`, `useSendMessage.ts` | Preview; optimistic `quotedText` |
| API | `src/lib/api/room.ts` | Today: `extend_info`; target: `quote` + `quote_id` on response |
| History | `UserMessageBlock`, `AgentResponseDetailPane` | Turn quote vs `task_content` |

**Target frontend changes:**

- Send `quote: { text, source_message_id, source_kind, sender_display_name }` on `sendMessage`.
- Store returned `quote_id` on user message entity when API provides it.
- Stop using `related_message_id` as the only quote provenance field (keep graph semantics separate).
- Hydration: `convert-api-message.ts` today reads only `extend_info.quoted_text`; add `quote_id` + denormalized fields from inquiry API (Phase 5.2).
- Optimistic send: `quotedText` without `quoteId` until response; replay must not clear quote when only `quote_id` is present.

**Out of scope:** `/c/chat` landing until a thread exists to quote from.

---

## 7. Target backend modules

| Module | Responsibility |
|--------|----------------|
| `models/quote.py` (TBD) | `QuotedSnippet` Pydantic model |
| `database/*` | CRUD `room_quotes`, indexes by `room_id` |
| `common/turn_context.py` | `TurnContext`, `TurnQuote`, `load_turn_context()` |
| `services/quote_service.py` (TBD) | Create snippet on send; get by id |
| `services/prompt_envelope.py` (TBD) | `build_agent_execution_envelope(turn, agent_task, …)` |
| `RoomMessageCenter` | Load `turn` once; pass to supervisor / queue |
| `AgentMessageProcessor` | Accept `turn`; no bare `quoted_text` param |
| `ContextAssemblyService` | Called **only** from envelope builder; gains `agent_task` section param (§5.5) |
| `room_services.process_agent_message` | Calls envelope builder; stops passing agent body as `current_task` |

---

## 8. Design decisions & policies

### 8.1 Immutability

The quoted snippet is a **snapshot**. Editing the source agent message after send does not change the snippet.

### 8.2 Quote vs `task_content` (UI)

- **Quoted context** = `QuotedSnippet.text` (what the user selected).
- **Task** = supervisor/queue wording (`task_content` in SSE/UI).

Agents see both; users should see both in the UI where relevant.

### 8.3 Truncation

Today `ContextAssemblyService` may truncate quotes to 500 chars under token budget.

**Decision: Policy A** — quote is **never truncated** at dispatch; reserve budget from `agent_task` / room memory first. If over hard model limit, fail the turn with a visible error rather than silently clipping the highlight.

(Alternatives B/C remain documented for rollback: truncate at insert with client warning, or at dispatch with `… [truncated]` marker in UI and logs.)

### 8.4 Token budget

Quote + user message + task compete for context window. Envelope builder owns ordering and budget enforcement with quote protected if policy A.

### 8.5 Single quote per turn

One `quote_id` per user message. Replacing quote in composer before send replaces the payload (one snippet per send).

### 8.6 HITL

New user messages while HITL is pending are blocked (`409`). Clarify **answers** are separate user messages; they do not inherit the original turn’s `quote_id` unless product says otherwise.

### 8.7 Room memory policy

On send, `_initialize_room_memory` today stores **only** `user_message.message_content.message_text`—not the quote. Later turns’ `conversation_context` therefore lacks the excerpt unless planners load `turn.quote`.

**Decision: Policy A (v1)** — room memory user turn = `message_text` only. Quote is loaded via `TurnContext` for:

- All agent dispatches on the **originating** turn
- Planners that explicitly receive `turn.quote` / excerpt in Phase 4

**Not chosen for v1:**

- **B:** Append excerpt to memory line (`[Quoted excerpt]\n…\n\n[User]\n…`) — affects compaction and all future supervisor context.
- **C:** Store `quote_id` in memory metadata and resolve when rendering history — more moving parts.

Revisit B/C if supervisor planning without `turn` on later turns proves insufficient.

### 8.8 Security & integrity

| Rule | Action |
|------|--------|
| **Room scope** | Reject quote create if provenance does not resolve in the same `room_id` (see table below). |
| **Transactional create** | Prefer Mongo multi-doc transaction when deployment supports it (replica set). **Fallback:** insert `QuotedSnippet` first → insert user message → on user-message failure, **delete snippet** by `quote_id` (compensating delete). Log orphan cleanup. |
| **Dual-write (migration)** | Snippet is **source of truth**; inline `extend_info.quoted_text` is derived copy for one release. If both exist and differ, **snippet wins**; log warning. |
| **Logs** | Observability fields only (`quote_id`, `quote_len`, `source_kind`) — no full quote text in logs. |

**`source_message_id` validation by `source_kind`:**

| `source_kind` | `source_message_id` must | On failure |
|---------------|--------------------------|------------|
| `agent` | Exist as `RoomAgentMessage.message_id` in `room_id` | `400` invalid quote source |
| `synthesis` | Exist as agent/synthesis message in `room_id` (same store as agent messages) | `400` |
| `user_turn` | Equal a **user** `message_id` in `room_id` (the turn anchor; used when quoting HYBRO final answer via `turnId`) | `400` |
| `unknown` | Optional skip strict check if client omits kind during rollout; still require non-empty `text` | — |

Do not require `source_message_id` to match an agent message when `source_kind` is `user_turn`.

### 8.9 Missing snippet

If `quote_id` is set but DB row is missing (deleted, corruption):

**Decision: fail closed** — do not dispatch agents without the quoted context; return error to user / mark turn failed with actionable message. Do not silently drop the quote.

**UX contract (implement in Phase 1–3):**

| Surface | Behavior |
|---------|----------|
| HTTP `sendMessage` | If snippet create/load fails before orchestration: `success: false`, `4xx/5xx`, `error` message e.g. “Could not save quoted context. Try again.” |
| Orchestration mid-turn | If `load_turn_context` finds missing snippet: fail turn; SSE `processing_status` terminal state with error; no partial agent dispatch |
| Frontend | Toast or inline error; do not show optimistic user message as “completed” if send failed |

Exact status codes are backend-owned; frontend should surface `error` string from `RoomCenterUserMessageResponse`.

### 8.10 Quote length limits

| Limit | Value | Where enforced |
|-------|-------|----------------|
| User message text | `MAX_MESSAGE_LENGTH` = **10_000** chars (`models/room.py`) | Existing user message validation |
| Quote snapshot `text` | **8_000** chars (v1 default) | `QuoteService.create_from_send` + client pre-check in composer |
| Combined prompt | Model context budget | Envelope builder (§8.3 Policy A protects quote; may **fail turn** if quote + task + history exceed hard cap) |

Quote cap is **independent** of user message cap (a short question + long selection is valid up to 8k quote). Reject at insert with `400` and client-visible message; do not silently truncate at insert in v1.

---

## 9. Comparison: ChatGPT

OpenAI does not document ChatGPT’s internal quote schema. Observable behavior:

| Aspect | ChatGPT (inferred) | Hybro (target) |
|--------|-------------------|----------------|
| Architecture | One model, one thread | Many agents, supervisor, queue, resume |
| Persistence | Quote embedded in next **user message** in conversation log | **`QuotedSnippet`** + `quote_id` on turn |
| Provenance | Implicit via full history; users report attribution issues | Explicit `source_kind`, `source_message_id`, role in envelope |
| Resume | Opaque server-side conversation state | Reload `quote_id` from user message |
| Multi-agent | N/A | **Must** use turn + envelope builder |

**Take from ChatGPT:** composer preview, clear separation of excerpt vs user question, quote as **focus pointer** on top of history.

**Go beyond ChatGPT:** persisted snapshot, stable id, mode-agnostic dispatch, no supervisor-only paraphrase as the quote.

---

## 10. Gap analysis (interim → target)

| Gap | Interim | Target fix |
|-----|---------|------------|
| Queue webhook resume | No `quoted_text` on `process_queue` resume | `load_turn_context(user_message_id)` |
| `quoted_text` threading | Optional param; easy to drop | `TurnContext` required at dispatch |
| `extend_info` wipe | Supervisor prep could clear quote | `quote_id` on message; merge helper for other keys |
| `related_message_id` overload | Quote source = related_message_id | Provenance on `QuotedSnippet` only |
| Fast planner blind | Decomposer lacks quote | `turn.quote` in parse/decompose input |
| Truncation | 500-char quote truncation possible | Policy §8.3 |
| HYBRO `turnId` as quote source | Wrong id type for agent provenance | `source_kind` on create |
| `[Current request]` uses agent task | User prompt buried in supervisor text | §5.2.1 — `current_task = turn.message_text` |
| Room memory omits quote | Planners blind on later turns | §8.7 Policy A + Phase 4 planner input |
| Double assembly paths | Legacy string memory + CAS | §5.5 single envelope owner |
| `source_message_id` trust | Cross-room spoof risk | §8.8 validation |
| Dual-write drift | Inline vs snippet mismatch | Snippet wins (§8.8) |
| Hydration `quote_id` only | UI loses quote on reload | Denormalize on inquiry API (§4.3) |
| Push/HITL agent resume | Same as queue resume | `load_turn_context(user_message_id)` |
| Mention-only fan-out | Bypasses supervisor | Envelope on each mention dispatch |
| Wire `user_input` vs `message_text` drift | Duplicate fields can disagree | §4.4 canonical rule |
| Quote > 8k / insert truncate | Uncapped or silent clip | §8.10 reject at insert |
| CAS/facade not updated in Phase 3 | Quote only in legacy path | §5.6 touch map |
| Mongo no transactions | Orphan snippets | §8.8 compensating delete |

---

## 11. Implementation plan

### Phase 0 — Interim fixes (no new collection)

| Step | Action |
|------|--------|
| 0.1 | Queue resume: load user message → `quoted_text` from `extend_info` → pass into `process_queue`. |
| 0.2 | Ensure all `extend_info` merges use spread (supervisor prep, clarify resume). |
| 0.3 | Frontend: `selection-plain-text`; UI quote on user bubble + detail pane. |

**Unblocks:** Fast mode resume; aligns with today’s inline model until Phase 1–3.

---

### Phase 1 — `QuotedSnippet` persistence (backend)

| Step | Action |
|------|--------|
| 1.1 | Model + Mongo collection `room_quotes` (or equivalent). |
| 1.2 | `QuoteService.create_from_send(quote_payload) → quote_id`. |
| 1.3 | On `send_message_to_room`: create snippet + user message (§8.8 transaction or compensating delete); set `user_message.quote_id` or `extend_info.quote_id`; dual-write inline copy if migrating. |
| 1.4 | API response includes `quote_id` + denormalized `quoted_text` / `quoted_sender_name` on `message` for UI. |
| 1.5 | Implement `source_message_id` validation table (§8.8). |
| 1.6 | Enforce `MAX_QUOTE_TEXT_LENGTH` = 8000 (§8.10). |

**Tests:** Create + read; room cascade delete; max length validation.

---

### Phase 2 — `TurnContext` loader (backend)

| Step | Action |
|------|--------|
| 2.1 | `TurnContext`, `load_turn_context(user_message)` — join `QuotedSnippet` by `quote_id`. |
| 2.2 | Legacy fallback: inline `extend_info.quoted_text` → synthetic in-memory `TurnQuote` (no id). |
| 2.3 | Unit tests: quote_id only, legacy only, both absent, both present (snippet wins), missing snippet (fail closed §8.9). |

**Files:** `common/turn_context.py`, `tests/test_turn_context.py`

---

### Phase 3 — Envelope builder + orchestration (backend)

| Step | Action |
|------|--------|
| 3.1 | `build_agent_execution_envelope(turn, agent_task, room_memory, …)` per §5.5. |
| 3.2 | `ContextAssemblyService`: add `agent_task` section; `current_task` = `turn.message_text` (§5.2.1). |
| 3.3 | `RoomMessageCenter`: `turn = load_turn_context(...)` once; pass to supervisor + queue. |
| 3.4 | `SupervisorExecutor`: `decide_next(turn)`; dispatch uses envelope builder. |
| 3.5 | `QueueExecutor.process_queue(turn)`; resume reloads `turn` by `user_message_id` only. |
| 3.6 | Unify legacy string-memory branch through envelope builder. |
| 3.7 | Remove threaded `quoted_text` kwargs after migration. |
| 3.8 | Update `context_memory/assembly.py` + facade per §5.6; remove quote truncation in assembly path. |
| 3.9 | Prompt-quality regression sample (§5.6) before merge. |

**Tests:** §13 acceptance matrix.

---

### Phase 4 — Planners (backend)

| Step | Action |
|------|--------|
| 4.1 | `parse_user_message` / decomposer: include `turn.message_text` + quote excerpt when `turn.quote` set. |
| 4.2 | Supervisor: instruct not to paraphrase quote into `task` when `turn.quote_id` set (envelope has verbatim). |
| 4.3 | Debate: template references user message; verbatim quote only in envelope. |

---

### Phase 5 — Frontend wire + display (frontend)

| Step | Action |
|------|--------|
| 5.1 | Send `quote` on nested `message` per §4.4; keep `user_input` in sync with `message_text`. |
| 5.2 | Message store: `quoteId` + `quotedText` (denormalized from API response). |
| 5.3 | Extend `QuoteData` per §4.6; set `source_kind` from quotable DOM. |
| 5.4 | Stop sending `quoteData.messageId` as `related_message_id` unless needed for non-quote graph. |

---

### Phase 6 — Hardening

| Step | Action |
|------|--------|
| 6.1 | Truncation policy §8.3 implemented in envelope builder. |
| 6.2 | Observability: `quote_id`, `quote_len`, `source_kind` per turn (no full text in logs). |
| 6.3 | `merge_extend_info()` helper for non-quote keys. |
| 6.4 | Optional: `POST /rooms/{id}/quotes` if pre-send creation needed. |

---

## 12. Migration from interim

| Stage | User messages | Orchestration |
|-------|---------------|---------------|
| **Now** | `extend_info.quoted_text` | Thread `quoted_text` param |
| **Dual-read** | `quote_id` or legacy inline | `load_turn_context` handles both |
| **Dual-write** | Create snippet + copy inline (one release) | Snippet is source of truth; inline for old clients |
| **Final** | `quote_id` only | `TurnContext` only |

**Dual-read precedence:** If `quote_id` resolves to a snippet, use it. Else use `extend_info.quoted_text`. If both exist and text differs, snippet wins + warning log (§8.8).

No backfill required for old rooms unless product wants historical quotes addressable by id.

---

## 13. Test matrix (acceptance)

When the turn has a quote (`quote_id` or legacy inline), every agent dispatch must include verbatim quote text in `[Quoted context]`:

| Scenario | Mode |
|----------|------|
| Single delegate | Ultimate |
| Multi delegate | Ultimate |
| Sequential agents | Fast |
| Queue webhook resume (remaining agents) | Fast |
| Debate step 1 and N | Fast debate |
| Debate fast-path | Ultimate debate |
| Direct chat / single agent | Fast |
| Supervisor clarify resume | Ultimate |
| @mention dispatch | Any |
| Supervisor prep does not clear `quote_id` | Ultimate |
| Missing snippet (deleted) | Fail closed (§8.9) |
| @mention-only fan-out | Any |
| Push-notification / HITL agent pause → resume | Ultimate / Fast |
| Hub / relay transport dispatch | Any |
| Legacy string `room_memory` path | Fast / edge rooms |
| Debate after `inject_short_debate` | Fast debate |
| Dual-write mismatch (inline ≠ snippet) | Snippet wins in envelope |
| `[Current request]` contains user prompt, not supervisor paraphrase only | All modes |
| Inquiry hydrate with `quote_id` only (denormalized text on API) | Frontend replay |
| Quote text > 8000 chars rejected at send | API `400` |
| Invalid `source_message_id` for `source_kind` | API `400` |
| Snippet save failure before orchestration | Fail closed + error to client (§8.9) |

---

## 14. Known product limitations

- Text-only quotes; structure approximated at capture.
- Single quote per compose.
- No keyboard shortcut.
- Quoting user messages not supported (agent/synthesis surfaces only).
- Quoting in-flight streaming content may capture incomplete text.
- Coordinator re-synthesis does not automatically re-run with quote (set UI expectation when quoting HYBRO synthesis).
- Code blocks / tables: capture may lose structure inside fences; v1 `format: plain`.
- Room search/index does not include `QuotedSnippet` text unless added separately.
- Compaction: user turns may summarize; quote snapshots in `room_quotes` are independent and not compacted away.

---

## 15. Code references

### Current (interim)

| Concept | Location |
|---------|----------|
| `QuoteData` | `src/lib/types/quote.ts` |
| Selection | `src/lib/selection-plain-text.ts` |
| Quote hook | `src/hooks/useTextSelectionQuote.ts` |
| Send API | `src/lib/api/room.ts` |
| Orchestration extract | `execution/orchestration/room_message_center.py` |
| Agent assembly (interim bug: task as `[Current request]`) | `services/context_assembly_service.py`, `room_services.process_agent_message` (~L3314) |
| Context memory assembly (quote truncation) | `context_memory/assembly.py`, `context_memory/facade.py` |
| Request model | `models/request.py` `RoomCenterUserMessageRequest` |
| Room memory (no quote) | `room_services._initialize_room_memory` (~L2587) |
| Queue resume gap | `execution/orchestration/queue_executor.py` `resume_from_continuation` |

### Target (to add)

| Concept | Location (planned) |
|---------|-------------------|
| `QuotedSnippet` model | `models/quote.py` |
| Turn loader | `common/turn_context.py` |
| Envelope builder | `services/prompt_envelope.py` (TBD) |
| Quote CRUD | `services/quote_service.py` (TBD) |

---

## 16. Related docs

- `docs/FINAL_ANSWER_FIRST_DESIGN.md` — coordinator vs supervisor display
- `docs/architecture.md` — room orchestration
- `docs/ROOM_SYNC_REFACTOR.md` — message hydration
- Backend: `docs/SUPERVISOR_V2_DESIGN.md`, `multi-agents-backend/docs/HITL_DESIGN.md`

---

## 17. Pre-implementation checklist

Resolve or implement before calling quote reply “done”:

| # | Item | Section |
|---|------|---------|
| 1 | `[Current request]` = `turn.message_text`, not agent task body | §5.2.1, Phase 3 |
| 2 | Single envelope owner; no duplicate quote in legacy path | §5.5, §5.6 |
| 3 | Truncation Policy A enforced (incl. `context_memory/assembly.py`) | §8.3, §5.6 |
| 4 | Room memory Policy A + planner quote input | §8.7, Phase 4 |
| 5 | `source_message_id` validation by `source_kind` | §8.8 |
| 6 | Snippet + user message persist (transaction or compensating delete) | §8.8, Phase 1 |
| 7 | Missing snippet fail closed + UX | §8.9 |
| 8 | Dual-read/write precedence | §8.8, §12 |
| 9 | Inquiry API denormalizes quote for UI | §4.3, Phase 1.4 / 5.2 |
| 10 | Phase 0 queue resume (interim) | §11 Phase 0 |
| 11 | Canonical wire: `message.quote` + `user_input` sync | §4.4 |
| 12 | Quote max length 8k enforced | §8.10 |
| 13 | Phase 3 prompt-quality regression | §5.6 |
| 14 | Hub/relay paths audited | §5.6, §13 |
| 15 | Full §13 acceptance matrix green | §13 |

---

## 18. Implementation contracts (summary)

Quick reference for engineers starting work—details in sections above.

| Topic | Contract |
|-------|----------|
| **Wire** | Quote payload on `request.message.quote`; `message_text` canonical; `user_input` must match (§4.4). |
| **Provenance** | On `QuotedSnippet` only; `related_message_id` not for quote-only (§4.2). |
| **Dispatch text** | Snapshot `text` at send; never re-fetch `source_message_id` (§4.1). |
| **Envelope** | `[Quoted context]` + `[Current request]` = user text + `[Task]` = agent_task (§5.2, §5.2.1). |
| **CAS / memory** | Phase 3 updates CAS + `context_memory/*` per §5.6. |
| **Validation** | `source_kind` table (§8.8); quote ≤ 8k chars (§8.10). |
| **Persist** | Mongo transaction if available; else snippet-first + compensating delete (§8.8). |
| **Failure** | Missing snippet / save failure → fail closed + user-visible error (§8.9). |
| **Migration** | Dual-read: snippet wins; dual-write one release (§12). |
| **Client** | `QuoteData` + `source_kind` in Phase 5 (§4.6). |
