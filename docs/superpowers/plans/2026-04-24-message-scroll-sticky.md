# Message Scroll Anchoring + Sticky User Messages — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix auto-scroll on user send, anchor to last user message on refresh/navigation, and add Cursor-style sticky user messages for both Legacy and Turn-based views.

**Architecture:** A shared `useMessageScrollAnchoring` hook handles P1 (auto-scroll) and P2 (initial anchor) for both views via abstracted `renderedAnchorIds` + `getEntityForAnchor` inputs. P3 (sticky) uses CSS `position: sticky` on per-turn-group wrappers — Legacy view groups messages with a pure function `groupMessagesByUserTurn`; Turn-based view adds a sticky wrapper in `OrchestraTurn`. No store changes, no backend changes.

**Tech Stack:** React 19, Zustand, Vitest, @testing-library/react, CSS sticky, Tailwind

**Spec:** `docs/superpowers/specs/2026-04-24-message-scroll-sticky-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/lib/room-timeline/message-groups.ts` | Create | Pure function: `groupMessagesByUserTurn()` + `escapeCssIdent()` helper |
| `src/lib/room-timeline/message-groups.test.ts` | Create | Tests for grouping function |
| `src/hooks/useMessageScrollAnchoring.ts` | Create | Shared P1 + P2 hook with abstracted anchor ids |
| `tests/unit/hooks/useMessageScrollAnchoring.test.ts` | Create | Scroll behavior tests |
| `src/components/room-messages.tsx` | Modify | Integrate hook + grouped rendering with sticky wrappers |
| `src/hooks/turn/useTurnScroll.ts` | Modify | Add P1/P2 via `useMessageScrollAnchoring` + contentVersion subscription |
| `src/components/turn/OrchestraTurn.tsx` | Modify | Add sticky wrapper with `data-message-id` around UserInputBlock |
| `src/components/turn/TurnList.tsx` | Modify | Relocate expand/collapse control, pass scrollContainerRef |
| `tests/unit/components/turn/OrchestraTurn.test.tsx` | Modify | Add sticky wrapper + data-message-id test |

---

### Task 1: `groupMessagesByUserTurn` pure function + `escapeCssIdent` helper

**Files:**
- Create: `src/lib/room-timeline/message-groups.ts`
- Create: `src/lib/room-timeline/message-groups.test.ts`

- [ ] **Step 1: Write failing tests**

Create `src/lib/room-timeline/message-groups.test.ts`:

