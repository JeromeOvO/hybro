# Design: Message Scroll Anchoring + Sticky User Messages

> **Date:** 2026-04-24
> **Status:** Approved
> **Scope:** hybro-frontend only (zero backend changes)
> **Related:**
> - `docs/PLAN-turn-store-single-writer.md` — turn store stabilization (orthogonal, no interference)
> - `docs/ROOM_TIMELINE_DESIGN.md` — current UI-layer turn model
> - `../hybro-multi-agents-backend/docs/TURN_MODEL_ANALYSIS.md` — long-term Run + Message graph direction

---

## Problem Statement

Three UX issues in the message display area:

1. **P1 — No auto-scroll on send.** After the user sends a message, the content area does not scroll to the bottom.
2. **P2 — Wrong position on refresh/navigation.** When the page is refreshed or the user navigates away and back, messages display starting from the first message instead of the last user message.
3. **P3 — No contextual anchoring while browsing.** When scrolling through history, there is no persistent indicator of which user request the current AI responses belong to.

## Goals

- Fix P1, P2, P3 for both Legacy view (`room-messages.tsx`) and Turn-based view (`TurnList.tsx`).
- Sticky user messages behave like Cursor's content area: the current turn's user message stays pinned at the top of the scroll container, replaced when the next user message scrolls into view.

## Non-Goals

- Changing `useMessageStore` structure or API.
- Changing `useTurnEventStore` structure or adding new call sites.
- Changing `useMessageStoreSync` bridge.
- Any backend changes.

---

## Data Dependency Principle

New experience logic does not add `useTurnEventStore` dependencies.

**Legacy / message-derived path** derives everything from message store:

```
orderedIds + entities
  |-- P1/P2: findLastUserMessageId(orderedIds, entities)
  |           where entities[id]?.messageType === 'user'
  |           -> scrollIntoView / scrollToBottom
  |
  +-- P3: groupMessagesByUserTurn(orderedIds, entities)
           -> [{ userMsgId, childMsgIds }, ...]
           -> each group's user message uses CSS sticky
```

**TurnList remains a short-term compatibility path:**
- Does not change `useTurnEventStore` — no structural changes, no new call sites; existing dependency used for compatibility only.
- Does not change bridge.
- Does not change backend.
- Only adds experience-level fixes (scroll behavior, sticky wrapper) on top of existing `UserInputBlock` / scroll hook layer.
- Long-term should migrate to message-derived `TurnViewModel[]` rendering path.

---

## P1: Auto-Scroll on User Send

### Behavior

| Phase | Trigger | Scroll behavior |
|-------|---------|-----------------|
| Hydration in progress | `hydrated === false` | No scrolling |
| After hydration + initial anchor, `lastUserMessageId` changes | User sent a new message | Force `scrollToBottom` |
| AI message arrives / streams | `lastUserMessageId` unchanged | Only scroll if `shouldAutoScroll === true` (existing logic, unchanged) |

### Trigger Source

Detect whether `lastUserMessageId` changed after hydration. No new store flags, no composer-to-timeline ref/callback.

```typescript
// Inside useMessageScrollAnchoring
useEffect(() => {
  if (!hydrated || !didInitialAnchor.current) return
  if (!lastUserMessageId) return
  if (lastUserMessageId === prevLastUserMessageId.current) return

  scrollToBottom({ behavior: 'auto' })
  prevLastUserMessageId.current = lastUserMessageId
}, [lastUserMessageId])
```

### AI Content Streaming

The existing `shouldAutoScroll` + near-bottom detection mechanism handles AI streaming. Implementation must ensure the hook exposes or internally connects to `messageCount` / `contentVersion` changes so that streaming AI content triggers the existing scroll-follow logic. This does not change the design — just an implementation wiring note.

---

## P2: Initial Anchor on Refresh / Navigation

### Trigger Conditions (all must be true)

- `hydrated === true`
- `orderedIds.length > 0`
- `lastUserMessageId` exists
- `didInitialAnchor.current === false` (not yet executed for this room)

### Execution

