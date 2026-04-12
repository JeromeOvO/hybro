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

  it('optimistic merge: clientRequestId replaces optimistic turnId', () => {
    const store = useTurnEventStore.getState()
    store.createOptimisticTurn('opt-abc', { text: 'hi', attachments: [] })

    expect(useTurnEventStore.getState().orderedTurnIds).toContain('opt-abc')

    store.append('real-123', evt({
      type: 'turn_started', seq: 1, eventId: 'e1', turnId: 'real-123',
      userInput, clientRequestId: 'opt-abc',
    }))

    const state = useTurnEventStore.getState()
    expect(state.orderedTurnIds).not.toContain('opt-abc')
    expect(state.orderedTurnIds).toContain('real-123')
    expect(state.turnLogs.has('real-123')).toBe(true)
    expect(state.turnLogs.has('opt-abc')).toBe(false)
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

  it('reset clears all state', () => {
    const store = useTurnEventStore.getState()
    store.append('turn-1', evt({ type: 'turn_started', seq: 1, eventId: 'e1', userInput }))
    store.reset()

    const state = useTurnEventStore.getState()
    expect(state.turnLogs.size).toBe(0)
    expect(state.orderedTurnIds).toEqual([])
    expect(state.composerState.mode).toBe('normal')
  })
})
