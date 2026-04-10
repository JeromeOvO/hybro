# Agent Matching & Dispatch Pipeline Redesign

> **Status**: Draft v3
> **Date**: 2026-04-09
> **Scope**: Refactor agent selection + dispatch into a two-stage pipeline with clear interfaces

## Problem Statement

The current agent matching → dispatch pipeline has several issues:

1. **LLM Routing is slow and redundant**: `analyze_message_routing()` (~10s) performs agent selection + strategy decision via LLM. This duplicates the supervisor's `decide_next()` in Ultimate mode, and adds unnecessary latency in Fast mode.
2. **Skill filter is brittle**: Hardcoded 14-keyword map in `_filter_by_skills()` has narrow coverage and doesn't leverage agent metadata (descriptions, tags, I/O modes).
3. **Two divergent debate implementations**: `debate_service.py` (Fast path) and `SupervisorExecutor._build_debate_task()` (Ultimate path) have duplicate prompt templates, different truncation logic, and no shared abstraction.
4. **Strategy decision is misplaced**: Strategy (single/parallel/sequential) is decided during matching, but it's a dispatch concern.
5. **No score-based filtering**: Pinecone vector scores are used for ranking but not for quality cutoff.

## Design Overview

Split the pipeline into two independent stages with a clean data contract between them:

```
┌──────────────────────────────────────────────────────────────┐
│  Stage 1: SCOPE RESOLUTION → CANDIDATE POOL                 │
│                                                              │
│  all_agents:                                                 │
│    AgentMatcher (VectorSearch → CapabilityFilter → Ranker)   │
│    Input:  message_text, user_id, debate_mode,               │
│            required_input_modes (from attachments)            │
│    Output: candidate pool (1-5 ranked agents)                │
│                                                              │
│  room_team / saved_group:                                    │
│    Pass-through — full membership becomes candidate pool      │
│                                                              │
│  mentions:                                                   │
│    Bypass — direct dispatch, no pool                          │
└──────────────────────────────┬───────────────────────────────┘
                               │ candidate pool
                               ▼
┌──────────────────────────────────────────────────────────────┐
│  Stage 2: AGENT ASSIGNMENT + DISPATCH                        │
│                                                              │
│  Fast:     LLM decomposer assigns agents from pool to steps  │
│            → StrategyResolver derives strategy from steps     │
│            → QueueExecutor dispatches sequentially            │
│            (SINGLE / SEQUENTIAL / SEQUENTIAL_DEBATE)         │
│                                                              │
│  Ultimate: supervisor selects agents from pool each iteration │
│            → decide_next() loop (unchanged)                  │
│                                                              │
│  Debate:   shared SequentialDebateDispatcher (both paths)    │
└──────────────────────────────────────────────────────────────┘
```

## Stage 1: Matching Pipeline (AgentMatcher)

### Current Flow (to be replaced)

```
agent_selection_service.select_agents_for_message()
  → database_service.query_similar_agents()     # Pinecone vector search
  → _filter_by_skills()                         # 14-keyword hardcoded map
  → _analyze_routing_needs()                    # LLM call (~10s) ← REMOVE
    → openai_service.analyze_message_routing()  # Strategy + agent selection
```

### New Flow

```
AgentMatcher.match()
  → Stage 1: VectorSearch (Pinecone, unchanged)
  → Stage 2: CapabilityFilter (enhanced, deterministic)
  → Stage 3: ScoreRanker (new, composite scoring + cutoff)
```

### 1.1 VectorSearch

Reuses `database_service.query_similar_agents()` **without changing its signature** (`list[Agent]`).

The existing method is consumed by other online paths (`agent_resolver_service.py:152`, `agent_service.py:407`), so its return type is not changed. Instead, `AgentMatcher` adds a **new** companion method that returns scores:

```python
# New method in database_service — does NOT replace query_similar_agents()
async def query_similar_agents_with_scores(
    self,
    query_text: str,
    count: int = 5,
    excluded_agent_ids: list[str] | None = None,
    active_only: bool = True,
    user_id: str | None = None,
) -> list[tuple[Agent, float]]:
    """Like query_similar_agents, but also returns Pinecone similarity scores."""
    ...
```

Implementation note: the Pinecone response already contains `.score` on each match (line ~296 of `database_service.py`). The new method preserves this score through the agent-fetch and filtering pipeline instead of discarding it.

**Additional adjustments:**
- Increase `top_k` from 10 to 15-20 to give the filter more candidates to work with
- Continue to filter by `active_only=True`, `excluded_agent_ids`, `user_id` visibility

### 1.2 CapabilityFilter (enhanced)

Replace the hardcoded `task_skill_map` with multi-dimensional matching against agent metadata.

**Scoring dimensions:**

