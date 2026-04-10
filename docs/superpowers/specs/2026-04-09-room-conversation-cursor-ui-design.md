# Room Conversation Timeline Redesign

> **Status:** Proposed
> **Date:** 2026-04-09
> **Branch Requirement:** All implementation work for this design must happen on a dedicated branch, not on `main`. This spec was written on `feat/room-cursor-timeline-ui`.

## 1. Summary

This document proposes a redesign of the room conversation UI so it feels closer to Cursor's conversation experience while fitting the existing Hybro data model and room workflow.

The target interaction model is:

- A `single-column event timeline` per user turn.
- `Process visibility first`, but with `compact defaults` so the screen does not explode with low-signal events.
- A `top unified summary` for each completed turn.
- Stronger `multi-agent identity and comparison` within the same turn.
- `HITL` shown as timeline context plus a `bottom interactive panel`.
- `Artifacts/files` shown immediately in the event flow, not as detached afterthoughts.

The core implementation strategy is to keep the existing store and SSE contracts for now, but add a new derived view-model layer that transforms the current flat message list into `turns`, `timeline events`, and `result blocks`.

## 2. Product Decisions Already Made

The following decisions were confirmed during brainstorming and are treated as fixed inputs for this design:

1. Primary goal: `process visualization`.
2. Secondary goal: `multi-agent comparison`.
3. Main layout: `single-column event flow`.
4. Default density: `compact by default`, showing only high-signal events.
5. Completed turns should prioritize a `unified summary` before detailed agent outputs.
6. `HITL` should use a `hybrid model`:
   - the timeline preserves the request/response context,
   - the active input control remains above the composer.
7. `Artifacts/files` should appear `immediately in the timeline`.
8. Work must not happen on `main`.

## 3. Problem Statement

The current room UI works as a message list, but it does not behave like a process-oriented collaborative workspace.

### 3.1 What the current UI does well

- It supports user messages, agent messages, streaming, HITL, attachments, and artifacts.
- It keeps the store normalized.
- It already has distinct lifecycle states for agent messages.

### 3.2 What feels wrong relative to the target experience

The current UI is fundamentally organized as `flat ordered message bubbles`, not as `turns with process`.

That creates several UX problems:

1. A single user request does not feel like one coherent unit.
2. Multi-agent work looks like a sequence of unrelated bubbles instead of a coordinated run.
3. HITL feels split between the transcript and the bottom panel, without a strong shared context.
4. Artifacts appear as attachments to messages, not as meaningful events in the workflow.
5. The UI surfaces too much implementation detail at the bubble level and not enough structure at the turn level.
6. Comparing multiple agent results requires scanning the entire transcript instead of reading one grouped section.

### 3.3 Current code shape driving the problem

The current implementation centers around these facts:

- [`src/components/room-messages.tsx`](/Users/caijiangnan/Desktop/Hybro/hybro-frontend/src/components/room-messages.tsx) renders `orderedIds` directly.
- [`src/components/message-bubble.tsx`](/Users/caijiangnan/Desktop/Hybro/hybro-frontend/src/components/message-bubble.tsx) is responsible for almost all agent lifecycle rendering.
- [`src/components/artifact-list.tsx`](/Users/caijiangnan/Desktop/Hybro/hybro-frontend/src/components/artifact-list.tsx) renders artifacts outside the main bubble flow.
- [`src/components/hitl-inline-reply-form.tsx`](/Users/caijiangnan/Desktop/Hybro/hybro-frontend/src/components/hitl-inline-reply-form.tsx) renders active HITL interaction in a separate panel above the composer.
- [`src/hooks/useRoomMessages.ts`](/Users/caijiangnan/Desktop/Hybro/hybro-frontend/src/hooks/useRoomMessages.ts) exposes message-level selectors, but nothing turn-oriented.
- [`src/stores/message-store/types.ts`](/Users/caijiangnan/Desktop/Hybro/hybro-frontend/src/stores/message-store/types.ts) models normalized messages, but does not expose first-class conversation turns or timeline events.

The problem is therefore not "bad styling"; it is "wrong rendering unit".

## 4. Goals

### 4.1 Primary goals

1. Make each user request feel like one coherent `turn`.
2. Show agent progress as a `continuous event flow`, not as disconnected bubble state.
3. Make multi-agent collaboration readable without losing the single-column transcript feel.
4. Preserve fast scanning by default through compact, high-signal rendering.
5. Promote final understanding with a clear summary-first result section.

