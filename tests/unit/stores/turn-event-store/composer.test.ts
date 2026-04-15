import { describe, it, expect } from 'vitest'
import { composerReducer } from '@/stores/turn-event-store/projections/composer'
import type { TurnEvent, ComposerStateView } from '@/stores/turn-event-store/types'

function evt(overrides: Partial<TurnEvent> & { type: TurnEvent['type'] }): TurnEvent {
  return {
    eventId: `evt-${Math.random().toString(36).slice(2)}`,
    turnId: 'turn-1',
    seq: 1,
    ts: 1000,
    ...overrides,
  } as TurnEvent
}

describe('composerReducer', () => {
  it('init returns normal mode with no pending HITLs', () => {
    const state = composerReducer.init()
    expect(state).toEqual({ mode: 'normal', pendingHitls: [], isProcessing: false })
  })

  it('turn_started sets isProcessing true', () => {
    const state = composerReducer.reduce(
      composerReducer.init(),
      evt({ type: 'turn_started', userInput: { text: 'hi', attachments: [] } }),
    )
    expect(state.isProcessing).toBe(true)
  })

  it('hitl_requested switches to hitl_responding mode', () => {
    let state = composerReducer.init()
    state = composerReducer.reduce(state, evt({
      type: 'turn_started', seq: 1, userInput: { text: 'hi', attachments: [] },
    }))
    state = composerReducer.reduce(state, evt({
      type: 'hitl_requested', seq: 2, hitlId: 'h1', source: 'agent',
      agentName: 'Agent A', prompt: 'Color?', promptType: 'choice',
      choices: ['Red', 'Blue'],
    }))

    expect(state.mode).toBe('hitl_responding')
    expect(state.pendingHitls).toHaveLength(1)
    expect(state.pendingHitls[0]).toMatchObject({
      hitlId: 'h1',
      turnId: 'turn-1',
      source: 'agent',
      agentName: 'Agent A',
      prompt: 'Color?',
      promptType: 'choice',
      choices: ['Red', 'Blue'],
    })
  })

  it('hitl_answered removes from pending and restores normal mode', () => {
    let state = composerReducer.init()
    state = composerReducer.reduce(state, evt({
      type: 'turn_started', seq: 1, userInput: { text: 'hi', attachments: [] },
    }))
    state = composerReducer.reduce(state, evt({
      type: 'hitl_requested', seq: 2, hitlId: 'h1', source: 'agent',
      prompt: 'Color?', promptType: 'text',
    }))
    state = composerReducer.reduce(state, evt({
      type: 'hitl_answered', seq: 3, hitlId: 'h1', answer: 'Blue',
    }))

    expect(state.mode).toBe('normal')
    expect(state.pendingHitls).toHaveLength(0)
  })

  it('multiple HITLs: answering one does not close others', () => {
    let state = composerReducer.init()
    state = composerReducer.reduce(state, evt({
      type: 'turn_started', seq: 1, userInput: { text: 'hi', attachments: [] },
    }))
    state = composerReducer.reduce(state, evt({
      type: 'hitl_requested', seq: 2, hitlId: 'h1', source: 'agent',
      prompt: 'Q1?', promptType: 'text',
    }))
    state = composerReducer.reduce(state, evt({
      type: 'hitl_requested', seq: 3, hitlId: 'h2', source: 'supervisor',
      prompt: 'Q2?', promptType: 'text',
    }))

    expect(state.pendingHitls).toHaveLength(2)
    expect(state.mode).toBe('hitl_responding')

    state = composerReducer.reduce(state, evt({
      type: 'hitl_answered', seq: 4, hitlId: 'h1', answer: 'yes',
    }))

    expect(state.pendingHitls).toHaveLength(1)
    expect(state.pendingHitls[0].hitlId).toBe('h2')
    expect(state.mode).toBe('hitl_responding')
  })

  it('turn_completed sets isProcessing false', () => {
    let state = composerReducer.init()
    state = composerReducer.reduce(state, evt({
      type: 'turn_started', seq: 1, userInput: { text: 'hi', attachments: [] },
    }))
    state = composerReducer.reduce(state, evt({
      type: 'turn_completed', seq: 10, durationMs: 1000,
    }))

    expect(state.isProcessing).toBe(false)
  })

  it('hitl_expired removes from pending', () => {
    let state = composerReducer.init()
    state = composerReducer.reduce(state, evt({
      type: 'hitl_requested', seq: 1, hitlId: 'h1', source: 'agent',
      prompt: 'Q?', promptType: 'text',
    }))
    state = composerReducer.reduce(state, evt({
      type: 'hitl_expired', seq: 2, hitlId: 'h1',
    }))

    expect(state.pendingHitls).toHaveLength(0)
    expect(state.mode).toBe('normal')
  })

  it('hitl_canceled removes from pending', () => {
    let state = composerReducer.init()
    state = composerReducer.reduce(state, evt({
      type: 'hitl_requested', seq: 1, hitlId: 'h1', source: 'agent',
      prompt: 'Q?', promptType: 'text',
    }))
    state = composerReducer.reduce(state, evt({
      type: 'hitl_canceled', seq: 2, hitlId: 'h1',
    }))

    expect(state.pendingHitls).toHaveLength(0)
    expect(state.mode).toBe('normal')
  })

  it('pendingHitls sort: by groupId then groupIndex then ts', () => {
    let state = composerReducer.init()
    state = composerReducer.reduce(state, evt({
      type: 'hitl_requested', seq: 1, ts: 3000, hitlId: 'h3', source: 'agent',
      prompt: 'Q3?', promptType: 'text',
    }))
    state = composerReducer.reduce(state, evt({
      type: 'hitl_requested', seq: 2, ts: 1000, hitlId: 'h1', source: 'agent',
      prompt: 'Q1?', promptType: 'text', groupId: 'g1', groupTotal: 2, groupIndex: 1,
    }))
    state = composerReducer.reduce(state, evt({
      type: 'hitl_requested', seq: 3, ts: 2000, hitlId: 'h2', source: 'agent',
      prompt: 'Q2?', promptType: 'text', groupId: 'g1', groupTotal: 2, groupIndex: 0,
    }))

    // Grouped items first (by groupId, then groupIndex), then ungrouped by ts
    expect(state.pendingHitls.map(h => h.hitlId)).toEqual(['h2', 'h1', 'h3'])
  })
})
