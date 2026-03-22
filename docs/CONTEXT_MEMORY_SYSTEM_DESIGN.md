# Context & Memory System Architecture Design

**Date**: February 25, 2026 (updated from Feb 21)  
**Status**: Fully Implemented — All 5 phases complete (data models, context assembly, compaction, search, supervisor integration)  
**Scope**: Unified context and memory management for multi-agent A2A rooms  
**Predecessor**: [SUPERVISOR_V2_DESIGN.md](./SUPERVISOR_V2_DESIGN.md) (Phase 5 complete)

> ⚠️ **Schema migration notice (Phase 3)**: This document describes the fully-implemented context memory system reading from `room_agent_messages.memory_content.conversation_history`. During `RECOMMENDED_ARCHITECTURE.md` Phase 3 (Persistence Unification), the `messages` and `artifacts` MongoDB collections replace `room_agent_messages`. The `context_memory/` module absorbs this change internally (see `PERSISTENCE_UNIFICATION_DESIGN.md §9`): `context_assembly.py` will read from `db.messages` instead of `db.room_agent_messages`, and the compaction trigger will use `messages` collection length instead of `conversation_history` array length. All public `ContextMemory` facade APIs remain unchanged. This document should be updated after Phase 3 is implemented.

---

## 1. Executive Summary

This document defines the architecture for a production-grade context and memory system for Hybro's multi-agent platform. The design draws from:

- **Manus**: KV-cache optimization, file-system as context, attention manipulation via recency
- **OpenClaw**: Multi-layer memory (daily logs + curated long-term), compaction, hybrid search
- **Hybro Supervisor Pattern**: Integration with planning, review, and synthesis phases

### 1.1 Key References

