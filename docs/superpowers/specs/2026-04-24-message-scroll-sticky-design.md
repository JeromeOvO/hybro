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
| After hydration + initial anchor, `lastUserSendKey` changes | User sent a new message | Force `scrollToBottom` |
| AI message arrives / streams | `lastUserSendKey` unchanged | Only scroll if `shouldAutoScroll === true`, driven by `contentVersion` |

### Trigger Source — `lastUserSendKey` (not raw message id)

The current send flow uses optimistic insertion: `useSendMessage` first inserts with `tempMessageId`, then calls `replaceMessageId(temp, real)` after POST returns. This means the same user send causes `lastUserMessageId` to change twice (temp insert, then temp→real swap), which would trigger two `scrollToBottom` calls. If the user scrolls up during the HTTP round-trip, the second call would yank them back down.

**Fix:** The comparison key for P1 uses `clientRequestId` (stable across the temp→real swap) instead of the raw message id:

```typescript
// Derive a stable send key that survives temp→real id replacement
function getLastUserSendKey(
  orderedIds: string[],
  entities: Record<string, MessageEntity>,
): string | null {
  for (let i = orderedIds.length - 1; i >= 0; i--) {
    const entity = entities[orderedIds[i]]
    if (entity?.messageType === 'user') {
      return entity.clientRequestId ?? entity.id
    }
  }
  return null
}
```

```typescript
// Inside useMessageScrollAnchoring
const lastUserSendKey = getLastUserSendKey(renderedAnchorIds, getEntityForAnchor)

useEffect(() => {
  if (!hydrated || !didInitialAnchor.current) return
  if (!lastUserSendKey) return
  if (lastUserSendKey === prevLastUserSendKey.current) return

  scrollToBottom({ behavior: 'auto' })
  prevLastUserSendKey.current = lastUserSendKey

  // User just sent a message and we scrolled to bottom.
  // Set shouldAutoScroll = true so subsequent AI streaming follows.
  // Without this, if user was browsing history before sending,
  // shouldAutoScroll would still be false and AI responses would
  // not trigger scroll-follow despite the user now being at bottom.
  setShouldAutoScroll(true)
}, [lastUserSendKey])
```

P2's DOM anchor still uses the current `entity.id` (which may be temp or real) via `data-message-id`. The `lastUserSendKey` is only for P1's change-detection to avoid double-triggering.

### AI Content Streaming

The existing `shouldAutoScroll` + near-bottom detection mechanism handles AI streaming. The hook accepts a `contentVersion` input to detect in-place content updates (streaming AI responses updating the same message entity). Without this, only new message arrivals (changing `orderedIds.length`) would trigger scroll-follow, but streaming content within an existing message would not.

- **Legacy view** passes `useMessageStore.version` as `contentVersion`.
- **Turn-based view** passes turn event count or render version from the existing turn scroll infrastructure.

---

## P2: Initial Anchor on Refresh / Navigation

### Trigger Conditions

- `hydrated === true`
- `didInitialAnchor.current === false` (not yet executed for this room)

Two sub-cases:

1. **`renderedAnchorIds.length > 0`** — normal room with messages. Find last user message, scroll if found.
2. **`renderedAnchorIds.length === 0`** — empty room (new room, no messages yet). Mark initial anchor complete immediately so P1 is ready for the first user send.

### Execution

```typescript
useLayoutEffect(() => {
  if (!hydrated || didInitialAnchor.current) return

  // Empty room: mark anchor complete, P1 will handle future sends
  if (renderedAnchorIds.length === 0) {
    didInitialAnchor.current = true
    prevLastUserSendKey.current = null
    setShouldAutoScroll(true)
    return
  }

  const lastUserMsgId = findLastUserMessageId(renderedAnchorIds, getEntityForAnchor)

  if (lastUserMsgId) {
    const el = scrollContainerRef.current?.querySelector(
      `[data-message-id="${escapeCssIdent(lastUserMsgId)}"]`
    )
    if (!el) return  // DOM not rendered yet, wait for next layout effect
    el.scrollIntoView({ block: 'start', behavior: 'auto' })
  }

  // All three MUST be set synchronously — including when no user message exists
  // (pure system/AI room with messages but no user messages):
  didInitialAnchor.current = true
  prevLastUserSendKey.current = lastUserSendKey ?? null
  setShouldAutoScroll(checkIfNearBottom())
}, [hydrated, roomId, renderedAnchorIds.length, lastUserSendKey])
```

