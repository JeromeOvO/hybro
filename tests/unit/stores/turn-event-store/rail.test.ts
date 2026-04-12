import { describe, it, expect } from 'vitest'
import { railReducer } from '@/stores/turn-event-store/projections/rail'
import type { TurnEvent, RailItemView } from '@/stores/turn-event-store/types'

function evt(overrides: Partial<TurnEvent> & { type: TurnEvent['type'] }): TurnEvent {
  return {
    eventId: `evt-${Math.random().toString(36).slice(2)}`,
    turnId: 'turn-1',
    seq: 1,
    ts: 1000,
    ...overrides,
  } as TurnEvent
}

describe('railReducer', () => {
  it('init returns empty array', () => {
    expect(railReducer.init()).toEqual([])
  })

  it('phase_changed planning adds spinner item', () => {
    const view = railReducer.reduce(
      railReducer.init(),
      evt({ type: 'phase_changed', phase: { name: 'planning' } }),
    )
    expect(view).toHaveLength(1)
    expect(view[0]).toMatchObject({
      icon: 'spinner',
      label: 'Planning...',
      isActive: true,
    })
  })

  it('phase_changed delegating adds delegating item and deactivates prior', () => {
    let view = railReducer.init()
    view = railReducer.reduce(view, evt({ type: 'phase_changed', seq: 1, phase: { name: 'planning' } }))
    view = railReducer.reduce(view, evt({
      type: 'phase_changed', seq: 2, ts: 2000,
      phase: { name: 'delegating', agentNames: ['Agent A', 'Agent B'], count: 2 },
    }))

    expect(view).toHaveLength(2)
    expect(view[0].isActive).toBe(false)
    expect(view[0].icon).toBe('check')
    expect(view[1]).toMatchObject({
      icon: 'spinner',
      label: 'Delegating to Agent A, Agent B',
      isActive: true,
    })
  })

  it('slot_opened adds agent working item', () => {
    const view = railReducer.reduce(
      railReducer.init(),
      evt({ type: 'slot_opened', slotId: 'msg-1', slotType: 'agent', agentName: 'Agent A' }),
    )
    expect(view).toHaveLength(1)
    expect(view[0]).toMatchObject({
      icon: 'spinner',
      label: 'Agent A: working',
      isActive: true,
    })
  })

  it('slot_terminated completed updates agent item', () => {
    let view = railReducer.init()
    view = railReducer.reduce(view, evt({ type: 'slot_opened', seq: 1, slotId: 'msg-1', slotType: 'agent', agentName: 'Agent A' }))
    view = railReducer.reduce(view, evt({ type: 'slot_terminated', seq: 2, slotId: 'msg-1', status: 'completed' }))

    expect(view[0]).toMatchObject({
      icon: 'check',
      label: 'Agent A: completed',
      isActive: false,
    })
  })

  it('slot_terminated failed updates agent item with x icon', () => {
    let view = railReducer.init()
    view = railReducer.reduce(view, evt({ type: 'slot_opened', seq: 1, slotId: 'msg-1', slotType: 'agent', agentName: 'Agent A' }))
    view = railReducer.reduce(view, evt({ type: 'slot_terminated', seq: 2, slotId: 'msg-1', status: 'failed' }))

    expect(view[0]).toMatchObject({
      icon: 'x',
      label: 'Agent A: failed',
      isActive: false,
    })
  })

  it('turn_completed adds completed item with duration', () => {
    let view = railReducer.init()
    view = railReducer.reduce(view, evt({ type: 'turn_completed', seq: 10, durationMs: 2300 }))

    const last = view[view.length - 1]
    expect(last).toMatchObject({
      icon: 'check',
      label: 'Completed (2.3s)',
      isActive: false,
    })
  })

  it('turn_failed adds failed item', () => {
    let view = railReducer.init()
    view = railReducer.reduce(view, evt({ type: 'turn_failed', seq: 10, reason: 'timeout' }))

    const last = view[view.length - 1]
    expect(last).toMatchObject({
      icon: 'x',
      label: 'Failed: timeout',
      isActive: false,
    })
  })

  it('hitl_requested adds pause item', () => {
    const view = railReducer.reduce(
      railReducer.init(),
      evt({
        type: 'hitl_requested', hitlId: 'h1', source: 'agent',
        agentName: 'Agent X', prompt: 'details?', promptType: 'text',
      }),
    )
    expect(view[0]).toMatchObject({
      icon: 'pause',
      label: 'Agent X asked for input',
      isActive: true,
    })
  })

  it('hitl_answered deactivates hitl item', () => {
    let view = railReducer.init()
    view = railReducer.reduce(view, evt({
      type: 'hitl_requested', seq: 1, hitlId: 'h1', source: 'agent',
      agentName: 'Agent X', prompt: 'details?', promptType: 'text',
    }))
    view = railReducer.reduce(view, evt({ type: 'hitl_answered', seq: 2, hitlId: 'h1', answer: 'yes' }))

    expect(view[0]).toMatchObject({
      icon: 'check',
      label: 'Agent X asked for input — answered',
      isActive: false,
    })
  })

  it('phase_changed round shows current/total', () => {
    const view = railReducer.reduce(
      railReducer.init(),
      evt({ type: 'phase_changed', phase: { name: 'round', current: 1, total: 3 } }),
    )
    expect(view[0]).toMatchObject({
      label: 'Round 1/3',
      icon: 'spinner',
      isActive: true,
    })
  })

  it('phase_changed workflow_step shows step info', () => {
    const view = railReducer.reduce(
      railReducer.init(),
      evt({ type: 'phase_changed', phase: { name: 'workflow_step', current: 2, total: 5, stepName: 'Analysis' } }),
    )
    expect(view[0]).toMatchObject({
      label: 'Step 2/5: Analysis',
      icon: 'spinner',
      isActive: true,
    })
  })
})