| Source                                                                                                                  | Key Lessons Applied                                               |
| ----------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| [Manus Blog: Context Engineering](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus) | KV-cache optimization, lossless compaction, file system as memory |
| [Lance Martin: Context Engineering in Manus](https://rlancemartin.github.io/2025/10/15/manus/)                          | Compaction vs compression, context isolation, context offloading  |
| [Peak's Slides (Google Drive)](https://drive.google.com/file/d/1QGJ-BrdiTGslS71sYH4OJoidsry3Ps9g/view)                  | Detailed compaction strategy, sub-agent context sharing           |
| [OpenClaw Multi-Agent](https://docs.openclaw.ai/concepts/multi-agent)                                                   | Agent isolation, workspace-per-agent, session routing             |
| [OpenClaw Context](https://docs.openclaw.ai/concepts/context)                                                           | Token budgeting, system prompt structure, tool schema costs       |
| [OpenClaw Memory](https://docs.openclaw.ai/concepts/memory)                                                             | Daily logs + MEMORY.md, vector search                             |
| [SYNAPSE arXiv 2601.02744 (Jan 2026)](https://arxiv.org/abs/2601.02744)                                                 | Episodic-semantic graph with spreading activation; solves "Contextual Tunneling" for multi-hop recall; 95% token reduction vs full-context |
| [MAGMA arXiv 2601.03236 (Jan 2026)](https://arxiv.org/abs/2601.03236)                                                   | Multi-graph memory (semantic + temporal + causal + entity); policy-guided traversal; SOTA on LoCoMo/LongMemEval |
| [Mnemis arXiv 2602.15313 (Feb 2026)](https://arxiv.org/abs/2602.15313)                                                  | Dual-route retrieval: fast similarity + deliberate hierarchical graph traversal; 93.9 LoCoMo, 91.6 LongMemEval-S |
| [A-MEM arXiv 2502.12110 (Feb 2025)](https://arxiv.org/abs/2502.12110)                                                   | Zettelkasten note generation at write time: keywords/tags/connections stored with each turn for richer future retrieval |
| [Focus arXiv 2601.07190 (Jan 2026)](https://arxiv.org/abs/2601.07190)                                                   | Agent-centric autonomous compression: agent decides when to consolidate to "Knowledge block" and prune; 22.7% token reduction, zero accuracy loss |
| [AgeMem arXiv 2601.01885 (Jan 2026)](https://arxiv.org/abs/2601.01885)                                                  | Unified LTM/STM: memory ops (store/retrieve/update/summarize/discard) as tool-based actions; RL-trained |
| [MEM1 arXiv 2506.15841 (ICLR 2026)](https://arxiv.org/abs/2506.15841)                                                   | RL-trained compact internal state updated each turn; 3.5× performance, 3.7× memory reduction vs full-context; constant context size |

### 1.2 Critical Distinction: Compaction vs Compression

> **IMPORTANT**: This design uses **compaction** (lossless), NOT **compression** (lossy).

| Approach                      | Description                                            | Data Loss | Example                                                |
| ----------------------------- | ------------------------------------------------------ | --------- | ------------------------------------------------------ |
| **Compression** (❌ NOT used) | Summarize content, discard original                    | Lossy     | "User discussed 5 topics about React"                  |
| **Compaction** (✅ Used)      | Replace content with pointer, keep original in storage | Lossless  | `{type: "message", id: "msg_123", storage: "mongodb"}` |

From Manus blog:

> _"Our compression strategies are always designed to be restorable. For instance, the content of a web page can be dropped from the context as long as the URL is preserved, and a document's contents can be omitted if its path remains available in the sandbox. This allows Manus to shrink context length without permanently losing information."_

**Key insight**: Compaction is like building an index for a book. You put the much smaller index into context; when you need the actual record, use the pointer to fetch the original from storage.

---

## 2. Design Principles

### 2.1 From Manus: Context Engineering

1. **KV-Cache Optimization**: Keep context prefixes stable; append-only updates
   - _"Even a single token difference invalidates the cache from that point forward"_
   - Avoid timestamps at start of prompts; use deterministic serialization
2. **File System as Memory**: Externalize long-term state to persistent storage
   - _"The file system is the ultimate context: unlimited size, naturally persistent"_
   - MongoDB serves as our "file system" for durability
3. **Attention Manipulation**: Use recency and summarization to guide model focus
   - _"By rewriting the todo list, Manus restates its goals at the end of context"_
   - Recent turns and current task always at context end
4. **Preserve Errors**: Keep failed attempts in context for learning
   - _"Erasing failures removes evidence. The model can't adapt without it."_
   - Store `was_successful` flag on conversation turns

### 2.2 From OpenClaw: Memory Architecture

1. **Multi-Layer Memory**: Separate ephemeral (session) from durable (room/user) memory
   - Session context: current request cycle only
   - Room memory: persistent across sessions
   - User memory: cross-room preferences
2. **Compaction** (NOT Compression): Replace content with pointers, keep originals
   - Auto-trigger based on turn count or token estimate
   - **Lossless**: Original data always retrievable from storage
   - Compaction = indexing, NOT summarization
3. **Hybrid Search**: Combine vector similarity with keyword matching
   - Vector: semantic similarity for paraphrased queries
   - BM25: exact token matching for IDs, code symbols, error strings
4. **Temporal Decay**: Boost recent memories in search results
   - Half-life decay: 30-day default, configurable per environment

### 2.3 Hybro-Specific Requirements

1. **Multi-Agent Awareness**: Each agent needs room context + peer awareness
2. **Supervisor Integration**: Memory feeds into the adaptive loop (`decide_next` → dispatch → synthesis)
3. **A2A Protocol Compatibility**: Context must serialize for external agents
4. **Horizontal Scalability**: No in-memory state that prevents scaling

### 2.4 From 2026 Research: Advanced Memory Principles

Five principles from Jan–Feb 2026 papers that inform the evolution of this design:

1. **Write-time Note Generation** (A-MEM / Zettelkasten, arXiv 2502.12110):
   When a turn is stored, generate structured metadata — keywords, tags, a brief entity list, and connections to related prior turns. This is cheap at write time and dramatically improves retrieval quality later. The `turn_notes` field on `ConversationTurn` (§6.2) implements this.

2. **Rolling Room Summary / Knowledge Block** (Focus, arXiv 2601.07190):
   Maintain a continuously-updated structured summary alongside raw turn history: current goal, key decisions, open questions, recent agent contributions. This "Knowledge block" eliminates the need to scan 50+ turns for context — the summary is always in-context. The `room_summary` field on `RoomMemory` (§4.2) implements this.
   > _"Capable models can autonomously self-regulate their context when given appropriate tools and prompting."_ — Focus paper

3. **Graph-Layer Retrieval for Multi-Hop Queries** (SYNAPSE/MAGMA/Mnemis, arXiv 2601.02744, 2601.03236, 2602.15313):
   Flat vector similarity fails on temporal and causal multi-hop queries — "what led to the failure in session 3?" Vector search finds semantically similar text, not causally upstream text. A graph layer (linking turns via entity, temporal, and causal edges) is needed to solve this class of query. See §8.3 for the planned upgrade path.

4. **Context Occupancy as a First-Class Metric** (production data, 2025–2026):
   Production systems operate at a 100:1 input/output token ratio; context costs dominate. Models degrade significantly before hitting hard token limits ("context rot": GPT-4 drops from 98.1% → 64.1% accuracy based on information structure alone). Track context occupancy % per request with a soft cap (70%) and hard cap (85–90%); alert on truncation events. See §15.

5. **Agent-Driven Compaction** (AgeMem/Focus, arXiv 2601.01885, 2601.07190):
   Static threshold-based compaction (`max_turns > 20`) is suboptimal — the agent often knows which past turns are irrelevant before the threshold fires. Exposing a `compact_context(rationale)` tool lets the supervisor proactively signal compaction based on semantic relevance, not just count. This is a medium-term evolution; threshold compaction is correct to ship first.

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         CONTEXT & MEMORY SYSTEM                                  │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │   Session    │  │    Room      │  │    User      │  │   Agent      │         │
│  │   Context    │  │   Memory     │  │   Memory     │  │   Memory     │         │
│  │  (Ephemeral) │  │  (Durable)   │  │  (Durable)   │  │  (Durable)   │         │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘         │
│         │                 │                 │                 │                  │
│         └────────────┬────┴────────┬────────┴────────┬────────┘                  │
│                      │             │                 │                           │
│                      ▼             ▼                 ▼                           │
│              ┌───────────────────────────────────────────┐                       │
│              │         Context Assembly Engine           │                       │
│              │  (Builds per-request context window)      │                       │
│              └───────────────────────────────────────────┘                       │
│                                    │                                             │
│         ┌──────────────────────────┼──────────────────────────┐                  │
│         ▼                          ▼                          ▼                  │
│  ┌─────────────┐          ┌─────────────┐          ┌─────────────┐               │
│  │  Supervisor │          │   Agent     │          │    A2A      │               │
│  │   Context   │          │   Context   │          │   Context   │               │
│  │  (Adaptive  │          │ (Execution) │          │  (External) │               │
│  │    Loop)    │          │             │          │             │               │
│  └──────┬──────┘          └──────┬──────┘          └──────┬──────┘               │
│         │                        │                        │                      │
│         │    ┌───────────────────┴────────────────────────┘                      │
│         │    │                                                                   │
│         ▼    ▼                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐         │
│  │                    HITL INTEGRATION LAYER                            │         │
│  │  (Human-in-the-Loop — see HITL_DESIGN.md)                            │         │
│  ├─────────────────────────────────────────────────────────────────────┤         │
│  │                                                                      │         │
│  │  Interrupt Triggers:                                                 │         │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐       │         │
│  │  │ Supervisor      │  │ Agent returns   │  │ Push-notification│       │         │
│  │  │ CLARIFY action  │  │ input_required  │  │ PAUSED state     │       │         │
│  │  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘       │         │
│  │           │                    │                    │                │         │
│  │           └────────────────────┼────────────────────┘                │         │
│  │                                ▼                                     │         │
│  │                    ┌───────────────────────┐                         │         │
│  │                    │ _save_interrupted_    │                         │         │
│  │                    │ state(kind=...)       │                         │         │
│  │                    │ • Serialize trajectory│                         │         │
│  │                    │ • Persist to MongoDB  │                         │         │
│  │                    └───────────┬───────────┘                         │         │
│  │                                │                                     │         │
│  │           ┌────────────────────┼────────────────────┐                │         │
│  │           ▼                    ▼                    ▼                │         │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐       │         │
│  │  │ HITL_SUPERVISOR │  │ HITL_AGENT      │  │ PUSH_NOTIFICATION│       │         │
│  │  │ → HITLService   │  │ → HITLService   │  │ → Webhook waits │       │         │
│  │  │ → SSE prompt    │  │ → SSE prompt    │  │                 │       │         │
│  │  │ → User replies  │  │ → User replies  │  │                 │       │         │
│  │  │ → Direct resume │  │ → Agent webhook │  │ → Agent webhook │       │         │
│  │  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘       │         │
│  │           │                    │                    │                │         │
│  │           └────────────────────┼────────────────────┘                │         │
│  │                                ▼                                     │         │
│  │                    ┌───────────────────────┐                         │         │
│  │                    │ _resume_supervisor_v2 │                         │         │
│  │                    │ • Deserialize         │                         │         │
│  │                    │ • Inject result/reply │                         │         │
│  │                    │ • Re-run loop         │                         │         │
│  │                    └───────────────────────┘                         │         │
│  │                                                                      │         │
│  │  Memory Integration (HITL_DESIGN.md §10.1):                          │         │
│  │  ┌─────────────────────────────────────────────────────────────┐     │         │
│  │  │ After HITL response, write to Room Memory:                   │     │         │
│  │  │ • ConversationTurn(role="assistant", turn_type="hitl_question")│     │         │
│  │  │ • ConversationTurn(role="user", turn_type="hitl_reply")       │     │         │
│  │  │ → Future conversation_context includes HITL exchanges        │     │         │
│  │  │ → room_summary updated at next synthesis boundary            │     │         │
│  │  └─────────────────────────────────────────────────────────────┘     │         │
│  │                                                                      │         │
│  └─────────────────────────────────────────────────────────────────────┘         │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 HITL Integration Points

The HITL (Human-in-the-Loop) system integrates with Context & Memory at three key points:

| Integration Point | Memory Layer | Description |
|-------------------|--------------|-------------|
| **Interrupt State** | Session Context | Trajectory serialized with `interrupt_kind` to `pending_continuation` |
| **HITL Turn Recording** | Room Memory | HITL question/reply pairs written as `ConversationTurn` entries |
| **Context Refresh** | Context Assembly | On resume, `conversation_context` is re-fetched to include any changes during pause |

**Cross-reference**: See [HITL_DESIGN.md](./HITL_DESIGN.md) for the full HITL architecture, including:
- §3 Architecture Overview (unified interrupt mechanism)
- §5.7 Continuation Payload (trajectory serialization)
- §10.1 HITL Turn Recording in Room Memory

---

## 4. Memory Layers

### 4.1 Session Context (Ephemeral)

**Purpose**: Current conversation state within a single user message processing cycle.

```python
class SessionContext(BaseModel):
    """Ephemeral context for a single request processing cycle."""
    session_id: str
    room_id: str
    user_id: str

    # Current request
    user_message: str
    user_message_id: str
    created_at: datetime

    # Supervisor V2 state (if multi-agent)
    # NOTE: There is no SupervisorPlan — V2 uses an adaptive loop.
    # The trajectory (all actions + results so far) is the single source of truth.
    # It lives in user_message.extend_info.supervisor_trajectory (SupervisorTrajectory).
    supervisor_trajectory: "SupervisorTrajectory | None" = None

    # Snapshot of room history passed to the supervisor LLM prompt.
    # Built once in _prepare_for_supervisor_v2() and FROZEN for the loop duration.
    # Agent results written during the loop are NOT reflected here — they come
    # through trajectory_summary in the supervisor prompt instead.
    conversation_context: str | None = None

    # Token tracking
    estimated_tokens: int = 0
    max_tokens: int = 128000  # Model-specific
```

**Lifecycle**: Created when user sends message → Destroyed after all agents respond

> **V1 → V2 changes**: `supervisor_plan`, `completed_steps`, `current_step_index`, and
> `step_results` are **eliminated**. V1's `SupervisorPlan`/`SupervisorStep`/`StepResult`
> models no longer exist. `SupervisorTrajectory` (from `models/supervisor_v2.py`) is the
> sole execution state. See [SUPERVISOR_V2_DESIGN.md §4](./SUPERVISOR_V2_DESIGN.md).

### 4.2 Room Memory (Durable)

**Purpose**: Persistent conversation history and learned context for a room.

```python
class RoomSummary(BaseModel):
    """
    Rolling structured summary of the room's current state — the "Knowledge Block"
    (Focus paper, arXiv 2601.07190). Maintained at each synthesis boundary and
    updated incrementally as conversations progress.

    Always kept FULL in context (it replaces scanning 50+ old turns).
    This is NOT a replacement for lossless compaction — it is a structured overlay
    that avoids the round-trip cost of fetch_turn_content for common context queries.
    """
    # Structured named slots (agent-maintainable)
    current_goal: str | None = None          # What the user/room is trying to accomplish
    key_decisions: list[str] = []            # Decisions made that should persist
    open_questions: list[str] = []           # Unresolved questions or blockers
    recent_agent_contributions: list[str] = []  # Last 3-5 agent result summaries
    important_constraints: list[str] = []    # Hard constraints the user stated

    # Metadata
    last_updated_at: datetime | None = None
    updated_after_turn_id: str | None = None  # Which turn triggered the last update


class RoomMemory(BaseModel):
    """Durable memory for a chat room."""
    room_id: str

    # Conversation history (mix of full and compact representations)
    conversation_history: list[ConversationTurn] = []
    max_history_turns: int = 100  # Total turns to keep (full + compact)

    # Rolling structured summary — always in context, updated at synthesis boundaries.
    # Replaces the need to scan 50+ compact turns for common context queries.
    # See §2.4 "Rolling Room Summary / Knowledge Block" and §4.5.
    room_summary: RoomSummary = Field(default_factory=RoomSummary)

    # Room-level learned facts (extracted from conversations)
    room_facts: list[RoomFact] = []

    # Agent interaction patterns
    agent_success_history: dict[str, AgentSuccessRecord] = {}

    # Metadata
    last_activity_at: datetime
    total_messages: int = 0
    total_compactions: int = 0  # Number of compaction operations performed


# ConversationTurn and ContentReference definitions have been moved to §6.2,
# which is the single canonical source of truth for these models.
# See §6.2 for the full definition including representation, content_ref,
# estimated_tokens_*, and brief_summary fields.
```

> **See §6.2** for the canonical `ConversationTurn` and `ContentReference` definitions.
> The code blocks that previously appeared here were stale (missing `brief_summary`,
> wrong `content_type` literals, wrong `ContentReference.storage_type`). They have
> been removed to avoid divergence. `RoomMemory.conversation_history` is typed as
> `list[ConversationTurn]` using the §6.2 definition.

**Storage**: MongoDB `room_memories` collection

**Key Design Points**:

1. **Lossless**: Compacted turns retain pointers to full content
2. **On-demand expansion**: Agent can request full content when needed
3. **Token-aware**: Track both full and compact token costs
4. **Rolling summary**: `room_summary` provides a structured in-context snapshot so the supervisor rarely needs to scan raw turn history

### 4.3 User Memory (Durable)

**Purpose**: Cross-room user preferences and interaction history.

```python
class UserMemory(BaseModel):
    """Durable memory for a user across all rooms."""
    user_id: str

    # User preferences (explicit)
    preferences: dict[str, Any] = {}

    # Learned patterns
    preferred_agents: list[str] = []  # agent_ids user frequently uses
    communication_style: str | None = None  # Detected style

    # Cross-room facts
    user_facts: list[UserFact] = []

    # Metadata
    created_at: datetime
    last_active_at: datetime
    total_interactions: int = 0
```

**Storage**: MongoDB `user_memories` collection

### 4.4 Agent Memory (Durable)

**Purpose**: Per-agent learned context and performance history.

```python
class AgentMemory(BaseModel):
    """Durable memory for an agent's learned context."""
    agent_id: str

    # Performance metrics
    total_calls: int = 0
    successful_calls: int = 0
    average_response_time_ms: float = 0

    # Task type performance
    task_type_success: dict[str, TaskTypeMetrics] = {}

    # Common failure patterns
    failure_patterns: list[FailurePattern] = []

    # Metadata
    last_called_at: datetime | None = None
```

**Storage**: MongoDB `agent_memories` collection

### 4.5 Storage Overview

All memory-related data is stored in MongoDB. The following collections are used:

| Collection | Purpose | Defined In |
|------------|---------|------------|
| `room_memories` | Durable room conversation history and learned context | §4.2 |
| `user_memories` | Cross-room user preferences and interaction history | §4.3 |
| `agent_memories` | Per-agent performance metrics and failure patterns | §4.4 |
| `conversation_content` | Full content for compacted turns (lossless compaction) | §6.6 |
| `hitl_requests` | Human-in-the-loop request lifecycle tracking | [HITL_DESIGN.md §5.7](./HITL_DESIGN.md) |

> **Note**: The `hitl_requests` collection is defined in `HITL_DESIGN.md` and stores pending,
> responded, expired, and canceled HITL requests. It is indexed by `request_id` (unique),
> `room_id + status` (for pending request lookup), and `expires_at + status` (for expiry job).

---

## 5. Context Assembly Engine

The Context Assembly Engine builds the final context window for each Agent call.

### 5.1 Assembly Pipeline

```
Input Request
     │
     ▼
┌─────────────────────────────────────────┐
│  1. LOAD MEMORY LAYERS                   │
│     - Session context (current)          │
│     - Room memory (from DB)              │
│     - User memory (from DB)              │
│     - Agent memory (for target agent)    │
└─────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│  2. BUDGET ALLOCATION                    │
│     - System prompt: ~2K tokens          │
│     - Room context: ~4K tokens           │
│     - Conversation history: ~8K tokens   │
│     - Current task: ~2K tokens           │
│     - Reserve for response: ~4K tokens   │
└─────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│  3. CONTEXT SELECTION                    │
│     - Recent turns (always include)      │
│     - Relevant summaries (by recency)    │
│     - Room facts (by relevance)          │
│     - Peer agent info (for awareness)    │
└─────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│  4. SERIALIZATION                        │
│     - Deterministic JSON ordering        │
│     - Stable prefixes for KV-cache       │
│     - Append-only structure              │
└─────────────────────────────────────────┘
     │
     ▼
Assembled Context Window
```

### 5.2 Token Budget Strategy

```python
class TokenBudget(BaseModel):
    """Token allocation for context assembly."""
    model_context_window: int = 128000

    # Fixed allocations
    system_prompt: int = 2000
    tool_schemas: int = 3000
    response_reserve: int = 4000

    # Dynamic allocations (percentages of remaining)
    room_context_pct: float = 0.15  # Room facts, agent roster
    conversation_history_pct: float = 0.60  # Full + compact turns
    current_task_pct: float = 0.25  # Current request + step context

    @property
    def available_for_content(self) -> int:
        return self.model_context_window - (
            self.system_prompt +
            self.tool_schemas +
            self.response_reserve
        )
```

**Note**: With lossless compaction, we don't need a separate budget for "summaries". Compact turns use ~20-50 tokens each (just pointers), so we can fit many more turns in the history budget.

---

## 6. Compaction System (Lossless)

> **CRITICAL**: Compaction is NOT summarization. It's replacing full content with pointers while preserving originals in storage. **Zero information loss.**

### 6.1 Compaction Philosophy

From [Lance Martin's analysis of Manus](https://rlancemartin.github.io/2025/10/15/manus/):

> _"Tool calls in Manus have a 'full' and 'compact' representation. The full version contains the raw content from tool invocation, which is stored in the sandbox (filesystem). The compact version stores a reference to the full result (e.g., a file path). Manus applies compaction to older ('stale') tool results. This just means swapping out the full tool result for the compact version."_

**Key principle**: The agent can always fetch the full result if needed, but saves tokens by removing "stale" results that have already been used.

### 6.2 Full vs Compact Representations

> **Canonical model**: This is the definitive `ConversationTurn` definition. The
> versions in §4.1 (which lacks `representation`, `content_ref`, and
> `estimated_tokens_*`) and §4.2 (which had wrong `content_type` literals,
> `ContentReference.storage_type: "file"` instead of `"s3"`, and no `brief_summary`)
> are pre-compaction snapshots. The §4.2 code blocks have been removed; §4.2 now
> redirects here. `models/memory.py` must be updated to match this definition
> before the compaction system is implemented.

```python
class ConversationTurn(BaseModel):
    """Single turn in conversation history. Supports full and compact representations."""
    turn_id: str
    role: Literal["user", "agent", "supervisor"]
    agent_id: str | None = None
    agent_name: str | None = None
    timestamp: datetime

    # Content representation
    representation: Literal["full", "compact"] = "full"

    # FULL representation: actual content in context
    content: str | None = None

    # COMPACT representation: pointer to storage
    content_ref: ContentReference | None = None

    # Metadata (always present regardless of representation)
    # Current: "text", "tool_result", "agent_response"
    # Future: "image", "file", "video", "audio" (see Section 6.8)
    content_type: Literal["text", "tool_result", "agent_response"] = "text"

    # Turn type — semantic classification of the turn's purpose.
    # Most turns are "message" (default). HITL interactions use "hitl_question" and
    # "hitl_reply" to distinguish agent/supervisor questions and user responses from
    # normal conversation flow. See HITL_DESIGN.md §10.1 for recording details.
    turn_type: Literal["message", "hitl_question", "hitl_reply"] = "message"

    # Token estimates — populated at turn creation time via estimate_tokens(content).
    # estimated_tokens_full MUST be set when the turn is created (not left at 0),
    # otherwise the compaction trigger (§6.5) and budget accounting (§5.2) are dead.
    estimated_tokens_full: int = 0   # Tokens if expanded; set at write time
    estimated_tokens_compact: int = 20  # Tokens as pointer (~20-50); static default is fine

    # Optional brief summary for very old turns (50+). Populated at compaction time
    # as a human-readable hint alongside the pointer. Replaces CompactedTurnWithSummary.
    # Never used instead of the full content — just a quick-scan label.
    brief_summary: str | None = None

    # Write-time structured note (Zettelkasten / A-MEM pattern, arXiv 2502.12110).
    # Populated when the turn is first stored via extract_turn_notes(content).
    # Enables richer retrieval without expanding compact content.
    # Schema: {"keywords": [...], "entities": [...], "tags": [...], "one_liner": "..."}
    turn_notes: dict | None = None


class ContentReference(BaseModel):
    """
    Pointer to full content in storage. Used for compact representation.

    Current implementation: MongoDB for text content
    Future extension: S3 for binary content (images, files, video)
    """
    # Current: "mongodb" for text
    # Future: "s3" for binary content
    storage_type: Literal["mongodb", "s3", "url"]

    # MongoDB reference (for text content)
    collection: str | None = None
    document_id: str | None = None

    # S3 reference (FUTURE: for binary content)
    s3_bucket: str | None = None
    s3_key: str | None = None

    # URL reference (for web content)
    url: str | None = None

    # Metadata for retrieval
    content_hash: str | None = None  # For cache validation
    mime_type: str | None = None  # e.g., "text/plain", "image/png"
    size_bytes: int | None = None  # For binary content
    created_at: datetime

    def to_compact_string(self) -> str:
        """Generate compact representation for context."""
        if self.storage_type == "mongodb":
            return f"[Content stored: db/{self.collection}/{self.document_id}]"
        elif self.storage_type == "s3":
            return f"[Content stored: s3://{self.s3_bucket}/{self.s3_key}]"
        elif self.storage_type == "url":
            return f"[Content from: {self.url}]"
```

> **`estimate_tokens` wiring requirement**: `estimated_tokens_full` MUST be populated
> at turn creation time. Leaving it at the default `0` silently breaks the compaction
> trigger (§6.5) and token budget accounting (§5.2). The wiring point is
> `add_turn_to_history` in `common/utils/context_utils.py`:
>
> ```python
> # In common/utils/context_utils.py → add_turn_to_history()
> turn = ConversationTurn(
>     ...
>     estimated_tokens_full=estimate_tokens(content),  # REQUIRED — never leave at 0
> )
> ```
>
> This is a migration task. See §9.2 and §18 checklist.

> **`turn_notes` wiring (write-time note generation, §2.4 Principle 1)**: Populate
> `turn_notes` when each turn is stored. A lightweight LLM call (or heuristic) extracts
> keywords, named entities, and a one-liner description. This is cheap at write time and
> dramatically improves retrieval quality — it enables keyword/entity search on compact
> turns without fetching their full content.
>
> ```python
> # In common/utils/context_utils.py → add_turn_to_history()
> turn = ConversationTurn(
>     ...
>     estimated_tokens_full=estimate_tokens(content),  # REQUIRED — never leave at 0
>     turn_notes=extract_turn_notes(content),          # Optional but recommended
>     # extract_turn_notes returns: {"keywords": [...], "entities": [...], "one_liner": "..."}
>     # For short turns (<100 tokens) a heuristic extractor suffices (no LLM call).
>     # For long agent responses, use a cheap fast model.
> )
> ```
>
> `turn_notes` is indexed in the `conversation_content` collection for fast keyword
> lookup without expanding compact turns. See §8 for use in hybrid search.

### 6.3 Compaction Process (Lossless)

> **Design constraints**:
> 1. **Idempotent**: If the server crashes between `store_full_content` and
>    `save_room_memory`, re-running compaction must not create duplicate documents.
>    Achieved by using `upsert` on a **unique** `(room_id, turn_id)` index (see §6.6).
> 2. **Trigger location matters**: This function is safe to call within the
>    per-room processing lock (on-demand after synthesis). For background job
>    usage, see §6.9 — a different write strategy is required.

```python
async def compact_room_memory(room_id: str) -> CompactionResult:
    """
    Compact older conversation turns by replacing full content with pointers.

    IMPORTANT: This is LOSSLESS. Original content is preserved in storage
    and can be retrieved on demand.

    Process:
    1. Load room memory
    2. Identify turns to compact (older than preserve_recent_turns)
    3. For each turn: upsert full content (idempotent) -> replace with pointer
    4. Update room memory with compact representations
    """
    room_memory = await load_room_memory(room_id)
    preserve_count = compaction_config.preserve_recent_turns

    # Guard: preserve_count=0 means compact everything.
    # Python's seq[:-0] == seq[:0] == [] — NOT seq[:], so we must handle this.
    if preserve_count == 0:
        turns_to_compact = [t for t in room_memory.conversation_history
                            if t.representation == "full"]
    else:
        turns_to_compact = [t for t in room_memory.conversation_history[:-preserve_count]
                            if t.representation == "full"]

    if not turns_to_compact:
        return CompactionResult(compacted_count=0, tokens_saved=0)

    tokens_saved = 0

    for turn in turns_to_compact:
        # 1. Upsert full content to MongoDB (IDEMPOTENT via unique index on room_id+turn_id).
        #    If a crash occurred after the previous upsert but before save_room_memory,
        #    re-running this correctly reuses the existing document_id.
        content_doc_id = await upsert_full_content(
            room_id=room_id,
            turn_id=turn.turn_id,
            content=turn.content,
            content_type=turn.content_type,
            content_hash=hash_content(turn.content),
        )

        # 2. Create reference pointer
        turn.content_ref = ContentReference(
            storage_type="mongodb",
            collection="conversation_content",
            document_id=content_doc_id,
            content_hash=hash_content(turn.content),
            created_at=utcnow(),
        )

        # 3. Calculate token savings
        tokens_saved += turn.estimated_tokens_full - turn.estimated_tokens_compact

        # 4. Optionally populate brief_summary for very old turns (>50 in history)
        #    to give a quick-scan label alongside the pointer.
        # (summary generation is optional and can be deferred)

        # 5. Switch to compact representation
        turn.content = None  # Remove full content from context
        turn.representation = "compact"

    await save_room_memory(room_memory)

    return CompactionResult(
        compacted_count=len(turns_to_compact),
        tokens_saved=tokens_saved,
    )


async def upsert_full_content(
    room_id: str,
    turn_id: str,
    content: str,
    content_type: str,
    content_hash: str,
) -> str:
    """
    Store full content idempotently. Returns the document_id.

    Uses update_one(upsert=True) on the unique (room_id, turn_id) index.
    If a document already exists for this turn (e.g., from a previous crashed
    compaction run), returns its existing _id without creating a duplicate.
    TODO: Create a new method in db services for it and use it from db services
    """
    result = await db.conversation_content.update_one(
        {"room_id": room_id, "turn_id": turn_id},   # filter on unique key
        {"$setOnInsert": {                            # only set fields on insert
            "_id": str(uuid4()),
            "room_id": room_id,
            "turn_id": turn_id,
            "content": content,
            "content_type": content_type,
            "content_hash": content_hash,
            "stored_at": utcnow(),
        }},
        upsert=True,
    )
    # For an insert: result.upserted_id is the new _id
    # For an update (already existed): fetch the existing _id
    if result.upserted_id:
        return str(result.upserted_id)
    # TODO: Create a new method in db services for it and use it from db services
    existing = await db.conversation_content.find_one(
        {"room_id": room_id, "turn_id": turn_id}, {"_id": 1}
    )
    return str(existing["_id"])
```

### 6.4 Content Expansion (On-Demand Retrieval)

```python
class ContentExpiredError(Exception):
    """Raised when a compacted turn's stored content can no longer be retrieved.

    Callers should log the error and fall back to the compact pointer string
    rather than crashing the request. This indicates a data integrity issue
    (TTL expiry, manual deletion, or migration error) that needs investigation.
    """
    def __init__(self, turn_id: str, document_id: str):
        self.turn_id = turn_id
        self.document_id = document_id
        super().__init__(f"Content for turn {turn_id} (doc {document_id}) not found in storage")


async def expand_turn_content(turn: ConversationTurn) -> str:
    """
    Expand a compacted turn back to full content.

    Called ONLY when an agent explicitly requests the full content of a specific
    compacted turn (e.g., via a fetch_turn_content tool call). NOT called
    proactively during context assembly — see expand_turns_for_context below.

    Raises:
        ContentExpiredError: If the stored document is missing (TTL, deletion, etc.)
        ValueError: If the turn is compact but has no content_ref.
    """
    # TODO: For turn representation, we can create a enum in the data model.
    if turn.representation == "full":
        return turn.content

    if not turn.content_ref:
        raise ValueError(f"Compact turn {turn.turn_id} missing content reference")

    ref = turn.content_ref

    # TODO: Create a enum for storage tyep.
    if ref.storage_type == "mongodb":
        doc = await db.conversation_content.find_one({"_id": ref.document_id})
        if doc is None:
            raise ContentExpiredError(turn.turn_id, ref.document_id)
        return doc["content"]

    elif ref.storage_type == "s3":
        # FUTURE: S3 retrieval for binary content
        raise NotImplementedError("S3 expansion not yet implemented")

    elif ref.storage_type == "url":
        async with httpx.AsyncClient() as client:
            response = await client.get(ref.url)
            response.raise_for_status()
            return response.text


async def expand_turns_for_context(
    turns: list[ConversationTurn],
) -> list[ConversationTurn]:
    """
    Prepare turns for inclusion in a context window.

    Strategy (recency-only — matches Manus reference design):
    - FULL turns: included as-is.
    - COMPACT turns: included as pointer strings (e.g., "[Agent: db/conversation_content/xyz]").
      They are NOT expanded proactively. If an agent needs the full content of a
      compacted turn, it must request it explicitly via the fetch_turn_content tool.

    Why not expand based on query relevance?
    - Compact turns have content=None; relevance can only be assessed from metadata
      (agent_name, role, timestamp), which is too coarse to be useful.
    - Proactive expansion of all turns defeats the purpose of compaction.
    - Storing embeddings at compaction time adds API cost and latency.
    - The Manus reference design uses recency only and lets agents request content
      explicitly — this is simpler and correct.

    Returns the turns list unchanged (full turns with content, compact turns with
    pointer strings rendered via to_context_string()).
    """
    return turns  # Assembly layer calls turn.to_context_string() for rendering
```

**`fetch_turn_content` tool (agent-facing)**:

When an agent needs the full content of a compacted turn, it calls a tool:

```python
async def fetch_turn_content(turn_id: str, room_id: str) -> str:
    """
    Tool callable by agents to retrieve full content of a compacted turn.

    Returns the full content string, or a descriptive error if unavailable.
    """
    room_memory = await load_room_memory(room_id)
    turn = next((t for t in room_memory.conversation_history if t.turn_id == turn_id), None)
    if turn is None:
        return f"[Error: Turn {turn_id} not found in room history]"
    try:
        return await expand_turn_content(turn)
    except ContentExpiredError:
        return f"[Error: Content for turn {turn_id} is no longer available (expired)]"
```

### 6.5 Compaction Triggers

```python
async def should_compact(room_id: str) -> bool:
    """Check if room memory needs compaction."""
    room_memory = await load_room_memory(room_id)

    if not compaction_config.enabled:
        return False

    # Count only full-representation turns
    full_turns = [t for t in room_memory.conversation_history if t.representation == "full"]
    token_estimate = sum(t.estimated_tokens_full for t in full_turns)

    return (
        len(full_turns) > compaction_config.max_turns_before_compaction or
        token_estimate > compaction_config.max_tokens_before_compaction
    )
```

### 6.6 Storage Schema for Full Content (Text)

```python
# MongoDB collection: conversation_content
# Used for TEXT-BASED content only (current implementation)
class StoredContent(BaseModel):
    """Full text content stored for compacted turns."""
    id: str = Field(alias="_id")
    room_id: str
    turn_id: str
    content: str  # Text content
    content_type: str  # "text", "tool_result", "agent_response"
    content_hash: str
    stored_at: datetime

    # TTL: None (keep forever) or set retention policy
    expires_at: datetime | None = None

# UNIQUE index — required for idempotent upsert in compact_room_memory (§6.3).
# unique=True ensures crashed-and-retried compaction never creates duplicate documents.
# db.conversation_content.create_index([("room_id", 1), ("turn_id", 1)], unique=True)
#
# Additional index for fast room-level queries:
# db.conversation_content.create_index([("room_id", 1), ("stored_at", -1)])
```

### 6.7 Compaction vs Summarization: When to Use Each

| Scenario             | Approach                              | Rationale                                                |
| -------------------- | ------------------------------------- | -------------------------------------------------------- |
| Recent turns (< 10)  | Keep FULL                             | Agent needs details for current task                     |
| Older turns (10-50)  | COMPACT (pointer)                     | Can expand on demand via `fetch_turn_content` tool       |
| Very old turns (50+) | COMPACT + optional `brief_summary`    | Summary hint aids quick scan; full content still available |

**Note**: Even when we generate a `brief_summary` for very old content, we **never
delete** the original. The summary is a human-readable hint stored on the
`ConversationTurn.brief_summary` field — not a replacement for the stored content.

> **`CompactedTurnWithSummary` removed**: The original design had a separate
> `CompactedTurnWithSummary` wrapper class. This is eliminated. The `brief_summary`
> field is instead added directly to `ConversationTurn` (see §6.2), avoiding a union
> type in `RoomMemory.conversation_history` and keeping the list uniformly typed.

### 6.8 Future Extension: Binary Content Storage (S3)

> **Status**: Design placeholder for future implementation  
> **Scope**: Images, files, video, audio attachments in conversations

When the system supports non-text content (images, files, video, audio), the compaction system will extend to use S3 for binary storage.

#### 6.8.1 Extended Content Types

```python
# Future content types (not yet implemented)
ContentType = Literal[
    # Current (text-based)
    "text",
    "tool_result",
    "agent_response",

    # Future (binary, stored in S3)
    "image",      # PNG, JPEG, GIF, WebP
    "file",       # PDF, documents, archives
    "video",      # MP4, WebM
    "audio",      # MP3, WAV, voice messages
]
```

#### 6.8.2 S3 Storage Schema

```python
# FUTURE: S3 storage for binary content
class S3StoredContent(BaseModel):
    """Binary content stored in S3 for compacted turns."""
    room_id: str
    turn_id: str

    # S3 location
    bucket: str
    key: str  # Format: "{room_id}/{turn_id}/{filename}"

    # Content metadata
    content_type: str  # "image", "file", "video", "audio"
    mime_type: str  # e.g., "image/png", "application/pdf"
    size_bytes: int
    content_hash: str  # SHA-256 for integrity

    # Original filename (if applicable)
    original_filename: str | None = None

    # Timestamps
    uploaded_at: datetime
    expires_at: datetime | None = None  # S3 lifecycle policy

    # Access
    presigned_url_ttl_seconds: int = 3600  # 1 hour default


class BinaryContentReference(ContentReference):
    """Extended reference for binary content in S3."""
    storage_type: Literal["s3"] = "s3"
    s3_bucket: str
    s3_key: str
    mime_type: str
    size_bytes: int

    # For images: include dimensions for context
    image_width: int | None = None
    image_height: int | None = None

    # For video/audio: include duration
    duration_seconds: float | None = None

    def to_compact_string(self) -> str:
        """Generate compact representation for context."""
        size_str = self._format_size(self.size_bytes)
        if self.mime_type.startswith("image/"):
            dims = f" ({self.image_width}x{self.image_height})" if self.image_width else ""
            return f"[Image: s3://{self.s3_bucket}/{self.s3_key} {size_str}{dims}]"
        elif self.mime_type.startswith("video/"):
            dur = f" ({self.duration_seconds:.1f}s)" if self.duration_seconds else ""
            return f"[Video: s3://{self.s3_bucket}/{self.s3_key} {size_str}{dur}]"
        elif self.mime_type.startswith("audio/"):
            dur = f" ({self.duration_seconds:.1f}s)" if self.duration_seconds else ""
            return f"[Audio: s3://{self.s3_bucket}/{self.s3_key} {size_str}{dur}]"
        else:
            return f"[File: s3://{self.s3_bucket}/{self.s3_key} {size_str}]"

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f}{unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f}TB"
```

#### 6.8.3 Future Environment Variables

```bash
# FUTURE: S3 Configuration for Binary Content
S3_BUCKET_NAME=hybro-conversation-content
S3_REGION=us-east-1
S3_ACCESS_KEY_ID=
S3_SECRET_ACCESS_KEY=
S3_PRESIGNED_URL_TTL=3600
S3_LIFECYCLE_DAYS=365  # Auto-delete after 1 year (0 = forever)
```

#### 6.8.4 Future Content Storage Service

```python
# FUTURE: Unified content storage service
class ContentStorageService:
    """
    Unified storage service for compacted content.

    Current: MongoDB for text
    Future: S3 for binary (images, files, video, audio)
    """

    async def store_content(
        self,
        room_id: str,
        turn_id: str,
        content: str | bytes,
        content_type: str,
        mime_type: str | None = None,
    ) -> ContentReference:
        """Store content and return reference."""

        if content_type in ("text", "tool_result", "agent_response"):
            # Text content -> MongoDB
            return await self._store_text_mongodb(room_id, turn_id, content)

        elif content_type in ("image", "file", "video", "audio"):
            # Binary content -> S3 (FUTURE)
            return await self._store_binary_s3(
                room_id, turn_id, content, content_type, mime_type
            )

    async def retrieve_content(
        self,
        ref: ContentReference,
    ) -> str | bytes:
        """Retrieve content from storage."""

        if ref.storage_type == "mongodb":
            return await self._retrieve_from_mongodb(ref)

        elif ref.storage_type == "s3":
            # FUTURE: S3 retrieval
            return await self._retrieve_from_s3(ref)

    async def get_presigned_url(
        self,
        ref: ContentReference,
        ttl_seconds: int = 3600,
    ) -> str | None:
        """
        Get presigned URL for binary content (S3 only).
        Returns None for MongoDB-stored text content.
        """
        if ref.storage_type != "s3":
            return None

        # FUTURE: Generate S3 presigned URL
        return await self._generate_presigned_url(ref, ttl_seconds)
```

#### 6.8.5 Implementation Notes

| Aspect                 | Current (Text)                 | Future (Binary)                     |
| ---------------------- | ------------------------------ | ----------------------------------- |
| Storage                | MongoDB `conversation_content` | S3 bucket                           |
| Retrieval              | Direct document fetch          | Presigned URL or direct download    |
| Context representation | `[Content stored: db/...]`     | `[Image: s3://... 1.2MB (800x600)]` |
| TTL management         | MongoDB TTL index              | S3 lifecycle policy                 |
| Cost                   | Included in MongoDB            | S3 storage + transfer costs         |

---

### 6.9 Compaction Trigger Locations

Where `compact_room_memory` is called determines the write safety guarantees needed:

| Trigger | Concurrency context | Write safety | Recommended approach |
|---|---|---|---|
| **On-demand after synthesis** | Inside `_handle_v2_run_result`, within the per-room processing lock | Only one writer at a time for this room | Standard read-modify-write is safe. Use `compact_room_memory` directly. |
| **Background cleanup job** | Outside the processing lock; may race with active message processing | Multiple potential writers | **Must NOT use read-modify-write.** Use MongoDB `$push` / atomic array append for the memory writes, or acquire a distributed per-room lock before running. |

**Recommended trigger order** (phase 5 implementation):

1. **Primary (on-demand)**: Call `should_compact()` + `compact_room_memory()` inside `_handle_v2_run_result` after every terminal status. This is safe because the per-room lock is still held.
2. **Secondary (background)**: A `StaleTaskChecker`-style job for rooms that never reach synthesis (e.g., chat rooms with single agents). Requires either the `$push` pattern or a distributed lock. Implement only after the on-demand path is stable.

> **Why the background job is risky**: `compact_room_memory` does a full
> read-modify-write of `conversation_history`. If a V2 loop writes a new
> agent result to the same field concurrently, the last writer wins and one
> write is silently lost. The on-demand trigger avoids this because the
> processing lock serializes all room memory writes.

---

## 7. Supervisor V2 Integration

> **V1 note**: V1 had three distinct context-assembly phases: Planning (`create_plan`),
> Review (`review_step`), and Synthesis (`synthesize_results`). **All three are
> eliminated in V2.** The methods have been deleted from `RoomSupervisorService`.
> This section describes the V2 integration only.

V2 uses a single adaptive loop (`decide_next` → dispatch → repeat). Context flows through
two distinct channels:

| Channel | What it contains | When built | Who sees it |
|---|---|---|---|
| `conversation_context` | Room history (prior sessions) | Once, before loop starts | Supervisor LLM (all iterations) |
| `trajectory_summary` | Actions + results in *this* loop | Grows each iteration | Supervisor LLM (all iterations) |
| Per-agent context | Room history + current task | Just before each dispatch | Individual agents |

### 7.1 Pre-Loop Context Preparation

`RoomServices._prepare_for_supervisor_v2()` is called once from `send_message_to_room`
before any agents run. It builds and freezes the supervisor's conversation context:

```python
async def _prepare_for_supervisor_v2(self, room, user_message, message_text, ...) -> ParseResult:
    """Lightweight preparation — no LLM call, no pre-generated agent messages."""

    # 1. Build agent registry from the room's active agents
    agent_registry = self._build_agent_registry(agents, selected_agent_set)

    # 2. Build RoomConfig (debate mode flag, agent set)
    room_config = RoomConfig(
        is_debate_mode=is_debate_mode,
        room_agent_set=selected_agent_set,
    )

    # 3. Build conversation_context from room memory (last 5 turns via build_minimal_context)
    #    This snapshot is FROZEN for the entire loop — agent results written during the loop
    #    appear in trajectory_summary, not here.
    conversation_context = build_minimal_context(
        room_memory.memory_content,
        current_task=message_text,
        max_turns=5,
    )

    # 4. Store everything in user_message.extend_info for SupervisorExecutor.run()
    user_message.extend_info.update({
        "supervisor_v2": True,
        "agent_registry": [...],
        "room_config": {...},
        "conversation_context": conversation_context,   # ← frozen snapshot
    })
```

**Important**: `conversation_context` is a snapshot of room history *before* this
user message's agents run. It does NOT update as the loop progresses. The current
loop's results are only visible through `trajectory_summary` in the prompt.

### 7.2 Supervisor Prompt Structure (All Iterations)

The same two-part prompt is used for every `decide_next` call:

```
SYSTEM: [instructions] + [agent registry]     ← identical across all iterations
USER:   ## Conversation Context
        {conversation_context}                 ← frozen snapshot, identical across iterations
        ## User Message
        {message_text}                         ← fixed
        ## Execution So Far
        {trajectory_summary}                   ← grows each iteration (windowed, 500-char truncation)
        ## What should happen next?
```

The stable portions (system prompt + `conversation_context` + `message_text`) are
always at the prompt prefix. The only changing part (`trajectory_summary`) is always
appended last. This structure maximizes **OpenAI prompt cache** hits across iterations:
iterations 2–8 pay ~50% of the token cost for the prefix. See §12.3 for the
optimization to move `conversation_context` to the system prompt to further extend
cache lifetime.

### 7.3 Agent Execution Context

Per-agent context is assembled inside `_process_single_message` →
`build_context_for_agent()`, independently from the supervisor's `conversation_context`:

```python
# Agents get: room history (all turns, not just 5) + current task + room awareness
context = build_context_for_agent(
    memory_content=room_memory.memory_content,
    current_task=target.task,          # ← supervisor's tailored task, not raw user message
    agent_name=agent.agent_name,
    room_awareness=room_awareness_str,
)
```

**Key point**: For sequential dispatch (one agent at a time), each agent sees the
results of all previously dispatched agents in this loop via room memory — because
`add_agent_response_to_memory()` is called after each DELEGATE completes.

For **concurrent dispatch** (multi-target DELEGATE via `asyncio.gather`), agents are
dispatched simultaneously before any results are written, so neither agent sees the
other's result in their context. The supervisor must compensate by including relevant
context from prior steps explicitly in each `DelegateTarget.task` description.

### 7.4 Room Memory Writes During the Loop

After each successful DELEGATE step, `SupervisorExecutor.run()` writes agent results
to room memory for cross-session persistence:

```python
for result in results:
    if result.status == StepStatus.SUCCESS and result.success and result.response_text:
        await self.room_memory_service.add_agent_response_to_memory(
            room_id=room_id,
            agent_id=result.agent_id,
            agent_name=result.agent_name,
            response_text=result.response_text,
        )
```

This is a **non-atomic read-modify-write** (see §8.10 of SUPERVISOR_V2_DESIGN.md).
Room-level locking in `RoomMessageCenter` prevents races within a single loop, but
external concurrent writers (e.g., webhook resumes from a different agent) could
still overwrite. Medium-term mitigation: MongoDB `$push` for atomic appends.

### 7.5 Synthesis Context

`RoomSupervisorService.synthesize_v2()` receives the full `SupervisorTrajectory` and
a `synthesis_instruction`. It uses a separate synthesis system prompt that renders
the trajectory (all agent results) as the context — **not** `conversation_context`.
The synthesis LLM is focused purely on combining what agents produced in this loop.

After synthesis completes, check `should_compact()` and trigger compaction if needed —
this is the natural boundary where the loop is fully done and room memory is stable.

```python
# After synthesize_v2() completes:
if await compaction_service.should_compact(room_id):
    await compaction_service.compact_room_memory(room_id)
```

### 7.6 HITL Resume Context Refresh

> **Cross-reference**: See [HITL_DESIGN.md §5.7 and Risk 13](./HITL_DESIGN.md) for full details.

When the supervisor loop is paused for Human-in-the-Loop (HITL) interactions — either
agent `input_required` or supervisor `CLARIFY` — the pause can last **minutes to hours**
(up to 24h expiry). The `conversation_context` snapshot serialized at interrupt time
may become stale: compaction cycles, new room memory entries, or agent registry changes
are not reflected.

**Requirement**: On HITL resume, `_resume_supervisor_v2()` must **re-fetch**
`conversation_context` and refresh `agent_registry` from the database, using the
serialized values only as fallbacks if the services are unavailable.

> **Implementation note:** The actual implementation unconditionally refreshes context
> for *all* resume types (not just HITL), using
> `context_assembly_service.build_supervisor_context()` — the same budget-aware assembly
> used in `_prepare_for_supervisor_v2()`. The `interrupt_kind` branching shown below is
> a future enhancement; the current uniform-refresh is more conservative and correct.

```python
# In RoomMessageCenter._resume_supervisor_v2(), step 5b:
# Re-fetch to avoid staleness after long pause
room_memory = await self.database_service.get_room_memory_by_room_id(room_id)
result_ctx = context_assembly_service.build_supervisor_context(
    room_memory=room_memory,
    current_task=message_text,
    agent_registry=agent_dicts,
    max_turns=5,
    memory_search_results=memory_search_results,
)
conversation_context = result_ctx.context
# Also refresh agent_registry (agents may have been added/removed/health-changed)
agents = await asyncio.gather(*(
    self.database_service.get_agent_by_agent_id(aid) for aid, _ in room_agent_items
))
```

---

## 8. Memory Search (Hybrid)

### 8.1 Search Architecture

```python
class MemorySearchService:
    """Hybrid search across memory layers. Config loaded from env."""

    def __init__(self):
        self.pinecone_client = pinecone_client
        self.openai_service = openai_service
        self.config = memory_search_config  # From env

    async def search(
        self,
        query: str,
        room_id: str,
        user_id: str,
    ) -> list[MemorySearchResult]:
        """
        Hybrid search combining:
        1. Vector similarity (semantic)
        2. BM25 keyword matching
        3. Temporal decay (recency boost)

        All weights and parameters loaded from environment config.
        """
        if not self.config.enabled:
            return []

        # 1. Vector search
        vector_results = await self._vector_search(query, room_id)

        # 2. Keyword search
        keyword_results = await self._keyword_search(query, room_id)

        # 3. Merge with weights from env config
        merged = self._merge_results(
            vector_results,
            keyword_results,
            vector_weight=self.config.vector_weight,
            keyword_weight=self.config.keyword_weight,
        )

        # 4. Apply temporal decay (if enabled)
        if self.config.temporal_decay_enabled:
            merged = self._apply_temporal_decay(
                merged,
                half_life_days=self.config.half_life_days,
            )

        # 5. MMR for diversity
        diverse = self._apply_mmr(
            merged,
            lambda_param=self.config.mmr_lambda,
        )

        return diverse[:self.config.max_results]
```

> **Implementation note:** The actual return type is `MemorySearchResponse`
> (see `models/search.py`), which wraps `list[MemorySearchResult]` with
> search diagnostics (timing, which sub-searches ran, decay/MMR flags).
> Results are available at `.results`.

### 8.2 Search Configuration

All search parameters are loaded from environment variables (see Section 14.1):

| Parameter      | Env Variable                   | Default | Description                     |
| -------------- | ------------------------------ | ------- | ------------------------------- |
| Vector weight  | `MEMORY_SEARCH_VECTOR_WEIGHT`  | 0.7     | Weight for semantic similarity  |
| Keyword weight | `MEMORY_SEARCH_KEYWORD_WEIGHT` | 0.3     | Weight for BM25 matching        |
| Half-life      | `MEMORY_SEARCH_HALF_LIFE_DAYS` | 30      | Days for score to decay 50%     |
| MMR lambda     | `MEMORY_SEARCH_MMR_LAMBDA`     | 0.7     | Diversity vs relevance tradeoff |
| Max results    | `MEMORY_SEARCH_MAX_RESULTS`    | 10      | Maximum results returned        |

### 8.3 `turn_notes` Integration in Hybrid Search

With `turn_notes` populated at write time (§6.2), the hybrid search gains a cheap keyword
index over **compact turns** — without fetching their full content:

```python
async def _keyword_search(self, query: str, room_id: str) -> list[MemorySearchResult]:
    """
    BM25 keyword search over conversation_content collection.

    For FULL turns: searches full `content` text.
    For COMPACT turns: searches `turn_notes.keywords` + `turn_notes.entities` +
                       `turn_notes.one_liner` — no content expansion needed.
    """
    # MongoDB text index on: content, turn_notes.keywords, turn_notes.entities, turn_notes.one_liner
    results = await db.conversation_content.find(
        {"room_id": room_id, "$text": {"$search": query}},
        {"score": {"$meta": "textScore"}, "turn_id": 1, "turn_notes": 1},
    ).sort([("score", {"$meta": "textScore"})]).to_list(50)
    ...
```

This is the primary short-term improvement to hybrid search before the graph layer is built.

### 8.4 Future: Graph-Based Retrieval (Dual-Route)

> **Status**: Future design; implement after Phase 4 hybrid search is stable.  
> **Motivation**: Flat vector similarity fails on temporal and causal multi-hop queries:
> - "What led to the failure in session 3?" (causal chain)
> - "What did we decide about X before Y happened?" (temporal ordering)
> - "Summarize all decisions about topic T across sessions" (entity-centric aggregation)

**Problem — Contextual Tunneling**: Vector search retrieves the most semantically similar
text, but the *causally upstream* or *temporally adjacent* content may have low surface
similarity to the query. SYNAPSE (arXiv 2601.02744) names this "Contextual Tunneling" and
demonstrates that spreading activation on a memory graph solves it. Mnemis (arXiv
2602.15313) achieves SOTA (93.9 LoCoMo) via dual-route: System-1 (fast similarity) +
System-2 (deliberate hierarchical traversal).

**Planned architecture**:

```
Query
  │
  ├── Route 1 (System-1, fast): Vector similarity + BM25 on turn_notes ← current §8.1
  │                              Returns: semantically similar turns
  │
  └── Route 2 (System-2, deliberate): Entity graph traversal            ← §8.4 future
                     │
                     ├── Entity nodes: extracted from turn_notes.entities
                     ├── Temporal edges: turn sequence (derived from timestamps)
                     ├── Causal edges: "because of" / "led to" links (LLM-extracted)
                     └── Returns: causally/temporally connected turns missing from Route 1

Final result: merge(Route 1, Route 2) → deduplicate → MMR re-rank
```

**Minimal viable graph** (Phase 4B, after basic hybrid search ships):

| Graph element | Source | Storage |
|---|---|---|
| Entity nodes | `turn_notes.entities` (populated at write time) | MongoDB `memory_entities` collection |
| Temporal edges | Turn sequence (implicit from timestamps) | No extra storage; derived at query time |
| Causal edges | LLM extraction from synthesis output | MongoDB `memory_edges` collection |

The full multi-graph (MAGMA: semantic + temporal + causal + entity) is a longer-term
evolution. Temporal edges alone already recover much multi-hop recall at near-zero cost.

### 9.1 New Collections

| Collection             | Purpose                                          | Indexes                             |
| ---------------------- | ------------------------------------------------ | ----------------------------------- |
| `room_memories`        | Room conversation history (full + compact turns) | `room_id` (unique)                  |
| `conversation_content` | **Full content storage for compacted turns**     | `room_id`, `turn_id`, `document_id` |
| `user_memories`        | User preferences + patterns                      | `user_id` (unique)                  |
| `agent_memories`       | Agent performance history                        | `agent_id` (unique)                 |
| `room_facts`           | Extracted durable facts                          | `room_id`, `created_at`             |

> **Implementation note:** `room_facts` is embedded as `list[RoomFact]` inside `room_memories` rather than a standalone collection. This simplifies atomic updates (facts are pushed/sliced alongside room_summary in a single `update_one`) and avoids cross-collection consistency concerns. The trade-off is that fact-level queries by `created_at` require scanning the parent document's embedded array rather than using a dedicated index. At current scale this is acceptable.

### 9.2 Model Files to Create/Update

```
models/
├── context.py          # SessionContext, TokenBudget
├── memory.py           # (UPDATE existing) RoomMemory, UserMemory, AgentMemory
│                       #   → ConversationTurn must be replaced with §6.2 canonical shape
│                       #     (add turn_id, representation, content_ref, estimated_tokens_*,
│                       #     brief_summary; change content from required str to str | None)
├── compaction.py       # ContentReference, StoredContent, CompactionResult
└── search.py           # MemorySearchConfig, MemorySearchResult
```

> **`models/memory.py` migration note**: The current `ConversationTurn` in
> `models/memory.py` is the active runtime model. It is missing all fields required
> for the compaction system: `turn_id`, `representation`, `content_ref`,
> `estimated_tokens_full`, `estimated_tokens_compact`, and `brief_summary`. It also
> has `content: str` (required) instead of `content: str | None = None`.
> This file must be updated before any compaction code can be wired in.
> The §6.2 definition is the migration target. See §18 checklist for the task.

---

## 10. Service Design

### 10.1 New Services

```
services/
├── context_assembly_service.py    # Context Assembly Engine
├── compaction_service.py          # Lossless compaction (pointer-based)
├── content_storage_service.py     # Store/retrieve full content for compacted turns
├── memory_search_service.py       # Hybrid search
└── memory_service.py              # (extend existing) CRUD for all memory types
```

### 10.2 Service Dependencies

```
ContextAssemblyService
    ├── MemoryService (load/save memories)
    ├── CompactionService (trigger compaction)
    ├── ContentStorageService (expand compacted turns on demand)
    ├── MemorySearchService (relevant context retrieval)
    └── OpenAIService (token estimation)

CompactionService
    ├── MemoryService
    └── ContentStorageService (store full content, create pointers)

ContentStorageService
    ├── MongoDBClient (conversation_content collection)
    └── FileStorageClient (optional, for file-based storage)

MemorySearchService
    ├── PineconeClient (vector search)
    ├── MongoDBClient (keyword search)
    └── OpenAIService (embeddings)
```

---

## 11. Integration with Supervisor V2

> **V1 note**: The original §11 described wiring context assembly into `create_plan()`,
> `review_step()`, and `synthesize_results()`. All three methods are deleted in V2
> (Phase 5 complete). This section describes the V2 integration points.

### 11.1 Pre-Loop: Supervisor Prompt Context

The `ContextAssemblyService` should be wired into `_prepare_for_supervisor_v2()` to
replace the current raw `build_minimal_context()` call with a budget-aware assembly:

```python
# In RoomServices._prepare_for_supervisor_v2()
# CURRENT (simple):
conversation_context = build_minimal_context(
    room_memory.memory_content, current_task=message_text, max_turns=5
)

# FUTURE (Context Assembly Engine):
conversation_context = await context_assembly_service.build_supervisor_context(
    room_memory=room_memory,
    agent_registry=agent_registry,
    current_task=message_text,
    max_tokens=SUPERVISOR_CONTEXT_TOKEN_BUDGET,  # leaves room for trajectory growth
)
```

This is the only place `conversation_context` is built for the supervisor. It is
passed unchanged through all `decide_next` iterations. No re-assembly occurs mid-loop.

### 11.2 During Loop: Agent Execution Context

The `ContextAssemblyService` should be wired into `_process_single_message` to build
per-agent context. The key difference from V1: there is no `context_from_steps` field
to inject — the supervisor already embedded relevant prior results in `DelegateTarget.task`.

```python
# In _process_single_message() / build_context_for_agent()
context = await context_assembly_service.build_agent_execution_context(
    room_memory=room_memory,
    current_task=target.task,       # supervisor's tailored task (may include prior results)
    agent_name=agent.agent_name,
    room_awareness=room_awareness_str,
    max_tokens=AGENT_CONTEXT_TOKEN_BUDGET,
)
```

**Concurrent dispatch note**: When multiple agents are dispatched simultaneously
(multi-target DELEGATE), all their contexts are built from the same room memory
snapshot (before any of them write results). The supervisor compensates by including
relevant inter-agent context in each `DelegateTarget.task` string directly.

### 11.3 Post-Loop: Synthesis and Compaction Trigger

`RoomSupervisorService.synthesize_v2(trajectory, synthesis_instruction)` is the sole
synthesis method. After it completes, trigger compaction if needed:

```python
# In RoomMessageCenter._handle_v2_run_result() after synthesis is emitted:
if result.synthesis_text:
    # Add synthesis to room memory
    await memory_service.add_synthesis_to_history(
        room_id=room_id,
        synthesis=result.synthesis_text,
        trajectory=result.trajectory,
    )

    # Check if compaction needed (natural boundary — loop is done, memory is stable)
    if await compaction_service.should_compact(room_id):
        await compaction_service.compact_room_memory(room_id)
```

For `RunStatus.DONE` (no synthesis), agent results were already written to room memory
during the loop by `add_agent_response_to_memory()`. The compaction check should still
run on any terminal status.

### 11.4 Trajectory as Working Memory (No Assembly Needed)

Within a single user message's loop, the `SupervisorTrajectory` acts as working memory
for the supervisor. The `_format_trajectory()` method in `RoomSupervisorService`
handles windowing and truncation automatically — the `ContextAssemblyService` does NOT
need to manage this. Trajectory rendering is internal to the supervisor service.

---

## 12. KV-Cache Optimization Strategy

### 12.1 Stable Prefix Design

```python
def build_stable_context_prefix(
    room_id: str,
    agent_registry: list[AgentProfile],
) -> str:
    """
    Build a stable prefix that rarely changes.
    This maximizes KV-cache hits across requests.
    """

    # 1. System instructions (static)
    prefix_parts = [SYSTEM_INSTRUCTIONS]

    # 2. Agent registry (changes only when agents added/removed)
    # Sort deterministically for cache stability
    sorted_agents = sorted(agent_registry, key=lambda a: a.agent_id)
    prefix_parts.append(format_agent_registry(sorted_agents))

    # 3. Room configuration (rarely changes)
    prefix_parts.append(format_room_config(room_id))

    return "\n\n".join(prefix_parts)


def build_dynamic_context_suffix(
    session: SessionContext,
    room_memory: RoomMemory,
) -> str:
    """
    Build the dynamic suffix that changes per request.
    This is appended after the stable prefix.
    """

    suffix_parts = []

    # 1. Conversation history (mix of full and compact turns)
    # Compact turns render as pointers, full turns render content
    for turn in room_memory.conversation_history:
        suffix_parts.append(turn.to_context_string())

    # 2. Current request (always last)
    suffix_parts.append(format_current_request(session))

    return "\n\n".join(suffix_parts)
```

### 12.2 Serialization Rules

```python
class ContextSerializer:
    """Deterministic serialization for KV-cache optimization."""

    @staticmethod
    def serialize_dict(d: dict) -> str:
        """Serialize dict with stable key ordering."""
        return json.dumps(d, sort_keys=True, ensure_ascii=False)

    @staticmethod
    def serialize_turn(turn: ConversationTurn) -> str:
        """Serialize conversation turn deterministically."""
        return turn.to_context_string()  # Handles both full and compact

    @staticmethod
    def serialize_agent_profile(agent: AgentProfile) -> str:
        """Serialize agent profile deterministically."""
        return (
            f"- {agent.agent_name} ({agent.agent_id})\n"
            f"  Description: {agent.description}\n"
            f"  Capabilities: {', '.join(sorted(agent.capabilities))}"
        )
```

### 12.3 OpenAI Prompt Caching for the Supervisor Loop

In V2, `decide_next` is called 3–8 times per user message. Each call constructs
the same system prompt + `conversation_context` + `message_text` prefix, with only
`trajectory_summary` appended at the end. OpenAI automatically caches prompt prefixes
≥1024 tokens that are reused within a short window, at **50% token cost + lower latency**.

The V2 prompt structure already satisfies the cache conditions — the changing content
(`trajectory_summary`) is always last. To maximize cache lifetime across the full
supervisor loop, move `conversation_context` from the user prompt into the **system
prompt**:

```python
# CURRENT structure — conversation_context in user prompt
SUPERVISOR_V2_SYSTEM_PROMPT = """...instructions... {agent_registry}..."""
SUPERVISOR_V2_USER_PROMPT = """
## Conversation Context
{conversation_context}         ← changes user-turn prefix, shorter cache window

## User Message
{message_text}
## Execution So Far
{trajectory_summary}           ← grows each iteration
"""

# OPTIMIZED structure — conversation_context in system prompt
SUPERVISOR_V2_SYSTEM_PROMPT = """...instructions... {agent_registry}...

## Room Conversation Background
{conversation_context}         ← now in system prompt → cached up to 1 hour
"""
SUPERVISOR_V2_USER_PROMPT = """
## User Message
{message_text}
## Execution So Far
{trajectory_summary}           ← only this changes per iteration
"""
```

**Effect**: System-prompt cache entries are shared across multiple user messages in
the same room (as long as the room's agent registry and conversation_context haven't
changed). This gives cross-message caching, not just within-message caching.

**Prerequisite**: Ensure the system prompt is ≥1024 tokens to qualify. For rooms with
sparse history (`conversation_context = "No prior conversation."`), add padding via
agent registry descriptions or extended instructions.

---

## 13. Migration Plan

### Phase 1: Data Models & Storage (Week 1)

1. Create new model files (`context.py`, `compaction.py`, `search.py`)
2. Extend existing `memory.py` with new fields
3. Create MongoDB indexes for new collections
4. Migrate existing `room_memories` to new schema

### Phase 2: Context Assembly Engine (Week 2)

1. Implement `ContextAssemblyService`
2. Implement token budget allocation
3. Implement stable prefix/dynamic suffix pattern
4. Unit tests for context assembly

### Phase 3: Compaction System (Week 3)

1. Implement `CompactionService` with lossless pointer-based compaction
2. Implement `ContentStorageService` for storing/retrieving full content
3. Implement `expand_turn_content()` for on-demand retrieval
4. Add compaction triggers to message processing
5. Create `conversation_content` MongoDB collection with indexes

### Phase 4: Memory Search (Week 4)

1. Implement `MemorySearchService`
2. Set up Pinecone index for memory embeddings
3. Implement hybrid search with temporal decay
4. Integration tests for search

### Phase 5: Supervisor V2 Integration (Week 5)

> **Note**: Supervisor V2 itself is **complete** (Phase 5 of SUPERVISOR_V2_DESIGN.md,
> Feb 21 2026). What remains here is wiring the **Context Assembly Engine** (Phases 2–3
> above) into V2's integration points. The V1 methods (`create_plan`, `review_step`,
> `synthesize_results`) are deleted and must not be referenced.

1. Wire `ContextAssemblyService.build_supervisor_context()` into `_prepare_for_supervisor_v2()` to replace `build_minimal_context()` (see §11.1)
2. Wire `ContextAssemblyService.build_agent_execution_context()` into `_process_single_message` / `build_context_for_agent()` (see §11.2)
3. Wire compaction trigger into `_handle_v2_run_result()` after terminal statuses (see §11.3)
4. Wire `add_synthesis_to_history()` into `_handle_v2_run_result()` for `SYNTHESIZE` results
5. End-to-end tests with Supervisor V2 loop
6. Performance benchmarks: measure `conversation_context` token size per iteration, cache hit rate

---

## 14. Configuration (Environment Variables)

All configuration parameters are loaded from environment variables via `config/settings.py`.

### 14.1 Settings Extension

Add the following to `config/settings.py`:

```python
class Settings(BaseSettings):
    # ... existing settings ...

    # ===========================================
    # Context & Memory System Settings
    # ===========================================

    # Token Budget Settings
    context_model_window: int = 128000  # Model's max context window
    context_system_prompt_tokens: int = 2000  # Reserved for system prompt
    context_tool_schema_tokens: int = 3000  # Reserved for tool schemas
    context_response_reserve_tokens: int = 4000  # Reserved for response
    context_room_pct: float = 0.15  # % of remaining for room context
    context_history_pct: float = 0.60  # % of remaining for conversation history
    context_task_pct: float = 0.25  # % of remaining for current task

    # Compaction Settings (LOSSLESS - pointer-based, not summarization)
    compaction_enabled: bool = True  # Enable/disable auto-compaction
    compaction_max_full_turns: int = 20  # Max turns to keep in FULL representation
    compaction_max_total_tokens: int = 80000  # Trigger compaction when full turns exceed this
    compaction_preserve_recent: int = 10  # Always keep this many recent turns FULL
    compaction_content_ttl_days: int = 0  # TTL for stored content (0 = forever)

    # Memory Search Settings
    memory_search_enabled: bool = True  # Enable/disable memory search
    memory_search_vector_weight: float = 0.7  # Weight for vector similarity
    memory_search_keyword_weight: float = 0.3  # Weight for BM25 keyword matching
    memory_search_temporal_decay_enabled: bool = True  # Enable recency boost
    memory_search_half_life_days: int = 30  # Half-life for temporal decay
    memory_search_mmr_lambda: float = 0.7  # MMR diversity parameter (0=diverse, 1=relevant)
    memory_search_max_results: int = 10  # Max results to return
    memory_search_max_snippet_chars: int = 500  # Max chars per snippet
    memory_search_index_name: str = "room-memory"  # Pinecone index for memory

    # ===========================================
    # FUTURE: S3 Settings for Binary Content
    # (Not implemented - placeholder for future extension)
    # ===========================================
    # s3_bucket_name: str = ""
    # s3_region: str = "us-east-1"
    # s3_access_key_id: str = ""
    # s3_secret_access_key: str = ""
    # s3_presigned_url_ttl: int = 3600
    # s3_lifecycle_days: int = 365
```

### 14.2 Environment Variables (.env)

```bash
# ===========================================
# Context & Memory System Configuration
# ===========================================

# Token Budget (adjust based on your model)
CONTEXT_MODEL_WINDOW=128000
CONTEXT_SYSTEM_PROMPT_TOKENS=2000
CONTEXT_TOOL_SCHEMA_TOKENS=3000
CONTEXT_RESPONSE_RESERVE_TOKENS=4000
CONTEXT_ROOM_PCT=0.15
CONTEXT_HISTORY_PCT=0.60
CONTEXT_TASK_PCT=0.25

# Compaction Settings (LOSSLESS - stores full content in MongoDB, uses pointers in context)
# NOTE: Current implementation supports TEXT content only (stored in MongoDB)
# Future: Binary content (images, files, video) will use S3 (see Section 6.8)
COMPACTION_ENABLED=true
COMPACTION_MAX_FULL_TURNS=20
COMPACTION_MAX_TOTAL_TOKENS=80000
COMPACTION_PRESERVE_RECENT=10
COMPACTION_CONTENT_TTL_DAYS=0

# Memory Search Settings
MEMORY_SEARCH_ENABLED=true
MEMORY_SEARCH_VECTOR_WEIGHT=0.7
MEMORY_SEARCH_KEYWORD_WEIGHT=0.3
MEMORY_SEARCH_TEMPORAL_DECAY_ENABLED=true
MEMORY_SEARCH_HALF_LIFE_DAYS=30
MEMORY_SEARCH_MMR_LAMBDA=0.7
MEMORY_SEARCH_MAX_RESULTS=10
MEMORY_SEARCH_MAX_SNIPPET_CHARS=500
MEMORY_SEARCH_INDEX_NAME=room-memory
```

### 14.3 Configuration Classes (Runtime)

These classes load from `settings` and provide typed access:

```python
# models/context_config.py
from config.settings import settings


class TokenBudget:
    """Token allocation for context assembly. Loaded from env."""

    @property
    def model_context_window(self) -> int:
        return settings.context_model_window

    @property
    def system_prompt(self) -> int:
        return settings.context_system_prompt_tokens

    @property
    def tool_schemas(self) -> int:
        return settings.context_tool_schema_tokens

    @property
    def response_reserve(self) -> int:
        return settings.context_response_reserve_tokens

    @property
    def room_context_pct(self) -> float:
        return settings.context_room_pct

    @property
    def conversation_history_pct(self) -> float:
        return settings.context_history_pct

    @property
    def current_task_pct(self) -> float:
        return settings.context_task_pct

    @property
    def available_for_content(self) -> int:
        return self.model_context_window - (
            self.system_prompt +
            self.tool_schemas +
            self.response_reserve
        )


class CompactionConfig:
    """
    Compaction configuration. Loaded from env.

    NOTE: This is LOSSLESS compaction (pointer-based), NOT summarization.

    Current implementation: Text content stored in MongoDB
    Future extension: Binary content (images, files, video) will use S3 (see Section 6.8)
    """

    @property
    def enabled(self) -> bool:
        return settings.compaction_enabled

    @property
    def max_full_turns(self) -> int:
        """Max turns to keep in FULL representation."""
        return settings.compaction_max_full_turns

    @property
    def max_total_tokens(self) -> int:
        """Trigger compaction when full turns exceed this token count."""
        return settings.compaction_max_total_tokens

    @property
    def preserve_recent_turns(self) -> int:
        """Always keep this many recent turns in FULL representation."""
        return settings.compaction_preserve_recent

    @property
    def content_ttl_days(self) -> int:
        """TTL for stored content (0 = forever)."""
        return settings.compaction_content_ttl_days


class MemorySearchConfig:
    """Memory search configuration. Loaded from env."""

    @property
    def enabled(self) -> bool:
        return settings.memory_search_enabled

    @property
    def vector_weight(self) -> float:
        return settings.memory_search_vector_weight

    @property
    def keyword_weight(self) -> float:
        return settings.memory_search_keyword_weight

    @property
    def temporal_decay_enabled(self) -> bool:
        return settings.memory_search_temporal_decay_enabled

    @property
    def half_life_days(self) -> int:
        return settings.memory_search_half_life_days

    @property
    def mmr_lambda(self) -> float:
        return settings.memory_search_mmr_lambda

    @property
    def max_results(self) -> int:
        return settings.memory_search_max_results

    @property
    def max_snippet_chars(self) -> int:
        return settings.memory_search_max_snippet_chars

    @property
    def index_name(self) -> str:
        return settings.memory_search_index_name


# Singleton instances
token_budget = TokenBudget()
compaction_config = CompactionConfig()
memory_search_config = MemorySearchConfig()
```

### 14.4 Environment-Specific Defaults

| Environment | `COMPACTION_MAX_TURNS` | `MEMORY_SEARCH_HALF_LIFE_DAYS` | `CONTEXT_MODEL_WINDOW` |
| ----------- | ---------------------- | ------------------------------ | ---------------------- |
| Development | 15                     | 7                              | 32000                  |
| Staging     | 25                     | 14                             | 128000                 |
| Production  | 30                     | 30                             | 128000                 |

---

## 15. Observability

### 15.1 Metrics

```python
# Context assembly metrics
context_assembly_duration_ms: Histogram
context_tokens_used: Histogram
context_occupancy_pct: Gauge        # tokens_used / model_context_window — PRIMARY HEALTH SIGNAL
context_truncation_events: Counter  # Incremented when hard cap fires; alert if non-zero
cache_prefix_hit_rate: Gauge

# Compaction metrics
compaction_duration_ms: Histogram
compaction_turns_processed: Counter
compaction_tokens_saved: Counter

# Memory search metrics
search_duration_ms: Histogram
search_results_count: Histogram
vector_search_score: Histogram
```

**Context occupancy thresholds** (see §2.4 Principle 4):

| Occupancy | Signal | Action |
|---|---|---|
| < 70% | Healthy | None |
| 70–85% | Soft warning | Log; consider triggering early compaction |
| 85–90% | Hard cap zone | Truncate history; fire `context_truncation_events` counter |
| > 90% | Emergency | Hard truncate + alert on-call; investigate verbose agent |

> **Production context rot**: Studies show GPT-4 accuracy drops from 98.1% → 64.1%
> based solely on information placement within the context window. Staying under 70%
> occupancy is not just a cost concern — it is a correctness concern.

### 15.2 Logging

```python
logger.info(
    "Context assembled",
    extra={
        "room_id": room_id,
        "session_id": session_id,
        "total_tokens": total_tokens,
        "occupancy_pct": round(total_tokens / model_context_window * 100, 1),
        "full_turns": full_turn_count,
        "compact_turns": compact_turn_count,
        "cache_prefix_tokens": prefix_tokens,
        "truncated": truncated,  # True if hard cap fired
    }
)

logger.info(
    "Compaction completed",
    extra={
        "room_id": room_id,
        "turns_compacted": compacted_count,
        "tokens_saved": tokens_saved,
        "content_stored_in": storage_type,
    }
)
```

---

## 16. Summary

This architecture provides:

1. **Multi-layer memory**: Session (ephemeral) + Room/User/Agent (durable)
2. **KV-cache optimization**: Stable prefixes, append-only updates, deterministic serialization
3. **Lossless compaction**: Pointer-based compaction preserves all original content
4. **On-demand expansion**: Agent can retrieve full content when needed
5. **Hybrid search**: Vector + keyword with temporal decay and MMR diversity
6. **Supervisor V2 integration**: Context assembly for the adaptive loop (`decide_next`), per-agent dispatch, and synthesis
7. **Horizontal scalability**: All state in MongoDB/Pinecone, no in-memory dependencies
8. **HITL integration**: Interrupt state serialization, HITL turn recording, context refresh on resume (see §3.1)

The design draws from production lessons at Manus (lossless compaction, KV-cache optimization) and OpenClaw (multi-layer memory, hybrid search) while adapting to Hybro's multi-agent A2A architecture and Supervisor V2 (adaptive loop, Phase 5 complete Feb 21 2026).

### 16.1 Cross-Document Dependencies

See [SYSTEM_DESIGN_REVIEW.md §5](./SYSTEM_DESIGN_REVIEW.md) for the **unified implementation dependency graph** across all three design documents. Key Context Memory dependencies:

| Dependency | Document | Impact on Context Memory |
|------------|----------|--------------------------|
| **[HITL Phase 7] Turn Recording** | HITL_DESIGN.md | Depends on `ConversationTurn` model updates (Phase 1) |
| **[SDR 2.11] Unbounded Memory** | SYSTEM_DESIGN_REVIEW.md | Resolved by Phase 2 (Context Assembly) + Phase 3 (Compaction) |

---

## 17. Design Review & Gap Analysis

### 17.1 Strengths

| Aspect                     | Assessment                                                          |
| -------------------------- | ------------------------------------------------------------------- |
| **Scalability**            | ✅ All state in MongoDB/Pinecone; no in-memory dependencies         |
| **Supervisor V2 Integration** | ✅ Clear integration points: pre-loop context, agent dispatch, synthesis |
| **KV-Cache Optimization**  | ✅ Stable prefix + append-only suffix pattern; prompt caching ready |
| **Configuration**          | ✅ All tunable params via environment variables                     |
| **Lossless Compaction**    | ✅ Pointer-based compaction implemented (Phase 3); original content always in `conversation_content` |

### 17.2 Identified Gaps & Mitigations

| Gap                                | Risk                                                | Mitigation                                                        |
| ---------------------------------- | --------------------------------------------------- | ----------------------------------------------------------------- |
| **No cross-room memory sharing**   | Agents can't learn from interactions in other rooms | Phase 2: Add optional `shared_facts` collection with user consent |
| **Pinecone dependency for search** | Single point of failure for memory search           | Fallback to MongoDB text search if Pinecone unavailable           |
| **Token estimation accuracy**      | Budget allocation may be off                        | Use tiktoken for accurate counts; add 10% buffer                  |
| **Storage growth**                 | Full content storage grows unbounded                | TTL policy on `conversation_content`; archive old rooms           |
| **Expansion latency**              | Fetching full content adds latency                  | Cache recently expanded content; batch expansions                 |
| **`MAX_CONTEXT_CHARS` not enforced** | Silent context overflow for verbose agents        | **Immediate fix**: enforce hard cap in `build_context_for_agent`; add `context_truncation_events` counter |
| **Flat turn list — no graph layer** | Multi-hop temporal/causal queries return wrong or missing results ("Contextual Tunneling") | Short-term: `turn_notes` entity extraction at write time; Medium-term: dual-route retrieval (§8.4) |
| **No rolling room summary**        | Supervisor must scan 20+ compact turns to reconstruct current state; expensive and error-prone | **Short-term**: populate `RoomMemory.room_summary` at each synthesis boundary (§2.4 Principle 2) |
| **Threshold-based compaction only** | Agent continues appending irrelevant turns until threshold fires; wastes tokens in early-to-mid sessions | Medium-term: expose `compact_context(rationale)` tool for agent-driven compaction (§2.4 Principle 5) |
| **No turn_notes extraction pipeline** | Keyword search on compact turns requires full content expansion | Short-term: implement `extract_turn_notes()` wired into `add_turn_to_history` (§6.2) |
| **SSE in-memory state (cross-doc)** | SSE events cannot reach clients connected to different backend instances; affects HITL prompts and context updates | See [SYSTEM_DESIGN_REVIEW.md §2.1](./SYSTEM_DESIGN_REVIEW.md) — requires Redis Pub/Sub or polling fallback; HITL design depends on this fix |

### 17.3 Comparison with Reference Systems

| Feature                | Manus                        | OpenClaw               | SYNAPSE/MAGMA/Mnemis (2026)          | Hybro (This Design)                          |
| ---------------------- | ---------------------------- | ---------------------- | ------------------------------------ | -------------------------------------------- |
| Memory layers          | File system (sandbox)        | Daily logs + MEMORY.md | Episodic + Semantic graph nodes      | Session + Room + User + Agent                |
| Compaction             | **Lossless (pointer-based)** | Summarization          | Continuous RL-based state update     | **Lossless (pointer-based) — implemented** |
| Full content storage   | Sandbox filesystem           | N/A                    | N/A (always-compressed state)        | MongoDB `conversation_content` — implemented |
| On-demand expansion    | ✅ (file read)               | N/A                    | N/A                                  | ✅ (DB fetch) — implemented |
| Retrieval              | glob + grep                  | Hybrid (vector + BM25) | Graph spreading activation + vector  | Hybrid (vector + BM25 + temporal decay + MMR) — implemented; §8.4 graph layer planned |
| Multi-hop recall       | ❌ (grep-only)               | ❌ (no graph)          | ✅ (graph traversal)                 | ❌ short-term; ✅ planned (§8.4)             |
| Write-time enrichment  | ❌                           | ❌                     | ✅ (structured notes + linking)      | ✅ `turn_notes` (§6.2)                       |
| Rolling summary        | ✅ (todo.md rewrite)         | ✅ (MEMORY.md)         | ✅ (compact internal state)          | ✅ `room_summary` (§4.2)                     |
| KV-cache optimization  | ✅ Explicit                  | ✅ Implicit            | N/A                                  | ✅ Explicit (stable prefix + prompt caching) |
| Multi-agent awareness  | Sub-agent isolation          | Per-agent isolation    | N/A                                  | Peer awareness injection                     |
| Supervisor integration | Planner + Executor           | N/A                    | N/A                                  | ✅ Adaptive loop (decide_next), dispatch, synthesis |

### 17.4 Open Questions

1. **Memory retention policy**: How long to keep compaction archives? (Proposed: 90 days TTL)
2. **Cross-agent memory**: Should agents share learned facts? (Proposed: Opt-in per room)
3. **Memory search scope**: Search room-only or include user memory? (Proposed: Room-first, user as fallback)
4. **Compaction frequency**: Background job vs on-demand? (Proposed: On-demand after synthesis, background cleanup)
5. **`turn_notes` extraction cost**: LLM call vs heuristic extractor? (Proposed: heuristic for turns <100 tokens, cheap fast model for long agent responses; never block the main write path — run async)
6. **`room_summary` update granularity**: Update after every synthesis, or only when goal/decisions change? (Proposed: Always update `recent_agent_contributions`; update `key_decisions`/`open_questions`/`current_goal` only if changed — use a short LLM diff check)
7. **Graph layer trigger**: At what point does multi-hop retrieval failure become a user-visible problem? (Proposed: Instrument "context miss" events via user feedback; build §8.4 graph layer when miss rate exceeds 5%)

### 17.5 Dependencies on Other Systems

| Dependency         | Status                    | Notes                                                                |
| ------------------ | ------------------------- | -------------------------------------------------------------------- |
| Supervisor V2      | ✅ Implemented (Feb 2026) | Phase 5 complete; adaptive loop replaces plan/review/synthesis       |
| Pinecone           | ✅ Existing               | Reuse `agentmatch` infra, add `room-memory` index                    |
| MongoDB            | ✅ Existing               | Add new collections with indexes                                     |
| OpenAI             | ✅ Existing               | Embeddings + token estimation (tiktoken)                             |

### 17.6 Risk Assessment

| Risk                    | Likelihood | Impact | Mitigation                                              |
| ----------------------- | ---------- | ------ | ------------------------------------------------------- |
| Token budget overflow   | Medium     | High   | Hard cap with truncation; alert on repeated truncation  |
| Expansion latency       | Medium     | Medium | Cache recently expanded content; batch expansions       |
| Search relevance issues | Medium     | Medium | A/B test weights; user feedback loop                    |
| Storage growth          | Medium     | Medium | TTL policy on `conversation_content`; archive old rooms |

---

## 18. Implementation Checklist

### Phase 1: Data Models & Storage

- [ ] **Update `models/memory.py` `ConversationTurn` to §6.2 canonical shape** — add
  `turn_id`, `representation`, `content_ref`, `estimated_tokens_full`,
  `estimated_tokens_compact`, `brief_summary`, `turn_notes`; change `content: str` to
  `content: str | None = None` (precondition for all compaction work)
- [ ] Create `models/context.py` with `SessionContext`, `TokenBudget`
- [ ] Create `models/compaction.py` with `ContentReference`, `StoredContent`, `CompactionResult`
- [ ] Create `models/search.py` with `MemorySearchConfig`, `MemorySearchResult`
- [ ] Extend `models/memory.py` with `UserMemory`, `AgentMemory`, `RoomFact`, **`RoomSummary`**
- [ ] **Wire `estimate_tokens(content)` into `add_turn_to_history()`** in
  `common/utils/context_utils.py` — set `estimated_tokens_full` at turn creation
  time (not left at 0) so compaction triggers and budget accounting work correctly
- [ ] **Wire `extract_turn_notes(content)` into `add_turn_to_history()`** — populate
  `turn_notes` at write time (keywords, entities, one_liner). Use heuristic for short
  turns; fast LLM for long agent responses; always run async to avoid blocking writes
- [ ] Add env variables to `config/settings.py`
- [ ] Create MongoDB `conversation_content` collection with indexes (add text index on
  `turn_notes.keywords`, `turn_notes.entities`, `turn_notes.one_liner` for §8.3)
- [ ] Migration script for existing `room_memories`

### Phase 2: Context Assembly Engine

- [ ] Implement `services/context_assembly_service.py`
- [ ] Implement token budget allocation
- [ ] Implement stable prefix / dynamic suffix builders
- [ ] Unit tests for context assembly
- [ ] Integration with existing `build_context_for_agent()`

### Phase 3: Compaction System (Lossless)

- [ ] Implement `services/compaction_service.py` with pointer-based compaction
- [ ] Implement `services/content_storage_service.py` for full content storage
- [ ] Implement `expand_turn_content()` for on-demand retrieval
- [ ] Add compaction triggers to `RoomMessageCenter`
- [ ] Unit tests for compaction + expansion round-trip

### Phase 4: Memory Search

- [ ] Implement `services/memory_search_service.py`
- [ ] Create Pinecone index `room-memory`
- [ ] Implement hybrid search with temporal decay
- [ ] Implement MMR re-ranking
- [ ] Integration tests for search accuracy

### Phase 5: Supervisor V2 Integration

- [ ] Wire `ContextAssemblyService.build_supervisor_context()` into `_prepare_for_supervisor_v2()` (replaces `build_minimal_context(max_turns=5)`)
- [ ] Wire `ContextAssemblyService.build_agent_execution_context()` into `_process_single_message` / `build_context_for_agent()`
- [ ] Wire compaction trigger into `_handle_v2_run_result()` (after all terminal statuses)
- [ ] Wire `add_synthesis_to_history()` into `_handle_v2_run_result()` for `SYNTHESIZE` results
- [ ] **Wire `update_room_summary()` into `_handle_v2_run_result()` after synthesis** — update `RoomMemory.room_summary` (current_goal, key_decisions, open_questions, recent_agent_contributions) at each synthesis boundary
- [ ] Move `conversation_context` into system prompt for OpenAI prompt cache optimization (see §12.3)
- [ ] Enforce `MAX_CONTEXT_CHARS` (currently defined but not checked in `build_context_for_agent`); emit `context_truncation_events` counter when fired
- [ ] Add `context_occupancy_pct` logging to every `build_context_for_agent` call (see §15)
- [ ] End-to-end tests with Supervisor V2 loop
- [ ] Performance benchmarks: conversation_context token size per iteration, cache hit rate

### Phase 4B: Graph-Based Retrieval (Future, after Phase 4 ships)

- [ ] Design `extract_turn_notes()` — keyword/entity extraction at write time (§6.2, §8.3)
- [ ] Add MongoDB text index on `turn_notes` fields in `conversation_content`
- [ ] Implement `memory_entities` collection: index entity nodes from `turn_notes.entities`
- [ ] Implement temporal edge resolution at query time (order turns by timestamp for sequential traversal)
- [ ] Implement dual-route search in `MemorySearchService` (§8.4): merge Route-1 vector results with Route-2 entity-graph traversal results
- [ ] Evaluate on internal multi-hop recall test cases before rolling to production

---

## 19. Appendix: Token Estimation

```python
def estimate_tokens(text: str, model: str = "gpt-4") -> int:
    """
    Estimate token count for text.

    Uses tiktoken for accuracy. Falls back to char/4 heuristic.
    """
    try:
        import tiktoken
        encoding = tiktoken.encoding_for_model(model)
        return len(encoding.encode(text))
    except Exception:
        # Fallback: ~4 chars per token for English
        return len(text) // 4
```

---

## 20. Appendix: Temporal Decay Formula

```python
import math
from datetime import datetime, timezone

def apply_temporal_decay(
    score: float,
    timestamp: datetime,
    half_life_days: int = 30,
) -> float:
    """
    Apply exponential decay based on age.

    Formula: decayed_score = score × e^(-λ × age_days)
    where λ = ln(2) / half_life_days

    Examples (half_life=30):
    - Today: 100% of score
    - 7 days ago: ~84%
    - 30 days ago: 50%
    - 90 days ago: 12.5%
    """
    now = datetime.now(timezone.utc)
    age_days = (now - timestamp).total_seconds() / 86400

    decay_constant = math.log(2) / half_life_days
    decay_factor = math.exp(-decay_constant * age_days)

    return score * decay_factor
```

---

## 20. Implementation Reality vs. Design

> **Update (Feb 25 2026)**: All 5 phases of the Context Memory System are now implemented.
> The gaps documented in §20.2 and §20.5 below have been **fully closed**. This section
> is retained for historical reference. See `SYSTEM_DESIGN_REVIEW.md §6.4` for the
> comprehensive item-by-item compliance audit.

This section documents the gap between the current implementation (as of Feb 21, 2026)
and the design described in this document. It serves as an honest changelog for
engineering prioritization.

### 20.1 What Is Implemented

| Component | Status | Location |
|---|---|---|
| Room memory CRUD (`RoomMemoryService`) | ✅ Implemented | `services/memory_service.py` |
| Conversation history (sliding window, 20 turns) | ✅ Implemented | `common/utils/context_utils.py` |
| `add_turn_to_history` / `build_context_for_agent` | ✅ Implemented | `common/utils/context_utils.py` |
| `build_minimal_context` for supervisor | ✅ Implemented | `common/utils/context_utils.py` |
| `add_agent_response_to_memory` (V2 loop writes) | ✅ Implemented | `services/memory_service.py` |
| Legacy memory migration (`memory_text` → `summary`) | ✅ Implemented | `context_utils.migrate_legacy_memory` |
| Per-agent context via `ContextAssemblyService.build_agent_execution_context` | ✅ Implemented | `services/context_assembly_service.py` |
| Supervisor V2 adaptive loop | ✅ Implemented | `modules/SupervisorExecutor.py` |
| **Context Assembly Engine** (`ContextAssemblyService`) | ✅ Implemented (Phase 2) | `services/context_assembly_service.py` |
| **Lossless compaction** (pointer-based, §6) | ✅ Implemented (Phase 3) | `services/compaction_service.py`, `services/content_storage_service.py` |
| **`conversation_content` MongoDB collection** | ✅ Implemented (Phase 1) | `database/mongodb.py` — indexes, TTL, text search |
| **Token budget enforcement** | ✅ Implemented (Phase 2) | `ContextAssemblyService` + `MAX_CONTEXT_CHARS` in `build_context_for_agent` |
| **Memory Search** (hybrid vector + keyword) | ✅ Implemented (Phase 4) | `services/memory_search_service.py` |
| **User Memory / Agent Memory models** | ✅ Implemented (Phase 1) | `models/memory.py` — models defined; not yet populated |
| **Room Summary / Knowledge Block** | ✅ Implemented (Phase 5) | `update_room_summary()` in `memory_service.py` |
| **Prompt caching optimization** (§12.3) | ✅ Implemented (Phase 5) | `conversation_context` moved to system prompt |
| **Supervisor V2 Integration** (§11) | ✅ Implemented (Phase 5) | Pre-loop, during-loop, and post-loop wiring complete |

### 20.2 What Is NOT Implemented (Design vs. Reality)

> **All items below have been resolved as of Feb 25, 2026.**

| Design Element | Reality (Feb 25 2026) | Status |
|---|---|---|
| **Lossless compaction** (pointer-based, §6) | ✅ Implemented — `CompactionService` with `ContentStorageService` | RESOLVED |
| **`conversation_content` MongoDB collection** | ✅ Implemented — with unique index, text index, TTL index | RESOLVED |
| **Token budget enforcement** | ✅ Implemented — `ContextAssemblyService` + `MAX_CONTEXT_CHARS` hard cap | RESOLVED |
| **Context Assembly Engine** (`ContextAssemblyService`) | ✅ Implemented — budget-aware, KV-cache optimized | RESOLVED |
| **Memory Search** (`MemorySearchService`, Pinecone) | ✅ Implemented — hybrid vector + keyword + temporal decay + MMR | RESOLVED |
| **User Memory / Agent Memory** | ⚠️ Partial — models defined, collections indexed; runtime writes interaction counters and agent success/failure stats (`_track_user_interaction`, `_track_agent_call`); not yet consumed by context assembly | PARTIAL |
| **Room Facts extraction** | ⚠️ Partial — `RoomFact` model defined; LLM-based extraction from synthesis text implemented in `update_room_summary()`; no per-turn extraction pipeline | PARTIAL |
| **Prompt caching optimization** (§12.3) | ✅ Implemented — `conversation_context` in system prompt | RESOLVED |

### 20.3 Intentional Implementation Divergences

These are cases where the implementation intentionally deviates from the spec.
Documented here so future readers don't treat them as bugs.

| Spec | Implementation | Rationale |
|---|---|---|
| §9.1: `room_facts` is a standalone collection with `room_id` and `created_at` indexes | `room_facts` is embedded as `list[RoomFact]` inside `room_memories` | Simplifies atomic updates (facts pushed/sliced with `room_summary` in a single `update_one`); avoids cross-collection consistency. Acceptable at current scale. |
| §8.1: `search() -> list[MemorySearchResult]` | `search() -> MemorySearchResponse` (wraps list + diagnostics) | Richer return value includes timing, sub-search flags, decay/MMR metadata. Results at `.results`. |
| §8.1: MMR uses full embedding vectors | MMR uses 3-element score profile `[vector_score, keyword_score, temporal_decay_factor]` as a diversity proxy | Avoids storing/passing full embeddings on `MemorySearchResult`; sufficient for deduplication. |
| §7.6: HITL resume branches on `interrupt_kind` and calls `room_memory_service.build_conversation_context()` | Single resume path calls `context_assembly_service.build_supervisor_context()` for all pause types | `build_conversation_context` was never implemented; `build_supervisor_context` is the real equivalent. `interrupt_kind` branching is a future enhancement. |
| §11.3: Two-step `should_compact()` + `compact_room_memory()` | Single `compact_if_needed()` combines check + compact to avoid redundant DB load | Functionally equivalent; avoids double-fetching room memory. |

### 20.3.1 Known Implementation Gaps (Not Yet Built)

| Gap | Impact | Mitigation | Priority |
|---|---|---|---|
| **Pinecone reconciliation worker** — compaction proceeds even when Pinecone indexing fails, but there's no background job to retry failed indexes | Un-indexed turns are missing from vector search (keyword search and direct expansion still work) | Manual re-index via `memory_search_service.index_turn_for_search()` if needed; add `indexed_at` field to `conversation_content` to track | P2 |
| **User Memory / Agent Memory** — partially implemented: `_track_user_interaction()` and `_track_agent_call()` write interaction counters and success/failure stats, but the data is not yet consumed by context assembly or exposed to agents | Tracking data accumulates but has no downstream consumer; no impact on agent behavior yet | Wire into `ContextAssemblyService` when personalization features are prioritized | P3 |
| **Room Facts auto-extraction** — partially implemented: `update_room_summary()` extracts `room_facts` from synthesis text via LLM at synthesis boundaries; no standalone extraction pipeline for individual turns | Facts are captured at synthesis granularity (every N turns), not per-turn; short conversations that never trigger synthesis won't accumulate facts | Acceptable coverage for current use cases; per-turn extraction is a future enhancement | P3 |

### 20.4 Known Behavioral Gaps in the V2 Loop

These are not design omissions — they are observable behaviors of the current
implementation that engineers should be aware of:

| Behavior | Root Cause | Consequence |
|---|---|---|
| `conversation_context` is a **frozen snapshot** built before the loop starts | `_prepare_for_supervisor_v2` runs once; no re-assembly per iteration | Supervisor never sees current-loop agent results in its room history view — it sees them only in `trajectory_summary`. This is **correct by design** but must be understood. |
| Concurrent agents don't see each other's results in per-agent context | `asyncio.gather` dispatches all agents before any write to room memory | For multi-target DELEGATE, agents have no awareness of sibling results. Supervisor must compensate via `DelegateTarget.task`. |
| Current user message appears **twice** in supervisor prompt | `initialize_or_update_room_memory` runs before `_prepare_for_supervisor_v2`, so current message is in room history AND in `## User Message` section | Minor redundancy; does not affect correctness. |

### 20.5 Priority Order for Closing Gaps

> **All items below have been completed as of Feb 25, 2026.**

1. ~~**Enforce `MAX_CONTEXT_CHARS`** in `build_context_for_agent`~~ ✅ Done (Phase 2)
2. ~~**Move `conversation_context` to system prompt** (§12.3)~~ ✅ Done (Phase 5)
3. ~~**Build `ContextAssemblyService`** with token budget awareness~~ ✅ Done (Phase 2)
4. ~~**Build lossless compaction**~~ ✅ Done (Phase 3)
5. ~~**Build Memory Search**~~ ✅ Done (Phase 4)

---

## 21. 2026 SOTA Alignment & Future Evolution

This section documents how the design aligns with state-of-the-art research as of Feb 2026,
and the planned evolution path. Researched Feb 21, 2026.

### 21.1 Alignment Assessment

| SOTA Concept | Paper(s) | This Design | Status |
|---|---|---|---|
| **Lossless pointer-based compaction** | Manus (production) | §6 Compaction System | ✅ Implemented (Phase 3) |
| **KV-cache stable prefix** | Manus, production guides | §12 KV-Cache Optimization | ✅ Implemented (Phase 2) |
| **Hybrid vector + keyword search** | OpenClaw, MAGMA | §8.1 Search Architecture | ✅ Implemented (Phase 4) |
| **Temporal decay in retrieval** | OpenClaw, production guides | §8.1 temporal decay | ✅ Implemented (Phase 4) |
| **Write-time note generation** | A-MEM (arXiv 2502.12110) | `turn_notes` field §6.2 | ✅ Implemented (Phase 1) |
| **Rolling structured summary ("Knowledge Block")** | Focus (arXiv 2601.07190), Manus todo.md | `room_summary` §4.2 | ✅ Implemented (Phase 5) |
| **Context occupancy monitoring** | Production guides 2025–2026 | §15.1 metrics | ✅ Implemented (Phase 2 + 5) |
| **Multi-hop graph retrieval** | SYNAPSE/MAGMA/Mnemis (2026) | §8.4 Future | 🔵 Planned (Phase 4B) |
| **Agent-driven compaction (tool)** | Focus, AgeMem (2026) | §2.4 Principle 5 | 🔵 Planned (post-Phase 4) |
| **RL-trained memory consolidation** | MEM1 (ICLR 2026) | Not in roadmap | ⬜ Research-stage; monitor for viability |

### 21.2 What the 2026 Research Validates

The following design choices are directly validated by 2026 SOTA:

1. **Lossless over lossy**: MEM1 and Focus both show that agents need recoverable context. The design's insistence on pointer-based compaction (not summarization) is correct.

2. **Token cost dominates**: Production data shows 100:1 input/output ratio. Context engineering is cost engineering. The KV-cache optimization in §12 is a high-leverage investment.

3. **Recency bias is not enough**: SYNAPSE demonstrates that temporal/causal multi-hop queries fail with flat vector similarity. The `turn_notes` entity index (§8.3) is the right intermediate step before a full graph layer.

4. **Structured context beats raw transcripts**: The "Knowledge Block" in Focus and "MEMORY.md" in OpenClaw both show that a compact, structured summary maintained in context dramatically reduces turn-scanning overhead. The `room_summary` field (§4.2) implements this.

5. **Write-time enrichment pays off**: A-MEM's core insight is that the cost of enriching memory at write time (Zettelkasten notes) is small compared to the retrieval quality improvement. `turn_notes` is the direct application.

### 21.3 What the 2026 Research Challenges

1. **Threshold-based compaction is suboptimal**: AgeMem/Focus show that agent-controlled compaction outperforms fixed thresholds. The threshold approach (`max_turns > 20`) is correct to ship first, but should be augmented with a `compact_context(rationale)` supervisor tool in a later phase.

2. **Flat sequential list is insufficient for long-horizon rooms**: Rooms with 10+ sessions and multi-topic threads will see degraded recall on temporal/causal queries with the flat `list[ConversationTurn]` architecture. The §8.4 graph layer is the long-term fix; the `turn_notes` entity index (§8.3) is the near-term mitigation.

3. **"Context rot" is real and underestimated**: Studies confirm GPT-4 accuracy degrades from ~98% to ~64% based solely on information placement in the context window. This means the design's §5 token budget allocation (history at 60%) may concentrate too much irrelevant content in the middle of context. The `room_summary` (always near the front) and KV-cache stable prefix (static content first) partially mitigate this.

### 21.4 Recommended Evolution Sequence

Based on research alignment and implementation reality (§20):

```
✅ COMPLETED — Immediate (days, no new architecture):
  1. ✅ Enforce MAX_CONTEXT_CHARS  →  eliminate silent overflow
  2. ✅ Move conversation_context to system prompt  →  50% cost reduction on V2 loop
  3. ✅ Add context_occupancy_pct logging  →  visibility before fixes

✅ COMPLETED — Short-term (weeks, within current architecture):
  4. ✅ Wire estimate_tokens in add_turn_to_history  →  compaction triggers work
  5. ✅ Wire extract_turn_notes in add_turn_to_history  →  richer retrieval for free
  6. ✅ Populate room_summary at synthesis boundary  →  Knowledge Block in context
  7. ✅ Build ContextAssemblyService (Phase 2)  →  budget-aware context

✅ COMPLETED — Medium-term (1–2 months):
  8. ✅ Build lossless compaction (Phase 3)  →  correctness, not just efficiency
  9. ✅ Build Memory Search with turn_notes integration (Phase 4 + §8.3)  →  recall

REMAINING — Long-term (3+ months, research-validated):
  10. Dual-route graph retrieval (§8.4)  →  multi-hop recall for long-horizon rooms
  11. Agent-driven compaction tool  →  semantic-relevance-based pruning
```