| Dimension | Weight | Matching Logic | Data Source |
|-----------|--------|----------------|-------------|
| Skill name relevance | 0.35 | Tokenized overlap: message tokens ∩ skill.name tokens (case-insensitive, stemmed) | `AgentSkill.name` |
| Skill description relevance | 0.25 | Tokenized overlap: message tokens ∩ skill.description tokens | `AgentSkill.description` |
| Tag match | 0.25 | Exact match: message keywords ∩ skill.tags | `AgentSkill.tags` |
| I/O mode compatibility | 0.15 | Binary: does the agent support files at all? Uses the same coarse `supports_files` model as `_build_message_parts()` (line 819). Only applied when caller provides `required_input_modes` (non-None = message has attachments). When absent, this dimension scores 1.0 (no penalty). | `AgentCard.defaultInputModes/outputModes` |

**I/O mode scoring detail:**

`required_input_modes` is a **boolean signal carried as a list of actual MIME types** from resolved attachments (e.g., `["image/png", "application/pdf"]`). The caller derives them after `_resolve_and_apply_attachments()` runs. The list is non-None when any attachment exists, None for text-only messages.

**The matcher uses the same coarse binary model as the current runtime.** The runtime's `_build_message_parts()` (line 819-826) computes a single `supports_files` boolean — if the agent matches ANY file capability, ALL attachments are sent regardless of their individual MIME types. The matcher mirrors this:

- `required_input_modes` is non-None (message has attachments):
  - Agent's `default_input_modes` hits any of `FILE_CAPABLE_EXACT` / `FILE_CAPABLE_PREFIXES` / `FILE_CAPABLE_MIMES` → **1.0** (supports files)
  - Otherwise → **0.0** (does not support files)
- `required_input_modes` is None (text-only) → **1.0** (no penalty)

**Why binary, not per-MIME scoring:** The runtime currently sends all attachments to any file-capable agent — an agent declaring only `image/` still receives PDFs at dispatch time. If the matcher scored per-MIME, it would exclude agents that the runtime would actually use, creating a mismatch between pre-filtering and dispatch behavior. The MIME list is preserved in `required_input_modes` for future use if the runtime is tightened to per-MIME dispatch, but the current matcher treats it as a presence flag only.

