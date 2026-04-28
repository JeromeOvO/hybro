# Room Conversation UI/UX Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the room conversation renderer from a dual-view/triple-store architecture to a single, cleanup-first pipeline: message-store → pure selectors → dumb presentational components. Aligned with Qoder IDE Expert Team chat style.

**Architecture:** Delete legacy bubble view and turn-event-store bridge first. Build pure selector functions (`selectConversationTurns`, `selectComposerState`, `mapAgentDisplayProps`) that consume message-store directly and return grouped `ConversationTurnView[]`. New presentational components receive typed props and render — no business logic inside. `clientRequestId` is live correlation only; stable turn identity is persisted user message `id`.

**Tech Stack:** Next.js 15, React 19, Zustand, Vitest, shadcn/ui, CSS custom properties, TypeScript

---

## File Structure

### New Files (created during this plan)

| File | Responsibility |
|------|---------------|
| `src/lib/selectors/conversation-types.ts` | Type definitions: `ConversationTurnView`, `ConversationBlock`, `AgentDisplayProps`, `AgentTheme`, `PendingHitl`, `HitlState`, `ComposerState`, `ContentView` |
| `src/lib/selectors/route-agent.ts` | `routeAgentToTurn()` — persisted-first three-tier routing |
| `src/lib/selectors/map-agent-display.ts` | `mapAgentDisplayProps()` — pure mapper from `MessageEntity` → `AgentDisplayProps` |
| `src/lib/selectors/select-conversation-turns.ts` | `selectConversationTurns()` — core turn grouping selector |
| `src/lib/selectors/select-hitl.ts` | `selectPendingHitls()`, `selectAgentHitlState()` — HITL single source of truth |
| `src/lib/selectors/select-composer-state.ts` | `selectComposerState()` — composer mode/processing from message-store |
| `src/lib/selectors/index.ts` | Barrel re-export |
| `src/hooks/useConversationTurnViews.ts` | Container hook — only store subscription point |
| `src/components/conversation/conversation-tokens.css` | CSS custom properties for conversation UI |
| `src/components/conversation/ConversationMessageList.tsx` | Scroll container, sticky logic, elastic spacer |
| `src/components/conversation/ConversationTurn.tsx` | Turn grouping shell — branches on `userMessage` presence |
| `src/components/conversation/UserMessageBlock.tsx` | User message with truncation + expand |
| `src/components/conversation/AgentCard.tsx` | Agent status card, purely presentational |
| `src/components/conversation/AgentContentBlock.tsx` | Markdown content + artifact list with download |
| `src/components/conversation/UserAttachmentCard.tsx` | Extracted from message-bubble.tsx — renders image/audio/video/file attachments |
| `src/components/conversation/UserAnswerCard.tsx` | HITL Q&A record |
| `src/components/conversation/UnresolvedAgentGroup.tsx` | Unattributed response group |
| `src/components/conversation/ScrollToBottomButton.tsx` | Scroll control with badge |
| `src/components/conversation/shimmer.css` | Card shimmer + typewriter cursor animations |
| `tests/unit/lib/selectors/route-agent.test.ts` | Tests for three-tier routing |
| `tests/unit/lib/selectors/map-agent-display.test.ts` | Tests for display mapper |
| `tests/unit/lib/selectors/select-conversation-turns.test.ts` | Tests for turn grouping + identity lifecycle |
| `tests/unit/lib/selectors/select-hitl.test.ts` | Tests for HITL selectors |
| `tests/unit/lib/selectors/select-composer-state.test.ts` | Tests for composer state |

### Modified Files

| File | Change |
|------|--------|
| `src/app/c/room/[id]/page.tsx` | Remove dead code (Phase 0), remove `turnBasedTimeline` (Phase 1), wire new renderer (Phase 4) |
| `src/stores/room-ui-store.ts` | Remove `globalTurnBasedTimeline` / `setGlobalTurnBasedTimeline` |
| `src/components/room-page-shell.tsx` | Remove view-switching, simplify to direct render (Phase 1), replace with new components (Phase 4) |
| `src/hooks/room/useSendMessage.ts` | Add `clientRequestId` to placeholder (Phase 2 prereq), remove `createOptimisticTurn`/`removeTurn` (Phase 4) |
| `src/hooks/room/sse-handlers/pending-turn-buffer.ts` | TTL 30s → 120s, production logging on eviction |
| `src/hooks/room/sse-handlers/index.ts` | Regression test for terminal race guard, single-write relaxation |
| `src/components/composer/ComposerShell.tsx` | Migrate from `useTurnEventStore` → `selectComposerState` |
| `src/app/globals.css` | Import conversation-tokens.css |
| `tests/fixtures/index.ts` | Add `createHitlMessage`, `createHitlGroupMessages` helpers |

### Deleted Files (Phase 1 + Phase 5)

| Phase | Files |
|-------|-------|
| Phase 0 | `src/components/settings/appearance-section.tsx` |
| Phase 1 | `src/components/room-messages.tsx` |
| Phase 5 | `src/stores/turn-event-store/` (entire directory), `src/hooks/turn/` (entire directory), `src/components/turn/` (entire directory), `src/components/conversation-timeline.tsx`, `src/components/conversation-turn.tsx`, `src/components/agent-result-card.tsx`, `src/components/agent-result-stack.tsx`, `src/components/agent-placeholder-row.tsx`, `src/components/supervisor-header.tsx`, `src/components/agent-badge.tsx`, `src/components/message-bubble.tsx` |

---

## PR Structure

| PR | Phases | Merge Policy |
|----|--------|-------------|
| PR 1 | Phase 0 | Standalone, safe to merge independently |
| PR 2 | Phase 1 | Standalone, safe to merge independently |
| PR 3 | Phase 1.5 + 2 + 3 + 4 | Single feature branch. Phase 1.5 is NOT mergeable alone. Merge gate: ConversationMessageList wired in, all scenarios pass. |
| PR 4 | Phase 5 | Standalone cleanup after PR 3 is verified in production |
| PR 5 | SSE fixes (Sec 7) | Can be merged independently at any point |

---

## Task 1: Phase 0 — Dead code cleanup in page.tsx

**Files:**
- Modify: `src/app/c/room/[id]/page.tsx:8,10-15,50-53,284-315`

- [ ] **Step 1: Remove dead imports**

In `src/app/c/room/[id]/page.tsx`, replace line 8:

```typescript
import { Users, Pencil, Check, X as XIcon } from 'lucide-react'
```

with nothing (delete the entire line). Then delete lines 10-15:

```typescript
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
```

- [ ] **Step 2: Remove dead state and callbacks**

Delete lines 50-53 (inline name editing state):

```typescript
  // Inline room name editing
  const [editingName, setEditingName] = useState(false)
  const [editNameValue, setEditNameValue] = useState('')
  const nameInputRef = useRef<HTMLInputElement>(null)
```

Delete lines 284-315 (the three dead callbacks: `startEditingName`, `saveRoomName`, `cancelEditingName`).

- [ ] **Step 3: Clean up unused imports in React import**

Check if `useRef` is still needed after removing `nameInputRef`. The `initialMessageSentRef` and `confirmedChatModeRef` still use `useRef`, so keep it. Remove only imports that become orphaned.

- [ ] **Step 4: Verify build**

```bash
npx tsc --noEmit
```

Expected: no new errors.

- [ ] **Step 5: Commit**

```bash
git add src/app/c/room/[id]/page.tsx
git commit -m "refactor(room): remove dead name-editing state and unused imports from page.tsx"
```

---

## Task 2: Phase 0 — Delete appearance-section.tsx

**Files:**
- Delete: `src/components/settings/appearance-section.tsx`

- [ ] **Step 1: Verify no imports reference it**

```bash
grep -r "appearance-section" src/ --include="*.ts" --include="*.tsx"
```

Expected: only the file itself (already removed from settings-dialog.tsx in prior work).

- [ ] **Step 2: Delete the file**

```bash
rm src/components/settings/appearance-section.tsx
```

- [ ] **Step 3: Verify build**

```bash
npx tsc --noEmit
```

- [ ] **Step 4: Commit**

```bash
git add -u
git commit -m "refactor(settings): delete orphaned appearance-section.tsx"
```

---

## Task 3: Phase 0 — Clean dead signals from TurnList and room-messages

**Files:**
- Modify: `src/components/turn/TurnList.tsx:24-25`
- Modify: `src/components/room-messages.tsx:139`

- [ ] **Step 1: Remove constant signals from TurnList.tsx**

In `src/components/turn/TurnList.tsx`, delete lines 24-25:

```typescript
  const [expandSignal] = useState(0)
  const [collapseSignal] = useState(0)
```

And the memoized value that wraps them (around line 27):

```typescript
  const value = useMemo(() => ({ expandSignal, collapseSignal }), [expandSignal, collapseSignal])
```

Replace with a constant:

```typescript
  const value = useMemo(() => ({ expandSignal: 0, collapseSignal: 0 }), [])
```

Remove `useState` from import if no longer needed.

- [ ] **Step 2: Remove dead setCollapseSignal from room-messages.tsx**

In `src/components/room-messages.tsx`, line 139 has:

```typescript
  const [collapseSignal, setCollapseSignal] = useState(0)
```

Change to:

```typescript
  const [collapseSignal] = useState(0)
```

- [ ] **Step 3: Verify build**

```bash
npx tsc --noEmit
```

- [ ] **Step 4: Commit**

```bash
git add src/components/turn/TurnList.tsx src/components/room-messages.tsx
git commit -m "refactor(room): remove dead expand/collapse signals from TurnList and room-messages"
```

---

## Task 4: Phase 1 — Remove globalTurnBasedTimeline from room-ui-store

**Files:**
- Modify: `src/stores/room-ui-store.ts:51,87,139-142`

- [ ] **Step 1: Remove the state field and setter**

In `src/stores/room-ui-store.ts`:

1. Delete line 51: `globalTurnBasedTimeline: boolean`
2. Delete the `setGlobalTurnBasedTimeline` type declaration from the interface
3. Delete line 87 (initial value): `globalTurnBasedTimeline: readLocalStorageBool('hybro:turnBasedTimeline', false),`
4. Delete lines 139-142 (setter implementation):
```typescript
  setGlobalTurnBasedTimeline: (v) => {
    set({ globalTurnBasedTimeline: v })
    try { localStorage.setItem('hybro:turnBasedTimeline', String(v)) } catch { /* ignore */ }
  },
```

- [ ] **Step 2: Remove consumption in page.tsx**

In `src/app/c/room/[id]/page.tsx`, delete line 102:

```typescript
  const turnBasedTimeline = useRoomUiStore(s => s.globalTurnBasedTimeline)
```

And update line 395-398: remove `turnBasedTimeline` prop from `<RoomPageShell>`:

```typescript
        <RoomPageShell
          adapter={timelineAdapter}
        />
```

- [ ] **Step 3: Run reference check for any remaining consumers**

```bash
grep -r "globalTurnBasedTimeline\|turnBasedTimeline\|hybro:turnBasedTimeline" src/ --include="*.ts" --include="*.tsx"
```

Expected: zero matches (appearance-section.tsx already deleted in Task 2).

- [ ] **Step 4: Verify build**

```bash
npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add src/stores/room-ui-store.ts src/app/c/room/[id]/page.tsx
git commit -m "refactor(room): remove globalTurnBasedTimeline feature flag"
```

---

## Task 4b: Phase 1 prereq — Extract QuoteData to shared types

**Files:**
- Create: `src/lib/types/quote.ts`
- Modify: all files that `import type { QuoteData } from '@/lib/types/quote'`

> `QuoteData` is defined in `message-bubble.tsx` and imported by 6+ src/ files.
> Before we can delete the legacy bubble view, we must move this type out.

- [ ] **Step 1: Create shared QuoteData type**

Create `src/lib/types/quote.ts`:

```typescript
export interface QuoteData {
  messageId: string
  content: string
  senderName: string
}
```

Verify the fields match the existing definition in `src/components/message-bubble.tsx` (search for `export interface QuoteData`). If there are extra fields, include them.

- [ ] **Step 2: Update imports across the codebase**

```bash
grep -r "import.*QuoteData.*from.*message-bubble" src/ --include="*.ts" --include="*.tsx" -l
```

For every file listed, change:

```typescript
import type { QuoteData } from '@/components/message-bubble'
```

to:

```typescript
import type { QuoteData } from '@/lib/types/quote'
```

Files expected: `page.tsx`, `room-page-shell.tsx`, `room-chat-input.tsx`, `useSendMessage.ts`, `agent-result-card.tsx`, `agent-result-stack.tsx`, `conversation-turn.tsx`, `chat/page.tsx`.

If `message-bubble.tsx` has other exports consumed by `room-messages.tsx` only (e.g. `EntityUserBubble`), those will be deleted with `room-messages.tsx` in the next task.

- [ ] **Step 3: Extract UserAttachmentCard to standalone file**

`UserAttachmentCard` is defined in `message-bubble.tsx` and renders image/audio/video/file attachments. Move it to `src/components/conversation/UserAttachmentCard.tsx`:

1. Copy the `UserAttachmentCard` function and its helpers (`AttachmentExpiredBanner`, `ImageLightbox` usage) from `message-bubble.tsx` into the new file.
2. Add necessary imports (`AttachmentData` from `@/lib/types/attachments`, icons from `lucide-react`, etc.).
3. In `message-bubble.tsx`, replace the function body with:

```typescript
export { UserAttachmentCard } from './conversation/UserAttachmentCard'
```

