import { describe, it, expect, beforeEach } from 'vitest'
import { useTurnEventStore } from '@/stores/turn-event-store'
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

describe('TurnEventLogManager store', () => {
  beforeEach(() => {
    useTurnEventStore.getState().reset()
  })

  it('append creates TurnEventLog on first event for a turnId', () => {
    const store = useTurnEventStore.getState()
    store.append('turn-1', evt({ type: 'turn_started', seq: 1, eventId: 'e1', userInput }))

    const state = useTurnEventStore.getState()
    expect(state.turnLogs.has('turn-1')).toBe(true)
    expect(state.orderedTurnIds).toEqual(['turn-1'])
  })

  it('append for existing turnId adds to same log', () => {
    const store = useTurnEventStore.getState()
    store.append('turn-1', evt({ type: 'turn_started', seq: 1, eventId: 'e1', userInput }))
    store.append('turn-1', evt({ type: 'phase_changed', seq: 2, eventId: 'e2', phase: { name: 'planning' } }))

    expect(useTurnEventStore.getState().turnLogs.get('turn-1')!.getEvents()).toHaveLength(2)
  })

  it('multiple turns maintain order', () => {
    const store = useTurnEventStore.getState()
    store.append('turn-1', evt({ type: 'turn_started', seq: 1, eventId: 'e1', turnId: 'turn-1', userInput }))
    store.append('turn-2', evt({ type: 'turn_started', seq: 1, eventId: 'e2', turnId: 'turn-2', userInput }))

    expect(useTurnEventStore.getState().orderedTurnIds).toEqual(['turn-1', 'turn-2'])
  })

  it('keeps canonical clientRequestId turn alongside non-canonical ids', () => {
    const store = useTurnEventStore.getState()
    store.createOptimisticTurn('opt-abc', { text: 'hi', attachments: [] })

    expect(useTurnEventStore.getState().orderedTurnIds).toContain('opt-abc')

    store.append('real-123', evt({
      type: 'turn_started', seq: 1, eventId: 'e1', turnId: 'real-123',
      userInput, clientRequestId: 'opt-abc',
    }))

    const state = useTurnEventStore.getState()
    expect(state.orderedTurnIds).toContain('opt-abc')
    expect(state.orderedTurnIds).toContain('real-123')
    expect(state.turnLogs.has('real-123')).toBe(true)
    expect(state.turnLogs.has('opt-abc')).toBe(true)
  })

  it('composerState updates on hitl_requested', () => {
    const store = useTurnEventStore.getState()
    store.append('turn-1', evt({ type: 'turn_started', seq: 1, eventId: 'e1', userInput }))
    store.append('turn-1', evt({
      type: 'hitl_requested', seq: 2, eventId: 'e2', hitlId: 'h1',
      source: 'agent', prompt: 'Q?', promptType: 'text',
    }))

    expect(useTurnEventStore.getState().composerState.mode).toBe('hitl_responding')
    expect(useTurnEventStore.getState().composerState.pendingHitls).toHaveLength(1)
  })

  it('composerState isProcessing tracks active turns', () => {
    const store = useTurnEventStore.getState()
    store.append('turn-1', evt({ type: 'turn_started', seq: 1, eventId: 'e1', userInput }))
    expect(useTurnEventStore.getState().composerState.isProcessing).toBe(true)

    store.append('turn-1', evt({ type: 'turn_completed', seq: 5, eventId: 'e5', durationMs: 1000 }))
    expect(useTurnEventStore.getState().composerState.isProcessing).toBe(false)
  })

  it('retains canonical and non-canonical turn ids without remap', () => {
    const store = useTurnEventStore.getState()

    // Step 1: Optimistic turn created with clientRequestId
    store.createOptimisticTurn('opt-abc', { text: 'hi', attachments: [] })
    expect(useTurnEventStore.getState().orderedTurnIds).toEqual(['opt-abc'])

    // Step 2: slot_opened turn_event arrives with real turnId (no clientRequestId)
    store.append('real-123', evt({
      type: 'slot_opened', seq: 1, eventId: 'slot-e1', turnId: 'real-123',
      slotId: 'slot-1', slotType: 'agent', agentId: 'agent-1',
    }))
    // Now orderedTurnIds has both: [opt-abc, real-123]
    expect(useTurnEventStore.getState().orderedTurnIds).toEqual(['opt-abc', 'real-123'])

    // Step 3: additional turn_started does not rewrite canonical id
    store.append('real-123', evt({
      type: 'turn_started', seq: 1, eventId: 'merge-e1', turnId: 'real-123',
      userInput, clientRequestId: 'opt-abc',
    }))

    const state = useTurnEventStore.getState()
    expect(state.orderedTurnIds).toEqual(['opt-abc', 'real-123'])
    expect(state.turnLogs.has('opt-abc')).toBe(true)
    expect(state.turnLogs.has('real-123')).toBe(true)
  })

  it('normal append does not duplicate turnId already in orderedTurnIds', () => {
    // Edge case: turnId is added via optimistic merge, then a late slot_opened
    // arrives for same turnId — should not push a second entry.
    const store = useTurnEventStore.getState()

    // Create a turn normally
    store.append('turn-1', evt({ type: 'turn_started', seq: 1, eventId: 'e1', turnId: 'turn-1', userInput }))
    expect(useTurnEventStore.getState().orderedTurnIds).toEqual(['turn-1'])

    // Simulate a different zustand instance that doesn't have the log in memory
    // (technically won't happen, but ensures the guard works):
    // Force-clear the turnLog but keep the orderedTurnIds entry
    const rawState = useTurnEventStore.getState()
    const newLogs = new Map(rawState.turnLogs)
    newLogs.delete('turn-1')
    useTurnEventStore.setState({ turnLogs: newLogs })

    // Now appending to 'turn-1' will see isNewTurn=true but orderedTurnIds already has it
    store.append('turn-1', evt({
      type: 'slot_opened', seq: 2, eventId: 'e2', turnId: 'turn-1',
      slotId: 'slot-1', slotType: 'agent', agentId: 'agent-1',
    }))

    const state = useTurnEventStore.getState()
    // Should NOT have ['turn-1', 'turn-1']
    expect(state.orderedTurnIds).toEqual(['turn-1'])
    expect(state.turnLogs.has('turn-1')).toBe(true)
  })

  it('createOptimisticTurn is idempotent for same clientRequestId', () => {
    const store = useTurnEventStore.getState()

    store.createOptimisticTurn('req-idem', { text: 'hello', attachments: [] })
    store.createOptimisticTurn('req-idem', { text: 'hello again', attachments: [] })

    const state = useTurnEventStore.getState()
    expect(state.orderedTurnIds).toEqual(['req-idem'])
    expect(state.turnLogs.has('req-idem')).toBe(true)
    expect(state.turnIdByClientRequestId.get('req-idem')).toBe('req-idem')
    expect(state.turnLogs.get('req-idem')!.getEvents()).toHaveLength(1)
  })

  it('createOptimisticTurn remains idempotent on canonical turn id', () => {
    const store = useTurnEventStore.getState()

    store.createOptimisticTurn('req-abc', { text: 'hello', attachments: [] })
    store.append('real-1', evt({
      type: 'turn_started',
      seq: 1,
      eventId: 'real-start',
      turnId: 'real-1',
      userInput,
      clientRequestId: 'req-abc',
    }))

    // Repeated send-anchor call with same request id should no-op now.
    store.createOptimisticTurn('req-abc', { text: 'hello', attachments: [] })

    const state = useTurnEventStore.getState()
    expect(state.orderedTurnIds).toEqual(['req-abc', 'real-1'])
    expect(state.turnLogs.has('real-1')).toBe(true)
    expect(state.turnLogs.has('req-abc')).toBe(true)
    expect(state.turnIdByClientRequestId.get('req-abc')).toBe('real-1')
  })

  it('reset clears all state', () => {
    const store = useTurnEventStore.getState()
    store.append('turn-1', evt({ type: 'turn_started', seq: 1, eventId: 'e1', userInput }))
    store.markHydrated()
    store.reset()

    const state = useTurnEventStore.getState()
    expect(state.turnLogs.size).toBe(0)
    expect(state.orderedTurnIds).toEqual([])
    expect(state.composerState.mode).toBe('normal')
    expect(state.hydrated).toBe(false)
  })

  it('markHydrated sets hydrated to true', () => {
    const store = useTurnEventStore.getState()
    expect(store.hydrated).toBe(false)
    store.markHydrated()
    expect(useTurnEventStore.getState().hydrated).toBe(true)
  })
})
