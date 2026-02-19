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

| Source                                                                                                                 | Key Lessons Applied                                               |
| ---------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| [Manus Blog: Context Engineering](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus) | KV-cache optimization, lossless compaction, file system as memory |
| [Lance Martin: Context Engineering in Manus](https://rlancemartin.github.io/2025/10/15/manus/)                         | Compaction vs compression, context isolation, context offloading  |
| [Peak's Slides (Google Drive)](https://drive.google.com/file/d/1QGJ-BrdiTGslS71sYH4OJoidsry3Ps9g/view)                 | Detailed compaction strategy, sub-agent context sharing           |
| [OpenClaw Multi-Agent](https://docs.openclaw.ai/concepts/multi-agent)                                                  | Agent isolation, workspace-per-agent, session routing             |
| [OpenClaw Context](https://docs.openclaw.ai/concepts/context)                                                          | Token budgeting, system prompt structure, tool schema costs       |
| [OpenClaw Memory](https://docs.openclaw.ai/concepts/memory)                                                            | Daily logs + MEMORY.md, vector search                             |

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

    # Conversation history (mix of full and compact representations)
    conversation_history: list[ConversationTurn] = []
    max_history_turns: int = 100  # Total turns to keep (full + compact)

    # Room-level learned facts (extracted from conversations)
    room_facts: list[RoomFact] = []

    # Agent interaction patterns
    agent_success_history: dict[str, AgentSuccessRecord] = {}

    # Metadata
    last_activity_at: datetime
    total_messages: int = 0
    total_compactions: int = 0  # Number of compaction operations performed


class ConversationTurn(BaseModel):
    """
    Single turn in conversation history.

    Supports two representations:
    - FULL: Complete content in context (for recent turns)
    - COMPACT: Pointer to stored content (for older turns, lossless)
    """
    turn_id: str
    role: Literal["user", "agent", "supervisor"]
    agent_id: str | None = None
    agent_name: str | None = None
    timestamp: datetime

    # Representation mode
    representation: Literal["full", "compact"] = "full"

    # FULL: actual content (None when compact)
    content: str | None = None

    # COMPACT: pointer to storage (None when full)
    content_ref: ContentReference | None = None

    # Content metadata
    content_type: Literal["text", "tool_result", "agent_response", "file"] = "text"

    # For agent turns
    task_description: str | None = None
    step_id: str | None = None
    was_successful: bool = True

    # Token estimates
    estimated_tokens_full: int = 0  # Tokens when expanded
    estimated_tokens_compact: int = 20  # Tokens as pointer (~20-50)

    @property
    def estimated_tokens(self) -> int:
        """Current token cost based on representation."""
        return self.estimated_tokens_compact if self.representation == "compact" else self.estimated_tokens_full

    def to_context_string(self) -> str:
        """Render turn for context window."""
        if self.representation == "full":
            return f"[{self.role.upper()}] {self.content}"
        else:
            return f"[{self.role.upper()}] {self.content_ref.to_compact_string()}"


class ContentReference(BaseModel):
    """
    Pointer to full content in storage.

    This is the key to LOSSLESS compaction - we never delete content,
    just replace it with a pointer that can be dereferenced on demand.
    """
    storage_type: Literal["mongodb", "file", "url"]

    # MongoDB reference
    collection: str | None = None
    document_id: str | None = None

    # File reference
    file_path: str | None = None

    # URL reference
    url: str | None = None

    # Metadata
    content_hash: str | None = None
    created_at: datetime

    def to_compact_string(self) -> str:
        """Generate compact representation for context."""
        if self.storage_type == "mongodb":
            return f"[Stored: db/{self.collection}/{self.document_id}]"
        elif self.storage_type == "file":
            return f"[Stored: file/{self.file_path}]"
        elif self.storage_type == "url":
            return f"[Source: {self.url}]"
```

**Storage**: MongoDB `room_memories` collection

**Key Design Points**:

1. **Lossless**: Compacted turns retain pointers to full content
2. **On-demand expansion**: Agent can request full content when needed
3. **Token-aware**: Track both full and compact token costs

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

```python
class ConversationTurn(BaseModel):
    """Single turn in conversation history."""
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
    estimated_tokens_full: int = 0  # Tokens if expanded
    estimated_tokens_compact: int = 0  # Tokens as pointer (~20-50)


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

### 6.3 Compaction Process (Lossless)

```python
async def compact_room_memory(room_id: str) -> CompactionResult:
    """
    Compact older conversation turns by replacing full content with pointers.

    IMPORTANT: This is LOSSLESS. Original content is preserved in storage
    and can be retrieved on demand.

    Process:
    1. Load room memory
    2. Identify turns to compact (older than preserve_recent_turns)
    3. For each turn: store full content -> replace with pointer
    4. Update room memory with compact representations
    """
    room_memory = await load_room_memory(room_id)
    preserve_count = compaction_config.preserve_recent_turns

    # Identify turns to compact (keep recent turns in full)
    turns_to_compact = [
        t for t in room_memory.conversation_history[:-preserve_count]
        if t.representation == "full"
    ]

    if not turns_to_compact:
        return CompactionResult(compacted_count=0, tokens_saved=0)

    tokens_saved = 0

    for turn in turns_to_compact:
        # 1. Store full content in MongoDB
        content_doc = await store_full_content(
            room_id=room_id,
            turn_id=turn.turn_id,
            content=turn.content,
            content_type=turn.content_type,
        )

        # 2. Create reference pointer
        turn.content_ref = ContentReference(
            storage_type="mongodb",
            collection="conversation_content",
            document_id=content_doc.id,
            content_hash=hash_content(turn.content),
            created_at=utcnow(),
        )

        # 3. Calculate token savings
        tokens_saved += turn.estimated_tokens_full - turn.estimated_tokens_compact

        # 4. Switch to compact representation
        turn.content = None  # Remove full content from context
        turn.representation = "compact"

    await save_room_memory(room_memory)

    return CompactionResult(
        compacted_count=len(turns_to_compact),
        tokens_saved=tokens_saved,
    )
```

### 6.4 Content Expansion (On-Demand Retrieval)

```python
async def expand_turn_content(turn: ConversationTurn) -> str:
    """
    Expand a compacted turn back to full content.

    Called when the agent needs the full content of a previously compacted turn.
    This is the key to LOSSLESS compaction - we can always get the original back.
    """
    if turn.representation == "full":
        return turn.content

    if not turn.content_ref:
        raise ValueError(f"Compact turn {turn.turn_id} missing content reference")

    ref = turn.content_ref

    if ref.storage_type == "mongodb":
        doc = await db.conversation_content.find_one({"_id": ref.document_id})
        return doc["content"]

    elif ref.storage_type == "file":
        # For file-based content (e.g., agent sandbox files)
        async with aiofiles.open(ref.file_path, "r") as f:
            return await f.read()

    elif ref.storage_type == "url":
        # For web content, re-fetch if needed
        async with httpx.AsyncClient() as client:
            response = await client.get(ref.url)
            return response.text


async def expand_turns_for_context(
    turns: list[ConversationTurn],
    query: str | None = None,
) -> list[ConversationTurn]:
    """
    Selectively expand turns that are relevant to the current query.

    Strategy:
    1. Recent turns: always expand (already full)
    2. Older turns: expand only if relevant to current query
    """
    expanded = []

    for turn in turns:
        if turn.representation == "full":
            expanded.append(turn)
        elif query and is_relevant_to_query(turn, query):
            # Expand relevant compacted turns
            content = await expand_turn_content(turn)
            expanded_turn = turn.model_copy()
            expanded_turn.content = content
            expanded_turn.representation = "full"
            expanded.append(expanded_turn)
        else:
            # Keep as compact pointer
            expanded.append(turn)

    return expanded
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

# Index for efficient retrieval
# db.conversation_content.create_index([("room_id", 1), ("turn_id", 1)])
```

### 6.7 Compaction vs Summarization: When to Use Each

| Scenario             | Approach                   | Rationale                                                |
| -------------------- | -------------------------- | -------------------------------------------------------- |
| Recent turns (< 10)  | Keep FULL                  | Agent needs details for current task                     |
| Older turns (10-50)  | COMPACT (pointer)          | Can expand on demand if needed                           |
| Very old turns (50+) | COMPACT + optional summary | Summary for quick overview, full content still available |

**Note**: Even when we generate a summary for very old content, we **never delete** the original. The summary is an additional index, not a replacement.

```python
class CompactedTurnWithSummary(BaseModel):
    """For very old turns, we can add a summary while keeping the full content."""
    turn: ConversationTurn  # Compact representation with pointer
    summary: str | None = None  # Optional summary for quick context
    summary_generated_at: datetime | None = None
```

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

| Parameter      | Env Variable                   | Default | Description                     |
| -------------- | ------------------------------ | ------- | ------------------------------- |
| Vector weight  | `MEMORY_SEARCH_VECTOR_WEIGHT`  | 0.7     | Weight for semantic similarity  |
| Keyword weight | `MEMORY_SEARCH_KEYWORD_WEIGHT` | 0.3     | Weight for BM25 matching        |
| Half-life      | `MEMORY_SEARCH_HALF_LIFE_DAYS` | 30      | Days for score to decay 50%     |
| MMR lambda     | `MEMORY_SEARCH_MMR_LAMBDA`     | 0.7     | Diversity vs relevance tradeoff |
| Max results    | `MEMORY_SEARCH_MAX_RESULTS`    | 10      | Maximum results returned        |

---

## 9. Data Models Summary

### 9.1 New Collections

| Collection             | Purpose                                          | Indexes                             |
| ---------------------- | ------------------------------------------------ | ----------------------------------- |
| `room_memories`        | Room conversation history (full + compact turns) | `room_id` (unique)                  |
| `conversation_content` | **Full content storage for compacted turns**     | `room_id`, `turn_id`, `document_id` |
| `user_memories`        | User preferences + patterns                      | `user_id` (unique)                  |
| `agent_memories`       | Agent performance history                        | `agent_id` (unique)                 |
| `room_facts`           | Extracted durable facts                          | `room_id`, `created_at`             |

### 9.2 Model Files to Create

```
models/
├── context.py          # SessionContext, TokenBudget
├── memory.py           # (extend existing) RoomMemory, UserMemory, AgentMemory
├── compaction.py       # ConversationTurn, ContentReference, StoredContent
└── search.py           # MemorySearchConfig, MemorySearchResult
```

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
        "full_turns": full_turn_count,
        "compact_turns": compact_turn_count,
        "cache_prefix_tokens": prefix_tokens,
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
6. **Supervisor integration**: Context assembly for planning, review, and synthesis phases
7. **Horizontal scalability**: All state in MongoDB/Pinecone, no in-memory dependencies

The design draws from production lessons at Manus (lossless compaction, KV-cache optimization) and OpenClaw (multi-layer memory, hybrid search) while adapting to Hybro's multi-agent A2A architecture and upcoming Supervisor pattern.

---

## 17. Design Review & Gap Analysis

### 17.1 Strengths

| Aspect                     | Assessment                                                  |
| -------------------------- | ----------------------------------------------------------- |
| **Scalability**            | ✅ All state in MongoDB/Pinecone; no in-memory dependencies |
| **Supervisor Integration** | ✅ Clear integration points for planning, review, synthesis |
| **KV-Cache Optimization**  | ✅ Stable prefix + append-only suffix pattern               |
| **Configuration**          | ✅ All tunable params via environment variables             |
| **Lossless Compaction**    | ✅ Pointer-based, original content always retrievable       |

### 17.2 Identified Gaps & Mitigations

| Gap                                | Risk                                                | Mitigation                                                        |
| ---------------------------------- | --------------------------------------------------- | ----------------------------------------------------------------- |
| **No cross-room memory sharing**   | Agents can't learn from interactions in other rooms | Phase 2: Add optional `shared_facts` collection with user consent |
| **Pinecone dependency for search** | Single point of failure for memory search           | Fallback to MongoDB text search if Pinecone unavailable           |
| **Token estimation accuracy**      | Budget allocation may be off                        | Use tiktoken for accurate counts; add 10% buffer                  |
| **Storage growth**                 | Full content storage grows unbounded                | TTL policy on `conversation_content`; archive old rooms           |
| **Expansion latency**              | Fetching full content adds latency                  | Cache recently expanded content; batch expansions                 |

### 17.3 Comparison with Reference Systems

| Feature                | Manus                        | OpenClaw               | Hybro (This Design)                     |
| ---------------------- | ---------------------------- | ---------------------- | --------------------------------------- |
| Memory layers          | File system (sandbox)        | Daily logs + MEMORY.md | Session + Room + User + Agent           |
| Compaction             | **Lossless (pointer-based)** | Summarization          | **Lossless (pointer-based)**            |
| Full content storage   | Sandbox filesystem           | N/A                    | MongoDB `conversation_content`          |
| On-demand expansion    | ✅ (file read)               | N/A                    | ✅ (DB fetch)                           |
| Search                 | glob + grep                  | Hybrid (vector + BM25) | Hybrid (vector + BM25 + temporal decay) |
| KV-cache optimization  | ✅ Explicit                  | ✅ Implicit            | ✅ Explicit (stable prefix pattern)     |
| Multi-agent awareness  | Sub-agent isolation          | Per-agent isolation    | Peer awareness injection                |
| Supervisor integration | Planner + Executor           | N/A                    | ✅ Planning, review, synthesis          |

### 17.4 Open Questions

1. **Memory retention policy**: How long to keep compaction archives? (Proposed: 90 days TTL)
2. **Cross-agent memory**: Should agents share learned facts? (Proposed: Opt-in per room)
3. **Memory search scope**: Search room-only or include user memory? (Proposed: Room-first, user as fallback)
4. **Compaction frequency**: Background job vs on-demand? (Proposed: On-demand after synthesis, background cleanup)

### 17.5 Dependencies on Other Systems

| Dependency         | Status      | Notes                                                      |
| ------------------ | ----------- | ---------------------------------------------------------- |
| Supervisor Pattern | 📋 Planned  | Context assembly integrates with planning/review/synthesis |
| Pinecone           | ✅ Existing | Reuse `agentmatch` infra, add `room-memory` index          |
| MongoDB            | ✅ Existing | Add new collections with indexes                           |
| OpenAI             | ✅ Existing | Embeddings + compaction summarization                      |

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

- [ ] Create `models/context.py` with `SessionContext`, `TokenBudget`
- [ ] Create `models/compaction.py` with `ConversationTurn`, `ContentReference`, `StoredContent`
- [ ] Create `models/search.py` with `MemorySearchConfig`, `MemorySearchResult`
- [ ] Extend `models/memory.py` with `UserMemory`, `AgentMemory`, `RoomFact`
- [ ] Add env variables to `config/settings.py`
- [ ] Create MongoDB `conversation_content` collection with indexes
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