```typescript
import { describe, it, expect } from 'vitest'
import { groupMessagesByUserTurn, escapeCssIdent } from './message-groups'
import type { MessageEntity } from '@/stores/message-store'

function entity(id: string, messageType: 'user' | 'agent'): MessageEntity {
  return {
    id,
    messageType,
    displayType: messageType === 'user' ? 'user-bubble' : 'agent-bubble',
    roomId: 'room-1',
    createdAt: Date.now(),
  } as MessageEntity
}

describe('groupMessagesByUserTurn', () => {
  it('returns empty array for empty input', () => {
    expect(groupMessagesByUserTurn([], {})).toEqual([])
  })

  it('groups system prefix (non-user messages before first user message)', () => {
    const entities: Record<string, MessageEntity> = {
      a1: entity('a1', 'agent'),
      a2: entity('a2', 'agent'),
    }
    const result = groupMessagesByUserTurn(['a1', 'a2'], entities)
    expect(result).toEqual([
      { userMsgId: null, childMsgIds: ['a1', 'a2'] },
    ])
  })

  it('groups normal user -> agent sequence', () => {
    const entities: Record<string, MessageEntity> = {
      u1: entity('u1', 'user'),
      a1: entity('a1', 'agent'),
      a2: entity('a2', 'agent'),
    }
    const result = groupMessagesByUserTurn(['u1', 'a1', 'a2'], entities)
    expect(result).toEqual([
      { userMsgId: 'u1', childMsgIds: ['a1', 'a2'] },
    ])
  })

  it('handles consecutive user messages as separate groups', () => {
    const entities: Record<string, MessageEntity> = {
      u1: entity('u1', 'user'),
      u2: entity('u2', 'user'),
      a1: entity('a1', 'agent'),
    }
    const result = groupMessagesByUserTurn(['u1', 'u2', 'a1'], entities)
    expect(result).toEqual([
      { userMsgId: 'u1', childMsgIds: [] },
      { userMsgId: 'u2', childMsgIds: ['a1'] },
    ])
  })

  it('handles system prefix followed by user turn', () => {
    const entities: Record<string, MessageEntity> = {
      a0: entity('a0', 'agent'),
      u1: entity('u1', 'user'),
      a1: entity('a1', 'agent'),
    }
    const result = groupMessagesByUserTurn(['a0', 'u1', 'a1'], entities)
    expect(result).toEqual([
      { userMsgId: null, childMsgIds: ['a0'] },
      { userMsgId: 'u1', childMsgIds: ['a1'] },
    ])
  })

  it('handles multiple user turns', () => {
    const entities: Record<string, MessageEntity> = {
      u1: entity('u1', 'user'),
      a1: entity('a1', 'agent'),
      u2: entity('u2', 'user'),
      a2: entity('a2', 'agent'),
      a3: entity('a3', 'agent'),
    }
    const result = groupMessagesByUserTurn(['u1', 'a1', 'u2', 'a2', 'a3'], entities)
    expect(result).toEqual([
      { userMsgId: 'u1', childMsgIds: ['a1'] },
      { userMsgId: 'u2', childMsgIds: ['a2', 'a3'] },
    ])
  })

  it('groups agent with relatedMessageId by timeline order (documents divergence from build-turns.ts)', () => {
    // build-turns.ts routes by relatedMessageId; this function groups by timeline only.
    // An agent whose relatedMessageId points at u1 still lands in u2's group if it
    // appears after u2 in the timeline.
    const entities: Record<string, MessageEntity> = {
      u1: entity('u1', 'user'),
      a1: entity('a1', 'agent'),
      u2: entity('u2', 'user'),
      a2: { ...entity('a2', 'agent'), relatedMessageId: 'u1' } as MessageEntity,
    }
    const result = groupMessagesByUserTurn(['u1', 'a1', 'u2', 'a2'], entities)
    expect(result).toEqual([
      { userMsgId: 'u1', childMsgIds: ['a1'] },
      { userMsgId: 'u2', childMsgIds: ['a2'] },
    ])
  })

  it('skips missing entities gracefully', () => {
    const entities: Record<string, MessageEntity> = {
      u1: entity('u1', 'user'),
    }
    const result = groupMessagesByUserTurn(['u1', 'missing-id'], entities)
    expect(result).toEqual([
      { userMsgId: 'u1', childMsgIds: ['missing-id'] },
    ])
  })
})

describe('escapeCssIdent', () => {
  it('passes through simple strings', () => {
    expect(escapeCssIdent('abc-123')).toBe('abc-123')
  })

  it('escapes double quotes in fallback mode', () => {
    const original = CSS.escape
    // @ts-expect-error test override
    CSS.escape = undefined
    expect(escapeCssIdent('a"b')).toBe('a\\"b')
    CSS.escape = original
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run src/lib/room-timeline/message-groups.test.ts`
Expected: FAIL — module `./message-groups` does not exist.

- [ ] **Step 3: Write implementation**

Create `src/lib/room-timeline/message-groups.ts`:

```typescript
import type { MessageEntity } from '@/stores/message-store'

export interface MessageTurnGroup {
  userMsgId: string | null
  childMsgIds: string[]
}

/**
 * Groups messages by user turn for sticky rendering.
 *
 * This is a simple time-ordered grouping for CSS sticky layout.
 * It does NOT replicate build-turns.ts's relatedMessageId routing
 * or system turn logic. It groups strictly by timeline order:
 * each user message starts a new group, and all subsequent
 * non-user messages belong to that group until the next user message.
 */
export function groupMessagesByUserTurn(
  orderedIds: string[],
  entities: Record<string, MessageEntity>,
): MessageTurnGroup[] {
  if (orderedIds.length === 0) return []

  const groups: MessageTurnGroup[] = []
  let current: MessageTurnGroup | null = null

  for (const id of orderedIds) {
    const entity = entities[id]
    const isUser = entity?.messageType === 'user'

    if (isUser) {
      current = { userMsgId: id, childMsgIds: [] }
      groups.push(current)
    } else {
      if (!current) {
        current = { userMsgId: null, childMsgIds: [] }
        groups.push(current)
      }
      current.childMsgIds.push(id)
    }
  }

  return groups
}

export function escapeCssIdent(value: string): string {
  return typeof CSS !== 'undefined' && CSS.escape
    ? CSS.escape(value)
    : value.replace(/"/g, '\\"')
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/lib/room-timeline/message-groups.test.ts`
Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/room-timeline/message-groups.ts src/lib/room-timeline/message-groups.test.ts
git commit -m "feat: add groupMessagesByUserTurn pure function for sticky user messages"
```

---

### Task 2: `useMessageScrollAnchoring` shared hook

**Files:**
- Create: `src/hooks/useMessageScrollAnchoring.ts`

- [ ] **Step 1: Write the hook**

Create `src/hooks/useMessageScrollAnchoring.ts`:

```typescript
import { useRef, useEffect, useLayoutEffect, useState, useCallback } from 'react'
import { escapeCssIdent } from '@/lib/room-timeline/message-groups'

interface AnchorEntity {
  messageType: string
  clientRequestId?: string
}