### 4.2 Secondary goals

1. Reduce visual duplication between bubble, artifact, and HITL surfaces.
2. Make room conversations feel more like a work session than a generic chat app.
3. Keep the redesign incremental enough to ship without rewriting the backend protocol.

## 5. Non-Goals

This design does not attempt to do the following in the first iteration:

1. Rewrite the backend SSE protocol into a first-class event stream.
2. Replace the normalized message store with a brand-new event store.
3. Change group selection, room settings, or room creation flows.
4. Invent new AI-generated summaries on the client via additional model calls.
5. Build a parallel desktop-style split-pane compare UI as the primary view.

## 6. Proposed Architecture

### 6.1 Core idea

Keep the current `MessageEntity` store intact as the source of truth, but stop rendering it directly as a flat list.

Instead, introduce a new derived layer:

- `TurnViewModel`
- `TimelineEventViewModel`
- `TurnSummaryViewModel`
- `AgentResultViewModel`

The UI will render:

`RoomMessages -> ConversationTimeline -> ConversationTurn[] -> Event Flow + Summary + Agent Results`

### 6.2 Why this is the right scope

This redesign needs enough structure to change the user experience, but not so much scope that it destabilizes hydration, SSE reconciliation, or optimistic updates.

This view-model approach is the right middle ground:

- more powerful than visual-only restyling,
- much safer than rewriting the whole data model.

### 6.3 Proposed new data shapes

The exact TypeScript naming can change, but the design needs the following concepts.

#### `TurnViewModel`

Represents one user-initiated conversation turn.

Suggested fields:

```ts
type TurnStatus = 'active' | 'awaiting_input' | 'completed' | 'failed' | 'partial'

interface TurnViewModel {
  id: string
  roomId: string
  userMessageId: string | null
  userContent: string
  userAttachments: AttachmentData[]
  timestamp: string
  status: TurnStatus
  events: TimelineEventViewModel[]
  summary: TurnSummaryViewModel | null
  agentResults: AgentResultViewModel[]
  activeAgentIds: string[]
}
```

#### `TimelineEventViewModel`

Represents one high-signal event within a turn.

Suggested event kinds:

```ts
type TimelineEventKind =
  | 'user_prompt'
  | 'agent_started'
  | 'agent_progress'
  | 'hitl_requested'
  | 'hitl_answered'
  | 'artifact_emitted'
  | 'agent_completed'
  | 'agent_failed'
```

Each event should include:

- stable id,
- event kind,
- timestamp,
- owning agent identity if applicable,
- compact label,
- optional detailed body,
- optional artifact payload,
- optional HITL payload,
- whether the event is `live`,
- whether the event is hidden in compact mode.

#### `TurnSummaryViewModel`

Represents the single top summary shown before the detailed results.

Suggested fields:

```ts
interface TurnSummaryViewModel {
  sourceAgentId?: string
  sourceAgentName: string
  title: string
  body: string
  confidence?: 'high' | 'medium' | 'low'
}
```

#### `AgentResultViewModel`

Represents a final result block for one agent inside the turn.

Suggested fields:

```ts
interface AgentResultViewModel {
  agentId?: string
  agentName: string
  agentSource?: 'hub' | 'cloud'
  messageId: string
  status: 'completed' | 'failed' | 'awaiting_input'
  content: string
  artifacts: ArtifactData[]
  hitlHistory?: {
    prompt: string
    answer: string
  }[]
}
```

## 7. Turn Construction Rules

### 7.1 Turn boundary rule

The primary turn boundary is the next user message.

All subsequent agent-side activity belongs to that user turn until another user message starts the next turn.

### 7.2 Correlation rule

When available, `relatedMessageId` should be used to improve grouping accuracy, but it should not be the sole source of truth. The grouping algorithm must still work when some older data lacks clean correlation metadata.

### 7.3 Synthetic fallback

If the timeline contains agent-side messages before the first user message, build a synthetic system turn so rendering stays stable instead of dropping those entities.

### 7.4 Active turn rule

The most recent turn is treated as the active turn. It may contain:

- running agents,
- pending HITL,
- in-progress artifacts,
- partial results.

It stays visually more expanded than older turns.