```typescript
useLayoutEffect(() => {
  if (!hydrated || orderedIds.length === 0 || didInitialAnchor.current) return

  const lastUserMsgId = findLastUserMessageId(orderedIds, entities)

  if (lastUserMsgId) {
    const el = scrollContainerRef.current?.querySelector(
      `[data-message-id="${escapeCssIdent(lastUserMsgId)}"]`
    )
    if (!el) return  // DOM not rendered yet, wait for next layout effect
    el.scrollIntoView({ block: 'start', behavior: 'auto' })
  }

  // All three MUST be set synchronously:
  didInitialAnchor.current = true
  prevLastUserMessageId.current = lastUserMsgId ?? null
  setShouldAutoScroll(checkIfNearBottom())
}, [hydrated, roomId, orderedIds.length, lastUserMessageId])
```

### Key Constraints

- **P2 must initialize `prevLastUserMessageId`** after anchoring. Otherwise P1's effect will see `prevLastUserMessageId === null` on next render and misinterpret it as "user just sent a new message", causing an unwanted `scrollToBottom`.
- **`setShouldAutoScroll(checkIfNearBottom())`** after initial anchor. If the last user message has long AI replies below it, user is not near bottom — `shouldAutoScroll` must reflect that, otherwise the next AI update will pull the page to the bottom and destroy the P2 position.
- **Room change** resets `didInitialAnchor.current = false`.

### CSS.escape Helper

```typescript
const escapeCssIdent = (value: string) =>
  typeof CSS !== 'undefined' && CSS.escape
    ? CSS.escape(value)
    : value.replace(/"/g, '\\"')
```

### DOM Query Scope

All `querySelector` calls are scoped to `scrollContainerRef.current`, not `document`, to avoid hitting DOM nodes from other rooms or hidden containers.

---

## Shared Hook: `useMessageScrollAnchoring`

P1 and P2 live in the same hook to share refs and guarantee execution order:

```typescript
function useMessageScrollAnchoring({
  scrollContainerRef,
  hydrated,
  roomId,
  orderedIds,
  entities,
}: {
  scrollContainerRef: RefObject<HTMLDivElement>
  hydrated: boolean
  roomId: string
  orderedIds: string[]
  entities: Record<string, MessageEntity>
}) {
  // Internal state:
  // - didInitialAnchor: RefObject<boolean>
  // - prevLastUserMessageId: RefObject<string | null>
  // - shouldAutoScroll: state + setter
  // - derived: lastUserMessageId = findLastUserMessageId(orderedIds, entities)

  // P2: useLayoutEffect — initial anchor
  // P1: useEffect — subsequent user sends
  // Reset: roomId change resets didInitialAnchor
}
```

**Legacy view** (`room-messages.tsx`): imports and calls `useMessageScrollAnchoring`.

**Turn-based view** (`useTurnScroll.ts`): adds equivalent P1 + P2 semantics. Can either import the same hook (if scroll container ref is compatible) or replicate the logic inline with the same contract.

---

## P3: Sticky User Messages

### Core Approach

CSS `position: sticky` on each turn group's user message wrapper. Browser natively handles the pinning and replacement — no JS scroll position tracking needed.

### Legacy View Implementation

**Grouping function** at `src/lib/room-timeline/message-groups.ts` (same directory as `build-turns.ts`):

```typescript
interface MessageTurnGroup {
  userMsgId: string | null
  childMsgIds: string[]
}

function groupMessagesByUserTurn(
  orderedIds: string[],
  entities: Record<string, MessageEntity>,
): MessageTurnGroup[]
```

This is a simple time-ordered grouping, NOT a full TurnViewModel router. It does not replicate `build-turns.ts`'s `relatedMessageId` routing or system turn logic. A comment in the file must state this explicitly.

**Test coverage:**
- System prefix (first messages are non-user)
- Consecutive user messages (each becomes its own group)
- Normal user -> agent grouping
- `agent.relatedMessageId` pointing at an older user message (initial version groups by timeline order; comment documents divergence from `build-turns.ts`)

**Rendering structure:**