export interface ScrollAnchoringInput {
  scrollContainerRef: React.RefObject<HTMLDivElement | null>
  hydrated: boolean
  roomId: string
  renderedAnchorIds: string[]
  getEntityForAnchor: (id: string) => AnchorEntity | undefined
  contentVersion: number
}

function getLastUserSendKey(
  anchorIds: string[],
  getEntity: (id: string) => AnchorEntity | undefined,
): string | null {
  for (let i = anchorIds.length - 1; i >= 0; i--) {
    const entity = getEntity(anchorIds[i])
    if (entity?.messageType === 'user') {
      return entity.clientRequestId ?? anchorIds[i]
    }
  }
  return null
}

function findLastUserMessageId(
  anchorIds: string[],
  getEntity: (id: string) => AnchorEntity | undefined,
): string | null {
  for (let i = anchorIds.length - 1; i >= 0; i--) {
    const entity = getEntity(anchorIds[i])
    if (entity?.messageType === 'user') {
      return anchorIds[i]
    }
  }
  return null
}

export function useMessageScrollAnchoring({
  scrollContainerRef,
  hydrated,
  roomId,
  renderedAnchorIds,
  getEntityForAnchor,
  contentVersion,
}: ScrollAnchoringInput) {
  const didInitialAnchor = useRef(false)
  const prevLastUserSendKey = useRef<string | null>(null)
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true)

  const lastUserSendKey = getLastUserSendKey(renderedAnchorIds, getEntityForAnchor)

  const checkIfNearBottom = useCallback(() => {
    const container = scrollContainerRef.current
    if (!container) return false
    const threshold = 100
    return container.scrollHeight - container.scrollTop - container.clientHeight < threshold
  }, [scrollContainerRef])

  const scrollToBottom = useCallback(() => {
    const container = scrollContainerRef.current
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior: 'auto' })
    }
  }, [scrollContainerRef])

  const handleScroll = useCallback((event: React.UIEvent<HTMLDivElement>) => {
    if (event.currentTarget.dataset.programmaticScroll === 'true') {
      event.currentTarget.dataset.programmaticScroll = 'false'
      return
    }
    setShouldAutoScroll(checkIfNearBottom())
  }, [checkIfNearBottom])

  // Reset on room change
  useEffect(() => {
    didInitialAnchor.current = false
    prevLastUserSendKey.current = null
    setShouldAutoScroll(true)
  }, [roomId])

  // P2: Initial anchor on hydration
  useLayoutEffect(() => {
    let rafId: number | null = null
    let canceled = false

    const cleanup = () => {
      canceled = true
      if (rafId !== null) cancelAnimationFrame(rafId)
    }

    if (!hydrated || didInitialAnchor.current) return cleanup

    if (renderedAnchorIds.length === 0) {
      didInitialAnchor.current = true
      prevLastUserSendKey.current = null
      setShouldAutoScroll(true)
      return cleanup
    }

    const lastUserMsgId = findLastUserMessageId(renderedAnchorIds, getEntityForAnchor)

    const completeAnchor = () => {
      didInitialAnchor.current = true
      prevLastUserSendKey.current = lastUserSendKey ?? null
      setShouldAutoScroll(checkIfNearBottom())
    }

    if (lastUserMsgId) {
      const el = scrollContainerRef.current?.querySelector(
        `[data-message-id="${escapeCssIdent(lastUserMsgId)}"]`
      )
      if (!el) {
        rafId = requestAnimationFrame(() => {
          if (canceled || didInitialAnchor.current) return
          const retryEl = scrollContainerRef.current?.querySelector(
            `[data-message-id="${escapeCssIdent(lastUserMsgId)}"]`
          )
          if (!retryEl) return
          retryEl.scrollIntoView({ block: 'start', behavior: 'auto' })
          completeAnchor()
        })
        return cleanup
      }
      el.scrollIntoView({ block: 'start', behavior: 'auto' })
    }

    completeAnchor()
    return cleanup
  }, [hydrated, roomId, renderedAnchorIds.length, lastUserSendKey, contentVersion, getEntityForAnchor, scrollContainerRef, checkIfNearBottom])

  // P1: Force scroll on new user send
  useEffect(() => {
    if (!hydrated || !didInitialAnchor.current) return
    if (!lastUserSendKey) return
    if (lastUserSendKey === prevLastUserSendKey.current) return

    scrollToBottom()
    prevLastUserSendKey.current = lastUserSendKey
    setShouldAutoScroll(true)
  }, [lastUserSendKey, hydrated, scrollToBottom])

  // AI streaming: scroll follow on contentVersion change
  useEffect(() => {
    if (!hydrated || !didInitialAnchor.current) return
    if (!shouldAutoScroll) return

    const container = scrollContainerRef.current
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior: 'auto' })
    }
  }, [contentVersion, hydrated, shouldAutoScroll, scrollContainerRef])

  return {
    shouldAutoScroll,
    handleScroll,
    scrollToBottom,
  }
}
```

- [ ] **Step 2: Verify it compiles**

Run: `npx tsc --noEmit src/hooks/useMessageScrollAnchoring.ts 2>&1 | head -20`
Expected: No errors (or only unrelated project-level warnings).

- [ ] **Step 3: Commit**

```bash
git add src/hooks/useMessageScrollAnchoring.ts
git commit -m "feat: add useMessageScrollAnchoring shared hook for P1 + P2"
```

---

### Task 3: Tests for `useMessageScrollAnchoring`

**Files:**
- Create: `tests/unit/hooks/useMessageScrollAnchoring.test.ts`

- [ ] **Step 1: Write the test file**

Create `tests/unit/hooks/useMessageScrollAnchoring.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useMessageScrollAnchoring, type ScrollAnchoringInput } from '@/hooks/useMessageScrollAnchoring'

