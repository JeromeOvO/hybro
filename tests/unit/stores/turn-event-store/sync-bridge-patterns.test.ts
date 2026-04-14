/**
 * Tests for the sync bridge patterns used by useMessageStoreSync.
 *
 * These test the store + projection behavior that the sync bridge depends on:
 * 1. clientRequestId on turn_started triggers optimistic merge (prevents duplicate user messages)
 * 2. slot_opened must precede slot_snapshot for content to render (prevents missing agent content)
 * 3. slot_opened must precede slot_terminated for rail to show (prevents missing rail items)
 * 4. Orphan optimistic turn cleanup via removeTurn
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { useTurnEventStore } from '@/stores/turn-event-store'
import { contentSlotsReducer } from '@/stores/turn-event-store/projections/content-slots'
import { railReducer, replayRail } from '@/stores/turn-event-store/projections/rail'
import type { TurnEvent, UserInputData } from '@/stores/turn-event-store/types'

const userInput: UserInputData = { text: 'hello', attachments: [] }

function evt(overrides: Partial<TurnEvent> & { type: TurnEvent['type'] }): TurnEvent {
  return {
    eventId: `evt-${Math.random().toString(36).slice(2)}`,
    turnId: 'turn-1',
    seq: 1,
    ts: Date.now(),
    ...overrides,
  } as TurnEvent
}

describe('Sync bridge patterns (useMessageStoreSync behavior)', () => {
  beforeEach(() => {
    useTurnEventStore.getState().reset()
  })

  describe('clientRequestId optimistic merge', () => {
    it('merges optimistic turn when turn_started carries matching clientRequestId', () => {
      const store = useTurnEventStore.getState()

      // Step 1: Create optimistic turn (simulates what useSendMessage does)
      store.createOptimisticTurn('req-abc', { text: 'hello', attachments: [] })
      expect(useTurnEventStore.getState().orderedTurnIds).toContain('req-abc')

      // Step 2: Sync bridge delivers turn_started with clientRequestId
      // (simulates what useMessageStoreSync.buildTurnEvents does after the fix)
      store.append('real-turn-id', evt({
        type: 'turn_started',
        seq: 1,
        eventId: 'sync_started_real-turn-id',
        turnId: 'real-turn-id',
        userInput: { text: 'hello', attachments: [] },
        clientRequestId: 'req-abc',
      }))

      const state = useTurnEventStore.getState()
      expect(state.orderedTurnIds).not.toContain('req-abc')
      expect(state.orderedTurnIds).toContain('real-turn-id')
      expect(state.turnLogs.has('real-turn-id')).toBe(true)
      expect(state.turnLogs.has('req-abc')).toBe(false)
    })

    it('chains merge through tempMessageId → realMessageId via preserved clientRequestId', () => {
      const store = useTurnEventStore.getState()

      // Step 1: User sends message → optimistic turn
      store.createOptimisticTurn('req-abc', { text: 'hello', attachments: [] })
      expect(useTurnEventStore.getState().orderedTurnIds).toEqual(['req-abc'])

      // Step 2: Sync bridge first run → merge optimistic into tempMessageId
      store.append('temp-msg-123', evt({
        type: 'turn_started', seq: 1, eventId: 'sync-1',
        turnId: 'temp-msg-123', userInput, clientRequestId: 'req-abc',
      }))

      let state = useTurnEventStore.getState()
      expect(state.orderedTurnIds).toEqual(['temp-msg-123'])
      expect(state.turnLogs.has('req-abc')).toBe(false)
      // clientRequestId mapping updated (not deleted) to point to tempMessageId
      expect(state.turnIdByClientRequestId.get('req-abc')).toBe('temp-msg-123')

      // Step 3: Message store swaps temp → real ID. Sync bridge fires again
      // with realMessageId. The second merge should chain through.
      store.append('real-msg-456', evt({
        type: 'turn_started', seq: 1, eventId: 'sync-2',
        turnId: 'real-msg-456', userInput, clientRequestId: 'req-abc',
      }))

      state = useTurnEventStore.getState()
      // Only the real turn should remain — no duplicates
      expect(state.orderedTurnIds).toEqual(['real-msg-456'])
      expect(state.turnLogs.has('temp-msg-123')).toBe(false)
      expect(state.turnLogs.has('real-msg-456')).toBe(true)
      // clientRequestId mapping updated to real ID
      expect(state.turnIdByClientRequestId.get('req-abc')).toBe('real-msg-456')
    })

    it('does NOT merge when clientRequestId is missing from turn_started (the bug)', () => {
      const store = useTurnEventStore.getState()

      store.createOptimisticTurn('req-abc', { text: 'hello', attachments: [] })

      // turn_started WITHOUT clientRequestId — reproduces the duplicate user message bug
      store.append('real-turn-id', evt({
        type: 'turn_started',
        seq: 1,
        eventId: 'sync_started_real-turn-id',
        turnId: 'real-turn-id',
        userInput: { text: 'hello', attachments: [] },
        // clientRequestId intentionally omitted
      }))

      const state = useTurnEventStore.getState()
      // Both turns coexist — the duplicate user message bug
      expect(state.orderedTurnIds).toContain('req-abc')
      expect(state.orderedTurnIds).toContain('real-turn-id')
      expect(state.orderedTurnIds).toHaveLength(2)
    })

    it('preserves turn ordering after optimistic merge', () => {
      const store = useTurnEventStore.getState()

      // Pre-existing turn
      store.append('turn-0', evt({
        type: 'turn_started', seq: 1, eventId: 'e0', turnId: 'turn-0', userInput,
      }))

      // Optimistic turn
      store.createOptimisticTurn('req-xyz', { text: 'world', attachments: [] })
      expect(useTurnEventStore.getState().orderedTurnIds).toEqual(['turn-0', 'req-xyz'])

      // Merge
      store.append('turn-1', evt({
        type: 'turn_started', seq: 1, eventId: 'e1', turnId: 'turn-1',
        userInput: { text: 'world', attachments: [] },
        clientRequestId: 'req-xyz',
      }))

      expect(useTurnEventStore.getState().orderedTurnIds).toEqual(['turn-0', 'turn-1'])
    })

    it('transfers optimistic events to real turn log during merge', () => {
      const store = useTurnEventStore.getState()

      store.createOptimisticTurn('req-abc', { text: 'hello', attachments: [] })

      // The optimistic turn has a synthetic turn_started event
      const optimisticLog = useTurnEventStore.getState().turnLogs.get('req-abc')
      expect(optimisticLog!.getEvents()).toHaveLength(1)

      // Merge with real turn
      store.append('real-id', evt({
        type: 'turn_started', seq: 1, eventId: 'e-real', turnId: 'real-id',
        userInput: { text: 'hello', attachments: [] },
        clientRequestId: 'req-abc',
      }))

      // Real log should have the event
      const realLog = useTurnEventStore.getState().turnLogs.get('real-id')
      expect(realLog!.getEvents()).toHaveLength(1)
      expect(realLog!.getEvents()[0].eventId).toBe('e-real')
    })
  })

  describe('slot_opened before slot_snapshot (content-slots projection)', () => {
    it('drops slot_snapshot when slot_opened has not been received', () => {
      let view = contentSlotsReducer.init()

      view = contentSlotsReducer.reduce(view, evt({
        type: 'slot_snapshot', seq: 1, slotId: 'agent-msg-1',
        content: 'Hello from agent', artifacts: [],
      }))

      expect(view).toHaveLength(0)
    })

    it('accepts slot_snapshot after slot_opened', () => {
      let view = contentSlotsReducer.init()

      view = contentSlotsReducer.reduce(view, evt({
        type: 'slot_opened', seq: 1, slotId: 'agent-msg-1', slotType: 'agent',
        agentId: 'agent-1', agentName: 'Test Agent',
      }))

      view = contentSlotsReducer.reduce(view, evt({
        type: 'slot_snapshot', seq: 2, slotId: 'agent-msg-1',
        content: 'Hello from agent', artifacts: [],
      }))

      expect(view).toHaveLength(1)
      expect(view[0].content).toBe('Hello from agent')
      expect(view[0].agentId).toBe('agent-1')
    })

    it('drops slot_terminated when slot_opened has not been received', () => {
      let view = contentSlotsReducer.init()

      view = contentSlotsReducer.reduce(view, evt({
        type: 'slot_terminated', seq: 1, slotId: 'agent-msg-1', status: 'completed',
      }))

      expect(view).toHaveLength(0)
    })

    it('handles multiple agents in a single turn', () => {
      let view = contentSlotsReducer.init()

      // Open both slots
      view = contentSlotsReducer.reduce(view, evt({
        type: 'slot_opened', seq: 1, slotId: 's1', slotType: 'agent',
        agentId: 'a1', agentName: 'Agent One',
      }))
      view = contentSlotsReducer.reduce(view, evt({
        type: 'slot_opened', seq: 2, slotId: 's2', slotType: 'agent',
        agentId: 'a2', agentName: 'Agent Two',
      }))

      // Snapshot both
      view = contentSlotsReducer.reduce(view, evt({
        type: 'slot_snapshot', seq: 3, slotId: 's1',
        content: 'From agent 1', artifacts: [],
      }))
      view = contentSlotsReducer.reduce(view, evt({
        type: 'slot_snapshot', seq: 4, slotId: 's2',
        content: 'From agent 2', artifacts: [],
      }))

      expect(view).toHaveLength(2)
      expect(view[0].content).toBe('From agent 1')
      expect(view[1].content).toBe('From agent 2')
    })

    it('duplicate slot_opened is idempotent', () => {
      let view = contentSlotsReducer.init()

      const openEvent = evt({
        type: 'slot_opened', seq: 1, slotId: 's1', slotType: 'agent', agentId: 'a1',
      })

      view = contentSlotsReducer.reduce(view, openEvent)
      view = contentSlotsReducer.reduce(view, { ...openEvent, eventId: 'dup' })

      expect(view).toHaveLength(1)
    })
  })

  describe('slot_opened before slot_snapshot (rail projection)', () => {
    it('slot_snapshot alone does not create a rail item', () => {
      let railView = railReducer.init()

      railView = railReducer.reduce(railView, evt({
        type: 'slot_snapshot', seq: 1, slotId: 'msg-1',
        content: 'Agent response', artifacts: [],
      }))

      expect(railView).toHaveLength(0)
    })

    it('slot_opened creates a rail item for agent slots', () => {
      let railView = railReducer.init()

      railView = railReducer.reduce(railView, evt({
        type: 'slot_opened', seq: 1, slotId: 'msg-1', slotType: 'agent',
        agentId: 'a1', agentName: 'Agent One',
      }))

      expect(railView).toHaveLength(1)
      expect(railView[0].label).toContain('Agent One')
      expect(railView[0].isActive).toBe(true)
    })
  })

  describe('full sync bridge sequence', () => {
    it('produces correct content-slots and rail from complete event sequence', () => {
      const events: TurnEvent[] = [
        evt({ type: 'turn_started', seq: 1, eventId: 'e1', userInput }),
        evt({ type: 'slot_opened', seq: 2, eventId: 'e2', slotId: 'msg-1', slotType: 'agent', agentId: 'a1', agentName: 'Agent One' }),
        evt({ type: 'slot_snapshot', seq: 3, eventId: 'e3', slotId: 'msg-1', content: 'Response', artifacts: [] }),
        evt({ type: 'slot_terminated', seq: 4, eventId: 'e4', slotId: 'msg-1', status: 'completed' }),
        evt({ type: 'turn_completed', seq: 5, eventId: 'e5', durationMs: 1000 }),
      ]

      // Content slots
      let contentView = contentSlotsReducer.init()
      for (const e of events) {
        contentView = contentSlotsReducer.reduce(contentView, e)
      }
      expect(contentView).toHaveLength(1)
      expect(contentView[0].content).toBe('Response')
      expect(contentView[0].status).toBe('completed')

      // Rail
      const railItems = replayRail(events)
      const agentItem = railItems.find(r => r.key === 'slot-msg-1')
      expect(agentItem).toBeDefined()
      expect(agentItem!.isActive).toBe(false)
      const terminalItem = railItems.find(r => r.key === 'turn-terminal')
      expect(terminalItem).toBeDefined()
      expect(terminalItem!.label).toContain('Completed')
    })

    it('without slot_opened, sync bridge sequence produces empty projections', () => {
      // Reproduces the pre-fix behavior where pushIncrementalUpdates
      // did not emit slot_opened
      const events: TurnEvent[] = [
        evt({ type: 'turn_started', seq: 1, eventId: 'e1', userInput }),
        // Missing: slot_opened
        evt({ type: 'slot_snapshot', seq: 2, eventId: 'e2', slotId: 'msg-1', content: 'Response', artifacts: [] }),
        evt({ type: 'slot_terminated', seq: 3, eventId: 'e3', slotId: 'msg-1', status: 'completed' }),
        evt({ type: 'turn_completed', seq: 4, eventId: 'e4', durationMs: 1000 }),
      ]

      let contentView = contentSlotsReducer.init()
      for (const e of events) {
        contentView = contentSlotsReducer.reduce(contentView, e)
      }
      // Content is empty — the bug
      expect(contentView).toHaveLength(0)

      // Rail has no agent item — only the turn terminal
      const railItems = replayRail(events)
      const agentItem = railItems.find(r => r.key === 'slot-msg-1')
      expect(agentItem).toBeUndefined()
    })
  })

  describe('synthesis slot classification (supervisor duplicate fix)', () => {
    it('summary slot is rendered by content-slots as summary type', () => {
      // When sync bridge classifies a synthesis entity as summary,
      // the slot_opened event should have slotType: 'summary'
      let view = contentSlotsReducer.init()

      // Agent slot (task-tracked)
      view = contentSlotsReducer.reduce(view, evt({
        type: 'slot_opened', seq: 1, slotId: 'agent-msg', slotType: 'agent',
        agentId: 'a1', agentName: 'Agent One',
      }))
      view = contentSlotsReducer.reduce(view, evt({
        type: 'slot_snapshot', seq: 2, slotId: 'agent-msg',
        content: 'Agent response', artifacts: [],
      }))

      // Synthesis slot (summary type from classifySlotType)
      view = contentSlotsReducer.reduce(view, evt({
        type: 'slot_opened', seq: 3, slotId: 'synthesis-msg', slotType: 'summary',
        mode: 'supervisor',
      }))
      view = contentSlotsReducer.reduce(view, evt({
        type: 'slot_snapshot', seq: 4, slotId: 'synthesis-msg',
        content: 'Synthesized response', artifacts: [],
      }))

      expect(view).toHaveLength(2)
      expect(view[0].slotType).toBe('agent')
      expect(view[0].content).toBe('Agent response')
      expect(view[1].slotType).toBe('summary')
      expect(view[1].content).toBe('Synthesized response')
    })

    it('summary slot does NOT create a rail item', () => {
      const events: TurnEvent[] = [
        evt({ type: 'turn_started', seq: 1, eventId: 'e1', userInput }),
        // Agent slot (shows in rail)
        evt({ type: 'slot_opened', seq: 2, eventId: 'e2', slotId: 'agent-msg', slotType: 'agent', agentId: 'a1', agentName: 'Agent One' }),
        evt({ type: 'slot_terminated', seq: 3, eventId: 'e3', slotId: 'agent-msg', status: 'completed' }),
        // Summary slot (should NOT show in rail)
        evt({ type: 'slot_opened', seq: 4, eventId: 'e4', slotId: 'synthesis-msg', slotType: 'summary', mode: 'supervisor' }),
        evt({ type: 'slot_terminated', seq: 5, eventId: 'e5', slotId: 'synthesis-msg', status: 'completed' }),
        evt({ type: 'turn_completed', seq: 6, eventId: 'e6', durationMs: 1000 }),
      ]

      const railItems = replayRail(events)
      const agentItems = railItems.filter(r => r.key.startsWith('slot-'))
      expect(agentItems).toHaveLength(1) // only the agent, not the summary
      expect(agentItems[0].key).toBe('slot-agent-msg')
    })
  })

  describe('orphan optimistic turn cleanup', () => {
    it('removeTurn removes orphaned optimistic turns', () => {
      const store = useTurnEventStore.getState()

      store.createOptimisticTurn('opt-123', { text: 'test', attachments: [] })
      expect(useTurnEventStore.getState().orderedTurnIds).toContain('opt-123')

      store.removeTurn('opt-123')

      const state = useTurnEventStore.getState()
      expect(state.orderedTurnIds).not.toContain('opt-123')
      expect(state.turnLogs.has('opt-123')).toBe(false)
    })

    it('removeTurn recomputes composer state from remaining turns', () => {
      const store = useTurnEventStore.getState()

      // Create a single active turn
      store.append('turn-1', evt({
        type: 'turn_started', seq: 1, eventId: 'e1', turnId: 'turn-1', userInput,
      }))
      expect(useTurnEventStore.getState().composerState.isProcessing).toBe(true)

      // Remove the active turn (orphan cleanup)
      store.removeTurn('turn-1')

      // Composer recomputes from empty → not processing
      expect(useTurnEventStore.getState().composerState.isProcessing).toBe(false)
    })

    it('removeTurn preserves isProcessing when other active turns remain', () => {
      const store = useTurnEventStore.getState()

      // Two turns, second is still active
      store.append('turn-1', evt({
        type: 'turn_started', seq: 1, eventId: 'e1', turnId: 'turn-1', userInput,
      }))
      store.append('turn-2', evt({
        type: 'turn_started', seq: 1, eventId: 'e2', turnId: 'turn-2', userInput,
      }))

      // Remove turn-1 but turn-2 still active
      store.removeTurn('turn-1')

      // Composer replays turn-2's turn_started → isProcessing stays true
      expect(useTurnEventStore.getState().composerState.isProcessing).toBe(true)
    })

    it('removeTurn cleans up turnIdByClientRequestId mapping', () => {
      const store = useTurnEventStore.getState()

      store.createOptimisticTurn('req-id', { text: 'test', attachments: [] })
      expect(useTurnEventStore.getState().turnIdByClientRequestId.get('req-id')).toBe('req-id')

      store.removeTurn('req-id')
      expect(useTurnEventStore.getState().turnIdByClientRequestId.has('req-id')).toBe(false)
    })
  })

  describe('artifact-only agent rendering', () => {
    it('slot_snapshot with empty content but artifacts creates a slot with artifacts', () => {
      let view = contentSlotsReducer.init()

      view = contentSlotsReducer.reduce(view, evt({
        type: 'slot_opened', seq: 1, slotId: 'img-agent', slotType: 'agent',
        agentId: 'img-1', agentName: 'Image Generator',
      }))
      view = contentSlotsReducer.reduce(view, evt({
        type: 'slot_snapshot', seq: 2, slotId: 'img-agent',
        content: '',
        artifacts: [{
          artifactId: 'art-img-1',
          name: 'generated.png',
          parts: [{ kind: 'file', file: { uri: 'https://s3/image.png', mime_type: 'image/png' } }],
        }],
      }))

      expect(view).toHaveLength(1)
      expect(view[0].content).toBe('')
      expect(view[0].artifacts).toHaveLength(1)
      expect(view[0].artifacts[0].parts[0].kind).toBe('file')
    })

    it('full sequence for artifact-only agent: opened → snapshot(empty content + artifacts) → terminated', () => {
      const events: TurnEvent[] = [
        evt({ type: 'turn_started', seq: 1, eventId: 'e1', userInput }),
        evt({
          type: 'slot_opened', seq: 2, eventId: 'e2', slotId: 'img-msg',
          slotType: 'agent', agentId: 'img-1', agentName: 'Image Generator',
        }),
        evt({
          type: 'slot_snapshot', seq: 3, eventId: 'e3', slotId: 'img-msg',
          content: '',
          artifacts: [{
            artifactId: 'art-1', name: 'output.png',
            parts: [{ kind: 'file', file: { uri: 'https://s3/img.png', mime_type: 'image/png' } }],
          }],
        }),
        evt({ type: 'slot_terminated', seq: 4, eventId: 'e4', slotId: 'img-msg', status: 'completed' }),
        evt({ type: 'turn_completed', seq: 5, eventId: 'e5', durationMs: 5000 }),
      ]

      let contentView = contentSlotsReducer.init()
      for (const e of events) {
        contentView = contentSlotsReducer.reduce(contentView, e)
      }

      expect(contentView).toHaveLength(1)
      expect(contentView[0].content).toBe('')
      expect(contentView[0].artifacts).toHaveLength(1)
      expect(contentView[0].status).toBe('completed')

      // Rail should still show the agent
      const railItems = replayRail(events)
      const agentItem = railItems.find(r => r.key === 'slot-img-msg')
      expect(agentItem).toBeDefined()
      expect(agentItem!.label).toContain('Image Generator')
    })
  })

  describe('SSE source propagation via hydrated flag', () => {
    it('slot_snapshot with hydrated: false (SSE source) produces non-hydrated slot', () => {
      let view = contentSlotsReducer.init()

      view = contentSlotsReducer.reduce(view, evt({
        type: 'slot_opened', seq: 1, slotId: 'msg-1', slotType: 'agent',
        agentId: 'a1', agentName: 'Test Agent',
      }))
      view = contentSlotsReducer.reduce(view, evt({
        type: 'slot_snapshot', seq: 2, slotId: 'msg-1',
        content: 'Hello World', artifacts: [],
        hydrated: false, // SSE source
      }))

      expect(view[0].hydrated).toBe(false)
      expect(view[0].content).toBe('Hello World')
    })

    it('slot_snapshot with hydrated: true (DB source) produces hydrated slot', () => {
      let view = contentSlotsReducer.init()

      view = contentSlotsReducer.reduce(view, evt({
        type: 'slot_opened', seq: 1, slotId: 'msg-1', slotType: 'agent',
        agentId: 'a1', agentName: 'Test Agent',
      }))
      view = contentSlotsReducer.reduce(view, evt({
        type: 'slot_snapshot', seq: 2, slotId: 'msg-1',
        content: 'Hello World', artifacts: [],
        hydrated: true, // DB source
      }))

      expect(view[0].hydrated).toBe(true)
    })

    it('full SSE sequence: non-hydrated snapshot enables streaming animation window', () => {
      // Simulates the live SSE flow:
      // 1. slot_opened (task_submitted) → streaming
      // 2. slot_snapshot with hydrated:false (artifact_update) → still streaming, content present
      // 3. slot_terminated (task_update completed) → completed
      const events: TurnEvent[] = [
        evt({ type: 'turn_started', seq: 1, eventId: 'e1', userInput }),
        evt({
          type: 'slot_opened', seq: 2, eventId: 'e2', slotId: 'msg-1',
          slotType: 'agent', agentId: 'a1', agentName: 'GPT Agent',
        }),
        evt({
          type: 'slot_snapshot', seq: 3, eventId: 'e3', slotId: 'msg-1',
          content: 'Agent response text', artifacts: [],
          hydrated: false, // from SSE
        }),
      ]

      let view = contentSlotsReducer.init()
      for (const e of events) {
        view = contentSlotsReducer.reduce(view, e)
      }

      // Mid-stream: content present, still streaming, NOT hydrated
      expect(view[0].content).toBe('Agent response text')
      expect(view[0].status).toBe('streaming')
      expect(view[0].hydrated).toBe(false)

      // Now terminate
      view = contentSlotsReducer.reduce(view, evt({
        type: 'slot_terminated', seq: 4, slotId: 'msg-1', status: 'completed',
      }))
      expect(view[0].status).toBe('completed')
      expect(view[0].hydrated).toBe(false) // hydrated flag preserved after termination
    })

    it('full DB hydration sequence: hydrated snapshot skips streaming animation', () => {
      // Simulates page refresh: all events arrive at once, snapshots are hydrated
      const events: TurnEvent[] = [
        evt({ type: 'turn_started', seq: 1, eventId: 'e1', userInput }),
        evt({
          type: 'slot_opened', seq: 2, eventId: 'e2', slotId: 'msg-1',
          slotType: 'agent', agentId: 'a1', agentName: 'GPT Agent',
        }),
        evt({
          type: 'slot_snapshot', seq: 3, eventId: 'e3', slotId: 'msg-1',
          content: 'Agent response text', artifacts: [],
          hydrated: true, // from DB
        }),
        evt({ type: 'slot_terminated', seq: 4, eventId: 'e4', slotId: 'msg-1', status: 'completed' }),
        evt({ type: 'turn_completed', seq: 5, eventId: 'e5', durationMs: 1000 }),
      ]

      let view = contentSlotsReducer.init()
      for (const e of events) {
        view = contentSlotsReducer.reduce(view, e)
      }

      expect(view[0].content).toBe('Agent response text')
      expect(view[0].status).toBe('completed')
      expect(view[0].hydrated).toBe(true) // hydrated — no animation
    })
  })
})