```tsx
// room-messages.tsx
{groups.map(group => (
  <div key={group.userMsgId ?? 'system-prefix'} className="turn-group">
    {group.userMsgId && (
      <div
        className="sticky top-0 z-10 bg-background shadow-[0_1px_3px_0_rgb(0_0_0/0.05)]"
        data-message-id={group.userMsgId}
      >
        <MemoizedMessage id={group.userMsgId} />
      </div>
    )}
    {group.childMsgIds.map(id => (
      <MemoizedMessage key={id} id={id} />
    ))}
  </div>
))}
```

The sticky wrapper owns `data-message-id` for P2 anchor targeting. Inner `MemoizedMessage` does not know whether it is sticky.

### Turn-based View Implementation (Short-term Compatibility)

Sticky wrapper goes in `OrchestraTurn`, not inside `UserInputBlock`:

```tsx
// OrchestraTurn.tsx
{userInput && (
  <div
    data-message-id={turnLog.turnId}
    className="sticky top-0 z-10 bg-background shadow-[0_1px_3px_0_rgb(0_0_0/0.05)]"
  >
    <UserInputBlock data={userInput} />
  </div>
)}
```

`UserInputBlock` remains a pure display component — no anchor/id semantics.

### Expand/Collapse Controls Conflict

Both views currently have sticky expand/collapse buttons at the top of the scroll container. These conflict with sticky user messages at `top-0`.

Resolution:
- Sticky user message owns `top-0`.
- Expand/collapse controls **no longer occupy the sticky top track**.
- Preferred: merge into the sticky user message row's top-right corner.
- Alternative: reposition as `absolute top-2 right-2 z-20` floating button within the scroll container.

### Styling

- `bg-background` (shadcn token) — opaque background required so content below doesn't show through. Dark mode auto-adapts.
- Persistent subtle shadow `shadow-[0_1px_3px_0_rgb(0_0_0/0.05)]` in initial version.
- Sticky does not change element width — existing `flex justify-end` + `max-w-[80%]` on user bubbles is unaffected.

### Edge Cases

| Scenario | Handling |
|----------|----------|
| First message is AI/system | Goes into `userMsgId: null` group, no sticky |
| Consecutive user messages | Each becomes its own group |
| Very long user message takes up too much sticky space | Initial version: no clamp. Evaluate after observing real usage. |
| Empty message list | `groups` is empty array, nothing renders |

---

## File Changes Summary

| File | Change |
|------|--------|
| `src/hooks/useMessageScrollAnchoring.ts` | **New.** Shared P1 + P2 hook. |
| `src/lib/room-timeline/message-groups.ts` | **New.** `groupMessagesByUserTurn()` pure function. |
| `src/components/room-messages.tsx` | Import `useMessageScrollAnchoring`. Wrap message rendering in turn groups with sticky user messages. Add `data-message-id` on sticky wrappers. |
| `src/components/turn/TurnList.tsx` | Add P1 + P2 scroll semantics via `useTurnScroll` or `useMessageScrollAnchoring`. |
| `src/components/turn/OrchestraTurn.tsx` | Add sticky wrapper with `data-message-id` around `UserInputBlock`. |
| `src/hooks/turn/useTurnScroll.ts` | Add P1 + P2 equivalent logic (lastUserMessageId detection, initial anchor). |
| `src/lib/room-timeline/message-groups.test.ts` | **New.** Tests for grouping function. |

**Not changed:**
- `useMessageStore` — no structural or API changes
- `useTurnEventStore` — no structural changes, no new call sites; TurnList existing dependency used for compatibility only
- `useMessageStoreSync` bridge — not touched
- Backend — zero changes

---

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Sticky user message overlaps with expand/collapse controls | High if not addressed | Relocate controls as specified above |
| Very long sticky user message blocks too much viewport | Low (most user messages are short) | Monitor; add clamp in follow-up if needed |
| `groupMessagesByUserTurn` diverges from `build-turns.ts` causing inconsistency | Medium | Comment documents divergence; tests cover edge cases; long-term both views migrate to message-derived TurnViewModel |
| `useLayoutEffect` for P2 fires before message DOM is rendered | Low | Guard on `el` existence; effect re-runs on dependency changes |
| P1 false positive: `lastUserMessageId` changes due to message deletion/reorder, not user send | Low | Acceptable — scrolling to bottom on the rare deletion case is not harmful |