function makeEntity(id: string, type: 'user' | 'agent', clientRequestId?: string) {
  return { messageType: type, clientRequestId }
}

function makeInput(overrides: Partial<ScrollAnchoringInput> = {}): ScrollAnchoringInput {
  const container = document.createElement('div')
  Object.defineProperty(container, 'scrollHeight', { value: 1000, configurable: true })
  Object.defineProperty(container, 'scrollTop', { value: 900, configurable: true })
  Object.defineProperty(container, 'clientHeight', { value: 100, configurable: true })
  container.scrollTo = vi.fn()
  container.querySelector = vi.fn().mockReturnValue(null)

  return {
    scrollContainerRef: { current: container },
    hydrated: true,
    roomId: 'room-1',
    renderedAnchorIds: [],
    getEntityForAnchor: () => undefined,
    contentVersion: 0,
    ...overrides,
  }
}

describe('useMessageScrollAnchoring', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('marks didInitialAnchor for empty room without scrolling', () => {
    const input = makeInput({ renderedAnchorIds: [] })
    const { result, rerender } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // Empty room: shouldAutoScroll stays true (ready for P1)
    expect(result.current.shouldAutoScroll).toBe(true)

    // Simulate first user message arriving
    const entities: Record<string, ReturnType<typeof makeEntity>> = {
      'u1': makeEntity('u1', 'user', 'crid-1'),
    }
    rerender({
      ...input,
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id],
    })

    // P1 should have fired scrollToBottom
    const container = input.scrollContainerRef.current!
    expect(container.scrollTo).toHaveBeenCalled()
  })

  it('does not double-scroll on temp→real id swap', () => {
    const entities: Record<string, ReturnType<typeof makeEntity>> = {
      'temp-1': makeEntity('temp-1', 'user', 'crid-1'),
    }
    const mockEl = { scrollIntoView: vi.fn() }
    const container = makeInput().scrollContainerRef.current!
    container.querySelector = vi.fn().mockReturnValue(mockEl)

    const input = makeInput({
      renderedAnchorIds: ['temp-1'],
      getEntityForAnchor: (id: string) => entities[id],
      scrollContainerRef: { current: container },
    })

    const { rerender } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // Initial anchor fires scrollIntoView
    expect(mockEl.scrollIntoView).toHaveBeenCalledTimes(1)
    const scrollToCallCount = (container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length

    // Simulate temp→real swap: id changes but clientRequestId stays same
    delete entities['temp-1']
    entities['real-1'] = makeEntity('real-1', 'user', 'crid-1')

    rerender({
      ...input,
      renderedAnchorIds: ['real-1'],
      getEntityForAnchor: (id: string) => entities[id],
    })

    // scrollTo should NOT have been called again (lastUserSendKey is still 'crid-1')
    expect((container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length).toBe(scrollToCallCount)
  })

  it('does not scroll on AI streaming when shouldAutoScroll is false', () => {
    const entities = { u1: makeEntity('u1', 'user', 'crid-1') }
    const mockEl = { scrollIntoView: vi.fn() }
    const container = makeInput().scrollContainerRef.current!
    container.querySelector = vi.fn().mockReturnValue(mockEl)
    // Simulate user NOT near bottom
    Object.defineProperty(container, 'scrollTop', { value: 0, configurable: true })

    const input = makeInput({
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id as keyof typeof entities],
      scrollContainerRef: { current: container },
    })

    const { rerender } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // After P2 anchor, shouldAutoScroll should be false (not near bottom)
    const scrollCallsBefore = (container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length

    // Increment contentVersion (AI streaming)
    rerender({ ...input, contentVersion: 1 })
    rerender({ ...input, contentVersion: 2 })

    // No additional scrollTo calls
    expect((container.scrollTo as ReturnType<typeof vi.fn>).mock.calls.length).toBe(scrollCallsBefore)
  })

  it('resets anchor state on room switch', () => {
    const entities = { u1: makeEntity('u1', 'user', 'crid-1') }
    const mockEl = { scrollIntoView: vi.fn() }
    const container = makeInput().scrollContainerRef.current!
    container.querySelector = vi.fn().mockReturnValue(mockEl)

    const input = makeInput({
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id as keyof typeof entities],
      scrollContainerRef: { current: container },
    })

    const { rerender } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // Switch rooms
    const newEntities = { u2: makeEntity('u2', 'user', 'crid-2') }
    const newMockEl = { scrollIntoView: vi.fn() }
    container.querySelector = vi.fn().mockReturnValue(newMockEl)

    rerender({
      ...input,
      roomId: 'room-2',
      renderedAnchorIds: ['u2'],
      getEntityForAnchor: (id: string) => newEntities[id as keyof typeof newEntities],
    })

    // P2 should re-execute for new room
    expect(newMockEl.scrollIntoView).toHaveBeenCalled()
  })

  it('retries P2 anchor via rAF when DOM element is initially missing', async () => {
    const entities = { u1: makeEntity('u1', 'user', 'crid-1') }
    const mockEl = { scrollIntoView: vi.fn() }
    const container = makeInput().scrollContainerRef.current!

    // First querySelector returns null (DOM not rendered), second returns element
    let queryCount = 0
    container.querySelector = vi.fn().mockImplementation(() => {
      queryCount++
      return queryCount >= 2 ? mockEl : null
    })

    const input = makeInput({
      renderedAnchorIds: ['u1'],
      getEntityForAnchor: (id: string) => entities[id as keyof typeof entities],
      scrollContainerRef: { current: container },
    })

    renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // First call found nothing; rAF scheduled
    expect(mockEl.scrollIntoView).not.toHaveBeenCalled()

    // Flush rAF
    await vi.runAllTimersAsync()

    // rAF retry should have found the element and scrolled
    expect(mockEl.scrollIntoView).toHaveBeenCalledWith({ block: 'start', behavior: 'auto' })
  })

  it('marks initial anchor without scrolling for room with only AI messages', () => {
    const entities = { a1: makeEntity('a1', 'agent') }
    const container = makeInput().scrollContainerRef.current!

    const input = makeInput({
      renderedAnchorIds: ['a1'],
      getEntityForAnchor: (id: string) => entities[id as keyof typeof entities],
      scrollContainerRef: { current: container },
    })

    const { result } = renderHook((props) => useMessageScrollAnchoring(props), {
      initialProps: input,
    })

    // No scrollIntoView called (no user message to anchor to)
    expect(container.querySelector).not.toHaveBeenCalled()
    // shouldAutoScroll reflects near-bottom check
    expect(typeof result.current.shouldAutoScroll).toBe('boolean')
  })
})
```

- [ ] **Step 2: Run tests**

Run: `npx vitest run tests/unit/hooks/useMessageScrollAnchoring.test.ts`
Expected: All 5 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/hooks/useMessageScrollAnchoring.test.ts
git commit -m "test: add useMessageScrollAnchoring scroll behavior tests"
```