This keeps existing consumers working until they're deleted.

4. Update imports in `conversation-turn.tsx` and `turn/UserInputBlock.tsx` to import from the new location (these files will be deleted in Phase 5, but this ensures they compile until then).

- [ ] **Step 4: Re-export QuoteData from message-bubble for stragglers**

In `src/components/message-bubble.tsx`, replace the `QuoteData` definition with a re-export:

```typescript
export type { QuoteData } from '@/lib/types/quote'
```

Remove the old `export interface QuoteData { ... }` block.

- [ ] **Step 5: Verify build**

```bash
npx tsc --noEmit
```

- [ ] **Step 6: Commit**

```bash
git add src/lib/types/quote.ts src/components/conversation/UserAttachmentCard.tsx && git add -u
git commit -m "refactor(types): extract QuoteData and UserAttachmentCard before legacy deletion"
```

---

## Task 5: Phase 1 — Remove RoomPageShell view-switching, delete legacy bubble view

**Files:**
- Modify: `src/components/room-page-shell.tsx`
- Delete: `src/components/room-messages.tsx`

- [ ] **Step 1: Simplify RoomPageShell to turn-based only**

Replace the entire `src/components/room-page-shell.tsx` content. Remove `LegacyView`, `LegacyViewProps`, and the `turnBasedTimeline` prop. The shell now directly renders `TurnBasedView`:

```typescript
'use client'

import React from 'react'
import type { AgentGroup } from '@/lib/types/agent-group'
import type { QuoteData } from '@/lib/types/quote'
import type { PendingAttachment } from '@/lib/types/attachments'
import type { ChatMode } from '@/lib/types/chat-mode'
import { TurnList } from '@/components/turn/TurnList'
import { ComposerShell } from '@/components/composer/ComposerShell'
import { useTurnHydration } from '@/hooks/turn/useTurnHydration'
import { useMessageStoreSync } from '@/hooks/turn/useMessageStoreSync'

export interface GroupManagementAdapter {
  groups: AgentGroup[]
  loadingGroups: boolean
  selectedGroup: string
  isOverride: boolean
  handleGroupChange: (groupId: string) => void
  handleClearOverride: () => void
  handleCreateGroup: () => void
  handleEditGroup: (group: AgentGroup) => void
  handleDeleteGroup: (group: AgentGroup) => void
  onEditRoomAgents: () => void
}

export interface QuoteState {
  quote: QuoteData | null
  setQuote: (data: QuoteData) => void
  clearQuote: () => void
}

export interface TimelineAdapter {
  roomId: string
  getToken?: () => Promise<string | null>
  onSendMessage: (message: string, targetGroup?: string, quoteData?: QuoteData | null, attachments?: PendingAttachment[]) => void
  onCancelProcessing: () => void
  onRespondToHitl: (hitlId: string, answer: string) => Promise<void>
  onChatModeChange: (mode: ChatMode) => void
  isSending: boolean
  isProcessing: boolean
  isCancelling: boolean
  agents: { id: string; name: string; iconUrl?: string }[]
  roomAgentIds: string[]
  groupManagement: GroupManagementAdapter
  quoteState: QuoteState
  chatMode: ChatMode
  externalValue?: string
  onExternalValueConsumed?: () => void
}

interface RoomPageShellProps {
  adapter: TimelineAdapter
}

export function RoomPageShell({ adapter }: RoomPageShellProps) {
  useMessageStoreSync()
  useTurnHydration(adapter.roomId, adapter.getToken)

  return (
    <>
      <main className="flex-1 overflow-hidden">
        <TurnList />
      </main>
      <div className="bg-background p-4">
        <div className="max-w-4xl mx-auto">
          <ComposerShell adapter={adapter} />
        </div>
      </div>
    </>
  )
}
```

- [ ] **Step 2: Delete legacy view file**

```bash
rm src/components/room-messages.tsx
```

- [ ] **Step 3: Run reference check for deleted symbols**

```bash
grep -r "RoomMessages\|EntityUserBubble\|EntityAgentBubble" src/ --include="*.ts" --include="*.tsx"
```

Expected: zero matches in src/ (only in tests/docs which are acceptable).

```bash
grep -r "groupMessagesByUserTurn" src/ --include="*.ts" --include="*.tsx"
```

If `groupMessagesByUserTurn` is still imported by any file other than the deleted `room-messages.tsx`, leave `message-groups.ts` intact. If only `escapeCssIdent` remains referenced, remove the `groupMessagesByUserTurn` export but keep the file.

- [ ] **Step 4: Check for orphaned message-bubble.tsx**

```bash
grep -r "message-bubble" src/ --include="*.ts" --include="*.tsx"
```

If `message-bubble.tsx` is still imported by page.tsx (for `QuoteData` type), leave it. Only delete if zero imports remain.

- [ ] **Step 5: Verify build and tests**

```bash
npx tsc --noEmit && npx vitest run --reporter=verbose 2>&1 | tail -20
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(room): remove legacy bubble view and view-switching shell"
```

---

## Task 6: Phase 2 prereq — Add clientRequestId to processing placeholder

**Files:**
- Modify: `src/hooks/room/useSendMessage.ts:81-91`

- [ ] **Step 1: Add clientRequestId to placeholder entity**

In `src/hooks/room/useSendMessage.ts`, the placeholder entity at lines 81-91 currently reads:

```typescript
      {
        id: processingPlaceholderId,
        roomId,
        messageType: 'agent',
        content: '',
        senderName: 'HYBRO AI',
        taskStatus: TASK_STATE.WORKING,
        taskContent: 'Processing your request\u2026',
        timestamp: new Date(Date.now() + 1).toISOString(),
        isEphemeral: true,
      },
```

Add `clientRequestId` (same value used by the optimistic user message on line 78):

```typescript
      {
        id: processingPlaceholderId,
        roomId,
        messageType: 'agent',
        content: '',
        senderName: 'HYBRO AI',
        taskStatus: TASK_STATE.WORKING,
        taskContent: 'Processing your request\u2026',
        timestamp: new Date(Date.now() + 1).toISOString(),
        isEphemeral: true,
        clientRequestId,
      },
```

- [ ] **Step 2: Verify build**

```bash
npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add src/hooks/room/useSendMessage.ts
git commit -m "fix(send): add clientRequestId to ephemeral processing placeholder for turn correlation"
```

---

## Task 7: Phase 2 — Define conversation selector types

**Files:**
- Create: `src/lib/selectors/conversation-types.ts`

- [ ] **Step 1: Create the types file**

```typescript
import type { MessageEntity, ArtifactData } from '@/stores/message-store/types'
import type { HITLPromptType } from '@/lib/types/sse'

// ── Agent theme ─────────────────────────────────────────────

export interface AgentTheme {
  name: string
  accent: string   // CSS custom property name, e.g. '--conversation-agent-green'
  bg: string       // subtle background tint class
}

export const AGENT_THEMES: AgentTheme[] = [
  { name: 'green',  accent: 'var(--conversation-agent-green)',  bg: 'bg-green-500/5' },
  { name: 'blue',   accent: 'var(--conversation-agent-blue)',   bg: 'bg-blue-500/5' },
  { name: 'purple', accent: 'var(--conversation-agent-purple)', bg: 'bg-purple-500/5' },
  { name: 'amber',  accent: 'var(--conversation-agent-amber)',  bg: 'bg-amber-500/5' },
  { name: 'rose',   accent: 'var(--conversation-agent-rose)',   bg: 'bg-rose-500/5' },
]

export const UNRESOLVED_THEME: AgentTheme = {
  name: 'muted', accent: 'var(--conversation-text-muted)', bg: 'bg-zinc-500/5',
}

export function getAgentTheme(agentId: string | undefined, agentName: string): AgentTheme {
  const key = agentId ?? agentName
  let hash = 0
  for (let i = 0; i < key.length; i++) {
    hash = ((hash << 5) - hash + key.charCodeAt(i)) | 0
  }
  return AGENT_THEMES[Math.abs(hash) % AGENT_THEMES.length]
}

// ── Agent display props ─────────────────────────────────────

export interface AgentDisplayProps {
  label: string
  tone: 'accent' | 'muted' | 'danger' | 'warning'
  isAnimated: boolean
  ariaLabel: string
}

// ── Conversation blocks ─────────────────────────────────────

export type ConversationBlock =
  | { type: 'agent_card'; agentId: string; agentName: string; display: AgentDisplayProps; taskDescription: string; theme: AgentTheme }
  | { type: 'agent_content'; agentId: string; agentName: string; content: string; isStreaming: boolean; artifacts?: ArtifactData[] }
  | { type: 'user_answer'; agentName: string; question: string; answer: string }
  | { type: 'agent_divider' }
  | { type: 'unresolved_content'; entity: MessageEntity }

// ── Conversation turn view ──────────────────────────────────

export interface ConversationTurnView {
  turnId: string
  userMessage: MessageEntity | null
  blocks: ConversationBlock[]
}

// ── HITL ────────────────────────────────────────────────────

export interface PendingHitl {
  hitlId: string
  agentName: string
  question: string
  promptType: HITLPromptType
  choices?: string[]
  messageId: string
  groupId?: string
  groupTotal?: number
  groupIndex?: number
  isAnswered: boolean
}

export interface HitlState {
  hitlId: string
  resolved: boolean
  question: string
  answer: string | null
}

// ── Composer ────────────────────────────────────────────────

export interface ComposerState {
  mode: 'normal' | 'hitl_responding'
  isProcessing: boolean
  pendingHitls: PendingHitl[]
}

// ── Content view ────────────────────────────────────────────

export interface ContentView {
  text: string
  isStreaming: boolean
}
```

- [ ] **Step 2: Verify build**

```bash
npx tsc --noEmit
```

Expected: no errors — this file only defines types and has no barrel yet.

- [ ] **Step 3: Commit**

```bash
git add src/lib/selectors/conversation-types.ts
git commit -m "feat(selectors): define conversation selector types"
```

> **Note:** The barrel `src/lib/selectors/index.ts` is NOT created here.
> It will be created in Task 12 Step 5 (after all selector files exist)
> so that every commit passes `tsc --noEmit`.

---

## Task 8: Phase 2 — Implement routeAgentToTurn

**Files:**
- Create: `src/lib/selectors/route-agent.ts`
- Create: `tests/unit/lib/selectors/route-agent.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/lib/selectors/route-agent.test.ts`:

```typescript
import { describe, it, expect, beforeEach } from 'vitest'
import { routeAgentToTurn } from '@/lib/selectors/route-agent'
import type { MessageEntity } from '@/stores/message-store/types'
import { createUserMessage, createAgentMessage, resetCounters } from '../../../fixtures'
import { useMessageStore } from '@/stores/message-store'

function makeEntities(msgs: ReturnType<typeof createUserMessage>[]) {
  const store = useMessageStore.getState()
  store.clearRoom()
  store.setRoom('room-1')
  for (const m of msgs) store.upsertMessage(m, m.id.startsWith('cr:') ? 'optimistic' : 'db')
  const s = useMessageStore.getState()
  return { entities: s.entities, orderedIds: s.orderedIds }
}

describe('routeAgentToTurn', () => {
  beforeEach(() => {
    useMessageStore.getState().clearRoom()
    resetCounters()
  })

  it('routes agent via relatedMessageId to user message (tier 1)', () => {
    const user = createUserMessage({ id: 'user-1', roomId: 'room-1' })
    const agent = createAgentMessage({
      id: 'agent-1',
      roomId: 'room-1',
      relatedMessageId: 'user-1',
    })
    const { entities } = makeEntities([user, agent])
    const userMessageIds = new Set(['user-1'])

    const result = routeAgentToTurn(entities['agent-1'], userMessageIds, entities)
    expect(result).toBe('user-1')
  })

  it('routes via relatedMessageId chain (2 hops max)', () => {
    const user = createUserMessage({ id: 'user-1', roomId: 'room-1' })
    const intermediate = createAgentMessage({
      id: 'agent-mid',
      roomId: 'room-1',
      relatedMessageId: 'user-1',
    })
    const leaf = createAgentMessage({
      id: 'agent-leaf',
      roomId: 'room-1',
      relatedMessageId: 'agent-mid',
    })
    const { entities } = makeEntities([user, intermediate, leaf])
    const userMessageIds = new Set(['user-1'])

    const result = routeAgentToTurn(entities['agent-leaf'], userMessageIds, entities)
    expect(result).toBe('user-1')
  })

  it('does NOT follow more than 2 hops', () => {
    const user = createUserMessage({ id: 'user-1', roomId: 'room-1' })
    const hop1 = createAgentMessage({ id: 'hop1', roomId: 'room-1', relatedMessageId: 'user-1' })
    const hop2 = createAgentMessage({ id: 'hop2', roomId: 'room-1', relatedMessageId: 'hop1' })
    const hop3 = createAgentMessage({ id: 'hop3', roomId: 'room-1', relatedMessageId: 'hop2' })
    const { entities } = makeEntities([user, hop1, hop2, hop3])
    const userMessageIds = new Set(['user-1'])

    const result = routeAgentToTurn(entities['hop3'], userMessageIds, entities)
    expect(result).toBe('unresolved')
  })

  it('routes via clientRequestId to optimistic user (tier 2, live only)', () => {
    const user = createUserMessage({
      id: 'cr:req-123',
      roomId: 'room-1',
      clientRequestId: 'req-123',
    })
    const agent = createAgentMessage({
      id: 'agent-1',
      roomId: 'room-1',
      clientRequestId: 'req-123',
    })
    const { entities } = makeEntities([user, agent])
    const userMessageIds = new Set(['cr:req-123'])

    const result = routeAgentToTurn(entities['agent-1'], userMessageIds, entities)
    expect(result).toBe('cr:req-123')
  })

  it('prefers relatedMessageId over clientRequestId when both present', () => {
    const user = createUserMessage({ id: 'user-1', roomId: 'room-1', clientRequestId: 'req-123' })
    const agent = createAgentMessage({
      id: 'agent-1',
      roomId: 'room-1',
      relatedMessageId: 'user-1',
      clientRequestId: 'req-123',
    })
    const { entities } = makeEntities([user, agent])
    const userMessageIds = new Set(['user-1'])

    const result = routeAgentToTurn(entities['agent-1'], userMessageIds, entities)
    expect(result).toBe('user-1')
  })

  it('returns unresolved for agent without relatedMessageId or clientRequestId', () => {
    const user = createUserMessage({ id: 'user-1', roomId: 'room-1' })
    const orphan = createAgentMessage({ id: 'orphan-1', roomId: 'room-1' })
    const { entities } = makeEntities([user, orphan])
    const userMessageIds = new Set(['user-1'])

    const result = routeAgentToTurn(entities['orphan-1'], userMessageIds, entities)
    expect(result).toBe('unresolved')
  })

  it('does NOT auto-attach unresolved agent to most recent turn', () => {
    const user1 = createUserMessage({ id: 'user-1', roomId: 'room-1' })
    const user2 = createUserMessage({ id: 'user-2', roomId: 'room-1' })
    const orphan = createAgentMessage({ id: 'orphan-1', roomId: 'room-1' })
    const { entities } = makeEntities([user1, user2, orphan])
    const userMessageIds = new Set(['user-1', 'user-2'])

    const result = routeAgentToTurn(entities['orphan-1'], userMessageIds, entities)
    expect(result).toBe('unresolved')
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run tests/unit/lib/selectors/route-agent.test.ts --reporter=verbose 2>&1 | tail -20
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement routeAgentToTurn**

Create `src/lib/selectors/route-agent.ts`:

```typescript
import type { MessageEntity } from '@/stores/message-store/types'