## 8. Summary Selection Rules

The redesign requires a summary-first layout, but we should not invent a new summary generation service in v1.

### 8.1 Summary source priority

Choose the turn summary from existing content using this priority:

1. A supervisor/system summary agent result if one exists.
2. A designated room summary agent if one exists.
3. The highest-priority completed agent result based on explicit product heuristics.
4. The latest completed non-empty agent result as the fallback.

### 8.2 Summary rendering rule

The summary card should show:

- source agent identity,
- a one-line title,
- the first meaningful paragraph or extracted short-form summary body,
- a quick action to jump to detailed agent results.

### 8.3 Important limitation

This summary is a presentation choice, not a new backend feature. The client is selecting an existing result to represent the turn.

## 9. Event Flow Design

### 9.1 Main structure

Each turn should render in this order:

1. User prompt card.
2. Compact event timeline.
3. Unified summary card if available.
4. Agent result stack.

### 9.2 Event rail

The event flow should use a subtle vertical rail with small event nodes, closer to a process log than to chat bubbles.

Every event row should include:

- timestamp or relative freshness,
- agent avatar/pill where relevant,
- concise event label,
- optional expandable details,
- clear live vs terminal state.

### 9.3 Compact default behavior

Because the user explicitly chose compact defaults, the timeline should show only high-signal events by default.

Default-visible events:

- user prompt,
- agent started,
- HITL requested,
- HITL answered,
- artifact emitted,
- agent completed,
- agent failed.

Default-hidden or compressed events:

- repetitive streaming status updates,
- low-signal task status churn,
- repeated append-only artifact updates of the same item.

### 9.4 Expanded process mode

Each turn should provide a `Show process` toggle.

Expanded mode reveals:

- detailed progress/status events,
- intermediate task labels,
- richer timing details,
- per-agent progress states that are hidden in compact mode.

This gives Cursor-like transparency without making the default screen noisy.

## 10. Multi-Agent Comparison Design

### 10.1 Principle

The primary view remains a single-column transcript, but the user still needs a strong sense of which agent did what.

The design should therefore express multi-agent collaboration through identity, grouping, and final result structure, not through a multi-column main layout.

### 10.2 Agent identity in the timeline

Every agent-related event row should carry:

- avatar,
- name,
- source badge if needed (`hub` or `cloud`),
- consistent accent/pill treatment.

This prevents the single-column timeline from becoming anonymous.

### 10.3 Final result comparison block

After the unified summary, render a stacked set of `agent result cards`.

Each card should include:

- agent identity row,
- final status,
- final content preview or full body,
- inline artifacts produced by that agent,
- an expand/collapse affordance for long content.

This keeps the primary reading flow vertical, but still supports comparison because all agent results for one turn are grouped in one place.

### 10.4 Ordering rule for results

Sort agent results by:

1. summary source agent first,
2. completed non-empty results,
3. awaiting-input results,
4. failed results,
5. empty terminal results.

This makes the most useful output appear first without hiding other outcomes.

## 11. HITL Design

### 11.1 Chosen model

The chosen HITL model is `hybrid`.

That means:

- the timeline records that HITL happened,
- the active interaction still lives above the composer.

### 11.2 Timeline behavior

When a HITL request appears, the turn timeline should insert a `hitl_requested` event card that shows:

- requesting agent,
- question/prompt,
- prompt type,
- pending state.

When the user answers, the timeline should update to show:

- the same question,
- the user's answer,
- the handoff back to processing.

The event should remain in history even after completion.

### 11.3 Composer-area behavior

The existing bottom `HitlPanel` remains the actual interaction surface in v1.

However, it should be visually tied to the active turn through:

- clearer agent identity,
- a compact label indicating which turn it belongs to,
- a jump link back to the matching timeline event.

### 11.4 Why this is better than fully inline now

Full inline HITL input inside the transcript is attractive, but it introduces more focus, scroll, keyboard, and optimistic-update complexity than needed for this iteration.

The hybrid model captures the context benefit without destabilizing the interaction model.

## 12. Artifact and File Design

### 12.1 Chosen model

Artifacts/files should appear `immediately in the event flow`.

### 12.2 Timeline behavior

When an artifact is created, the timeline should add an `artifact_emitted` event row that renders the artifact card inline.

Examples:

- images show thumbnail + open action,
- documents show file card + filename,
- audio/video show media-specific preview,
- structured output shows a compact structured block.

### 12.3 Streaming artifact behavior

For append-style or streaming artifacts, the timeline should show a single evolving artifact event rather than many duplicate rows.

That means the event builder needs to merge updates for the same logical artifact id.

### 12.4 Result block behavior

Artifacts that already appeared in the timeline should still appear in the final agent result block if they are part of the final outcome, but the result block should treat them as the final collected output, not as new events.

This dual appearance is acceptable because the two contexts answer different questions:

- timeline: what happened,
- result block: what did this agent produce.

## 13. Visual Direction

The target feel is closer to Cursor than to a standard bubble chat, but it should still fit Hybro's existing design system.

### 13.1 Visual principles

1. Reduce large isolated bubble shapes in the main process flow.
2. Use denser, more editorial spacing for process events.
3. Reserve stronger card treatment for user prompt, summary, and final result blocks.
4. Keep the background calm and neutral so agent identity and state carry the visual emphasis.

### 13.2 Surface hierarchy

Suggested hierarchy:

- `User Prompt Card`
  - strongest turn opener,
  - still visually distinct from agent output.
- `Timeline Event Rows`
  - lighter-weight than cards,
  - log-like structure.
- `Summary Card`
  - visually prominent but compact,
  - the "answer-first" section.
- `Agent Result Cards`
  - medium emphasis,
  - clearly grouped and scannable.

### 13.3 State language

Use state styling consistently:

- running: subtle animated live treatment,
- awaiting input: amber emphasis,
- failed: red emphasis,
- completed: neutral/success emphasis without over-celebration.

## 14. Proposed Component Structure

The exact filenames can shift, but the redesign should move toward the following component split.

### 14.1 View-model layer

Suggested additions:

- `src/lib/room-timeline/types.ts`
- `src/lib/room-timeline/build-turns.ts`
- `src/lib/room-timeline/build-summary.ts`
- `src/lib/room-timeline/build-events.ts`

### 14.2 Presentation layer

Suggested additions:

- `src/components/conversation-timeline.tsx`
- `src/components/conversation-turn.tsx`
- `src/components/turn-user-prompt-card.tsx`
- `src/components/turn-event-timeline.tsx`
- `src/components/timeline-event-row.tsx`
- `src/components/turn-summary-card.tsx`
- `src/components/agent-result-stack.tsx`
- `src/components/agent-result-card.tsx`

### 14.3 Existing files that should shrink in responsibility

- [`src/components/room-messages.tsx`](/Users/caijiangnan/Desktop/Hybro/hybro-frontend/src/components/room-messages.tsx)
  - should become the conversation timeline entry point, not the whole renderer.
- [`src/components/message-bubble.tsx`](/Users/caijiangnan/Desktop/Hybro/hybro-frontend/src/components/message-bubble.tsx)
  - should be decomposed so content rendering is reusable in result cards and summary contexts.
- [`src/components/artifact-list.tsx`](/Users/caijiangnan/Desktop/Hybro/hybro-frontend/src/components/artifact-list.tsx)
  - should become a lower-level artifact renderer used by timeline events and result cards.

## 15. Data Mapping Strategy

### 15.1 Preserve existing store contracts

The normalized `MessageEntity` store remains the source of truth.

This is critical because the following flows already depend on it:

- hydration from DB,
- SSE reconciliation,
- optimistic updates,
- HITL restoration,
- existing per-message updates.

### 15.2 Add derived selectors

Instead of reading `orderedIds` directly in the main renderer, add derived selectors/hooks such as:

- `useConversationTurns()`
- `useActiveTurn()`
- `useTurnById(id)`

These should build stable turn models from the message store.

### 15.3 Dedupe and merge rules

The builder layer must own dedupe rules for:

- repeated progress updates,
- append-style artifacts,
- text-only artifact duplicates that are already represented in message content,
- resolved HITL events that should collapse into one coherent history item.

## 16. Interaction Rules

### 16.1 Default expansion

Because the chosen default is compact:

- older turns render compact by default,
- the active turn is partially expanded by default,
- `Show process` reveals hidden event detail,
- long result cards still support `Show more`.

### 16.2 Scroll behavior

Live activity should keep the current active turn readable without causing aggressive jumpiness.