---

### Task 4: Integrate sticky groups + scroll anchoring into Legacy view

**Files:**
- Modify: `src/components/room-messages.tsx`

- [ ] **Step 1: Update imports and add hook**

In `src/components/room-messages.tsx`, replace the existing scroll imports and add new ones.

Replace lines 1-17:

```typescript
'use client'

import React, { useRef, useEffect, useState, useCallback, useMemo } from 'react'
import {
  ArrowDown,
  ChevronsDownUp,
  ChevronsUpDown,
  MessageCirclePlus,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { EntityUserBubble, EntityAgentBubble, derivePhase, type QuoteData } from './message-bubble'
import { useAutoHideScroll } from '@/hooks/useAutoHideScroll'
import { useOrderedIds, useMessage, useMessageCount, useMessagesHydrated } from '@/hooks/useRoomMessages'
import { useMessageStore } from '@/stores/message-store'
import { useShallow } from 'zustand/react/shallow'
import { useMessageScrollAnchoring } from '@/hooks/useMessageScrollAnchoring'
import { groupMessagesByUserTurn } from '@/lib/room-timeline/message-groups'
```

- [ ] **Step 2: Replace scroll state and add hook call**

In the `RoomMessages` component body, remove old scroll refs/state (lines 109-112 and lines 176-223) and replace with the new hook.

Remove these lines from the component:
```typescript
// REMOVE these:
const messagesEndRef = useRef<HTMLDivElement>(null)
const scrollContainerRef = useRef<HTMLDivElement>(null)
const [shouldAutoScroll, setShouldAutoScroll] = useState(true)
const prevCountRef = useRef(messageCount)
// ...
const scrollToBottom = useCallback(() => { ... }, [])
const checkIfNearBottom = useCallback(() => { ... }, [])
const handleScroll = useCallback((event: ...) => { ... }, [])
useEffect(() => { if (messageCount > prevCountRef.current) ... }, [messageCount, shouldAutoScroll])
```