### Key Constraints

- **P2 must initialize `prevLastUserSendKey`** after anchoring. Otherwise P1's effect will see `prevLastUserSendKey === null` on next render and misinterpret it as "user just sent a new message", causing an unwanted `scrollToBottom`.
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

P1 and P2 live in the same hook to share refs and guarantee execution order.

The hook is abstracted over **rendered anchor ids** so it works for both views without coupling to a specific store:

```typescript
interface ScrollAnchoringInput {
  scrollContainerRef: RefObject<HTMLDivElement>
  hydrated: boolean
  roomId: string
  // Abstracted: caller provides the relevant ids and lookup
  renderedAnchorIds: string[]       // Legacy: orderedIds; TurnList: orderedTurnIds
  getEntityForAnchor: (id: string) => { messageType: string; clientRequestId?: string } | undefined
  contentVersion: number            // Legacy: useMessageStore.version; TurnList: turn event/render version
}

function useMessageScrollAnchoring(input: ScrollAnchoringInput) {
  // Internal state:
  // - didInitialAnchor: RefObject<boolean>
  // - prevLastUserSendKey: RefObject<string | null>
  // - shouldAutoScroll: state + setter
  // - derived: lastUserSendKey via getLastUserSendKey(renderedAnchorIds, getEntityForAnchor)
  // - derived: lastUserMessageId via findLastUserMessageId(renderedAnchorIds, getEntityForAnchor)

  // P2: useLayoutEffect — initial anchor (uses lastUserMessageId for DOM query)
  // P1: useEffect — subsequent user sends (uses lastUserSendKey for change detection)
  // AI streaming: useEffect on contentVersion — scrollToBottom if shouldAutoScroll
  // Reset: roomId change resets didInitialAnchor
}
```

**Legacy view** (`room-messages.tsx`): passes `orderedIds`, `id => entities[id]`, `useMessageStore.version`.

**Turn-based view** (`useTurnScroll.ts`): passes `orderedTurnIds` as `renderedAnchorIds`, turn event/render version as `contentVersion`, and a `getEntityForAnchor` lookup that derives the required shape from turn data:

```typescript
// TurnList's getEntityForAnchor — derives from turn log, not message store
// Does NOT assume turnId === userMessageId (optimistic turns use clientRequestId as turnId)
getEntityForAnchor: (turnId: string) => {
  const turnLog = turnEventStore.turnLogs.get(turnId)
  if (!turnLog) return undefined
  const startEvent = turnLog.getEvents().find(e => e.type === 'turn_started')
  return {
    messageType: 'user',  // every turn is rooted in a user action
    clientRequestId: startEvent?.clientRequestId ?? turnId,
  }
}
```

This avoids assuming all `turnId`s equal `userMessageId` — during optimistic sends, the turn is keyed by `clientRequestId` (see `useTurnEventStore.createOptimisticTurn`). The `clientRequestId` from `turn_started` event is the stable key that survives both the optimistic and real phases.

**`roomId` source for TurnList:** `useTurnScroll` reads `roomId` from `useMessageStore(s => s.roomId)`. This avoids adding a new prop to `TurnList` — the message store already tracks the current room.

**`contentVersion` source for TurnList:** The current `useTurnEventStore.append()` updates the store-level `turnLogs` Map for new turns, but in-flight slot/content events on an existing turn only mutate the `TurnEventLog` instance internally — Zustand's top-level state does not change, so `orderedTurnIds.length` stays constant. This means streaming AI content within an existing turn produces no version signal the hook can react to.

`useTurnScroll` must maintain a local `contentVersion` counter by subscribing to the active turn's `TurnEventLog`:

```typescript
// Inside useTurnScroll
const [contentVersion, setContentVersion] = useState(0)

useEffect(() => {
  const activeTurnId = orderedTurnIds[orderedTurnIds.length - 1]
  if (!activeTurnId) return

  const turnLog = turnEventStore.turnLogs.get(activeTurnId)
  if (!turnLog) return

  const unsubscribe = turnLog.subscribe(() => {
    setContentVersion(v => v + 1)
  })
  return unsubscribe
}, [orderedTurnIds[orderedTurnIds.length - 1]])
```

This targets only the latest turn's event log — no global subscription, minimal re-renders. The local `contentVersion` is passed into `useMessageScrollAnchoring` as the `contentVersion` input.

P2's `useLayoutEffect` dependency includes `renderedAnchorIds.length` so it retries when TurnList DOM renders after turn store hydration.

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
| `src/hooks/useMessageScrollAnchoring.test.ts` | **New.** Tests for scroll anchoring behavior. |

**Not changed:**
- `useMessageStore` — no structural or API changes
- `useTurnEventStore` — no structural changes, no new call sites; TurnList existing dependency used for compatibility only
- `useMessageStoreSync` bridge — not touched
- Backend — zero changes

---

## Test Plan

### `message-groups.test.ts` — Grouping function

- System prefix (first messages are non-user) → `userMsgId: null` group
- Consecutive user messages → each becomes its own group
- Normal user → agent grouping
- `agent.relatedMessageId` pointing at older user message (groups by timeline order)
- Empty input → empty array

### `useMessageScrollAnchoring.test.ts` — Scroll behavior

- **P2 initial anchor does not trigger P1 bottom scroll.** After hydration + initial anchor, `prevLastUserSendKey` is initialized, so P1 effect does not fire.
- **temp→real id swap does not cause double scroll.** Simulate: insert user message with `tempId` + `clientRequestId=X`, then replace id to `realId` while keeping `clientRequestId=X`. Assert `scrollToBottom` is called exactly once (on temp insert), not twice (the swap does not change `lastUserSendKey`).
- **AI streaming only follows when near bottom.** Simulate: `contentVersion` increments while `shouldAutoScroll === false`. Assert no `scrollToBottom`. Then set `shouldAutoScroll === true`, increment again, assert `scrollToBottom` fires.
- **Room switch resets anchor state.** Change `roomId`, verify `didInitialAnchor` resets and P2 re-executes for the new room.
- **No user messages in room.** Room with only system/AI messages: P2 marks `didInitialAnchor = true` without scrolling, P1 activates normally for future user sends.
- **Empty room first send triggers P1.** Hydrate with empty `renderedAnchorIds`, verify `didInitialAnchor = true` immediately. Then simulate first user message arriving: `lastUserSendKey` changes from `null`, P1 fires `scrollToBottom` + `setShouldAutoScroll(true)`.

### `OrchestraTurn` / render tests

- TurnList sticky wrapper renders `data-message-id` attribute matching `turnLog.turnId`.
- Legacy sticky wrapper renders `data-message-id` attribute matching `group.userMsgId`.

---

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Sticky user message overlaps with expand/collapse controls | High if not addressed | Relocate controls as specified above |
| Very long sticky user message blocks too much viewport | Low (most user messages are short) | Monitor; add clamp in follow-up if needed |
| `groupMessagesByUserTurn` diverges from `build-turns.ts` causing inconsistency | Medium | Comment documents divergence; tests cover edge cases; long-term both views migrate to message-derived TurnViewModel |
| `useLayoutEffect` for P2 fires before message DOM is rendered | Low | Guard on `el` existence; effect re-runs on `renderedAnchorIds.length` changes |
| P1 false positive: `lastUserSendKey` changes due to message deletion/reorder, not user send | Low | Acceptable — scrolling to bottom on the rare deletion case is not harmful |
| `clientRequestId` missing on hydrated messages (loaded from DB, not sent in this session) | Low | `getLastUserSendKey` falls back to `entity.id` when `clientRequestId` is undefined — only active-session sends need the stable key |
| TurnList DOM not yet rendered when P2 fires (turn store hydrates after message store) | Medium | P2 depends on `renderedAnchorIds.length`; TurnList passes `orderedTurnIds` so effect retries when turns arrive |