const MAX_HOPS = 2

/**
 * Persisted-first three-tier routing:
 *   1. relatedMessageId chain → persisted user message id
 *   2. clientRequestId → optimistic user entity (live correlation only)
 *   3. 'unresolved'
 *
 * clientRequestId fallback must never determine ConversationTurnView.turnId;
 * it only locates the current optimistic user entity, whose id may later be
 * replaced by the persisted user message id via replaceMessageId.
 */
export function routeAgentToTurn(
  entity: MessageEntity,
  userMessageIds: Set<string>,
  entityById: Record<string, MessageEntity>,
): string | 'unresolved' {
  // Tier 1: relatedMessageId chain (stable path)
  if (entity.relatedMessageId) {
    let current = entity.relatedMessageId
    for (let hop = 0; hop < MAX_HOPS; hop++) {
      if (userMessageIds.has(current)) return current
      const parent = entityById[current]
      if (!parent?.relatedMessageId) break
      current = parent.relatedMessageId
    }
  }

  // Tier 2: clientRequestId (live correlation only)
  if (entity.clientRequestId) {
    for (const uid of userMessageIds) {
      const userEntity = entityById[uid]
      if (userEntity?.clientRequestId === entity.clientRequestId) return uid
    }
  }

  // Tier 3: unresolved
  return 'unresolved'
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run tests/unit/lib/selectors/route-agent.test.ts --reporter=verbose
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/selectors/route-agent.ts tests/unit/lib/selectors/route-agent.test.ts
git commit -m "feat(selectors): implement routeAgentToTurn with persisted-first routing"
```

---

## Task 9: Phase 2 — Implement mapAgentDisplayProps

**Files:**
- Create: `src/lib/selectors/map-agent-display.ts`
- Create: `tests/unit/lib/selectors/map-agent-display.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/lib/selectors/map-agent-display.test.ts`:

```typescript
import { describe, it, expect, beforeEach } from 'vitest'
import { mapAgentDisplayProps } from '@/lib/selectors/map-agent-display'
import { createAgentMessage, createTaskMessage, resetCounters } from '../../../fixtures'
import { TASK_STATE } from '@/lib/types/sse'
import type { MessageEntity } from '@/stores/message-store/types'

function asEntity(msg: ReturnType<typeof createAgentMessage>): MessageEntity {
  return {
    ...msg,
    source: 'db' as const,
    sourceVersion: 1,
    displayType: 'agent-bubble' as const,
    isEphemeral: false,
    createdAt: Date.now(),
    updatedAt: Date.now(),
  } as MessageEntity
}

describe('mapAgentDisplayProps', () => {
  beforeEach(() => resetCounters())

  it('returns Working for submitted status', () => {
    const entity = asEntity(createTaskMessage(TASK_STATE.SUBMITTED, { senderName: 'Analyst' }))
    const result = mapAgentDisplayProps(entity)
    expect(result.label).toBe('Working')
    expect(result.tone).toBe('accent')
    expect(result.isAnimated).toBe(true)
    expect(result.ariaLabel).toBe('Analyst — Working')
  })

  it('returns Working for working status without content', () => {
    const entity = asEntity(createTaskMessage(TASK_STATE.WORKING, { content: '' }))
    const result = mapAgentDisplayProps(entity)
    expect(result.label).toBe('Working')
    expect(result.isAnimated).toBe(true)
  })

  it('returns Streaming for working status with content', () => {
    const entity = asEntity(createTaskMessage(TASK_STATE.WORKING, { content: 'partial response...' }))
    const result = mapAgentDisplayProps(entity)
    expect(result.label).toBe('Streaming')
    expect(result.tone).toBe('accent')
    expect(result.isAnimated).toBe(true)
  })

  it('returns Completed with relative time for completed status', () => {
    const entity = asEntity(createTaskMessage(TASK_STATE.COMPLETED, {
      taskUpdatedAt: new Date(Date.now() - 120_000).toISOString(),
    }))
    const result = mapAgentDisplayProps(entity)
    expect(result.label).toMatch(/^Completed/)
    expect(result.tone).toBe('muted')
    expect(result.isAnimated).toBe(false)
  })

  it('returns Failed for failed status', () => {
    const entity = asEntity(createTaskMessage(TASK_STATE.FAILED))
    const result = mapAgentDisplayProps(entity)
    expect(result.label).toBe('Failed')
    expect(result.tone).toBe('danger')
  })

  it('returns Rejected for rejected status', () => {
    const entity = asEntity(createTaskMessage(TASK_STATE.REJECTED))
    const result = mapAgentDisplayProps(entity)
    expect(result.label).toBe('Rejected')
    expect(result.tone).toBe('danger')
  })

  it('returns Canceled with muted tone for canceled status', () => {
    const entity = asEntity(createTaskMessage(TASK_STATE.CANCELED))
    const result = mapAgentDisplayProps(entity)
    expect(result.label).toBe('Canceled')
    expect(result.tone).toBe('muted')
  })

  it('returns Needs Input for input-required status', () => {
    const entity = asEntity(createTaskMessage(TASK_STATE.INPUT_REQUIRED))
    const result = mapAgentDisplayProps(entity)
    expect(result.label).toBe('Needs Input')
    expect(result.tone).toBe('warning')
    expect(result.isAnimated).toBe(true)
  })

  it('returns Auth Required for auth-required status', () => {
    const entity = asEntity(createTaskMessage(TASK_STATE.AUTH_REQUIRED))
    const result = mapAgentDisplayProps(entity)
    expect(result.label).toBe('Auth Required')
    expect(result.tone).toBe('warning')
    expect(result.isAnimated).toBe(false)
  })

  it('returns Working for unknown status', () => {
    const entity = asEntity(createTaskMessage('unknown' as any))
    const result = mapAgentDisplayProps(entity)
    expect(result.label).toBe('Working')
    expect(result.tone).toBe('accent')
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run tests/unit/lib/selectors/map-agent-display.test.ts --reporter=verbose 2>&1 | tail -5
```

- [ ] **Step 3: Implement mapAgentDisplayProps**

Create `src/lib/selectors/map-agent-display.ts`:

```typescript
import type { MessageEntity } from '@/stores/message-store/types'
import type { AgentDisplayProps } from './conversation-types'
import type { TaskState } from '@/lib/types/sse'

function relativeTime(isoDate: string | undefined | null): string {
  if (!isoDate) return ''
  const diffMs = Date.now() - new Date(isoDate).getTime()
  const mins = Math.floor(diffMs / 60_000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

function make(name: string, label: string, tone: AgentDisplayProps['tone'], isAnimated: boolean): AgentDisplayProps {
  return { label, tone, isAnimated, ariaLabel: `${name} — ${label}` }
}

export function mapAgentDisplayProps(entity: MessageEntity): AgentDisplayProps {
  const name = entity.senderName ?? 'Agent'
  const status: TaskState | null | undefined = entity.taskStatus

  if (status == null) return make(name, 'Working', 'accent', true)

  switch (status) {
    case 'submitted':
      return make(name, 'Working', 'accent', true)

    case 'working': {
      const hasContent = (entity.content ?? '').trim().length > 0
      return hasContent
        ? make(name, 'Streaming', 'accent', true)
        : make(name, 'Working', 'accent', true)
    }

    case 'completed': {
      const time = relativeTime(entity.taskUpdatedAt)
      const label = time ? `Completed · ${time}` : 'Completed'
      return make(name, label, 'muted', false)
    }

    case 'failed':
      return make(name, 'Failed', 'danger', false)

    case 'rejected':
      return make(name, 'Rejected', 'danger', false)

    case 'canceled':
      return make(name, 'Canceled', 'muted', false)

    case 'input-required':
      return make(name, 'Needs Input', 'warning', true)

    case 'auth-required':
      return make(name, 'Auth Required', 'warning', false)

    case 'unknown':
      return make(name, 'Working', 'accent', true)

    default: {
      const _exhaustive: never = status
      return _exhaustive
    }
  }
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run tests/unit/lib/selectors/map-agent-display.test.ts --reporter=verbose
```

Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/selectors/map-agent-display.ts tests/unit/lib/selectors/map-agent-display.test.ts
git commit -m "feat(selectors): implement mapAgentDisplayProps pure mapper"
```

---

## Task 10: Phase 2 — Implement selectPendingHitls and selectAgentHitlState

**Files:**
- Create: `src/lib/selectors/select-hitl.ts`
- Create: `tests/unit/lib/selectors/select-hitl.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/lib/selectors/select-hitl.test.ts`:

```typescript
import { describe, it, expect, beforeEach } from 'vitest'
import { selectPendingHitls, selectAgentHitlState } from '@/lib/selectors/select-hitl'
import { useMessageStore } from '@/stores/message-store'
import { createAgentMessage, resetCounters } from '../../../fixtures'
import { TASK_STATE } from '@/lib/types/sse'

function setup(msgs: ReturnType<typeof createAgentMessage>[]) {
  const store = useMessageStore.getState()
  store.clearRoom()
  store.setRoom('room-1')
  for (const m of msgs) store.upsertMessage(m, 'db')
  const s = useMessageStore.getState()
  return { entities: s.entities, orderedIds: s.orderedIds }
}

describe('selectPendingHitls', () => {
  beforeEach(() => {
    useMessageStore.getState().clearRoom()
    resetCounters()
  })

  it('returns non-grouped unresolved HITL', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({
        id: 'hitl-1', roomId: 'room-1',
        hitlRequestId: 'req-1', hitlPrompt: 'What is your name?',
        senderName: 'Analyst', taskStatus: TASK_STATE.INPUT_REQUIRED,
      }),
    ])
    const result = selectPendingHitls('room-1', entities, orderedIds)
    expect(result).toHaveLength(1)
    expect(result[0].hitlId).toBe('req-1')
    expect(result[0].question).toBe('What is your name?')
    expect(result[0].isAnswered).toBe(false)
  })

  it('excludes resolved non-grouped HITL', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({
        id: 'hitl-1', roomId: 'room-1',
        hitlRequestId: 'req-1', hitlResolved: true,
        hitlPrompt: 'Done?', hitlUserAnswer: 'Yes',
        senderName: 'Analyst', taskStatus: TASK_STATE.COMPLETED,
      }),
    ])
    const result = selectPendingHitls('room-1', entities, orderedIds)
    expect(result).toHaveLength(0)
  })

  it('returns entire group when any member is unanswered', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({
        id: 'g1-q1', roomId: 'room-1',
        hitlRequestId: 'req-1', hitlPrompt: 'Q1?',
        hitlGroupId: 'group-A', hitlGroupTotal: 2, hitlGroupIndex: 0,
        hitlResolved: true, hitlUserAnswer: 'A1',
        senderName: 'Analyst', taskStatus: TASK_STATE.INPUT_REQUIRED,
      }),
      createAgentMessage({
        id: 'g1-q2', roomId: 'room-1',
        hitlRequestId: 'req-2', hitlPrompt: 'Q2?',
        hitlGroupId: 'group-A', hitlGroupTotal: 2, hitlGroupIndex: 1,
        senderName: 'Analyst', taskStatus: TASK_STATE.INPUT_REQUIRED,
      }),
    ])
    const result = selectPendingHitls('room-1', entities, orderedIds)
    expect(result).toHaveLength(2)
    expect(result[0].isAnswered).toBe(true)
    expect(result[1].isAnswered).toBe(false)
    expect(result[0].groupId).toBe('group-A')
  })

  it('passes through choice promptType and choices', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({
        id: 'hitl-c', roomId: 'room-1',
        hitlRequestId: 'req-c', hitlPrompt: 'Pick one',
        hitlPromptType: 'choice', hitlChoices: ['A', 'B', 'C'],
        senderName: 'Analyst', taskStatus: TASK_STATE.INPUT_REQUIRED,
      }),
    ])
    const result = selectPendingHitls('room-1', entities, orderedIds)
    expect(result).toHaveLength(1)
    expect(result[0].promptType).toBe('choice')
    expect(result[0].choices).toEqual(['A', 'B', 'C'])
  })

  it('passes through confirmation promptType', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({
        id: 'hitl-cf', roomId: 'room-1',
        hitlRequestId: 'req-cf', hitlPrompt: 'Approve deploy?',
        hitlPromptType: 'confirmation',
        senderName: 'Analyst', taskStatus: TASK_STATE.INPUT_REQUIRED,
      }),
    ])
    const result = selectPendingHitls('room-1', entities, orderedIds)
    expect(result).toHaveLength(1)
    expect(result[0].promptType).toBe('confirmation')
    expect(result[0].choices).toBeUndefined()
  })

  it('defaults promptType to text when hitlPromptType is undefined', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({
        id: 'hitl-t', roomId: 'room-1',
        hitlRequestId: 'req-t', hitlPrompt: 'What?',
        senderName: 'Analyst', taskStatus: TASK_STATE.INPUT_REQUIRED,
      }),
    ])
    const result = selectPendingHitls('room-1', entities, orderedIds)
    expect(result[0].promptType).toBe('text')
  })

  it('excludes HITL from other rooms', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({
        id: 'hitl-other', roomId: 'room-2',
        hitlRequestId: 'req-1', hitlPrompt: 'Q?',
        senderName: 'Analyst', taskStatus: TASK_STATE.INPUT_REQUIRED,
      }),
    ])
    const result = selectPendingHitls('room-1', entities, orderedIds)
    expect(result).toHaveLength(0)
  })
})

describe('selectAgentHitlState', () => {
  beforeEach(() => resetCounters())

  it('returns null for entity without hitlRequestId', () => {
    const entity = {
      ...createAgentMessage({ id: 'a1', roomId: 'room-1' }),
      source: 'db' as const, sourceVersion: 1,
      displayType: 'agent-bubble' as const,
      isEphemeral: false, createdAt: Date.now(), updatedAt: Date.now(),
    }
    expect(selectAgentHitlState(entity as any)).toBeNull()
  })

  it('returns HitlState with question from hitlPrompt', () => {
    const entity = {
      ...createAgentMessage({
        id: 'a1', roomId: 'room-1',
        hitlRequestId: 'req-1', hitlPrompt: 'How?', hitlUserAnswer: 'Like this',
        hitlResolved: true,
      }),
      source: 'db' as const, sourceVersion: 1,
      displayType: 'agent-bubble' as const,
      isEphemeral: false, createdAt: Date.now(), updatedAt: Date.now(),
    }
    const result = selectAgentHitlState(entity as any)
    expect(result).toEqual({
      hitlId: 'req-1',
      resolved: true,
      question: 'How?',
      answer: 'Like this',
    })
  })

  it('falls back to content when hitlPrompt is missing', () => {
    const entity = {
      ...createAgentMessage({
        id: 'a1', roomId: 'room-1',
        hitlRequestId: 'req-1', content: 'Agent needs input',
      }),
      source: 'db' as const, sourceVersion: 1,
      displayType: 'agent-bubble' as const,
      isEphemeral: false, createdAt: Date.now(), updatedAt: Date.now(),
    }
    const result = selectAgentHitlState(entity as any)
    expect(result!.question).toBe('Agent needs input')
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run tests/unit/lib/selectors/select-hitl.test.ts --reporter=verbose 2>&1 | tail -5
```

- [ ] **Step 3: Implement HITL selectors**

Create `src/lib/selectors/select-hitl.ts`:

```typescript
import type { MessageEntity } from '@/stores/message-store/types'
import type { PendingHitl, HitlState } from './conversation-types'

export function selectPendingHitls(
  roomId: string,
  entities: Record<string, MessageEntity>,
  orderedIds: string[],
): PendingHitl[] {
  const hitlEntities: MessageEntity[] = []
  for (const id of orderedIds) {
    const e = entities[id]
    if (e && e.roomId === roomId && e.hitlRequestId) hitlEntities.push(e)
  }

  const activeGroupIds = new Set<string>()
  for (const e of hitlEntities) {
    if (e.hitlGroupId && !e.hitlResolved && !e.hitlUserAnswer) {
      activeGroupIds.add(e.hitlGroupId)
    }
  }

  return hitlEntities
    .filter(e => {
      if (!e.hitlGroupId) return !e.hitlResolved
      return activeGroupIds.has(e.hitlGroupId)
    })
    .map(e => ({
      hitlId: e.hitlRequestId!,
      agentName: e.senderName,
      question: e.hitlPrompt ?? e.content ?? e.taskStatusMessage ?? '',
      promptType: e.hitlPromptType ?? 'text',
      choices: e.hitlChoices ?? undefined,
      messageId: e.id,
      groupId: e.hitlGroupId,
      groupTotal: e.hitlGroupTotal,
      groupIndex: e.hitlGroupIndex,
      isAnswered: e.hitlResolved === true || !!e.hitlUserAnswer,
    }))
}

export function selectAgentHitlState(entity: MessageEntity): HitlState | null {
  if (!entity.hitlRequestId) return null
  return {
    hitlId: entity.hitlRequestId,
    resolved: entity.hitlResolved === true,
    question: entity.hitlPrompt ?? entity.content ?? entity.taskStatusMessage ?? '',
    answer: entity.hitlUserAnswer ?? null,
  }
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run tests/unit/lib/selectors/select-hitl.test.ts --reporter=verbose
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/selectors/select-hitl.ts tests/unit/lib/selectors/select-hitl.test.ts
git commit -m "feat(selectors): implement selectPendingHitls and selectAgentHitlState"
```

---

## Task 11: Phase 2 — Implement selectComposerState

**Files:**
- Create: `src/lib/selectors/select-composer-state.ts`
- Create: `tests/unit/lib/selectors/select-composer-state.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/lib/selectors/select-composer-state.test.ts`:

```typescript
import { describe, it, expect, beforeEach } from 'vitest'
import { selectComposerState } from '@/lib/selectors/select-composer-state'
import { useMessageStore } from '@/stores/message-store'
import { createAgentMessage, createUserMessage, resetCounters } from '../../../fixtures'
import { TASK_STATE } from '@/lib/types/sse'

function setup(msgs: ReturnType<typeof createAgentMessage>[]) {
  const store = useMessageStore.getState()
  store.clearRoom()
  store.setRoom('room-1')
  for (const m of msgs) store.upsertMessage(m, 'db')
  const s = useMessageStore.getState()
  return { entities: s.entities, orderedIds: s.orderedIds }
}

describe('selectComposerState', () => {
  beforeEach(() => {
    useMessageStore.getState().clearRoom()
    resetCounters()
  })

  it('returns normal mode with no processing when idle', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'u1', roomId: 'room-1' }),
      createAgentMessage({ id: 'a1', roomId: 'room-1', taskStatus: TASK_STATE.COMPLETED }),
    ])
    const result = selectComposerState('room-1', entities, orderedIds)
    expect(result.mode).toBe('normal')
    expect(result.isProcessing).toBe(false)
    expect(result.pendingHitls).toHaveLength(0)
  })

  it('returns isProcessing=true when agent is working', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({ id: 'a1', roomId: 'room-1', taskStatus: TASK_STATE.WORKING }),
    ])
    const result = selectComposerState('room-1', entities, orderedIds)
    expect(result.isProcessing).toBe(true)
  })

  it('excludes ephemeral from processing check', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({ id: 'a1', roomId: 'room-1', taskStatus: TASK_STATE.WORKING, isEphemeral: true }),
    ])
    const result = selectComposerState('room-1', entities, orderedIds)
    expect(result.isProcessing).toBe(false)
  })

  it('returns hitl_responding mode when HITL is pending', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({
        id: 'a1', roomId: 'room-1',
        hitlRequestId: 'req-1', hitlPrompt: 'Q?',
        senderName: 'Agent', taskStatus: TASK_STATE.INPUT_REQUIRED,
      }),
    ])
    const result = selectComposerState('room-1', entities, orderedIds)
    expect(result.mode).toBe('hitl_responding')
    expect(result.pendingHitls).toHaveLength(1)
  })

  it('input-required is NOT counted as processing', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({
        id: 'a1', roomId: 'room-1',
        taskStatus: TASK_STATE.INPUT_REQUIRED,
        hitlRequestId: 'req-1', hitlPrompt: 'Q?', senderName: 'Agent',
      }),
    ])
    const result = selectComposerState('room-1', entities, orderedIds)
    expect(result.isProcessing).toBe(false)
  })

  it('excludes agents from other rooms', () => {
    const { entities, orderedIds } = setup([
      createAgentMessage({ id: 'a1', roomId: 'room-2', taskStatus: TASK_STATE.WORKING }),
    ])
    const result = selectComposerState('room-1', entities, orderedIds)
    expect(result.isProcessing).toBe(false)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run tests/unit/lib/selectors/select-composer-state.test.ts --reporter=verbose 2>&1 | tail -5
```

- [ ] **Step 3: Implement selectComposerState**

Create `src/lib/selectors/select-composer-state.ts`:

```typescript
import type { MessageEntity } from '@/stores/message-store/types'
import type { ComposerState } from './conversation-types'
import { selectPendingHitls } from './select-hitl'
import { isPendingState } from '@/lib/types/sse'

export function selectComposerState(
  roomId: string,
  entities: Record<string, MessageEntity>,
  orderedIds: string[],
): ComposerState {
  const pendingHitls = selectPendingHitls(roomId, entities, orderedIds)

  const hasActiveTask = orderedIds.some(id => {
    const e = entities[id]
    return e
      && e.roomId === roomId
      && !e.isEphemeral
      && e.messageType === 'agent'
      && e.taskStatus != null
      && isPendingState(e.taskStatus)
  })

  return {
    mode: pendingHitls.length > 0 ? 'hitl_responding' : 'normal',
    isProcessing: hasActiveTask,
    pendingHitls,
  }
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run tests/unit/lib/selectors/select-composer-state.test.ts --reporter=verbose
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/selectors/select-composer-state.ts tests/unit/lib/selectors/select-composer-state.test.ts
git commit -m "feat(selectors): implement selectComposerState with room/ephemeral filtering"
```

---

## Task 12: Phase 2 — Implement selectConversationTurns

**Files:**
- Create: `src/lib/selectors/select-conversation-turns.ts`
- Create: `tests/unit/lib/selectors/select-conversation-turns.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/lib/selectors/select-conversation-turns.test.ts`:

```typescript
import { describe, it, expect, beforeEach } from 'vitest'
import { selectConversationTurns } from '@/lib/selectors/select-conversation-turns'
import { useMessageStore } from '@/stores/message-store'
import { createUserMessage, createAgentMessage, resetCounters } from '../../../fixtures'
import { TASK_STATE } from '@/lib/types/sse'

function setup(msgs: ReturnType<typeof createUserMessage>[]) {
  const store = useMessageStore.getState()
  store.clearRoom()
  store.setRoom('room-1')
  for (const m of msgs) store.upsertMessage(m, m.id.startsWith('cr:') ? 'optimistic' : 'db')
  const s = useMessageStore.getState()
  return { entities: s.entities, orderedIds: s.orderedIds }
}

describe('selectConversationTurns', () => {
  beforeEach(() => {
    useMessageStore.getState().clearRoom()
    resetCounters()
  })

  it('groups agent under user message via relatedMessageId', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'user-1', roomId: 'room-1' }),
      createAgentMessage({
        id: 'agent-1', roomId: 'room-1',
        relatedMessageId: 'user-1',
        taskStatus: TASK_STATE.COMPLETED, content: 'Done',
      }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    expect(turns).toHaveLength(1)
    expect(turns[0].turnId).toBe('user-1')
    expect(turns[0].userMessage!.id).toBe('user-1')
    expect(turns[0].blocks.length).toBeGreaterThanOrEqual(2)
  })

  it('optimistic turn uses cr: prefix as temporary turnId', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'cr:req-123', roomId: 'room-1', clientRequestId: 'req-123' }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    expect(turns).toHaveLength(1)
    expect(turns[0].turnId).toBe('cr:req-123')
  })

  it('after replaceMessageId, turnId becomes persisted id', () => {
    const store = useMessageStore.getState()
    store.clearRoom()
    store.setRoom('room-1')
    store.upsertMessage(
      createUserMessage({ id: 'cr:req-123', roomId: 'room-1', clientRequestId: 'req-123' }),
      'optimistic',
    )
    store.replaceMessageId('cr:req-123', 'persisted-user-1')
    const s = useMessageStore.getState()
    const turns = selectConversationTurns('room-1', s.entities, s.orderedIds)
    expect(turns).toHaveLength(1)
    expect(turns[0].turnId).toBe('persisted-user-1')
    expect(turns[0].turnId).not.toMatch(/^cr:/)
  })

  it('hydrated history never produces cr: turnId', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'db-user-1', roomId: 'room-1', clientRequestId: 'old-req' }),
      createAgentMessage({
        id: 'db-agent-1', roomId: 'room-1',
        relatedMessageId: 'db-user-1', taskStatus: TASK_STATE.COMPLETED,
      }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    for (const t of turns) {
      expect(t.turnId).not.toMatch(/^cr:/)
    }
  })

  it('unresolved agents go to __unresolved__ bucket', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'user-1', roomId: 'room-1' }),
      createAgentMessage({ id: 'orphan-1', roomId: 'room-1', taskStatus: TASK_STATE.COMPLETED }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    const unresolved = turns.find(t => t.turnId === '__unresolved__')
    expect(unresolved).toBeDefined()
    expect(unresolved!.userMessage).toBeNull()
  })

  it('unresolved does NOT auto-attach to nearest user turn', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'user-1', roomId: 'room-1' }),
      createAgentMessage({ id: 'orphan-1', roomId: 'room-1', taskStatus: TASK_STATE.COMPLETED, senderName: 'Orphan Agent' }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    const userTurn = turns.find(t => t.turnId === 'user-1')
    const orphanInUserTurn = userTurn?.blocks.some(
      b => (b.type === 'agent_card' || b.type === 'agent_content') &&
           'agentId' in b && b.agentId === 'orphan-1'
    )
    expect(orphanInUserTurn).toBeFalsy()
    const unresolvedTurn = turns.find(t => t.turnId === '__unresolved__')
    expect(unresolvedTurn).toBeDefined()
    expect(unresolvedTurn!.blocks.some(
      b => b.type === 'agent_card' && b.agentId === 'orphan-1'
    )).toBe(true)
  })

  it('ephemeral placeholder produces synthetic working card', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'cr:req-1', roomId: 'room-1', clientRequestId: 'req-1' }),
      createAgentMessage({
        id: 'placeholder-1', roomId: 'room-1',
        isEphemeral: true, clientRequestId: 'req-1',
        taskStatus: TASK_STATE.WORKING, senderName: 'HYBRO AI',
      }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    expect(turns).toHaveLength(1)
    const cards = turns[0].blocks.filter(b => b.type === 'agent_card')
    expect(cards).toHaveLength(1)
    expect(cards[0].type === 'agent_card' && cards[0].display.label).toBe('Working')
  })

  it('deduplicates synthetic card when real agent arrives', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'cr:req-1', roomId: 'room-1', clientRequestId: 'req-1' }),
      createAgentMessage({
        id: 'placeholder-1', roomId: 'room-1',
        isEphemeral: true, clientRequestId: 'req-1',
        taskStatus: TASK_STATE.WORKING, senderName: 'HYBRO AI',
      }),
      createAgentMessage({
        id: 'real-agent-1', roomId: 'room-1',
        clientRequestId: 'req-1', relatedMessageId: 'cr:req-1',
        taskStatus: TASK_STATE.WORKING, senderName: 'Security Analyst',
        agentId: 'sa-1',
      }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    expect(turns).toHaveLength(1)
    const cards = turns[0].blocks.filter(b => b.type === 'agent_card')
    expect(cards).toHaveLength(1)
    expect(cards[0].type === 'agent_card' && cards[0].agentName).toBe('Security Analyst')
  })

  it('creates user_answer block for resolved HITL', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'user-1', roomId: 'room-1' }),
      createAgentMessage({
        id: 'hitl-1', roomId: 'room-1',
        relatedMessageId: 'user-1',
        hitlRequestId: 'req-1', hitlPrompt: 'Confirm?',
        hitlResolved: true, hitlUserAnswer: 'Yes',
        senderName: 'Analyst', taskStatus: TASK_STATE.COMPLETED,
      }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    const answers = turns[0].blocks.filter(b => b.type === 'user_answer')
    expect(answers).toHaveLength(1)
    expect(answers[0].type === 'user_answer' && answers[0].question).toBe('Confirm?')
    expect(answers[0].type === 'user_answer' && answers[0].answer).toBe('Yes')
  })

  it('adds agent_divider between different agents in same turn', () => {
    const { entities, orderedIds } = setup([
      createUserMessage({ id: 'user-1', roomId: 'room-1' }),
      createAgentMessage({
        id: 'a1', roomId: 'room-1', relatedMessageId: 'user-1',
        agentId: 'agent-a', senderName: 'Agent A',
        taskStatus: TASK_STATE.COMPLETED, content: 'Response A',
      }),
      createAgentMessage({
        id: 'a2', roomId: 'room-1', relatedMessageId: 'user-1',
        agentId: 'agent-b', senderName: 'Agent B',
        taskStatus: TASK_STATE.COMPLETED, content: 'Response B',
      }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    const dividers = turns[0].blocks.filter(b => b.type === 'agent_divider')
    expect(dividers.length).toBeGreaterThanOrEqual(1)
  })

  it('user message with attachments retains attachments on the turn', () => {
    const attachments = [
      { fileId: 'f1', mimeType: 'image/png', fileName: 'screenshot.png', sizeBytes: 1024 },
      { fileId: 'f2', mimeType: 'application/pdf', fileName: 'doc.pdf', sizeBytes: 2048 },
    ]
    const { entities, orderedIds } = seedMessages([
      createUserMessage({ id: 'user-1', roomId: 'room-1', attachments }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    expect(turns).toHaveLength(1)
    expect(turns[0].userMessage?.attachments).toEqual(attachments)
  })

  it('agent message with artifacts creates agent_content block with artifacts', () => {
    const artifacts = [
      { artifactId: 'art-1', name: 'result.json', parts: [{ type: 'text', text: '{}' }] },
    ]
    const { entities, orderedIds } = seedMessages([
      createUserMessage({ id: 'user-1', roomId: 'room-1' }),
      createAgentMessage({
        id: 'a1', roomId: 'room-1', relatedMessageId: 'user-1',
        agentId: 'agent-a', senderName: 'Agent A',
        taskStatus: TASK_STATE.COMPLETED, content: 'Here are the results',
        artifacts,
      }),
    ])
    const turns = selectConversationTurns('room-1', entities, orderedIds)
    const contentBlocks = turns[0].blocks.filter(b => b.type === 'agent_content')
    expect(contentBlocks).toHaveLength(1)
    expect(contentBlocks[0]).toHaveProperty('artifacts', artifacts)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run tests/unit/lib/selectors/select-conversation-turns.test.ts --reporter=verbose 2>&1 | tail -5
```

- [ ] **Step 3: Implement selectConversationTurns**

Create `src/lib/selectors/select-conversation-turns.ts`:

```typescript
import type { MessageEntity } from '@/stores/message-store/types'
import type { ConversationTurnView, ConversationBlock } from './conversation-types'
import { getAgentTheme, UNRESOLVED_THEME } from './conversation-types'
import { routeAgentToTurn } from './route-agent'
import { mapAgentDisplayProps } from './map-agent-display'
import { selectAgentHitlState } from './select-hitl'

export function selectConversationTurns(
  roomId: string,
  entities: Record<string, MessageEntity>,
  orderedIds: string[],
): ConversationTurnView[] {
  const userMessageIds = new Set<string>()
  const userEntitiesOrdered: MessageEntity[] = []
  const agentEntities: MessageEntity[] = []
  const ephemeralByClientReqId = new Map<string, MessageEntity>()

  for (const id of orderedIds) {
    const e = entities[id]
    if (!e || e.roomId !== roomId) continue

    if (e.messageType === 'user') {
      userMessageIds.add(e.id)
      userEntitiesOrdered.push(e)
    } else if (e.isEphemeral) {
      if (e.clientRequestId) ephemeralByClientReqId.set(e.clientRequestId, e)
    } else {
      agentEntities.push(e)
    }
  }

  // Route agents to turns
  const turnBlocks = new Map<string, ConversationBlock[]>()
  const unresolvedBlocks: ConversationBlock[] = []

  // Track which clientRequestIds have real agents (for dedup)
  const clientReqIdsWithRealAgent = new Set<string>()
  for (const agent of agentEntities) {
    if (agent.clientRequestId) clientReqIdsWithRealAgent.add(agent.clientRequestId)
  }

  for (const agent of agentEntities) {
    const targetTurn = routeAgentToTurn(agent, userMessageIds, entities)

    const blocks = targetTurn === 'unresolved'
      ? unresolvedBlocks
      : (turnBlocks.get(targetTurn) ?? (() => { const b: ConversationBlock[] = []; turnBlocks.set(targetTurn, b); return b })())

    const theme = targetTurn === 'unresolved'
      ? UNRESOLVED_THEME
      : getAgentTheme(agent.agentId, agent.senderName)

    // Agent card
    blocks.push({
      type: 'agent_card',
      agentId: agent.agentId ?? agent.id,
      agentName: agent.senderName,
      display: mapAgentDisplayProps(agent),
      taskDescription: agent.taskContent ?? agent.taskStatusMessage ?? '',
      theme,
    })

    // Agent content (if non-empty content or has artifacts)
    const content = (agent.content ?? '').trim()
    const hasArtifacts = agent.artifacts && agent.artifacts.length > 0
    if (content || hasArtifacts) {
      const isStreaming = agent.taskStatus === 'working' && content.length > 0
      blocks.push({
        type: 'agent_content',
        agentId: agent.agentId ?? agent.id,
        agentName: agent.senderName,
        content,
        isStreaming,
        artifacts: agent.artifacts,
      })
    }

    // HITL user answer record
    const hitl = selectAgentHitlState(agent)
    if (hitl && hitl.resolved && hitl.answer) {
      blocks.push({
        type: 'user_answer',
        agentName: agent.senderName,
        question: hitl.question,
        answer: hitl.answer,
      })
    }
  }

  // Add synthetic working cards for ephemeral placeholders without real agents
  for (const [crId, eph] of ephemeralByClientReqId) {
    if (clientReqIdsWithRealAgent.has(crId)) continue

    // Find the optimistic user turn
    let targetTurn: string | undefined
    for (const uid of userMessageIds) {
      const u = entities[uid]
      if (u?.clientRequestId === crId) { targetTurn = uid; break }
    }
    if (!targetTurn) continue

    const blocks = turnBlocks.get(targetTurn) ?? (() => { const b: ConversationBlock[] = []; turnBlocks.set(targetTurn, b); return b })()
    blocks.push({
      type: 'agent_card',
      agentId: eph.id,
      agentName: eph.senderName,
      display: { label: 'Working', tone: 'accent', isAnimated: true, ariaLabel: `${eph.senderName} — Working` },
      taskDescription: eph.taskContent ?? '',
      theme: getAgentTheme(undefined, eph.senderName),
    })
  }

  // Insert agent dividers between different agents in each turn
  for (const [, blocks] of turnBlocks) {
    insertDividers(blocks)
  }
  insertDividers(unresolvedBlocks)

  // Build turn views in user message order
  const turns: ConversationTurnView[] = []
  for (const user of userEntitiesOrdered) {
    turns.push({
      turnId: user.id,
      userMessage: user,
      blocks: turnBlocks.get(user.id) ?? [],
    })
  }

  if (unresolvedBlocks.length > 0) {
    turns.push({
      turnId: '__unresolved__',
      userMessage: null,
      blocks: unresolvedBlocks,
    })
  }

  return turns
}

function insertDividers(blocks: ConversationBlock[]): void {
  let lastAgentId: string | undefined
  let i = 0
  while (i < blocks.length) {
    const block = blocks[i]
    if (block.type === 'agent_card') {
      if (lastAgentId !== undefined && block.agentId !== lastAgentId) {
        blocks.splice(i, 0, { type: 'agent_divider' })
        i++
      }
      lastAgentId = block.agentId
    }
    i++
  }
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run tests/unit/lib/selectors/select-conversation-turns.test.ts --reporter=verbose
```

Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

- [ ] **Step 5: Create barrel index (all selector files now exist)**

Create `src/lib/selectors/index.ts`:

```typescript
export * from './conversation-types'
export { routeAgentToTurn } from './route-agent'
export { mapAgentDisplayProps } from './map-agent-display'
export { selectConversationTurns } from './select-conversation-turns'
export { selectPendingHitls, selectAgentHitlState } from './select-hitl'
export { selectComposerState } from './select-composer-state'
```

- [ ] **Step 6: Verify barrel build**

```bash
npx tsc --noEmit
```

Expected: no errors — all re-exported modules now exist.

- [ ] **Step 7: Commit**

```bash
git add src/lib/selectors/select-conversation-turns.ts tests/unit/lib/selectors/select-conversation-turns.test.ts src/lib/selectors/index.ts
git commit -m "feat(selectors): implement selectConversationTurns and create barrel index"
```

---

## Task 13: Phase 2 — SSE fixes

**Files:**
- Modify: `src/hooks/room/sse-handlers/pending-turn-buffer.ts:3,22-31`
- Modify: `src/hooks/room/sse-handlers/index.ts:159-165`

- [ ] **Step 1: Increase buffer TTL from 30s to 120s**

In `src/hooks/room/sse-handlers/pending-turn-buffer.ts`, change line 3:

```typescript
const PENDING_SSE_BUFFER_TTL_MS = 30_000
```

to:

```typescript
const PENDING_SSE_BUFFER_TTL_MS = 120_000
```

- [ ] **Step 2: Add production-visible logging on eviction**

In the same file, replace lines 22-31 (the `evictExpired` function):

```typescript
function evictExpired(now: number) {
  for (const [clientRequestId, pending] of pendingByClientRequestId) {
    if (now - pending.createdAt <= PENDING_SSE_BUFFER_TTL_MS) continue
    pendingByClientRequestId.delete(clientRequestId)
    warnOnce(
      'pending SSE buffer evicted for clientRequestId=%s — possible orphan SSE stream',
      clientRequestId,
    )
  }
}
```

Replace with:

```typescript
function evictExpired(now: number) {
  for (const [clientRequestId, pending] of pendingByClientRequestId) {
    if (now - pending.createdAt <= PENDING_SSE_BUFFER_TTL_MS) continue
    const eventCount = pending.events.length
    pendingByClientRequestId.delete(clientRequestId)
    console.warn(
      '[SSE buffer] evicted clientRequestId=%s after %dms — %d events dropped (possible orphan stream)',
      clientRequestId,
      now - pending.createdAt,
      eventCount,
    )
  }
}
```

- [ ] **Step 3: Verify terminal race guard (index.ts L159-165)**

Read `src/hooks/room/sse-handlers/index.ts` lines 159-165. The existing guard already allows `agent_response` through when entity is terminal but has no content. This is correct per spec. No code change needed — just verify and document.

- [ ] **Step 4: Verify build**

```bash
npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add src/hooks/room/sse-handlers/pending-turn-buffer.ts
git commit -m "fix(sse): increase pending buffer TTL to 120s, add production logging on eviction"
```

---

## Task 14: Phase 3 — CSS design tokens

**Files:**
- Create: `src/components/conversation/conversation-tokens.css`
- Create: `src/components/conversation/shimmer.css`
- Modify: `src/app/globals.css`

- [ ] **Step 1: Create conversation tokens CSS**

Create `src/components/conversation/conversation-tokens.css`:

```css
:root {
  --conversation-bg: #09090b;
  --conversation-surface: #0a0a0f;

  --conversation-border: #27272a;
  --conversation-border-subtle: #18181b;

  --conversation-text-primary: #fafafa;
  --conversation-text-secondary: #e4e4e7;
  --conversation-text-tertiary: #d4d4d8;
  --conversation-text-muted: #71717a;
  --conversation-text-dim: #52525b;

  --conversation-agent-green: #4ade80;
  --conversation-agent-blue: #3b82f6;
  --conversation-agent-purple: #a78bfa;
  --conversation-agent-amber: #fbbf24;
  --conversation-agent-rose: #fb7185;
  --conversation-agent-yellow: #eab308;

  --conversation-danger: #ef4444;
  --conversation-danger-border: #ef444433;

  --conversation-padding-outer: 16px;
  --conversation-padding-inner: 32px;
  --conversation-gap-turn: 32px;
  --conversation-gap-block: 8px;
  --conversation-sticky-top: 12px;
  --conversation-max-width: 800px;

  --conversation-shimmer-duration: 3.5s;
  --conversation-fade-duration: 200ms;
  --conversation-chevron-duration: 150ms;
  --conversation-cursor-duration: 800ms;
}

@media (max-width: 639px) {
  :root {
    --conversation-padding-outer: 12px;
    --conversation-padding-inner: 20px;
  }
}

@media (prefers-reduced-motion: reduce) {
  :root {
    --conversation-shimmer-duration: 0s;
    --conversation-fade-duration: 0s;
  }
}
```

- [ ] **Step 2: Create shimmer animation CSS**

Create `src/components/conversation/shimmer.css`:

```css
@keyframes conversation-shimmer {
  0% { background-position: -100% 0; }
  100% { background-position: 200% 0; }
}

@keyframes conversation-cursor-blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

.conversation-card-shimmer::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(
    90deg,
    transparent 0%,
    hsl(0 0% 100% / 0.03) 50%,
    transparent 100%
  );
  background-size: 300% 100%;
  animation: conversation-shimmer var(--conversation-shimmer-duration) ease-in-out infinite;
  pointer-events: none;
  border-radius: inherit;
}

.conversation-streaming-cursor::after {
  content: '|';
  color: var(--conversation-agent-blue);
  animation: conversation-cursor-blink var(--conversation-cursor-duration) step-end infinite;
}
```

- [ ] **Step 3: Import tokens in globals.css**

At the top of `src/app/globals.css` (after the existing tailwind imports), add:

```css
@import '../components/conversation/conversation-tokens.css';
@import '../components/conversation/shimmer.css';
```

- [ ] **Step 4: Verify build**

```bash
npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add src/components/conversation/conversation-tokens.css src/components/conversation/shimmer.css src/app/globals.css
git commit -m "feat(conversation): add CSS design tokens and shimmer animations"
```

---

## Task 15: Phase 3 — Build AgentCard component

**Files:**
- Create: `src/components/conversation/AgentCard.tsx`

- [ ] **Step 1: Implement AgentCard**

Create `src/components/conversation/AgentCard.tsx`:

```tsx
import type { AgentDisplayProps, AgentTheme } from '@/lib/selectors/conversation-types'

interface AgentCardProps {
  agentName: string
  agentId: string
  taskDescription: string
  theme: AgentTheme
  display: AgentDisplayProps
}

function AgentAvatar({ name, theme }: { name: string; theme: AgentTheme }) {
  const initials = name.slice(0, 2).toUpperCase()
  return (
    <div
      className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-medium shrink-0"
      style={{ backgroundColor: `color-mix(in srgb, ${theme.accent} 15%, transparent)`, color: theme.accent }}
    >
      {initials}
    </div>
  )
}

export function AgentCard({ agentName, agentId, taskDescription, theme, display }: AgentCardProps) {
  const toneColors: Record<AgentDisplayProps['tone'], string> = {
    accent: theme.accent,
    muted: 'var(--conversation-text-dim)',
    danger: 'var(--conversation-danger)',
    warning: 'var(--conversation-agent-yellow)',
  }

  return (
    <div
      className={`relative rounded-lg border px-3 py-2.5 overflow-hidden ${display.isAnimated ? 'conversation-card-shimmer' : ''}`}
      style={{
        backgroundColor: 'var(--conversation-surface)',
        borderColor: display.tone === 'danger' ? 'var(--conversation-danger-border)' : 'var(--conversation-border)',
      }}
    >
      <div className="flex items-center gap-2.5">
        <AgentAvatar name={agentName} theme={theme} />
        <span className="text-sm font-medium" style={{ color: 'var(--conversation-text-primary)' }}>
          {agentName}
        </span>
        <span
          className="ml-auto text-xs"
          role="status"
          aria-label={display.ariaLabel}
          style={{ color: toneColors[display.tone] }}
        >
          {display.label}
        </span>
      </div>
      {taskDescription && (
        <div className="flex items-center gap-1.5 mt-1.5 pl-[42px]">
          <span className="text-xs" style={{ color: 'var(--conversation-text-dim)' }}>└</span>
          <span className="text-xs truncate" style={{ color: 'var(--conversation-text-muted)' }}>
            {taskDescription}
          </span>
        </div>
      )}
      {display.tone === 'warning' && display.label === 'Needs Input' && (
        <div className="mt-1.5 pl-[42px]">
          <span className="text-xs" style={{ color: 'var(--conversation-text-dim)' }}>
            Agent is waiting for your response in the input panel below.
          </span>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Verify build**

```bash
npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add src/components/conversation/AgentCard.tsx
git commit -m "feat(conversation): add AgentCard presentational component"
```

---

## Task 16: Phase 3 — Build UserMessageBlock, AgentContentBlock, UserAnswerCard, UnresolvedAgentGroup

**Files:**
- Create: `src/components/conversation/UserMessageBlock.tsx`
- Create: `src/components/conversation/AgentContentBlock.tsx`
- Create: `src/components/conversation/UserAnswerCard.tsx`
- Create: `src/components/conversation/UnresolvedAgentGroup.tsx`

- [ ] **Step 1: Implement UserMessageBlock**

Create `src/components/conversation/UserMessageBlock.tsx`:

```tsx
'use client'

import { useState, useRef, useEffect } from 'react'
import type { MessageEntity } from '@/stores/message-store/types'
import { UserAttachmentCard } from './UserAttachmentCard'

interface UserMessageBlockProps {
  entity: MessageEntity
  onSentinelRef?: (el: HTMLDivElement | null) => void
}

export function UserMessageBlock({ entity, onSentinelRef }: UserMessageBlockProps) {
  const [expanded, setExpanded] = useState(false)
  const textRef = useRef<HTMLDivElement>(null)
  const [isOverflowing, setIsOverflowing] = useState(false)

  useEffect(() => {
    const el = textRef.current
    if (!el) return
    setIsOverflowing(el.scrollHeight > el.clientHeight + 1)
  }, [entity.content])

  const ts = new Date(entity.timestamp)
  const timeStr = ts.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

  return (
    <div
      ref={onSentinelRef}
      className="cursor-pointer"
      style={{ padding: '0 var(--conversation-padding-outer)' }}
      data-message-id={entity.id}
      onClick={() => isOverflowing && setExpanded(prev => !prev)}
    >
      <div
        className="rounded-lg border px-3 py-2.5"
        style={{
          backgroundColor: 'var(--conversation-surface)',
          borderColor: 'var(--conversation-border)',
        }}
      >
        <div className="flex items-start gap-2">
          <div className="w-6 h-6 rounded-full bg-zinc-700 flex items-center justify-center text-[10px] font-medium text-zinc-300 shrink-0">
            {(entity.senderName ?? 'U').charAt(0).toUpperCase()}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 mb-0.5">
              <span className="text-sm font-medium" style={{ color: 'var(--conversation-text-primary)' }}>
                {entity.senderName}
              </span>
              <span className="text-xs" style={{ color: 'var(--conversation-text-dim)' }}>
                {timeStr}
              </span>
            </div>
            <div
              ref={textRef}
              className="text-sm leading-[1.5] break-words"
              style={{
                color: 'var(--conversation-text-secondary)',
                maxHeight: expanded ? 'none' : '4.5em',
                overflow: 'hidden',
                WebkitMaskImage: !expanded && isOverflowing
                  ? 'linear-gradient(to bottom, black 60%, transparent 100%)'
                  : undefined,
                maskImage: !expanded && isOverflowing
                  ? 'linear-gradient(to bottom, black 60%, transparent 100%)'
                  : undefined,
              }}
            >
              {entity.content}
            </div>
            {entity.attachments && entity.attachments.length > 0 && (
              <div className="flex flex-wrap gap-2 mt-2">
                {entity.attachments.map(att => (
                  <UserAttachmentCard key={att.fileId} attachment={att} />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Implement AgentContentBlock**

Create `src/components/conversation/AgentContentBlock.tsx`:

```tsx
import { MarkdownContent } from '@/components/markdown-content'
import { ArtifactList } from '@/components/artifact-list'
import type { ArtifactData } from '@/stores/message-store/types'

interface AgentContentBlockProps {
  agentId: string
  agentName: string
  content: string
  isStreaming: boolean
  showAttribution?: boolean
  artifacts?: ArtifactData[]
}

export function AgentContentBlock({ agentName, content, isStreaming, showAttribution, artifacts }: AgentContentBlockProps) {
  return (
    <div style={{ padding: '0 var(--conversation-padding-inner)' }}>
      {showAttribution && (
        <div className="text-xs mb-1" style={{ color: 'var(--conversation-text-muted)' }}>
          {agentName}:
        </div>
      )}
      <div className={isStreaming ? 'conversation-streaming-cursor' : ''}>
        <MarkdownContent content={content} />
      </div>
      {artifacts && artifacts.length > 0 && (
        <ArtifactList artifacts={artifacts} />
      )}
    </div>
  )
}
```

> **Note:** `ArtifactList` and `ArtifactRenderer` are existing standalone components
> that already render file parts with icons and collapsible UI. They are NOT being deleted
> in Phase 5. The `ArtifactRenderer` renders each `ArtifactPart` — including file parts
> with download links (via `PartRenderer`). If a dedicated download button is needed beyond
> what `PartRenderer` provides, add it to `ArtifactRenderer` as a follow-up.

- [ ] **Step 3: Implement UserAnswerCard**

Create `src/components/conversation/UserAnswerCard.tsx`:

```tsx
interface UserAnswerCardProps {
  agentName: string
  question: string
  answer: string
}

export function UserAnswerCard({ agentName, question, answer }: UserAnswerCardProps) {
  return (
    <div
      className="rounded-lg border px-3 py-2.5"
      style={{
        backgroundColor: 'var(--conversation-surface)',
        borderColor: 'var(--conversation-border)',
      }}
    >
      <div className="text-xs mb-1.5" style={{ color: 'var(--conversation-text-muted)' }}>
        Response to {agentName}
      </div>
      <div className="pl-3 text-sm mb-1" style={{ color: 'var(--conversation-text-muted)' }}>
        {question}
      </div>
      <div className="pl-3 text-sm" style={{ color: 'var(--conversation-text-secondary)' }}>
        {answer}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Implement UnresolvedAgentGroup**

Create `src/components/conversation/UnresolvedAgentGroup.tsx`:

```tsx
import type { ConversationBlock } from '@/lib/selectors/conversation-types'
import { AgentCard } from './AgentCard'
import { AgentContentBlock } from './AgentContentBlock'

interface UnresolvedAgentGroupProps {
  blocks: ConversationBlock[]
}

export function UnresolvedAgentGroup({ blocks }: UnresolvedAgentGroupProps) {
  return (
    <div style={{ padding: '0 var(--conversation-padding-inner)' }}>
      <div className="text-xs font-medium mb-2" style={{ color: 'var(--conversation-text-muted)' }}>
        Unattributed responses
      </div>
      <div className="flex flex-col" style={{ gap: 'var(--conversation-gap-block)' }}>
        {blocks.map((block, i) => {
          if (block.type === 'agent_card') return <AgentCard key={i} {...block} />
          if (block.type === 'agent_content') return <AgentContentBlock key={i} {...block} />
          if (block.type === 'unresolved_content') {
            return (
              <div key={i} className="text-sm" style={{ color: 'var(--conversation-text-tertiary)' }}>
                {block.entity.content}
              </div>
            )
          }
          return null
        })}
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Verify build**

```bash
npx tsc --noEmit
```

- [ ] **Step 6: Commit**

```bash
git add src/components/conversation/UserMessageBlock.tsx src/components/conversation/AgentContentBlock.tsx src/components/conversation/UserAnswerCard.tsx src/components/conversation/UnresolvedAgentGroup.tsx
git commit -m "feat(conversation): add UserMessageBlock, AgentContentBlock, UserAnswerCard, UnresolvedAgentGroup"
```

---

## Task 17: Phase 3 — Build ConversationTurn and ConversationMessageList

**Files:**
- Create: `src/components/conversation/ConversationTurn.tsx`
- Create: `src/components/conversation/ScrollToBottomButton.tsx`
- Create: `src/components/conversation/ConversationMessageList.tsx`
- Create: `src/hooks/useConversationTurnViews.ts`

- [ ] **Step 1: Implement ConversationTurn**

Create `src/components/conversation/ConversationTurn.tsx`:

```tsx
import type { ConversationTurnView, ConversationBlock } from '@/lib/selectors/conversation-types'
import { UserMessageBlock } from './UserMessageBlock'
import { AgentCard } from './AgentCard'
import { AgentContentBlock } from './AgentContentBlock'
import { UserAnswerCard } from './UserAnswerCard'
import { UnresolvedAgentGroup } from './UnresolvedAgentGroup'

interface ConversationTurnProps {
  turn: ConversationTurnView
  onUserSentinelRef?: (el: HTMLDivElement | null) => void
  multiAgentTurn: boolean
}

function BlockRenderer({ block, multiAgent }: { block: ConversationBlock; multiAgent: boolean }) {
  switch (block.type) {
    case 'agent_card':
      return <div style={{ padding: '0 var(--conversation-padding-inner)' }}><AgentCard {...block} /></div>
    case 'agent_content':
      return <AgentContentBlock {...block} showAttribution={multiAgent} />
    case 'user_answer':
      return <div style={{ padding: '0 var(--conversation-padding-inner)' }}><UserAnswerCard {...block} /></div>
    case 'agent_divider':
      return (
        <div style={{ padding: '0 var(--conversation-padding-inner)', margin: '12px 0' }}>
          <div style={{ height: 1, backgroundColor: 'var(--conversation-border-subtle)' }} />
        </div>
      )
    case 'unresolved_content':
      return (
        <div className="text-sm" style={{ padding: '0 var(--conversation-padding-inner)', color: 'var(--conversation-text-tertiary)' }}>
          {block.entity.content}
        </div>
      )
    default:
      return null
  }
}

export function ConversationTurn({ turn, onUserSentinelRef, multiAgentTurn }: ConversationTurnProps) {
  if (turn.userMessage === null) {
    return <UnresolvedAgentGroup blocks={turn.blocks} />
  }

  return (
    <div className="flex flex-col" style={{ gap: 'var(--conversation-gap-block)' }}>
      <UserMessageBlock entity={turn.userMessage} onSentinelRef={onUserSentinelRef} />
      {turn.blocks.map((block, i) => (
        <BlockRenderer key={i} block={block} multiAgent={multiAgentTurn} />
      ))}
    </div>
  )
}
```

- [ ] **Step 2: Implement ScrollToBottomButton**

Create `src/components/conversation/ScrollToBottomButton.tsx`:

```tsx
interface ScrollToBottomButtonProps {
  visible: boolean
  hasNewContent: boolean
  onClick: () => void
}

export function ScrollToBottomButton({ visible, hasNewContent, onClick }: ScrollToBottomButtonProps) {
  if (!visible) return null

  return (
    <button
      onClick={onClick}
      className="absolute bottom-4 left-1/2 -translate-x-1/2 z-10 rounded-full border px-3 py-1.5 text-xs font-medium transition-opacity"
      style={{
        backgroundColor: 'var(--conversation-surface)',
        borderColor: 'var(--conversation-border)',
        color: 'var(--conversation-text-secondary)',
      }}
      aria-label="Scroll to bottom"
    >
      ↓ Bottom
      {hasNewContent && (
        <span className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-blue-500" />
      )}
    </button>
  )
}
```

- [ ] **Step 3: Implement useConversationTurnViews hook**

Create `src/hooks/useConversationTurnViews.ts`:

```typescript
import { useShallow } from 'zustand/react/shallow'
import { useMessageStore } from '@/stores/message-store'
import { selectConversationTurns } from '@/lib/selectors'
import type { ConversationTurnView } from '@/lib/selectors/conversation-types'

export function useConversationTurnViews(roomId: string): ConversationTurnView[] {
  return useMessageStore(
    useShallow(s => selectConversationTurns(roomId, s.entities, s.orderedIds))
  )
}
```

- [ ] **Step 4: Implement ConversationMessageList**

Create `src/components/conversation/ConversationMessageList.tsx`:

```tsx
'use client'

import { useRef, useState, useCallback, useEffect } from 'react'
import { useMessageStore } from '@/stores/message-store'
import { useConversationTurnViews } from '@/hooks/useConversationTurnViews'
import { ConversationTurn } from './ConversationTurn'
import { ScrollToBottomButton } from './ScrollToBottomButton'
import { UserMessageBlock } from './UserMessageBlock'
import type { ConversationTurnView } from '@/lib/selectors/conversation-types'

interface ConversationMessageListProps {
  roomId: string
}

export function ConversationMessageList({ roomId }: ConversationMessageListProps) {
  const turns = useConversationTurnViews(roomId)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [showScrollBtn, setShowScrollBtn] = useState(false)
  const [hasNewContent, setHasNewContent] = useState(false)
  const [stickyTurn, setStickyTurn] = useState<ConversationTurnView | null>(null)
  const [stickyVisible, setStickyVisible] = useState(false)
  const sentinelRefs = useRef<Map<string, HTMLDivElement>>(new Map())
  const isNearBottom = useCallback(() => {
    const el = scrollRef.current
    if (!el) return true
    return el.scrollHeight - el.scrollTop - el.clientHeight < 100
  }, [])

  const scrollToBottom = useCallback(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
    setHasNewContent(false)
  }, [])

  // Auto-scroll: subscribe to message-store orderedIds length (new turns)
  // AND entity content changes (streaming updates within existing turns).
  // We use a lightweight selector that returns a fingerprint that changes
  // whenever orderedIds or the last entity's content changes.
  const scrollFingerprint = useMessageStore(s => {
    const len = s.orderedIds.length
    if (len === 0) return '0:'
    const lastId = s.orderedIds[len - 1]
    const last = s.entities[lastId]
    return `${len}:${last?.content?.length ?? 0}:${last?.taskStatus ?? ''}`
  })

  const prevFingerprintRef = useRef(scrollFingerprint)
  useEffect(() => {
    if (scrollFingerprint !== prevFingerprintRef.current) {
      prevFingerprintRef.current = scrollFingerprint
      if (isNearBottom()) {
        scrollToBottom()
      } else {
        setHasNewContent(true)
      }
    }
  }, [scrollFingerprint, isNearBottom, scrollToBottom])

  // Scroll position tracking
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onScroll = () => {
      setShowScrollBtn(!isNearBottom())
      if (isNearBottom()) setHasNewContent(false)
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [isNearBottom])

  // Sticky user message via IntersectionObserver
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const observer = new IntersectionObserver(
      (entries) => {
        let lastVisibleTurn: ConversationTurnView | null = null
        for (const entry of entries) {
          const turnId = (entry.target as HTMLElement).dataset.messageId
          if (!turnId) continue
          const turn = turns.find(t => t.userMessage?.id === turnId)
          if (turn && !entry.isIntersecting) {
            lastVisibleTurn = turn
          }
        }
        if (lastVisibleTurn) {
          setStickyTurn(prev => {
            if (prev?.turnId === lastVisibleTurn!.turnId) return prev
            setStickyVisible(false)
            setTimeout(() => setStickyVisible(true), 20)
            return lastVisibleTurn
          })
        }
      },
      { root: el, rootMargin: `-${parseInt(getComputedStyle(document.documentElement).getPropertyValue('--conversation-sticky-top') || '12')}px 0px 0px 0px` }
    )

    for (const ref of sentinelRefs.current.values()) {
      observer.observe(ref)
    }
    return () => observer.disconnect()
  }, [turns])

  // Room entry: scroll to bottom
  useEffect(() => {
    if (turns.length > 0) scrollToBottom()
  }, [roomId]) // eslint-disable-line react-hooks/exhaustive-deps

  const registerSentinel = useCallback((turnId: string) => (el: HTMLDivElement | null) => {
    if (el) sentinelRefs.current.set(turnId, el)
    else sentinelRefs.current.delete(turnId)
  }, [])

  const hasMultipleAgents = (turn: ConversationTurnView) => {
    const agentIds = new Set<string>()
    for (const b of turn.blocks) {
      if (b.type === 'agent_card') agentIds.add(b.agentId)
    }
    return agentIds.size > 1
  }

  return (
    <div className="relative h-full" style={{ backgroundColor: 'var(--conversation-bg)' }}>
      {/* Sticky user message */}
      {stickyTurn?.userMessage && (
        <div
          className="sticky z-20 transition-opacity"
          style={{
            top: 'var(--conversation-sticky-top)',
            opacity: stickyVisible ? 1 : 0,
            transitionDuration: 'var(--conversation-fade-duration)',
            borderBottom: '1px solid var(--conversation-border)',
          }}
        >
          <div style={{ maxWidth: 'var(--conversation-max-width)', margin: '0 auto' }}>
            <UserMessageBlock entity={stickyTurn.userMessage} />
          </div>
        </div>
      )}

      <div
        ref={scrollRef}
        className="h-full overflow-y-auto"
        style={{ scrollBehavior: 'smooth' }}
      >
        <div style={{ maxWidth: 'var(--conversation-max-width)', margin: '0 auto' }}>
          <div className="flex flex-col" style={{ gap: 'var(--conversation-gap-turn)', paddingTop: '48px' }}>
            {turns.map(turn => (
              <ConversationTurn
                key={turn.turnId}
                turn={turn}
                onUserSentinelRef={turn.userMessage ? registerSentinel(turn.userMessage.id) : undefined}
                multiAgentTurn={hasMultipleAgents(turn)}
              />
            ))}
          </div>
          {/* Elastic spacer */}
          <div style={{ minHeight: '60vh' }} />
        </div>
      </div>

      <ScrollToBottomButton visible={showScrollBtn} hasNewContent={hasNewContent} onClick={scrollToBottom} />
    </div>
  )
}
```

- [ ] **Step 5: Verify build**

```bash
npx tsc --noEmit
```

- [ ] **Step 6: Commit**

```bash
git add src/components/conversation/ConversationTurn.tsx src/components/conversation/ScrollToBottomButton.tsx src/components/conversation/ConversationMessageList.tsx src/hooks/useConversationTurnViews.ts
git commit -m "feat(conversation): add ConversationTurn, ConversationMessageList, ScrollToBottomButton, and container hook"
```

---

## Task 18: Phase 3 — Migrate ComposerShell to selectComposerState

**Files:**
- Modify: `src/components/composer/ComposerShell.tsx`

- [ ] **Step 1: Inline HitlPromptView in HitlResponseBar**

In `src/components/composer/HitlResponseBar.tsx`, replace:

```typescript
import type { HitlPromptView } from '@/stores/turn-event-store/types'
```

with an inline exported interface (so ComposerShell can import it from here):

```typescript
export interface HitlPromptView {
  hitlId: string
  turnId: string
  ts: number
  source: 'supervisor' | 'agent'
  agentName?: string
  prompt: string
  promptType: 'text' | 'choice' | 'confirmation'
  choices?: string[]
  groupId?: string
  groupTotal?: number
  groupIndex?: number
}
```

Verify build: `npx tsc --noEmit`

- [ ] **Step 2: Replace useTurnEventStore with selectComposerState**

Replace the entire `src/components/composer/ComposerShell.tsx`:

```typescript
'use client'

import React from 'react'
import { useShallow } from 'zustand/react/shallow'
import { useMessageStore } from '@/stores/message-store'
import { selectComposerState } from '@/lib/selectors'
import type { PendingHitl } from '@/lib/selectors/conversation-types'
import { HitlResponseBar, type HitlPromptView } from './HitlResponseBar'
import { RoomChatInput } from '@/components/room-chat-input'

function toHitlPromptView(hitl: PendingHitl): HitlPromptView {
  return {
    hitlId: hitl.hitlId,
    turnId: hitl.messageId,
    ts: Date.now(),
    source: 'agent',
    agentName: hitl.agentName,
    prompt: hitl.question,
    promptType: hitl.promptType,
    choices: hitl.choices,
    groupId: hitl.groupId,
    groupTotal: hitl.groupTotal,
    groupIndex: hitl.groupIndex,
  }
}

export interface ComposerShellAdapter {
  roomId: string
  onSendMessage: (...args: any[]) => void
  onCancelProcessing: () => void
  onRespondToHitl: (hitlId: string, answer: string) => Promise<void>
  onChatModeChange?: (mode: any) => void
  isSending: boolean
  isProcessing: boolean
  isCancelling: boolean
  agents: any[]
  roomAgentIds: string[]
  groupManagement: {
    groups: any[]
    loadingGroups: boolean
    selectedGroup: string
    isOverride: boolean
    handleGroupChange: (groupId: string) => void
    handleClearOverride: () => void
    handleCreateGroup: () => void
    handleEditGroup: (group: any) => void
    handleDeleteGroup: (group: any) => void
    onEditRoomAgents: () => void
  }
  quoteState: {
    quote: any
    setQuote: (data: any) => void
    clearQuote: () => void
  }
  chatMode: any
  externalValue?: string
  onExternalValueConsumed?: () => void
}

interface ComposerShellProps {
  adapter: ComposerShellAdapter
}

export function ComposerShell({ adapter }: ComposerShellProps) {
  const composerState = useMessageStore(
    useShallow(s => selectComposerState(adapter.roomId, s.entities, s.orderedIds))
  )
  const isHitlMode = composerState.mode === 'hitl_responding'
  const isProcessing = composerState.isProcessing && adapter.isProcessing

  const hitlBar = composerState.pendingHitls.length > 0 ? (
    <HitlResponseBar
      hitls={composerState.pendingHitls.map(toHitlPromptView)}
      onSubmit={adapter.onRespondToHitl}
    />
  ) : undefined

  return (
    <RoomChatInput
      onSubmit={adapter.onSendMessage}
      disableSend={adapter.isSending || isProcessing || isHitlMode}
      sending={adapter.isSending}
      processing={isProcessing}
      cancelling={adapter.isCancelling && isProcessing}
      onCancel={adapter.onCancelProcessing}
      agents={adapter.agents}
      roomAgentIds={adapter.roomAgentIds}
      showGroupSelector={!isHitlMode}
      groups={adapter.groupManagement.groups}
      loadingGroups={adapter.groupManagement.loadingGroups}
      selectedGroup={adapter.groupManagement.selectedGroup}
      onGroupChange={adapter.groupManagement.handleGroupChange}
      roomAgentCount={adapter.roomAgentIds.length}
      onCreateGroup={adapter.groupManagement.handleCreateGroup}
      onEditGroup={adapter.groupManagement.handleEditGroup}
      onDeleteGroup={adapter.groupManagement.handleDeleteGroup}
      onEditRoomAgents={adapter.groupManagement.onEditRoomAgents}
      isOverride={adapter.groupManagement.isOverride}
      onClearOverride={adapter.groupManagement.handleClearOverride}
      quote={adapter.quoteState.quote}
      onClearQuote={adapter.quoteState.clearQuote}
      chatMode={adapter.chatMode}
      onChatModeChange={adapter.onChatModeChange}
      topSlot={hitlBar}
      externalValue={adapter.externalValue}
      onExternalValueConsumed={adapter.onExternalValueConsumed}
    />
  )
}
```

Note: The `adapter` interface now requires `roomId` — this is passed from `RoomPageShell` which already has `adapter.roomId`. The `toHitlPromptView` mapper bridges `PendingHitl` → `HitlPromptView` (now owned by `HitlResponseBar.tsx` per Step 1).

- [ ] **Step 3: Verify build**

```bash
npx tsc --noEmit
```

- [ ] **Step 4: Commit**

```bash
git add src/components/composer/HitlResponseBar.tsx src/components/composer/ComposerShell.tsx
git commit -m "refactor(composer): migrate ComposerShell from useTurnEventStore to selectComposerState, inline HitlPromptView"
```

---

## Task 19: Phase 4 — Wire new renderer into RoomPageShell

**Files:**
- Modify: `src/components/room-page-shell.tsx`

- [ ] **Step 1: Replace TurnList with ConversationMessageList**

Replace the content of `src/components/room-page-shell.tsx`:

```typescript
'use client'

import React from 'react'
import type { AgentGroup } from '@/lib/types/agent-group'
import type { QuoteData } from '@/lib/types/quote'
import type { PendingAttachment } from '@/lib/types/attachments'
import type { ChatMode } from '@/lib/types/chat-mode'
import { ConversationMessageList } from '@/components/conversation/ConversationMessageList'
import { ComposerShell } from '@/components/composer/ComposerShell'

export interface GroupManagementAdapter {
  groups: AgentGroup[]
  loadingGroups: boolean
  selectedGroup: string
  isOverride: boolean
  handleGroupChange: (groupId: string) => void
  handleClearOverride: () => void
  handleCreateGroup: () => void
  handleEditGroup: (group: AgentGroup) => void
  handleDeleteGroup: (group: AgentGroup) => void
  onEditRoomAgents: () => void
}

export interface QuoteState {
  quote: QuoteData | null
  setQuote: (data: QuoteData) => void
  clearQuote: () => void
}

export interface TimelineAdapter {
  roomId: string
  getToken?: () => Promise<string | null>
  onSendMessage: (message: string, targetGroup?: string, quoteData?: QuoteData | null, attachments?: PendingAttachment[]) => void
  onCancelProcessing: () => void
  onRespondToHitl: (hitlId: string, answer: string) => Promise<void>
  onChatModeChange: (mode: ChatMode) => void
  isSending: boolean
  isProcessing: boolean
  isCancelling: boolean
  agents: { id: string; name: string; iconUrl?: string }[]
  roomAgentIds: string[]
  groupManagement: GroupManagementAdapter
  quoteState: QuoteState
  chatMode: ChatMode
  externalValue?: string
  onExternalValueConsumed?: () => void
}

interface RoomPageShellProps {
  adapter: TimelineAdapter
}

export function RoomPageShell({ adapter }: RoomPageShellProps) {
  return (
    <>
      <main className="flex-1 overflow-hidden">
        <ConversationMessageList roomId={adapter.roomId} />
      </main>
      <div className="bg-background" style={{ borderTop: '1px solid var(--conversation-border)' }}>
        <div style={{ maxWidth: 'var(--conversation-max-width)', margin: '0 auto' }}>
          <ComposerShell adapter={adapter} />
        </div>
      </div>
    </>
  )
}
```

- [ ] **Step 2: Remove createOptimisticTurn / removeTurn from useSendMessage**

In `src/hooks/room/useSendMessage.ts`:

1. Delete line 8: `import { useTurnEventStore } from '@/stores/turn-event-store'`
2. Delete lines 61-65 (createOptimisticTurn call):
```typescript
    const turnStore = useTurnEventStore.getState()
    turnStore.createOptimisticTurn(clientRequestId, {
      text: userInput,
      attachments: optimisticAttachments ?? [],
    })
```
3. Delete line 144: `useTurnEventStore.getState().removeTurn(clientRequestId)` (no-messageId rollback)
4. Delete lines 191-202 (the turnStore.append call after replaceMessageId)
5. Delete line 253: `useTurnEventStore.getState().removeTurn(clientRequestId)` (error rollback)

Keep all message-store operations (`upsertMany`, `removeMessage`, `replaceMessageId`) intact.

- [ ] **Step 3: Verify build**

```bash
npx tsc --noEmit
```

- [ ] **Step 4: Run all existing tests**

```bash
npx vitest run --reporter=verbose 2>&1 | tail -30
```

Fix any failures caused by removal of turn-event-store imports.

- [ ] **Step 5: Commit**

```bash
git add src/components/room-page-shell.tsx src/hooks/room/useSendMessage.ts
git commit -m "refactor(room): wire ConversationMessageList into page, remove turn-event-store from send flow"
```

---

## Task 20: Phase 5 — Delete old turn infrastructure

**Files:**
- Delete: `src/stores/turn-event-store/` (entire directory)
- Delete: `src/hooks/turn/` (entire directory)
- Delete: `src/components/turn/` (entire directory)
- Delete: `src/components/conversation-timeline.tsx`
- Delete: `src/components/conversation-turn.tsx`
- Delete: `src/components/agent-result-card.tsx`
- Delete: `src/components/agent-result-stack.tsx`
- Delete: `src/components/agent-placeholder-row.tsx`
- Delete: `src/components/supervisor-header.tsx`
- Delete: `src/components/agent-badge.tsx`
- Delete: `src/components/message-bubble.tsx`

- [ ] **Step 1: Run reference check before deletion**

```bash
grep -r "turn-event-store\|useTurnEventStore" src/ --include="*.ts" --include="*.tsx" | grep -v "node_modules"
grep -r "useMessageStoreSync\|useTurnHydration\|useTurnProjection\|useTurnScroll" src/ --include="*.ts" --include="*.tsx"
grep -r "TurnList\|OrchestraTurn\|OrchestrationRail\|ContentSlotRenderer\|SummaryContentBlock\|HitlRecordBlock\|UserInputBlock\|expand-collapse-context" src/ --include="*.ts" --include="*.tsx"
grep -r "conversation-timeline\|conversation-turn\|AgentResultCard\|AgentResultStack\|AgentPlaceholderRow\|SupervisorHeader\|AgentBadge\|MemoizedTurn" src/ --include="*.ts" --include="*.tsx"
grep -r "message-bubble\|EntityUserBubble\|EntityAgentBubble" src/ --include="*.ts" --include="*.tsx"
```

Each grep should return zero matches outside the files being deleted. If any live `src/` file still imports these symbols, fix the import first.

> **Note:** `UserAttachmentCard` is **not** a legacy component — it was extracted from `message-bubble.tsx` in Task 4b and is now a first-class part of the new renderer (`UserMessageBlock` imports it). Do **not** delete `src/components/conversation/UserAttachmentCard.tsx`.

> **Note:** `message-bubble.tsx` can be safely deleted because:
> - `QuoteData` was moved to `src/lib/types/quote.ts` in Task 4b
> - `UserAttachmentCard` was extracted to `src/components/conversation/UserAttachmentCard.tsx` in Task 4b
> - `EntityUserBubble`/`EntityAgentBubble` were only consumed by `room-messages.tsx` (deleted Phase 1) and `conversation-timeline.tsx` (deleted here)

- [ ] **Step 2: Delete the directories and files**

```bash
rm -rf src/stores/turn-event-store/
rm -rf src/hooks/turn/
rm -rf src/components/turn/
rm -f src/components/conversation-timeline.tsx
rm -f src/components/conversation-turn.tsx
rm -f src/components/agent-result-card.tsx
rm -f src/components/agent-result-stack.tsx
rm -f src/components/agent-placeholder-row.tsx
rm -f src/components/supervisor-header.tsx
rm -f src/components/agent-badge.tsx
rm -f src/components/message-bubble.tsx
```

- [ ] **Step 3: Clean up useRoomMessages.ts**

In `src/hooks/useRoomMessages.ts`, the `useConversationTurns`, `useActiveTurn`, `useTurnById`, and `useHitlTurnContext` functions all depend on `buildTurnsIncremental` and `TurnViewModel`. These are now dead. Remove them:

Delete the imports:
```typescript
import { buildTurnsIncremental } from '@/lib/room-timeline/build-turns'
import { getEvents } from '@/lib/room-timeline/event-log'
import type { TurnViewModel } from '@/lib/room-timeline/types'
```

Delete functions: `useConversationTurns`, `useActiveTurn`, `useTurnById`, `useHitlTurnContext` (lines 94-145).

Keep: `useOrderedIds`, `useOrderedMessages`, `useMessage`, `useMessageCount`, `useMessagesHydrated`, `useActiveHitlRequests`, `useMessageStoreRoomId`.

- [ ] **Step 4: Run reference check on remaining consumers**

```bash
grep -r "useConversationTurns\|useActiveTurn\|useTurnById\|useHitlTurnContext" src/ --include="*.ts" --include="*.tsx"
```

Expected: zero matches (these hooks were consumed by now-deleted components).

- [ ] **Step 5: Verify build and tests**

```bash
npx tsc --noEmit && npx vitest run --reporter=verbose 2>&1 | tail -30
```

Tests under `tests/unit/stores/turn-event-store/` and `tests/unit/hooks/turn/` will fail because their source is deleted. Delete those test files:

```bash
rm -rf tests/unit/stores/turn-event-store/
rm -rf tests/unit/hooks/turn/
rm -f tests/unit/hooks/useRoomMessages-turns.test.ts
```

Re-run tests:

```bash
npx vitest run --reporter=verbose 2>&1 | tail -30
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(room): delete turn-event-store, turn hooks, turn components, and old timeline"
```

---

## Task 21: Post-cleanup — Final verification

- [ ] **Step 1: Full type check**

```bash
npx tsc --noEmit
```

- [ ] **Step 2: Full test suite**

```bash
npx vitest run --reporter=verbose
```

- [ ] **Step 3: Verify no orphaned imports**

```bash
grep -r "turn-event-store\|buildTurnsIncremental\|TurnViewModel\|useMessageStoreSync\|useTurnHydration\|globalTurnBasedTimeline" src/ --include="*.ts" --include="*.tsx"
```

Expected: zero matches.

- [ ] **Step 4: Dev server smoke test**

```bash
npm run dev
```

Open browser, navigate to a room. Verify:
- Messages render with new conversation layout
- Agent cards show with correct status (Working/Streaming/Completed/Failed/Canceled)
- Sticky user message appears when scrolling
- Scroll-to-bottom button works
- HITL prompts show "Needs Input" on card and appear in composer
- Sending a message shows optimistic user message immediately
- Agent responses stream in with typewriter cursor
- Room re-entry loads hydrated conversation correctly

- [ ] **Step 5: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "fix(conversation): post-cleanup fixes from smoke testing"
```