Replace with:

```typescript
const scrollContainerRef = useRef<HTMLDivElement>(null)
const roomId = useMessageStore(s => s.roomId) ?? ''
const version = useMessageStore(s => s.version)
const entities = useMessageStore(s => s.entities)

const getEntityForAnchor = useCallback(
  (id: string) => {
    const e = entities[id]
    if (!e) return undefined
    return { messageType: e.messageType, clientRequestId: e.clientRequestId }
  },
  [entities],
)

const { shouldAutoScroll, handleScroll, scrollToBottom } = useMessageScrollAnchoring({
  scrollContainerRef,
  hydrated,
  roomId,
  renderedAnchorIds: orderedIds,
  getEntityForAnchor,
  contentVersion: version,
})

const groups = useMemo(
  () => groupMessagesByUserTurn(orderedIds, entities),
  [orderedIds, entities],
)
```

- [ ] **Step 3: Replace message rendering with grouped rendering**

Replace the messages display section (the `<div className="space-y-4">` block, roughly lines 270-283) with grouped rendering:

```tsx
{/* Messages Display - Grouped by user turn with sticky headers */}
<div className="space-y-4">
  {groups.map(group => (
    <div key={group.userMsgId ?? 'system-prefix'}>
      {group.userMsgId && (
        <div
          className="sticky top-0 z-10 bg-background shadow-[0_1px_3px_0_rgb(0_0_0/0.05)]"
          data-message-id={group.userMsgId}
        >
          <MemoizedMessage
            id={group.userMsgId}
            isLatestAgent={false}
            collapseSignal={collapseSignal}
            autoCollapseVersion={autoCollapseVersion}
            isUserExpanded={userExpandedIds.has(group.userMsgId)}
            onUserToggle={handleUserToggle}
            onQuote={onQuote}
          />
        </div>
      )}
      {group.childMsgIds.map(id => (
        <MemoizedMessage
          key={id}
          id={id}
          isLatestAgent={id === lastAgentMessageId}
          collapseSignal={collapseSignal}
          autoCollapseVersion={autoCollapseVersion}
          isUserExpanded={userExpandedIds.has(id)}
          onUserToggle={handleUserToggle}
          onQuote={onQuote}
        />
      ))}
    </div>
  ))}
</div>
```

- [ ] **Step 4: Relocate expand/collapse pill**

Replace the existing floating expand/collapse pill (lines 243-267, the `<div className="sticky top-2 z-10 ...">` block) with a non-sticky floating version:

```tsx
{allAgentIds.length > 0 && (
  <div className="absolute top-2 right-2 z-20 pointer-events-none">
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          onClick={allExpanded ? collapseAll : expandAll}
          className="h-8 w-8 p-0 pointer-events-auto rounded-full bg-muted/60 backdrop-blur-sm shadow-sm hover:bg-muted hover:shadow-md transition-all"
          aria-label={allExpanded ? 'Collapse all messages' : 'Expand all messages'}
        >
          {allExpanded ? (
            <ChevronsDownUp className="h-4 w-4" />
          ) : (
            <ChevronsUpDown className="h-4 w-4" />
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent>
        {allExpanded ? 'Collapse all messages' : 'Expand all messages'}
      </TooltipContent>
    </Tooltip>
  </div>
)}
```

Note: The parent `<div className="h-full flex relative">` already has `relative`, so the `absolute` positioning works correctly.

- [ ] **Step 5: Remove the old messagesEndRef anchor div**

Remove `<div ref={messagesEndRef} className="h-4" />` (line 285). The hook no longer uses a `messagesEndRef` — it uses `container.scrollTo` directly.

Keep a spacer if needed for bottom padding:
```tsx
<div className="h-4" />
```

- [ ] **Step 6: Verify the component compiles**

Run: `npx tsc --noEmit 2>&1 | grep room-messages | head -10`
Expected: No errors for `room-messages.tsx`.

- [ ] **Step 7: Run existing tests**

Run: `npx vitest run tests/unit/components/room-messages.test.tsx`
Expected: Existing tests pass (may need minor adjustments if they relied on the old scroll structure).

- [ ] **Step 8: Commit**

```bash
git add src/components/room-messages.tsx
git commit -m "feat: integrate sticky user messages and scroll anchoring into Legacy view"
```

---

### Task 5: Add sticky wrapper to `OrchestraTurn`

**Files:**
- Modify: `src/components/turn/OrchestraTurn.tsx`
- Modify: `tests/unit/components/turn/OrchestraTurn.test.tsx`

