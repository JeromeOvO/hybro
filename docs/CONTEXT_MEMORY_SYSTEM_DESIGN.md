# Context & Memory System Architecture Design

**Date**: February 17, 2026  
**Status**: Proposal  
**Scope**: Unified context and memory management for multi-agent A2A rooms

---

## 1. Executive Summary

This document defines the architecture for a production-grade context and memory system for Hybro's multi-agent platform. The design draws from:

- **Manus**: KV-cache optimization, file-system as context, attention manipulation via recency
- **OpenClaw**: Multi-layer memory (daily logs + curated long-term), compaction, hybrid search
- **Hybro Supervisor Pattern**: Integration with planning, review, and synthesis phases

### 1.1 Key References

| Source | Key Lessons Applied |
|--------|---------------------|
| [Manus Context Engineering](https://manus.im/blog/Context-Engineering-for-AI-Agents) | KV-cache optimization, append-only context, todo.md pattern for attention |
| [OpenClaw Multi-Agent](https://docs.openclaw.ai/concepts/multi-agent) | Agent isolation, workspace-per-agent, session routing |
| [OpenClaw Context](https://docs.openclaw.ai/concepts/context) | Token budgeting, system prompt structure, tool schema costs |
| [OpenClaw Memory](https://docs.openclaw.ai/concepts/memory) | Daily logs + MEMORY.md, vector search, pre-compaction flush |
| [OpenClaw Compaction](https://docs.openclaw.ai/concepts/compaction) | Auto-compaction triggers, summary persistence, pruning vs compaction |

---

## 2. Design Principles

### 2.1 From Manus: Context Engineering

1. **KV-Cache Optimization**: Keep context prefixes stable; append-only updates
   - *"Even a single token difference invalidates the cache from that point forward"*
   - Avoid timestamps at start of prompts; use deterministic serialization
   
2. **File System as Memory**: Externalize long-term state to persistent storage
   - *"The file system is the ultimate context: unlimited size, naturally persistent"*
   - MongoDB serves as our "file system" for durability
   
3. **Attention Manipulation**: Use recency and summarization to guide model focus
   - *"By rewriting the todo list, Manus restates its goals at the end of context"*
   - Recent turns and current task always at context end
   
4. **Preserve Errors**: Keep failed attempts in context for learning
   - *"Erasing failures removes evidence. The model can't adapt without it."*
   - Store `was_successful` flag on conversation turns

### 2.2 From OpenClaw: Memory Architecture

1. **Multi-Layer Memory**: Separate ephemeral (session) from durable (room/user) memory
   - Session context: current request cycle only
   - Room memory: persistent across sessions
   - User memory: cross-room preferences
   
2. **Compaction**: Summarize old context to stay within token limits
   - Auto-trigger based on turn count or token estimate
   - Pre-compaction flush extracts durable facts before summarizing
   
3. **Hybrid Search**: Combine vector similarity with keyword matching
   - Vector: semantic similarity for paraphrased queries
   - BM25: exact token matching for IDs, code symbols, error strings
   
4. **Temporal Decay**: Boost recent memories in search results
   - Half-life decay: 30-day default, configurable per environment

### 2.3 Hybro-Specific Requirements

1. **Multi-Agent Awareness**: Each agent needs room context + peer awareness
2. **Supervisor Integration**: Memory feeds into planning, review, and synthesis
3. **A2A Protocol Compatibility**: Context must serialize for external agents
4. **Horizontal Scalability**: No in-memory state that prevents scaling

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         CONTEXT & MEMORY SYSTEM                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │   Session    │  │    Room      │  │    User      │  │   Agent      │ │
│  │   Context    │  │   Memory     │  │   Memory     │  │   Memory     │ │
│  │  (Ephemeral) │  │  (Durable)   │  │  (Durable)   │  │  (Durable)   │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘ │
│         │                 │                 │                 │          │
│         └────────────┬────┴────────┬────────┴────────┬────────┘          │
│                      │             │                 │                   │
│                      ▼             ▼                 ▼                   │
│              ┌───────────────────────────────────────────┐               │
│              │         Context Assembly Engine           │               │
│              │  (Builds per-request context window)      │               │
│              └───────────────────────────────────────────┘               │
│                                    │                                     │
│         ┌──────────────────────────┼──────────────────────────┐          │
│         ▼                          ▼                          ▼          │
│  ┌─────────────┐          ┌─────────────┐          ┌─────────────┐       │
│  │  Supervisor │          │   Agent     │          │    A2A      │       │
│  │   Context   │          │   Context   │          │   Context   │       │
│  │  (Planning) │          │ (Execution) │          │  (External) │       │
│  └─────────────┘          └─────────────┘          └─────────────┘       │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

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

    # Supervisor state (if multi-agent)
    supervisor_plan: SupervisorPlan | None = None
    completed_steps: list[CompletedStep] = []
    current_step_index: int = 0

    # Accumulated context during execution
    step_results: dict[str, StepResult] = {}

    # Token tracking
    estimated_tokens: int = 0
    max_tokens: int = 128000  # Model-specific
```

**Lifecycle**: Created when user sends message → Destroyed after all agents respond

### 4.2 Room Memory (Durable)

**Purpose**: Persistent conversation history and learned context for a room.

```python
class RoomMemory(BaseModel):
    """Durable memory for a chat room."""
    room_id: str

    # Conversation history (sliding window)
    conversation_history: list[ConversationTurn] = []
    max_history_turns: int = 50

    # Compacted summaries (older context)
    compaction_summaries: list[CompactionSummary] = []

    # Room-level learned facts
    room_facts: list[RoomFact] = []

    # Agent interaction patterns
    agent_success_history: dict[str, AgentSuccessRecord] = {}

    # Metadata
    last_activity_at: datetime
    total_messages: int = 0
    total_compactions: int = 0


class ConversationTurn(BaseModel):
    """Single turn in conversation history."""
    turn_id: str
    role: Literal["user", "agent", "supervisor"]
    agent_id: str | None = None
    agent_name: str | None = None
    content: str
    timestamp: datetime

    # For agent turns
    task_description: str | None = None
    step_id: str | None = None
    was_successful: bool = True

    # Token estimate for budget tracking
    estimated_tokens: int = 0


class CompactionSummary(BaseModel):
    """Summary of compacted conversation history."""
    summary_id: str
    content: str
    covers_turns: list[str]  # turn_ids that were compacted
    created_at: datetime
    estimated_tokens: int
```

**Storage**: MongoDB `room_memories` collection

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
    room_context_pct: float = 0.15
    conversation_history_pct: float = 0.50
    current_task_pct: float = 0.20
    compaction_summaries_pct: float = 0.15

    @property
    def available_for_content(self) -> int:
        return self.model_context_window - (
            self.system_prompt +
            self.tool_schemas +
            self.response_reserve
        )
```

---

## 6. Compaction System

### 6.1 Compaction Triggers

Compaction is triggered automatically based on environment configuration (see Section 14):

```python
# Trigger conditions (from env via compaction_config)
# - Turn count exceeds COMPACTION_MAX_TURNS (default: 30)
# - Token estimate exceeds COMPACTION_MAX_TOKENS (default: 50000)

async def should_compact(room_id: str) -> bool:
    """Check if room memory needs compaction."""
    room_memory = await load_room_memory(room_id)
    
    if not compaction_config.enabled:
        return False
    
    turn_count = len(room_memory.conversation_history)
    token_estimate = sum(t.estimated_tokens for t in room_memory.conversation_history)
    
    return (
        turn_count > compaction_config.max_turns_before_compaction or
        token_estimate > compaction_config.max_tokens_before_compaction
    )
```

### 6.2 Compaction Process

```python
async def compact_room_memory(room_id: str) -> CompactionSummary | None:
    """
    Compact older conversation history into a summary.

    Process:
    1. Load room memory
    2. Identify turns to compact (older than preserve_recent_turns)
    3. Pre-compaction flush: extract durable facts (if enabled)
    4. Generate summary via LLM
    5. Store summary, remove compacted turns
    6. Update room memory
    """
    room_memory = await load_room_memory(room_id)
    preserve_count = compaction_config.preserve_recent_turns

    # Identify turns to compact
    turns_to_compact = room_memory.conversation_history[:-preserve_count]

    if len(turns_to_compact) < 5:
        return None  # Not enough to compact

    # Pre-compaction flush (extract facts before summarizing)
    if compaction_config.pre_flush_enabled:
        facts = await pre_compaction_memory_flush(room_id, turns_to_compact)
        room_memory.room_facts.extend(facts)

    # Generate summary using configured model
    summary_text = await generate_compaction_summary(
        turns_to_compact,
        model=compaction_config.compaction_model
    )

    # Create summary record
    summary = CompactionSummary(
        summary_id=uuid4().hex,
        content=summary_text,
        covers_turns=[t.turn_id for t in turns_to_compact],
        created_at=utcnow(),
        estimated_tokens=estimate_tokens(summary_text),
    )

    # Update room memory
    room_memory.compaction_summaries.append(summary)
    room_memory.conversation_history = room_memory.conversation_history[-preserve_count:]
    room_memory.total_compactions += 1

    await save_room_memory(room_memory)

    return summary
```

### 6.3 Pre-Compaction Memory Flush

Before compaction, trigger a memory extraction pass (inspired by OpenClaw):

```python
async def pre_compaction_memory_flush(
    room_id: str,
    turns_to_compact: list[ConversationTurn]
) -> list[RoomFact]:
    """
    Extract durable facts before compacting conversation.

    This ensures important information isn't lost in summarization.
    """
    prompt = f"""
    Review this conversation segment and extract any durable facts
    that should be remembered long-term:

    {format_turns(turns_to_compact)}

    Extract facts like:
    - User preferences mentioned
    - Decisions made
    - Important context established
    - Recurring topics or patterns

    Return as JSON array of facts.
    """

    facts = await extract_facts_via_llm(prompt)
    return facts
```

---

## 7. Supervisor Integration

### 7.1 Context for Planning Phase

```python
async def build_supervisor_planning_context(
    session: SessionContext,
    room_memory: RoomMemory,
    agent_registry: list[AgentProfile],
) -> str:
    """Build context for Supervisor planning phase."""

    context_parts = []

    # 1. Agent registry (always first for cache stability)
    context_parts.append(format_agent_registry(agent_registry))

    # 2. Room context (compaction summaries + recent history)
    context_parts.append(format_room_context(room_memory))

    # 3. Current user message
    context_parts.append(f"## Current Request\n{session.user_message}")

    # 4. Agent success history (helps routing decisions)
    context_parts.append(format_agent_success_history(room_memory))

    return "\n\n".join(context_parts)
```

### 7.2 Context for Review Phase

```python
async def build_supervisor_review_context(
    session: SessionContext,
    completed_step: CompletedStep,
    remaining_steps: list[SupervisorStep],
) -> str:
    """Build context for Supervisor review phase."""

    context_parts = []

    # 1. Original plan (for reference)
    context_parts.append(format_plan_summary(session.supervisor_plan))

    # 2. Completed step details
    context_parts.append(format_completed_step(completed_step))

    # 3. Remaining steps
    context_parts.append(format_remaining_steps(remaining_steps))

    # 4. All step results so far
    context_parts.append(format_step_results(session.step_results))

    return "\n\n".join(context_parts)
```

### 7.3 Context for Agent Execution

```python
async def build_agent_execution_context(
    session: SessionContext,
    room_memory: RoomMemory,
    target_agent: Agent,
    step: SupervisorStep,
) -> str:
    """Build context for agent execution."""

    context_parts = []

    # 1. Room awareness (other agents in room)
    peer_agents = [a for a in room_memory.agent_success_history.keys()
                   if a != target_agent.agent_id]
    if peer_agents:
        context_parts.append(format_peer_awareness(peer_agents, room_memory))

    # 2. Relevant conversation history
    relevant_turns = select_relevant_turns(
        room_memory.conversation_history,
        step.task_description,
        max_turns=10
    )
    context_parts.append(format_conversation_history(relevant_turns))

    # 3. Results from dependent steps
    if step.context_from_steps:
        for step_id in step.context_from_steps:
            if step_id in session.step_results:
                context_parts.append(
                    format_step_result(session.step_results[step_id])
                )

    # 4. Task description
    context_parts.append(f"## Your Task\n{step.task_description}")

    return "\n\n".join(context_parts)
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

### 8.2 Search Configuration

All search parameters are loaded from environment variables (see Section 14.1):

| Parameter | Env Variable | Default | Description |
|-----------|--------------|---------|-------------|
| Vector weight | `MEMORY_SEARCH_VECTOR_WEIGHT` | 0.7 | Weight for semantic similarity |
| Keyword weight | `MEMORY_SEARCH_KEYWORD_WEIGHT` | 0.3 | Weight for BM25 matching |
| Half-life | `MEMORY_SEARCH_HALF_LIFE_DAYS` | 30 | Days for score to decay 50% |
| MMR lambda | `MEMORY_SEARCH_MMR_LAMBDA` | 0.7 | Diversity vs relevance tradeoff |
| Max results | `MEMORY_SEARCH_MAX_RESULTS` | 10 | Maximum results returned |

---

## 9. Data Models Summary

### 9.1 New Collections

| Collection             | Purpose                           | Indexes                 |
| ---------------------- | --------------------------------- | ----------------------- |
| `room_memories`        | Room conversation history + facts | `room_id` (unique)      |
| `user_memories`        | User preferences + patterns       | `user_id` (unique)      |
| `agent_memories`       | Agent performance history         | `agent_id` (unique)     |
| `compaction_summaries` | Archived conversation summaries   | `room_id`, `created_at` |
| `room_facts`           | Extracted durable facts           | `room_id`, `created_at` |

### 9.2 Model Files to Create

```
models/
├── context.py          # SessionContext, TokenBudget
├── memory.py           # (extend existing) RoomMemory, UserMemory, AgentMemory
├── compaction.py       # CompactionSummary, CompactionConfig
└── search.py           # MemorySearchConfig, MemorySearchResult
```

---

## 10. Service Design

### 10.1 New Services

```
services/
├── context_assembly_service.py    # Context Assembly Engine
├── compaction_service.py          # Compaction logic
├── memory_search_service.py       # Hybrid search
└── memory_service.py              # (extend existing) CRUD for all memory types
```

### 10.2 Service Dependencies

```
ContextAssemblyService
    ├── MemoryService (load/save memories)
    ├── CompactionService (trigger compaction)
    ├── MemorySearchService (relevant context retrieval)
    └── OpenAIService (token estimation)

CompactionService
    ├── MemoryService
    └── OpenAIService (summarization)

MemorySearchService
    ├── PineconeClient (vector search)
    ├── MongoDBClient (keyword search)
    └── OpenAIService (embeddings)
```

---

## 11. Integration with Supervisor Pattern

### 11.1 Planning Phase Integration

```python
# In RoomSupervisorService.create_plan()
async def create_plan(self, ...) -> SupervisorPlan:
    # Build planning context using Context Assembly Engine
    context = await context_assembly_service.build_supervisor_planning_context(
        session=session,
        room_memory=room_memory,
        agent_registry=agent_registry,
    )

    # Call Supervisor LLM with assembled context
    plan = await self._call_supervisor_llm(context, ...)

    return plan
```

### 11.2 Review Phase Integration

```python
# In RoomSupervisorService.review_step()
async def review_step(self, ...) -> SupervisorReview:
    # Build review context
    context = await context_assembly_service.build_supervisor_review_context(
        session=session,
        completed_step=completed_step,
        remaining_steps=remaining_steps,
    )

    # Call review LLM
    review = await self._call_review_llm(context, ...)

    # Update memory with step result
    await memory_service.record_step_result(
        room_id=session.room_id,
        step=completed_step,
        result=agent_result,
    )

    return review
```

### 11.3 Synthesis Phase Integration

```python
# In RoomSupervisorService.synthesize_results()
async def synthesize_results(self, ...) -> str:
    # Build synthesis context
    context = await context_assembly_service.build_synthesis_context(
        session=session,
        step_results=step_results,
        room_memory=room_memory,
    )

    # Generate synthesis
    synthesis = await self._call_synthesis_llm(context, ...)

    # Update room memory with final result
    await memory_service.add_synthesis_to_history(
        room_id=session.room_id,
        synthesis=synthesis,
        step_results=step_results,
    )

    # Check if compaction needed
    if await compaction_service.should_compact(session.room_id):
        await compaction_service.compact_room_memory(session.room_id)

    return synthesis
```

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

    # 1. Compaction summaries (append-only, ordered by time)
    for summary in room_memory.compaction_summaries:
        suffix_parts.append(format_summary(summary))

    # 2. Recent conversation (append-only)
    for turn in room_memory.conversation_history:
        suffix_parts.append(format_turn(turn))

    # 3. Current request (always last)
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
        return f"[{turn.role.upper()}] {turn.content}"

    @staticmethod
    def serialize_agent_profile(agent: AgentProfile) -> str:
        """Serialize agent profile deterministically."""
        return (
            f"- {agent.agent_name} ({agent.agent_id})\n"
            f"  Description: {agent.description}\n"
            f"  Capabilities: {', '.join(sorted(agent.capabilities))}"
        )
```

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

1. Implement `CompactionService`
2. Implement pre-compaction memory flush
3. Add compaction triggers to message processing
4. Background job for periodic compaction check

### Phase 4: Memory Search (Week 4)

1. Implement `MemorySearchService`
2. Set up Pinecone index for memory embeddings
3. Implement hybrid search with temporal decay
4. Integration tests for search

### Phase 5: Supervisor Integration (Week 5)

1. Wire context assembly into Supervisor planning
2. Wire context assembly into Supervisor review
3. Wire context assembly into agent execution
4. End-to-end tests with Supervisor pattern

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
    context_history_pct: float = 0.50  # % of remaining for conversation history
    context_task_pct: float = 0.20  # % of remaining for current task
    context_summaries_pct: float = 0.15  # % of remaining for compaction summaries
    
    # Compaction Settings
    compaction_enabled: bool = True  # Enable/disable auto-compaction
    compaction_max_turns: int = 30  # Trigger compaction after this many turns
    compaction_max_tokens: int = 50000  # Trigger compaction after this many tokens
    compaction_preserve_recent: int = 10  # Keep this many recent turns uncompacted
    compaction_model: str = "gpt-4o-mini"  # Model for generating summaries
    compaction_pre_flush_enabled: bool = True  # Extract facts before compacting
    
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
CONTEXT_HISTORY_PCT=0.50
CONTEXT_TASK_PCT=0.20
CONTEXT_SUMMARIES_PCT=0.15

# Compaction Settings
COMPACTION_ENABLED=true
COMPACTION_MAX_TURNS=30
COMPACTION_MAX_TOKENS=50000
COMPACTION_PRESERVE_RECENT=10
COMPACTION_MODEL=gpt-4o-mini
COMPACTION_PRE_FLUSH_ENABLED=true

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
    def compaction_summaries_pct(self) -> float:
        return settings.context_summaries_pct
    
    @property
    def available_for_content(self) -> int:
        return self.model_context_window - (
            self.system_prompt + 
            self.tool_schemas + 
            self.response_reserve
        )


class CompactionConfig:
    """Compaction configuration. Loaded from env."""
    
    @property
    def enabled(self) -> bool:
        return settings.compaction_enabled
    
    @property
    def max_turns_before_compaction(self) -> int:
        return settings.compaction_max_turns
    
    @property
    def max_tokens_before_compaction(self) -> int:
        return settings.compaction_max_tokens
    
    @property
    def preserve_recent_turns(self) -> int:
        return settings.compaction_preserve_recent
    
    @property
    def compaction_model(self) -> str:
        return settings.compaction_model
    
    @property
    def pre_flush_enabled(self) -> bool:
        return settings.compaction_pre_flush_enabled


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
|-------------|------------------------|--------------------------------|------------------------|
| Development | 15 | 7 | 32000 |
| Staging | 25 | 14 | 128000 |
| Production | 30 | 30 | 128000 |

---

## 15. Observability

### 15.1 Metrics

```python
# Context assembly metrics
context_assembly_duration_ms: Histogram
context_tokens_used: Histogram
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

### 15.2 Logging

```python
logger.info(
    "Context assembled",
    extra={
        "room_id": room_id,
        "session_id": session_id,
        "total_tokens": total_tokens,
        "history_turns": len(history_turns),
        "summaries_included": len(summaries),
        "cache_prefix_tokens": prefix_tokens,
    }
)
```

---

## 16. Summary

This architecture provides:

1. **Multi-layer memory**: Session (ephemeral) + Room/User/Agent (durable)
2. **KV-cache optimization**: Stable prefixes, append-only updates, deterministic serialization
3. **Compaction**: Automatic summarization with pre-compaction fact extraction
4. **Hybrid search**: Vector + keyword with temporal decay and MMR diversity
5. **Supervisor integration**: Context assembly for planning, review, and synthesis phases
6. **Horizontal scalability**: All state in MongoDB/Pinecone, no in-memory dependencies

The design draws from production lessons at Manus and OpenClaw while adapting to Hybro's multi-agent A2A architecture and upcoming Supervisor pattern.

---

## 17. Design Review & Gap Analysis

### 17.1 Strengths

| Aspect | Assessment |
|--------|------------|
| **Scalability** | ✅ All state in MongoDB/Pinecone; no in-memory dependencies |
| **Supervisor Integration** | ✅ Clear integration points for planning, review, synthesis |
| **KV-Cache Optimization** | ✅ Stable prefix + append-only suffix pattern |
| **Configuration** | ✅ All tunable params via environment variables |
| **Compaction** | ✅ Pre-flush fact extraction prevents information loss |

### 17.2 Identified Gaps & Mitigations

| Gap | Risk | Mitigation |
|-----|------|------------|
| **No cross-room memory sharing** | Agents can't learn from interactions in other rooms | Phase 2: Add optional `shared_facts` collection with user consent |
| **Pinecone dependency for search** | Single point of failure for memory search | Fallback to MongoDB text search if Pinecone unavailable |
| **Token estimation accuracy** | Budget allocation may be off | Use tiktoken for accurate counts; add 10% buffer |
| **Compaction summary quality** | LLM may lose important details | Pre-flush extracts facts; keep original turns in archive collection |
| **No memory versioning** | Can't rollback bad compactions | Add `compaction_archives` collection with TTL |

### 17.3 Comparison with Reference Systems

| Feature | Manus | OpenClaw | Hybro (This Design) |
|---------|-------|----------|---------------------|
| Memory layers | File system | Daily logs + MEMORY.md | Session + Room + User + Agent |
| Compaction | Manual via todo.md | Auto with pre-flush | Auto with pre-flush + fact extraction |
| Search | N/A | Hybrid (vector + BM25) | Hybrid (vector + BM25 + temporal decay) |
| KV-cache optimization | ✅ Explicit | ✅ Implicit | ✅ Explicit (stable prefix pattern) |
| Multi-agent awareness | N/A | Per-agent isolation | Peer awareness injection |
| Supervisor integration | N/A | N/A | ✅ Planning, review, synthesis |

### 17.4 Open Questions

1. **Memory retention policy**: How long to keep compaction archives? (Proposed: 90 days TTL)
2. **Cross-agent memory**: Should agents share learned facts? (Proposed: Opt-in per room)
3. **Memory search scope**: Search room-only or include user memory? (Proposed: Room-first, user as fallback)
4. **Compaction frequency**: Background job vs on-demand? (Proposed: On-demand after synthesis, background cleanup)

### 17.5 Dependencies on Other Systems

| Dependency | Status | Notes |
|------------|--------|-------|
| Supervisor Pattern | 📋 Planned | Context assembly integrates with planning/review/synthesis |
| Pinecone | ✅ Existing | Reuse `agentmatch` infra, add `room-memory` index |
| MongoDB | ✅ Existing | Add new collections with indexes |
| OpenAI | ✅ Existing | Embeddings + compaction summarization |

### 17.6 Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Token budget overflow | Medium | High | Hard cap with truncation; alert on repeated truncation |
| Compaction latency | Low | Medium | Async background job; don't block user response |
| Search relevance issues | Medium | Medium | A/B test weights; user feedback loop |
| Memory growth unbounded | Low | High | TTL on archives; compaction summaries capped |

---

## 18. Implementation Checklist

### Phase 1: Data Models & Storage
- [ ] Create `models/context.py` with `SessionContext`, `TokenBudget`
- [ ] Create `models/compaction.py` with `CompactionSummary`, `CompactionConfig`
- [ ] Create `models/search.py` with `MemorySearchConfig`, `MemorySearchResult`
- [ ] Extend `models/memory.py` with `UserMemory`, `AgentMemory`, `RoomFact`
- [ ] Add env variables to `config/settings.py`
- [ ] Create MongoDB indexes for new collections
- [ ] Migration script for existing `room_memories`

### Phase 2: Context Assembly Engine
- [ ] Implement `services/context_assembly_service.py`
- [ ] Implement token budget allocation
- [ ] Implement stable prefix / dynamic suffix builders
- [ ] Unit tests for context assembly
- [ ] Integration with existing `build_context_for_agent()`

### Phase 3: Compaction System
- [ ] Implement `services/compaction_service.py`
- [ ] Implement pre-compaction memory flush
- [ ] Add compaction triggers to `RoomMessageCenter`
- [ ] Background job for periodic compaction check
- [ ] Compaction archive collection with TTL

### Phase 4: Memory Search
- [ ] Implement `services/memory_search_service.py`
- [ ] Create Pinecone index `room-memory`
- [ ] Implement hybrid search with temporal decay
- [ ] Implement MMR re-ranking
- [ ] Integration tests for search accuracy

### Phase 5: Supervisor Integration
- [ ] Wire context assembly into `RoomSupervisorService.create_plan()`
- [ ] Wire context assembly into `RoomSupervisorService.review_step()`
- [ ] Wire context assembly into agent execution
- [ ] End-to-end tests with Supervisor pattern
- [ ] Performance benchmarks

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