The redesign should preserve the current "auto-scroll only when near bottom" behavior, but scrolling should anchor around the active turn rather than around raw message insertion.

### 16.3 Jump actions

Useful actions to include:

- jump from summary to detailed results,
- jump from bottom HITL panel to the timeline event,
- jump from artifact event to final result block for that agent if needed.

## 17. Accessibility and Mobile Behavior

### 17.1 Accessibility

The redesign must keep strong keyboard and screen-reader support:

- event toggles must be buttons,
- live state must not overwhelm assistive tech with noisy announcements,
- artifact cards must preserve accessible labels,
- HITL jump and action affordances must be keyboard reachable.

### 17.2 Mobile

On mobile:

- keep the single-column layout,
- reduce meta density in the event rail,
- stack summary and agent result content cleanly,
- avoid side-by-side compare layouts.

This is one reason the chosen design keeps comparison vertical rather than relying on columns.

## 18. Implementation Phasing

This document is a design spec, not the execution plan, but the intended rollout order matters.

### Phase 1: Introduce turn view-models

- Build turn/event/result derivation from existing store data.
- Add unit tests for grouping and summary selection.

### Phase 2: Replace flat room message rendering

- Move [`src/components/room-messages.tsx`](/Users/caijiangnan/Desktop/Hybro/hybro-frontend/src/components/room-messages.tsx) to render turns instead of raw ordered message ids.
- Preserve existing auto-scroll and memoization behavior where possible.

### Phase 3: Ship compact event flow

- Add user prompt card, event rail, process toggle, and live event states.

### Phase 4: Add summary and grouped result blocks

- Introduce summary-first section and ordered agent result cards.

### Phase 5: Integrate inline artifact events and HITL history

- Move artifact rendering into event flow.
- Connect bottom HITL panel more explicitly to matching timeline events.

### Phase 6: Polish and regression pass

- Visual polish,
- mobile adjustments,
- accessibility pass,
- performance pass.

## 19. Testing Strategy

The redesign will need tests at the selector and UI levels.

### 19.1 Selector tests

Add tests for:

- turn boundary construction,
- summary source selection,
- artifact merge behavior,
- HITL event history generation,
- ordering of result blocks.

### 19.2 Component tests

Add tests for:

- compact vs expanded event flow,
- active turn rendering,
- summary visibility,
- multi-agent result ordering,
- artifact event rendering,
- pending/resolved HITL display.

### 19.3 Manual verification

Manual QA should cover:

- one agent, one turn,
- multiple agents in one turn,
- streaming response,
- HITL pending then answered,
- artifact generation mid-turn,
- failed agent result,
- empty terminal result,
- mobile viewport.

## 20. Risks and Mitigations

### 20.1 Risk: grouping inaccuracies

If turn grouping is too naive, some agent messages may land in the wrong turn.

Mitigation:

- use clear fallback rules,
- add tests around `relatedMessageId`,
- prefer stable, deterministic grouping over brittle heuristics.

### 20.2 Risk: duplicated content

Artifacts and message content may appear redundant.

Mitigation:

- centralize dedupe rules in the builder layer,
- treat timeline and result sections as distinct contexts.

### 20.3 Risk: oversized component scope

If the redesign is implemented by further enlarging `message-bubble.tsx`, maintainability will get worse.

Mitigation:

- split responsibilities early,
- keep the new event/timeline model outside the legacy bubble component.

### 20.4 Risk: performance regressions

Grouping and event derivation could trigger expensive recalculations.

Mitigation:

- memoize turn builders,
- keep selectors granular,
- avoid rendering hidden detail rows until expanded.

## 21. Recommended Outcome

The recommended path is:

1. Keep the current backend/store contracts.
2. Introduce a dedicated turn/event/result view-model layer.
3. Replace the flat room transcript with a turn-based single-column event timeline.
4. Add summary-first result presentation and grouped multi-agent result cards.
5. Preserve hybrid HITL and immediate artifact events.

This is the smallest change set that can materially move the room experience toward Cursor-style process visibility and multi-agent readability.

## 22. Approval Gate

This design is ready for review.

Implementation should not begin until:

1. this spec is reviewed and approved,
2. any requested spec edits are applied,
3. the implementation plan is written in a follow-up step,
4. implementation continues only on a dedicated non-`main` branch.