- [ ] **Step 1: Add test for sticky wrapper and data-message-id**

Add to `tests/unit/components/turn/OrchestraTurn.test.tsx`:

```typescript
it('renders sticky wrapper with data-message-id around UserInputBlock', () => {
  const log = new TurnEventLog('turn-1')
  log.append({
    eventId: 'e1', turnId: 'turn-1', seq: 1, ts: 1000,
    type: 'turn_started', userInput,
  })

  render(<OrchestraTurn turnLog={log} />, { wrapper: Wrapper })

  const stickyWrapper = document.querySelector('[data-message-id="turn-1"]')
  expect(stickyWrapper).not.toBeNull()
  expect(stickyWrapper?.classList.contains('sticky')).toBe(true)
  expect(screen.getByText('What is AI?')).toBeTruthy()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/unit/components/turn/OrchestraTurn.test.tsx`
Expected: FAIL — no `data-message-id` attribute rendered.

- [ ] **Step 3: Modify OrchestraTurn to add sticky wrapper**

Replace line 29 in `src/components/turn/OrchestraTurn.tsx`:

Old:
```tsx
{userInput && <UserInputBlock data={userInput} />}
```

New:
```tsx
{userInput && (
  <div
    data-message-id={turnLog.turnId}
    className="sticky top-0 z-10 bg-background shadow-[0_1px_3px_0_rgb(0_0_0/0.05)]"
  >
    <UserInputBlock data={userInput} />
  </div>
)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run tests/unit/components/turn/OrchestraTurn.test.tsx`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/components/turn/OrchestraTurn.tsx tests/unit/components/turn/OrchestraTurn.test.tsx
git commit -m "feat: add sticky wrapper with data-message-id around UserInputBlock in OrchestraTurn"
```

---

### Task 6: Integrate scroll anchoring into TurnList + useTurnScroll

**Files:**
- Modify: `src/hooks/turn/useTurnScroll.ts`
- Modify: `src/components/turn/TurnList.tsx`

- [ ] **Step 1: Rewrite `useTurnScroll.ts`**

Replace `src/hooks/turn/useTurnScroll.ts` entirely:

```typescript
import { useRef, useEffect, useState, useCallback } from 'react'
import { useTurnEventStore } from '@/stores/turn-event-store'
import { useMessageStore } from '@/stores/message-store'
import { useMessageScrollAnchoring } from '@/hooks/useMessageScrollAnchoring'

export function useTurnScroll(scrollContainerRef: React.RefObject<HTMLDivElement | null>) {
  const orderedTurnIds = useTurnEventStore(s => s.orderedTurnIds)
  const turnLogs = useTurnEventStore(s => s.turnLogs)
  const hydrated = useTurnEventStore(s => s.hydrated)
  const roomId = useMessageStore(s => s.roomId) ?? ''

  // contentVersion: subscribe to the active turn's event log
  // to detect streaming content updates within a single turn
  const [contentVersion, setContentVersion] = useState(0)

  const activeTurnId = orderedTurnIds[orderedTurnIds.length - 1]

  useEffect(() => {
    if (!activeTurnId) return

    const turnLog = turnLogs.get(activeTurnId)
    if (!turnLog) return

    const unsubscribe = turnLog.subscribe(() => {
      setContentVersion(v => v + 1)
    })
    return unsubscribe
  }, [activeTurnId, turnLogs])

  const getEntityForAnchor = useCallback(
    (turnId: string) => {
      const turnLog = turnLogs.get(turnId)
      if (!turnLog) return undefined
      const startEvent = turnLog.getEvents().find(e => e.type === 'turn_started')
      return {
        messageType: 'user' as const,
        clientRequestId: startEvent?.clientRequestId ?? turnId,
      }
    },
    [turnLogs],
  )

  const { shouldAutoScroll, handleScroll, scrollToBottom } = useMessageScrollAnchoring({
    scrollContainerRef,
    hydrated,
    roomId,
    renderedAnchorIds: orderedTurnIds,
    getEntityForAnchor,
    contentVersion,
  })

  return {
    shouldAutoScroll,
    handleScroll,
    scrollToBottom,
  }
}
```

- [ ] **Step 2: Update TurnList to relocate expand/collapse and remove messagesEndRef**

In `src/components/turn/TurnList.tsx`, make these changes:

Replace the expand/collapse bar (lines 86-100):
```tsx
<div className="sticky top-0 z-10 flex justify-end mb-1 bg-background/80 backdrop-blur-sm">
```

With a floating button that doesn't compete for `top-0`:
```tsx
<div className="absolute top-2 right-2 z-20">
```

And remove the `messagesEndRef` usage — the hook no longer returns it. Update line 40:

Old:
```typescript
const { messagesEndRef, shouldAutoScroll, handleScroll, scrollToBottom } = useTurnScroll(scrollContainerRef)
```

New:
```typescript
const { shouldAutoScroll, handleScroll, scrollToBottom } = useTurnScroll(scrollContainerRef)
```

Remove `<div ref={messagesEndRef} className="h-4" />` (line 110) and replace with a simple spacer:
```tsx
<div className="h-4" />
```

Full updated TurnList content section (replace lines 85-109):

```tsx
<>
  <div className="space-y-0">
    {orderedTurnIds.map(turnId => {
      const log = turnLogs.get(turnId)
      if (!log) return null
      return <OrchestraTurn key={turnId} turnLog={log} />
    })}
  </div>
  <div className="h-4" />
