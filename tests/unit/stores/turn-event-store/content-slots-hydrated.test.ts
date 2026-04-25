/**
 * Tests for the `hydrated` flag on ContentSlotView.
 *
 * The hydrated flag distinguishes content loaded from DB (page refresh)
 * vs content arriving via live SSE. This controls whether the typewriter
 * animation plays in AgentContentBlock.
 *
 * - slot_snapshot without explicit hydrated → defaults to true (DB hydration)
 * - slot_snapshot with hydrated: false → preserved (live SSE)
 * - slot_delta events → no hydrated flag (live streaming)
 */
import { describe, it, expect } from 'vitest'
import { contentSlotsReducer } from '@/stores/turn-event-store/projections/content-slots'
import type { TurnEvent } from '@/stores/turn-event-store/types'

function evt(overrides: Partial<TurnEvent> & { type: TurnEvent['type'] }): TurnEvent {
  return {
    eventId: `evt-${Math.random().toString(36).slice(2)}`,
    turnId: 'turn-1',
    seq: 1,
    ts: Date.now(),
    ...overrides,
  } as TurnEvent
}

describe('contentSlotsReducer — hydrated flag', () => {
  it('slot_snapshot without explicit hydrated defaults to hydrated: true', () => {
    let view = contentSlotsReducer.init()
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_opened', seq: 1, slotId: 's1', slotType: 'agent', agentId: 'a1',
    }))
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_snapshot', seq: 2, slotId: 's1',
      content: 'DB content', artifacts: [],
      // hydrated intentionally omitted — should default to true
    }))

    expect(view[0].hydrated).toBe(true)
    expect(view[0].content).toBe('DB content')
  })

  it('slot_snapshot with hydrated: true preserves hydrated: true', () => {
    let view = contentSlotsReducer.init()
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_opened', seq: 1, slotId: 's1', slotType: 'agent', agentId: 'a1',
    }))
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_snapshot', seq: 2, slotId: 's1',
      content: 'DB content', artifacts: [],
      hydrated: true,
    }))

    expect(view[0].hydrated).toBe(true)
  })

  it('slot_snapshot with hydrated: false preserves hydrated: false (live SSE)', () => {
    let view = contentSlotsReducer.init()
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_opened', seq: 1, slotId: 's1', slotType: 'agent', agentId: 'a1',
    }))
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_snapshot', seq: 2, slotId: 's1',
      content: 'Live SSE content', artifacts: [],
      hydrated: false,
    }))

    expect(view[0].hydrated).toBe(false)
    expect(view[0].content).toBe('Live SSE content')
  })

  it('slot_delta does not set hydrated flag', () => {
    let view = contentSlotsReducer.init()
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_opened', seq: 1, slotId: 's1', slotType: 'agent', agentId: 'a1',
    }))
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_delta', seq: 2, slotId: 's1', textDelta: 'streaming token',
    }))

    expect(view[0].hydrated).toBeUndefined()
    expect(view[0].content).toBe('streaming token')
  })

  it('slot_snapshot after slot_delta sets hydrated to true with append-only content', () => {
    let view = contentSlotsReducer.init()
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_opened', seq: 1, slotId: 's1', slotType: 'agent', agentId: 'a1',
    }))
    // Live delta first
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_delta', seq: 2, slotId: 's1', textDelta: 'partial',
    }))
    expect(view[0].hydrated).toBeUndefined()

    // Then snapshot arrives (e.g. sync bridge reconciliation)
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_snapshot', seq: 3, slotId: 's1',
      content: 'partial + full content from DB', artifacts: [],
    }))
    expect(view[0].hydrated).toBe(true)
    expect(view[0].content).toBe('partial + full content from DB')
  })

  it('SSE snapshot (hydrated: false) followed by divergent DB snapshot keeps visible content', () => {
    let view = contentSlotsReducer.init()
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_opened', seq: 1, slotId: 's1', slotType: 'agent', agentId: 'a1',
    }))
    // SSE snapshot
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_snapshot', seq: 2, slotId: 's1',
      content: 'SSE content', artifacts: [],
      hydrated: false,
    }))
    expect(view[0].hydrated).toBe(false)

    // DB snapshot replaces it
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_snapshot', seq: 3, slotId: 's1',
      content: 'DB content', artifacts: [],
      hydrated: true,
    }))
    expect(view[0].hydrated).toBe(true)
    expect(view[0].content).toBe('SSE content')
  })

  it('slot_snapshot on terminated slot is ignored (hydrated not changed)', () => {
    let view = contentSlotsReducer.init()
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_opened', seq: 1, slotId: 's1', slotType: 'agent', agentId: 'a1',
    }))
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_snapshot', seq: 2, slotId: 's1',
      content: 'Original', artifacts: [], hydrated: false,
    }))
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_terminated', seq: 3, slotId: 's1', status: 'completed',
    }))
    // Attempt to snapshot after termination
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_snapshot', seq: 4, slotId: 's1',
      content: 'Should not apply', artifacts: [], hydrated: true,
    }))
    expect(view[0].content).toBe('Original')
    expect(view[0].hydrated).toBe(false)
  })
})