**Future improvement (out of scope):** Tighten `_build_message_parts()` to per-MIME dispatch (only send attachments whose MIME matches the agent's declared modes). Once that lands, the matcher can switch to per-MIME scoring using the same MIME list already available in `required_input_modes`.

**Capability score computation:**

```python
# --- Module-level constants and helper (top-level, not nested) ---

# Binary file capability check, same logic as _build_message_parts() (line 819-826):
FILE_CAPABLE_EXACT = {"file", "*/*"}
FILE_CAPABLE_PREFIXES = {"image/", "audio/", "video/"}
FILE_CAPABLE_MIMES = {"application/pdf", "application/octet-stream", ...}


def _agent_supports_files(agent: Agent) -> bool:
    """Does the agent support file input at all?
    Mirrors _build_message_parts() supports_files check."""
    agent_modes = set(agent.agent_card.default_input_modes or ["text"])
    if agent_modes & FILE_CAPABLE_EXACT:
        return True
    if agent_modes & FILE_CAPABLE_MIMES:
        return True
    if any(
        any(m.startswith(prefix) for prefix in FILE_CAPABLE_PREFIXES)
        for m in agent_modes
    ):
        return True
    return False


# --- Main scoring function ---

def compute_capability_score(
    message_tokens: set[str],
    agent: Agent,
    required_input_modes: list[str] | None = None,
) -> float:
    """Score 0.0-1.0 based on how well agent capabilities match the message."""
    skills = agent.agent_card.skills or []

    # I/O mode compatibility (independent of skills).
    # Binary check — mirrors _build_message_parts() supports_files (line 819-826).
    # If message has attachments and agent supports ANY file type → 1.0, else → 0.0.
    if required_input_modes:  # non-None = message has attachments
        io_score = 1.0 if _agent_supports_files(agent) else 0.0
    else:
        io_score = 1.0  # No attachments → no penalty

    if not skills:
        # General-purpose agents: baseline + I/O check
        return 0.3 * 0.85 + 0.15 * io_score

    best_skill_score = 0.0
    for skill in skills:
        name_tokens = tokenize(skill.name)
        desc_tokens = tokenize(skill.description or "")
        tags = set(t.lower() for t in (skill.tags or []))

        name_overlap = len(message_tokens & name_tokens) / max(len(name_tokens), 1)
        desc_overlap = len(message_tokens & desc_tokens) / max(len(desc_tokens), 1)
        tag_overlap = len(message_tokens & tags) / max(len(tags), 1)

        skill_score = (
            0.35 * name_overlap +
            0.25 * desc_overlap +
            0.25 * tag_overlap +
            0.15 * io_score
        )
        best_skill_score = max(best_skill_score, skill_score)

    return min(best_skill_score, 1.0)
```

**Key improvements over current `_filter_by_skills()`:**
- No hardcoded keyword map — uses agent's own metadata
- Scores instead of binary include/exclude — preserves ranking signal
- I/O mode awareness — caller-provided, not inferred from message text
- Graceful degradation — agents with no skills get a baseline score (general-purpose fallback)

### 1.3 ScoreRanker (new)

Combine vector similarity and capability scores into a final ranking, then apply smart cutoff.

**Composite score:**

```python
final_score = α * vector_score + β * capability_score
# Default: α=0.6, β=0.4
# Vector score captures semantic relevance (description match)
# Capability score captures functional fit (skills, modes)
```

**Smart cutoff — how many agents to return:**

```python
def select_top_agents(
    ranked: list[ScoredAgent],
    is_debate_mode: bool,
) -> list[ScoredAgent]:
    if not ranked:
        return []

    if is_debate_mode:
        # Debate: need diverse perspectives, 3-5 agents
        above_threshold = [a for a in ranked if a.final_score > DEBATE_THRESHOLD]
        count = max(3, min(len(above_threshold), 5))
        return ranked[:count]

    # Non-debate: quality-driven cutoff
    top = ranked[0]
    # If clear winner (large gap to #2), return single agent
    if len(ranked) >= 2 and (top.final_score - ranked[1].final_score) > GAP_THRESHOLD:
        return [top]

    # Otherwise, return agents above quality threshold (max 3)
    qualified = [a for a in ranked if a.final_score > QUALITY_THRESHOLD]
    return qualified[:3] if qualified else [ranked[0]]
```

**Configurable thresholds (env vars with sensible defaults):**

| Threshold | Default | Purpose |
|-----------|---------|---------|
| `MATCH_DEBATE_THRESHOLD` | 0.3 | Minimum score for debate participants |
| `MATCH_GAP_THRESHOLD` | 0.15 | Score gap that indicates a clear winner |
| `MATCH_QUALITY_THRESHOLD` | 0.4 | Minimum score for non-debate selection |
| `MATCH_VECTOR_WEIGHT` | 0.6 | Weight for vector similarity (α) |
| `MATCH_CAPABILITY_WEIGHT` | 0.4 | Weight for capability score (β) |

### 1.4 Interface

```python
@dataclass
class MatchedAgent:
    agent: Agent
    vector_score: float         # 0.0 for non-vector scopes
    capability_score: float
    final_score: float

@dataclass
class MatchResult:
    agents: list[MatchedAgent]       # Sorted by final_score descending
    total_candidates: int            # How many candidates from vector search
    filtered_count: int              # How many passed capability filter

class AgentMatcher:
    """Deterministic agent matching pipeline. No LLM calls."""

    async def match(
        self,
        message_text: str,
        scope: str,                          # "all_agents" only
        user_id: str | None = None,
        is_debate_mode: bool = False,
        required_input_modes: list[str] | None = None,  # From message attachments
    ) -> MatchResult:
        """
        Match agents for a user message. Only used for "all_agents" scope.

        For "all_agents" scope: vector search + capability filter + score ranking.
        Room_team, saved_group, and mentions bypass the matcher entirely.

        Args:
            required_input_modes: Actual MIME types from resolved attachments
                (e.g., ["image/png", "application/pdf"] for a mixed upload).
                Derived by the caller from resolved UserAttachment objects
                after _resolve_and_apply_attachments(), NOT inferred from
                message_text.
        """
        ...
```

**Scope handling — decisive semantics:**

Important distinction: matching produces a **candidate pool**, not the final dispatch list. For Fast mode, `parse_user_message_by_llm()` receives the candidate pool and assigns specific agents to task steps. For Ultimate mode, the supervisor's `decide_next()` selects from the pool each iteration. The pool defines "who is eligible"; decomposition/supervisor decides "who actually works."

| Scope | AgentMatcher? | Candidate pool |
|-------|---------------|----------------|
| `all_agents` | **Yes** — full pipeline | Vector search → CapabilityFilter → ScoreRanker → top-K candidates |
| `room_team` | **No** — pass-through | Full `room.room_agent_set` as candidate pool |
| `saved_group` | **No** — pass-through | Full saved group membership as candidate pool |
| Mentions | **No** — bypass | Mentioned agents dispatched directly (no decomposition) |

**For `room_team` and `saved_group`**: The full membership becomes the candidate pool — no pre-filtering or ranking at the matching stage. The downstream LLM decomposer (`parse_user_message_by_llm()`) or supervisor then decides which agents from this pool are assigned to actual task steps. This matches the current production behavior (`room_services.py:2279-2286` returns full `room_agent_set`, `room_services.py:2336` returns full group membership, then `parse_user_message()` at line 2010 passes the pool to decomposition).

**For `all_agents`**: The AgentMatcher narrows the entire agent catalog down to a relevant candidate pool (typically 1-5 agents). This pool is then passed to decomposition/supervisor, which may use all or a subset.

**Rationale for not filtering `room_team`/`saved_group`**: These represent explicit user intent ("I want these agents eligible"). The decomposer already handles agent-to-task assignment from any pool. Adding a matching layer would duplicate this logic and risk silently removing agents the user chose as eligible.

### 1.5 File Location

New file: `services/agent_matcher.py`

`agent_selection_service.py` becomes a thin facade that delegates to `AgentMatcher`:
- `select_agents_for_message()` → calls `agent_matcher.match()`
- Remove `_filter_by_skills()` and `_analyze_routing_needs()`
- Remove dependency on `openai_service.analyze_message_routing()`

---

## Stage 2: Dispatch Pipeline

### Current Flow

**Fast mode (QueueExecutor):**
```
parse_user_message_by_llm() → decompose into task_steps
  → _generate_agent_messages() → persist RoomAgentMessages
  → QueueExecutor.process_queue() → sequential pop + dispatch
     → if debate: debate_service.inject_short_debate()
  → _emit_unified_summary()
```

**Ultimate mode (SupervisorExecutor):**
```
_prepare_for_supervisor_v2() → store agent_registry, room_config
  → SupervisorExecutor.run()
     → if debate: sequential fast-path (one agent per step)
     → else: decide_next() loop (LLM-driven)
  → synthesize_v2()
```

### New Flow

The key changes:
1. **Strategy is derived at the dispatch entry point**, not in the matching phase
2. **Debate dispatch is unified** into a shared `SequentialDebateDispatcher`
3. **Fast mode gets explicit strategy awareness**

### 2.1 StrategyResolver

Determines the dispatch strategy based on known signals. Runs at the dispatch entry point (in `room_services.py` before delegation to QueueExecutor or SupervisorExecutor).

**Fast mode — strategy derived from decomposition:**

```python
def resolve_strategy_from_decomposition(
    task_steps: list[dict],
    is_debate_mode: bool,
) -> DispatchStrategy:
    """Derive dispatch strategy from LLM decomposition output."""
    if is_debate_mode:
        return DispatchStrategy.SEQUENTIAL_DEBATE

    if len(task_steps) == 1:
        return DispatchStrategy.SINGLE

    # Multi-step tasks are always sequential in Fast mode.
    # Even when steps have no explicit dependencies, the QueueExecutor
    # state machine requires sequential processing for HITL, pause/resume,
    # relay continuation, and cleanup.
    return DispatchStrategy.SEQUENTIAL
```

**Ultimate mode — supervisor-driven (unchanged):**

The supervisor's `decide_next()` already determines strategy dynamically each iteration. No change needed. For debate mode, the existing sequential fast-path applies. The supervisor can dispatch multiple agents in a single step via `asyncio.gather` because it owns its own state machine (trajectory-based, not continuation-based).

**Strategy enum:**

```python
class DispatchStrategy(str, Enum):
    SINGLE = "single"                         # 1 agent, direct dispatch
    SEQUENTIAL = "sequential"                 # N agents, dependency chain
    SEQUENTIAL_DEBATE = "sequential_debate"   # N agents, debate-enriched chain
    SUPERVISOR = "supervisor"                 # Supervisor LLM decides per-step
```

Note: no `PARALLEL` variant for Fast mode. See §2.2 for rationale.

### 2.2 Fast Mode Dispatch: Why No Parallel

**Decision: Fast mode dispatch remains sequential.** No `_dispatch_parallel()` in QueueExecutor.

**Rationale:**

The QueueExecutor's `process_queue()` is a sequential state machine with complex mid-stream transitions:

1. **PAUSED / RELAY_DISPATCHED** (`QueueExecutor.py:322`): When an agent returns a long-running A2A task, the queue saves a single-message continuation (`_save_continuation()` at line 574) containing `current_agent_id`, `remaining_queue`, and `room_id`. This continuation model assumes exactly one in-flight agent at a time.

2. **AWAITING_INPUT / HITL** (`QueueExecutor.py:265`): When an agent requests human input, the queue saves continuation, creates an HITL request with a single `continuation_message_id`, then pauses. Multiple concurrent HITL requests from parallel agents would require a multi-message continuation model that doesn't exist.

3. **Cleanup / cancellation** (`_managed_queue` at line 120): The RAII cleanup cancels remaining messages in the queue and bulk-cancels DB descendants. This assumes a linear dependency chain, not a parallel fan-out.

4. **Related-message chaining** (`_queue_next_messages` at line 707): The queue discovers next messages via `related_message_id`, which forms a tree. Parallel dispatch would require tracking multiple "current" nodes in this tree simultaneously.

**True parallel dispatch requires:**
- Multi-message continuation structure (saves N in-flight states)
- Concurrent HITL request support
- Fan-out/fan-in cleanup semantics
- This is effectively a new executor, not a QueueExecutor modification

**Where parallelism DOES work:**
- **Ultimate mode (SupervisorExecutor)**: The supervisor's trajectory model already supports multi-target DELEGATE actions with `asyncio.gather`. It doesn't use continuation-based pause/resume for individual agents — it uses trajectory checkpointing, which naturally handles multiple in-flight dispatches.

**Performance impact of keeping sequential:**
- The main latency win comes from removing LLM routing (~10s saved in matching). Parallel dispatch within Fast mode would save at most a few seconds of agent response time. This can be revisited as a future enhancement with a dedicated parallel executor.

**Changes to QueueExecutor:**

```
Fast + SINGLE:
  Unchanged. Direct chat path, skip LLM decomposition.

Fast + SEQUENTIAL:
  Unchanged. Current queue behavior is already sequential with
  dependency chaining via related_message_id.

Fast + SEQUENTIAL_DEBATE:
  Use shared SequentialDebateDispatcher (see §2.3).
  Replace debate_service.inject_short_debate() inline calls.
```

The strategy enum is stored in `user_message.extend_info["dispatch_strategy"]` for observability and future use, but in this design iteration, QueueExecutor always processes sequentially regardless of strategy value.

### 2.3 Unified Sequential Debate Dispatcher

Currently, debate prompt injection is duplicated:

| Path | Implementation | Truncation | Injection Point |
|------|---------------|------------|-----------------|
| Fast (QueueExecutor) | `debate_service.inject_short_debate_for_agent_message()` | No truncation | Modifies `task.history[-1].parts[0].root.text` |
| Ultimate (SupervisorExecutor) | `_build_debate_task()` | 3000 chars | Builds new task string |

**Problems:**
- Fast path has no truncation — long responses can blow up context windows
- Different prompt templates (minor wording differences)
- Fast path does a DB read + DB write per injection; Ultimate path is pure string manipulation
- No shared truncation/context window management

**Solution: `SequentialDebateDispatcher`**

Extract a shared debate dispatch abstraction used by both paths:

```python
class SequentialDebateDispatcher:
    """Shared sequential debate dispatch logic for Fast and Ultimate modes."""

    MAX_PRIOR_RESPONSE_CHARS: int = 3000

    @staticmethod
    def build_debate_prompt(
        original_task: str,
        prior_agent_name: str | None,
        prior_response: str | None,
        max_chars: int = 3000,
    ) -> str:
        """Build debate-enriched task prompt.

        First agent: returns original_task unchanged.
        Subsequent agents: injects last agent's response (truncated).
        """
        if not prior_agent_name or not prior_response:
            return original_task

        truncated = prior_response[:max_chars]
        if len(prior_response) > max_chars:
            truncated += f" ... [truncated — full response: {len(prior_response)} chars]"

        return (
            f"YOUR TASK: {original_task}\n\n"
            f"=== RESPONSE FROM PREVIOUS AGENT ({prior_agent_name}) ===\n"
            f"{truncated}\n"
            f"=== END PREVIOUS RESPONSE ===\n\n"
            "DEBATE MODE INSTRUCTIONS:\n"
            "- Review the previous agent's response above\n"
            "- Provide your own perspective — you may agree, disagree, "
            "or build upon their points\n"
            "- Focus on adding value: new insights, alternative viewpoints, "
            "or deeper analysis\n"
            "- Execute your task and deliver concrete results, not just "
            "commentary on the previous response"
        )
```

**Fast path integration:**
- `debate_service.inject_short_debate_for_agent_message()` calls `SequentialDebateDispatcher.build_debate_prompt()` instead of building the prompt inline
- Adds truncation (currently missing)

**Ultimate path integration:**
- `SupervisorExecutor._build_debate_task()` delegates to `SequentialDebateDispatcher.build_debate_prompt()`
- Remove the duplicate implementation

**File location:** `modules/debate_dispatcher.py` (new file, ~50 lines)

### 2.4 Summary Generation (no change)

The coordinator summary path (`_emit_unified_summary()`) remains unchanged:
- 2+ agent responses → `summarize_agent_responses(mode=debate|non_debate)`
- Supervisor path: `synthesize_v2()` result used directly

---

## Integration Points

### room_services.py Changes

**`required_input_modes` threading path:**

`send_message_to_room()` (line 1780) already receives the full `RoomCenterUserMessageRequest` which contains `request.attachments: list[UserAttachmentRequest] | None`. The input-mode derivation happens here — before calling `_resolve_explicit_target_scope()`:

```python
# In send_message_to_room(), AFTER _resolve_and_apply_attachments() (line 1816)
# but BEFORE _resolve_explicit_target_scope() (line 1854/1932):
resolved_attachments = user_message.message_content.attachments  # list[UserAttachment] | None
required_input_modes = self._derive_required_input_modes(resolved_attachments)

# New helper on RoomService:
@staticmethod
def _derive_required_input_modes(
    attachments: list[UserAttachment] | None,
) -> list[str] | None:
    """Derive required agent input modes from resolved attachments.

    Returns None for text-only messages (no mode constraint).

    Called AFTER _resolve_and_apply_attachments() (line 1816), so
    attachments are fully resolved UserAttachment objects with real
    mime_type from the file_uploads collection — NOT raw file_ids.

    Returns the set of actual MIME types present in the attachments.
    The current matcher only uses this as a binary presence flag
    (non-None = has attachments → check supports_files). The MIME
    list is preserved for future per-MIME scoring if the runtime
    dispatch is tightened (see §1.2 I/O mode scoring detail).
    """
    if not attachments:
        return None
    # Return actual MIME types for future per-MIME scoring.
    # Current matcher treats non-None as "has attachments" only.
    # e.g., ["image/png", "application/pdf"] for a mixed upload.
    return list({att.mime_type for att in attachments})
```

**Why return MIME types instead of a plain boolean:**

The current matcher uses `required_input_modes` as a binary presence flag — non-None triggers a `supports_files` check (1.0 / 0.0), matching the runtime's coarse model. However, we preserve the actual MIME list (not just `True`/`False`) so that when the runtime's `_build_message_parts()` is tightened to per-MIME dispatch in the future, the matcher can switch to per-MIME scoring without changing the caller interface.

**`_resolve_explicit_target_scope()` signature change** — add `required_input_modes` parameter:

```python
async def _resolve_explicit_target_scope(
    self,
    room: Room,
    message_text: str,
    target_group: str,
    is_debate_mode: bool,
    sender_user_id: str | None = None,
    required_input_modes: list[str] | None = None,  # NEW
) -> tuple[dict, bool, list] | RoomCenterUserMessageResponse:
```

Both call sites in `send_message_to_room()` (lines 1854 and 1932) pass the pre-computed value:

```python
scope_result = await self._resolve_explicit_target_scope(
    room, message_text, target_group, is_debate_mode,
    sender_user_id=request.user_id,
    required_input_modes=required_input_modes,  # NEW
)
```

Inside `_resolve_explicit_target_scope()`, the `all_agents` branch forwards it to the matcher:

```python
if target_group == "all_agents":
    match_result = await agent_matcher.match(
        message_text, scope="all_agents",
        user_id=sender_user_id, is_debate_mode=is_debate_mode,
        required_input_modes=required_input_modes,
    )
    if not match_result.agents:
        return error_response(...)
    selected = {m.agent.agent_id: m.agent.agent_card.name for m in match_result.agents}
    full_agents = [m.agent for m in match_result.agents]
    return selected, True, full_agents

# room_team and saved_group: unchanged (pass-through, no matching)
```

The `room_team` and `saved_group` branches do NOT use `required_input_modes` — these scopes pass through as-is. I/O mode filtering only applies to the `all_agents` scope where the matcher narrows the catalog.

**`parse_user_message()`** — add strategy derivation after decomposition:
```python
# After LLM decomposition, derive strategy
parsed_result = await self.openai_service.parse_user_message_by_llm(...)
strategy = resolve_strategy_from_decomposition(
    parsed_result.get("task_steps", []),
    is_debate_mode,
)
# Store in user_message.extend_info for observability
# QueueExecutor behavior is unchanged (always sequential), but the
# strategy field enables future parallel execution and analytics
```

### openai_service.py Changes

- `analyze_message_routing()` — mark as deprecated, then remove
- `parse_user_message_by_llm()` — unchanged (still does task decomposition + agent assignment)

### agent_selection_service.py Changes

- `select_agents_for_message()` — delegates to `AgentMatcher.match()`, maps result to existing `AgentSelectionResult` for backward compatibility
- Remove `_filter_by_skills()`, `_analyze_routing_needs()`
- Eventually deprecate `AgentSelectionResult.strategy` (strategy is now a dispatch concern)

---

## Data Flow Summary

### Fast Mode (non-supervisor)

```
User message
  → Scope resolution: build candidate pool
    ├─ all_agents:
    │   → AgentMatcher.match()                    # NEW: deterministic, no LLM
    │     → VectorSearch (Pinecone)
    │     → CapabilityFilter (multi-dimensional)
    │     → ScoreRanker (composite score + cutoff)
    │   → candidate pool (1-5 agents)
    ├─ room_team:  candidate pool = full room_agent_set
    ├─ saved_group: candidate pool = full group membership
    └─ mentions:   bypass pool entirely (direct dispatch)

  → parse_user_message(selected_agent_set=candidate_pool)
    → parse_user_message_by_llm()             # UNCHANGED: task decomposition
    │   LLM receives candidate pool, assigns specific agents to steps.
    │   Not all pool members necessarily get a step.
    → resolve_strategy_from_decomposition()   # NEW: derive from task_steps
    → _generate_agent_messages()              # One RoomAgentMessage per step
    → persist to DB

  → RoomMessageCenter._process_queue_message()
    → QueueExecutor.process_queue()           # Always sequential (unchanged)
      → SINGLE:            dispatch 1 agent
      → SEQUENTIAL:        queue pop one-by-one (current)
      → SEQUENTIAL_DEBATE: SequentialDebateDispatcher   # NEW (unified)
    → _emit_unified_summary()
```

### Ultimate Mode (supervisor)

```
User message
  → Scope resolution: build candidate pool
    (same as Fast — AgentMatcher for all_agents, pass-through otherwise)

  → _prepare_for_supervisor_v2()              # UNCHANGED
    → store candidate pool as agent_registry, room_config

  → RoomMessageCenter._process_supervisor_v2()
    → SupervisorExecutor.run()
      → Supervisor LLM selects from agent_registry each iteration
      → debate: SequentialDebateDispatcher    # NEW (unified prompt)
      → non-debate: decide_next() loop        # UNCHANGED
    → synthesize_v2()
```

---

## File Change Summary

| File | Change Type | Description |
|------|------------|-------------|
| `services/agent_matcher.py` | **New** | `AgentMatcher` class: VectorSearch + CapabilityFilter + ScoreRanker |
| `modules/debate_dispatcher.py` | **New** | `SequentialDebateDispatcher`: shared debate prompt builder (~50 lines) |
| `services/agent_selection_service.py` | **Modify** | Thin facade delegating to `AgentMatcher`; remove `_filter_by_skills`, `_analyze_routing_needs` |
| `services/room_services.py` | **Modify** | Use `AgentMatcher` for `all_agents` scope; add `StrategyResolver`; store strategy in extend_info |
| `modules/QueueExecutor.py` | **Modify** | Use `SequentialDebateDispatcher` for debate (replaces inline `debate_service` call) |
| `modules/SupervisorExecutor.py` | **Modify** | Delegate debate prompt to `SequentialDebateDispatcher` |
| `services/debate_service.py` | **Modify** | Use `SequentialDebateDispatcher.build_debate_prompt()`; add truncation |
| `services/openai_service.py` | **Modify** | Remove `analyze_message_routing()`; keep `parse_user_message_by_llm()` |
| `services/database_service.py` | **Modify** | Add new `query_similar_agents_with_scores()` method (does NOT change existing `query_similar_agents()` signature) |

### Files NOT changed

| File | Reason |
|------|--------|
| `services/room_supervisor_service.py` | Supervisor LLM prompts unchanged |
| `modules/RoomMessageCenter.py` | Entry points unchanged; strategy stored in extend_info but QueueExecutor behavior unchanged |
| `services/openai_service.py` (parse_user_message_by_llm) | Task decomposition LLM unchanged |
| `models/supervisor_v2.py` | AgentProfile already enhanced in previous PR |
| `modules/QueueExecutor.py` (process_queue state machine) | No parallel dispatch — sequential loop and continuation model unchanged |

---

## Migration Strategy

This is a Phase 2 change (per ARCHITECTURE_DESIGN.md: "Extract use-case services in-place, no directory restructuring").

**Rollout plan:**

1. **Step 1**: Add `query_similar_agents_with_scores()` to `database_service.py` (additive, no existing callers affected)
2. **Step 2**: Create `AgentMatcher` + wire into `agent_selection_service` as backward-compatible facade
3. **Step 3**: Create `SequentialDebateDispatcher` + integrate into both debate paths
4. **Step 4**: Add `StrategyResolver` to `room_services.py` (store in extend_info, no behavioral change yet)
5. **Step 5**: Remove `analyze_message_routing()` from `openai_service.py`
6. **Step 6**: Clean up `agent_selection_service.py` (remove dead code)

Each step is independently deployable and testable.

**Backward compatibility:**
- `AgentSelectionResult` interface preserved during transition (see below)
- `query_similar_agents()` signature unchanged — new method is additive
- `debate_service.inject_short_debate_for_agent_message()` preserved but delegates internally
- QueueExecutor behavior unchanged — strategy stored for observability only

**`AgentSelectionResult.strategy` and `.needs_debate` transition:**

After `_analyze_routing_needs()` is removed (Step 5), the facade in `agent_selection_service.py` must still populate `AgentSelectionResult.strategy` and `needs_debate` because `room_services.py` reads them at line 2247-2254:

```python
# room_services.py:2247-2254 — reads strategy + needs_debate (logging only)
logger.info(
    "All Agents mode: Selected %s agents with strategy=%s",
    len(selection_result.agents),
    selection_result.strategy.value,       # ← must be populated
)
if selection_result.needs_debate and not is_debate_mode:  # ← must be populated
    logger.info("All Agents mode: Debate mode suggested")
```

**Transition-period defaults** (in `agent_selection_service.py` facade):

```python
# In the facade's select_agents_for_message(), after AgentMatcher returns:
strategy = (
    RoutingStrategy.SINGLE if len(matched_agents) <= 1
    else RoutingStrategy.PARALLEL  # preserves old log semantics
)
return AgentSelectionResult(
    strategy=strategy,
    agents=[...],
    reasoning="Matched by AgentMatcher pipeline",
    needs_debate=False,  # debate is now a dispatch concern, not matching
)
```

These defaults are safe because:
1. `strategy.value` at line 2250 is only used for logging — no dispatch behavior depends on it
2. `needs_debate` at line 2253 is only used for an informational log line

**Both fields and the two read sites (lines 2247-2254) are removed together in Step 6** when `agent_selection_service.py` is cleaned up and `room_services.py` is updated to use `MatchResult` directly. This is a single atomic change — no orphaned reads or missing defaults.

---

## Testing Strategy

### Unit Tests

| Component | Test Cases |
|-----------|-----------|
| `CapabilityFilter` | Skill name matching, description matching, tag matching, I/O mode binary check (file-capable agent + attachments → 1.0, non-file-capable + attachments → 0.0, no attachments → 1.0), general-purpose agent baseline score, empty skills handling |
| `ScoreRanker` | Composite score calculation, debate mode count (3-5), gap threshold single-winner detection, quality threshold cutoff, all below threshold → fallback to first |
| `StrategyResolver` | Single step → SINGLE, multi-step → SEQUENTIAL, debate → SEQUENTIAL_DEBATE |
| `SequentialDebateDispatcher` | First agent gets raw task, subsequent agents get enriched prompt, truncation at 3000 chars, empty prior responses |
| `AgentMatcher.match()` | End-to-end: all_agents scope, debate mode, no candidates, with/without required_input_modes |
| `query_similar_agents_with_scores()` | Returns (Agent, score) pairs, preserves Pinecone ordering, respects filters |

### Integration Tests

| Scenario | Validation |
|----------|-----------|
| Fast + SEQUENTIAL_DEBATE | Agents see prior responses via shared SequentialDebateDispatcher |
| Ultimate + debate | Uses same shared debate prompt (same as Fast) |
| all_agents → AgentMatcher | Replaces old LLM routing, returns ranked agents |
| room_team / saved_group | Bypass matcher, full membership enters candidate pool; decomposer/supervisor may use a subset |
| No matching agents | Graceful fallback to best vector match |

---

## Performance Impact

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Matching latency (all_agents) | ~11s (embed + vector + LLM routing) | ~1.5s (embed + vector + filter) | **-85%** |
| Matching latency (room_team) | ~0s (pass-through) | ~0s (unchanged, pass-through) | No change |
| LLM calls per message | 2-3 (embed, routing, decompose) | 1-2 (embed, decompose) | **-1 call** |
| Fast dispatch (multi-agent) | Sequential | Sequential (unchanged) | No change (see §2.2) |
| Debate prompt quality | No truncation (Fast) | 3000 char truncation (both) | **Consistent** |

---

## Resolved Design Decisions

| Decision | Resolution | Rationale |
|----------|-----------|-----------|
| room_team / saved_group scope | Pass-through as candidate pool, no matching-stage filtering | User-explicit intent defines the eligible pool; downstream decomposer/supervisor selects who actually works from this pool |
| Fast mode parallel dispatch | Not implemented | QueueExecutor continuation model (single in-flight agent) cannot support concurrent PAUSED/HITL states; main latency win comes from removing LLM routing |
| `query_similar_agents()` return type | Add new method, keep old | Existing callers (`agent_resolver_service`, `agent_service`) depend on `list[Agent]` return type |
| I/O mode in matcher | Caller-provided via `required_input_modes` (MIME list used as binary presence flag); matcher checks `supports_files` (1.0 / 0.0), matching the runtime's coarse model | Matcher cannot infer input types from message text; binary model mirrors `_build_message_parts()` to avoid pre-filter / dispatch mismatch; MIME list preserved for future per-MIME scoring |
| `required_input_modes` threading | Derived from resolved `UserAttachment` MIME types (after `_resolve_and_apply_attachments()` at line 1816, which merges both `request.attachments` and `request.inline_file_ids`), passed through `_resolve_explicit_target_scope()` to matcher; current matcher treats non-None as "has attachments" binary signal | Attachments are already resolved before scope resolution; returning MIME list (not bool) preserves forward compatibility for per-MIME scoring when runtime dispatch is tightened |
| `AgentSelectionResult` transition | Facade fills `strategy` (SINGLE/PARALLEL by count) and `needs_debate=False` during transition; both fields + read sites removed together in Step 6 | Lines 2247-2254 are logging-only — safe to fill with defaults until the read sites are cleaned up |

## Open Questions

1. **Threshold tuning**: The score thresholds (GAP_THRESHOLD, QUALITY_THRESHOLD, etc.) need empirical tuning. Start with defaults and add observability logging to calibrate.
2. **Tokenizer**: Should `tokenize()` in CapabilityFilter use simple whitespace splitting, or a proper NLP tokenizer (e.g., NLTK word_tokenize)? Whitespace + lowercasing is probably sufficient for skill matching.
3. **Future: parallel dispatch**: If needed, this requires a new parallel executor with multi-message continuation support, not a modification to QueueExecutor. Candidate design: fan-out dispatch with single aggregated HITL/pause handling (one pause pauses all).