</>
```

And move the expand/collapse button outside the scroll content, into the `<div className="h-full flex relative">` wrapper alongside the scroll-to-bottom button:

```tsx
{/* Expand/collapse floating button */}
{hasTurns && (
  <Button
    variant="ghost"
    size="icon"
    onClick={handleToggleAll}
    className="absolute top-2 right-2 z-20 h-7 w-7 text-muted-foreground hover:text-foreground bg-muted/60 backdrop-blur-sm rounded-full shadow-sm hover:shadow-md"
    aria-label={allExpanded ? 'Collapse all responses' : 'Expand all responses'}
  >
    {allExpanded ? (
      <ChevronsDownUp className="h-4 w-4" />
    ) : (
      <ChevronsUpDown className="h-4 w-4" />
    )}
  </Button>
)}
```

- [ ] **Step 3: Verify compilation**

Run: `npx tsc --noEmit 2>&1 | grep -E 'TurnList|useTurnScroll' | head -10`
Expected: No errors.

- [ ] **Step 4: Run existing TurnList tests**

Run: `npx vitest run tests/unit/components/turn/TurnList.test.tsx`
Expected: Tests pass (may need minor updates if they tested `messagesEndRef`).

- [ ] **Step 5: Commit**

```bash
git add src/hooks/turn/useTurnScroll.ts src/components/turn/TurnList.tsx
git commit -m "feat: integrate scroll anchoring into TurnList via useMessageScrollAnchoring"
```

---

### Task 7: Manual smoke test + fix regressions

**Files:** None created — this is a verification task.

- [ ] **Step 1: Start dev server**

Run: `npm run dev`

- [ ] **Step 2: Test P1 — auto-scroll on user send**

1. Open a room with existing messages.
2. Scroll up in history.
3. Send a new message.
4. Verify: page scrolls to bottom immediately.
5. Verify: subsequent AI streaming responses scroll-follow.

- [ ] **Step 3: Test P2 — initial anchor on refresh**

1. In a room with multiple user messages + AI replies, refresh the page (F5).
2. Verify: page anchors to the last user message (not the top or bottom).
3. If the last user message has long AI replies below, verify you stay at the user message position and AI streaming does NOT pull you to bottom.

- [ ] **Step 4: Test P3 — sticky user messages**

1. Scroll through a conversation with multiple user → AI turns.
2. Verify: current turn's user message sticks at the top of the scroll container.
3. Verify: when the next user message scrolls up, it replaces the sticky header.
4. Verify: the sticky header has a subtle bottom shadow.
5. Verify: expand/collapse button is accessible and doesn't overlap the sticky header.

- [ ] **Step 5: Test Turn-based view**

1. Toggle the `turnBasedTimeline` feature flag (or whichever mechanism activates TurnList).
2. Repeat steps 2-4 in the turn-based view.
3. Verify: sticky wrapper appears around UserInputBlock with correct `data-message-id`.

- [ ] **Step 6: Test edge cases**

1. Empty room (new room, no messages) — send first message, verify scroll.
2. Room with only AI/system messages — verify no crash, page loads normally.
3. Room switch — navigate between rooms, verify scroll resets and anchors correctly.
4. Very long user message — verify sticky doesn't break layout.

- [ ] **Step 7: Fix any regressions found**

Address any issues found during smoke testing. Common fixes:
- CSS z-index conflicts
- Missing `data-message-id` attributes
- Scroll-to-bottom button visibility logic

- [ ] **Step 8: Run full test suite**

Run: `npx vitest run`
Expected: All tests pass.

- [ ] **Step 9: Commit any fixes**

```bash
git add -A
git commit -m "fix: address smoke test regressions for scroll anchoring + sticky messages"
```

---

## Implementation Order

```
Task 1 (pure function + tests)
  └→ Task 2 (shared hook)
       └→ Task 3 (hook tests)
            ├→ Task 4 (Legacy view integration)
            └→ Task 5 (OrchestraTurn sticky wrapper)
                 └→ Task 6 (TurnList + useTurnScroll integration)
                      └→ Task 7 (smoke test + fix regressions)
```

Tasks 4 and 5 are independent and can be done in parallel after Task 3.
