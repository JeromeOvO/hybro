import { describe, it, expect } from 'vitest'
import { contentSlotsReducer, getVisibleSlots } from '@/stores/turn-event-store/projections/content-slots'
import type { TurnEvent, ContentSlotView, UserInputData } from '@/stores/turn-event-store/types'

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

describe('contentSlotsReducer', () => {
  it('init returns empty array', () => {
    expect(contentSlotsReducer.init()).toEqual([])
  })

  it('slot_opened creates a new slot in streaming state', () => {
    const view = contentSlotsReducer.reduce(
      contentSlotsReducer.init(),
      evt({ type: 'slot_opened', slotId: 'msg-1', slotType: 'agent', agentId: 'a1', agentName: 'Agent A' }),
    )
    expect(view).toHaveLength(1)
    expect(view[0]).toMatchObject({
      slotId: 'msg-1',
      slotType: 'agent',
      agentId: 'a1',
      agentName: 'Agent A',
      content: '',
      artifacts: [],
      status: 'streaming',
    })
  })

  it('slot_delta accumulates text content', () => {
    let view = contentSlotsReducer.init()
    view = contentSlotsReducer.reduce(view, evt({ type: 'slot_opened', seq: 1, slotId: 'msg-1', slotType: 'agent', agentName: 'A' }))
    view = contentSlotsReducer.reduce(view, evt({ type: 'slot_delta', seq: 2, slotId: 'msg-1', textDelta: 'Hello ' }))
    view = contentSlotsReducer.reduce(view, evt({ type: 'slot_delta', seq: 3, slotId: 'msg-1', textDelta: 'World' }))
    expect(view[0].content).toBe('Hello World')
  })

  it('artifact_appended adds to artifacts array', () => {
    let view = contentSlotsReducer.init()
    view = contentSlotsReducer.reduce(view, evt({ type: 'slot_opened', seq: 1, slotId: 'msg-1', slotType: 'agent', agentName: 'A' }))
    view = contentSlotsReducer.reduce(view, evt({
      type: 'artifact_appended', seq: 2, slotId: 'msg-1',
      artifact: { artifactId: 'art-1', name: 'file.png', parts: [] },
    }))
    expect(view[0].artifacts).toHaveLength(1)
    expect(view[0].artifacts[0].artifactId).toBe('art-1')
  })

  it('slot_snapshot applies append-only content updates and artifacts', () => {
    let view = contentSlotsReducer.init()
    view = contentSlotsReducer.reduce(view, evt({ type: 'slot_opened', seq: 1, slotId: 'msg-1', slotType: 'agent', agentName: 'A' }))
    view = contentSlotsReducer.reduce(view, evt({ type: 'slot_delta', seq: 2, slotId: 'msg-1', textDelta: 'partial' }))
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_snapshot', seq: 3, slotId: 'msg-1',
      content: 'partial + final content',
      artifacts: [{ artifactId: 'art-1', name: 'result', parts: [] }],
    }))
    expect(view[0].content).toBe('partial + final content')
    expect(view[0].artifacts).toHaveLength(1)
  })

  it('slot_snapshot ignores divergent rewrite after visible content exists', () => {
    let view = contentSlotsReducer.init()
    view = contentSlotsReducer.reduce(view, evt({ type: 'slot_opened', seq: 1, slotId: 'msg-1', slotType: 'agent', agentName: 'A' }))
    view = contentSlotsReducer.reduce(view, evt({ type: 'slot_delta', seq: 2, slotId: 'msg-1', textDelta: 'partial' }))
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_snapshot', seq: 3, slotId: 'msg-1',
      content: 'totally different final text',
      artifacts: [{ artifactId: 'art-2', name: 'result-2', parts: [] }],
    }))
    expect(view[0].content).toBe('partial')
    expect(view[0].artifacts).toHaveLength(1)
    expect(view[0].artifacts[0].artifactId).toBe('art-2')
  })

  it('slot_terminated changes status', () => {
    let view = contentSlotsReducer.init()
    view = contentSlotsReducer.reduce(view, evt({ type: 'slot_opened', seq: 1, slotId: 'msg-1', slotType: 'agent', agentName: 'A' }))
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_terminated', seq: 2, slotId: 'msg-1', status: 'completed',
    }))
    expect(view[0].status).toBe('completed')
  })

  it('slot_terminated with error sets error field', () => {
    let view = contentSlotsReducer.init()
    view = contentSlotsReducer.reduce(view, evt({ type: 'slot_opened', seq: 1, slotId: 'msg-1', slotType: 'agent', agentName: 'A' }))
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_terminated', seq: 2, slotId: 'msg-1', status: 'failed', error: 'agent crashed',
    }))
    expect(view[0].status).toBe('failed')
    expect(view[0].error).toBe('agent crashed')
  })

  it('duplicate slot_terminated is ignored (idempotent)', () => {
    let view = contentSlotsReducer.init()
    view = contentSlotsReducer.reduce(view, evt({ type: 'slot_opened', seq: 1, slotId: 'msg-1', slotType: 'agent', agentName: 'A' }))
    view = contentSlotsReducer.reduce(view, evt({ type: 'slot_terminated', seq: 2, slotId: 'msg-1', status: 'completed' }))
    view = contentSlotsReducer.reduce(view, evt({ type: 'slot_terminated', seq: 3, slotId: 'msg-1', status: 'failed' }))
    expect(view[0].status).toBe('completed') // first wins
  })

  it('hitl_requested + hitl_answered creates hitl_record slot', () => {
    let view = contentSlotsReducer.init()
    view = contentSlotsReducer.reduce(view, evt({
      type: 'hitl_requested', seq: 1, hitlId: 'h1', source: 'agent', agentName: 'Agent A',
      prompt: 'What color?', promptType: 'text',
    }))
    // Pending marker stored internally (hitl-pending:h1)
    expect(view).toHaveLength(1)
    expect(view[0].slotId).toBe('hitl-pending:h1')
    expect(view[0].status).toBe('streaming')

    view = contentSlotsReducer.reduce(view, evt({
      type: 'hitl_answered', seq: 2, hitlId: 'h1', answer: 'Blue',
    }))
    // Pending marker removed, record created
    expect(view).toHaveLength(1)
    expect(view[0]).toMatchObject({
      slotId: 'hitl-record:h1',
      slotType: 'hitl_record',
      hitlPrompt: 'What color?',
      hitlAnswer: 'Blue',
      hitlSource: 'agent',
      status: 'completed',
    })
  })

  it('turn_completed closes unterminated slots as completed', () => {
    let view = contentSlotsReducer.init()
    view = contentSlotsReducer.reduce(view, evt({ type: 'slot_opened', seq: 1, slotId: 'msg-1', slotType: 'agent', agentName: 'A' }))
    view = contentSlotsReducer.reduce(view, evt({ type: 'turn_completed', seq: 5, durationMs: 1000 }))
    expect(view[0].status).toBe('completed')
  })

  it('turn_failed closes unterminated slots as failed', () => {
    let view = contentSlotsReducer.init()
    view = contentSlotsReducer.reduce(view, evt({ type: 'slot_opened', seq: 1, slotId: 'msg-1', slotType: 'agent', agentName: 'A' }))
    view = contentSlotsReducer.reduce(view, evt({ type: 'turn_failed', seq: 5, reason: 'error' }))
    expect(view[0].status).toBe('failed')
  })

  it('summary slot_opened creates summary-typed slot', () => {
    let view = contentSlotsReducer.init()
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_opened', seq: 1, slotId: 'sum-1', slotType: 'summary', mode: 'supervisor',
    }))
    expect(view[0]).toMatchObject({
      slotId: 'sum-1',
      slotType: 'summary',
      mode: 'supervisor',
      status: 'streaming',
    })
  })

  it('events for unknown slotId are ignored', () => {
    let view = contentSlotsReducer.init()
    view = contentSlotsReducer.reduce(view, evt({ type: 'slot_delta', seq: 1, slotId: 'unknown', textDelta: 'data' }))
    expect(view).toHaveLength(0)
  })
})

describe('getVisibleSlots filtering', () => {
  it('filters out canceled slots', () => {
    const slots: ContentSlotView[] = [
      {
        slotId: 'slot-1',
        slotType: 'agent',
        content: 'Completed response',
        artifacts: [],
        status: 'completed',
        agentId: 'a1',
        agentName: 'Agent One',
      },
      {
        slotId: 'slot-2',
        slotType: 'agent',
        content: 'Canceled',
        artifacts: [],
        status: 'canceled',
        agentId: 'a2',
        agentName: 'Agent Two',
      },
    ]

    const visible = getVisibleSlots(slots)

    expect(visible).toHaveLength(1)
    expect(visible[0].slotId).toBe('slot-1')
  })

  it('filters out failed slots', () => {
    const slots: ContentSlotView[] = [
      {
        slotId: 'slot-1',
        slotType: 'agent',
        content: 'Completed response',
        artifacts: [],
        status: 'completed',
        agentId: 'a1',
        agentName: 'Agent One',
      },
      {
        slotId: 'slot-2',
        slotType: 'agent',
        content: 'Failed',
        artifacts: [],
        status: 'failed',
        error: 'agent crashed',
        agentId: 'a2',
        agentName: 'Agent Two',
      },
    ]

    const visible = getVisibleSlots(slots)

    expect(visible).toHaveLength(1)
    expect(visible[0].slotId).toBe('slot-1')
  })

  it('filters out rejected slots', () => {
    const slots: ContentSlotView[] = [
      {
        slotId: 'slot-1',
        slotType: 'agent',
        content: 'Completed response',
        artifacts: [],
        status: 'completed',
        agentId: 'a1',
        agentName: 'Agent One',
      },
      {
        slotId: 'slot-2',
        slotType: 'agent',
        content: 'Rejected',
        artifacts: [],
        status: 'rejected',
        agentId: 'a2',
        agentName: 'Agent Two',
      },
    ]

    const visible = getVisibleSlots(slots)

    expect(visible).toHaveLength(1)
    expect(visible[0].slotId).toBe('slot-1')
  })

  it('preserves completed and streaming slots', () => {
    const slots: ContentSlotView[] = [
      {
        slotId: 'slot-1',
        slotType: 'agent',
        content: 'Completed',
        artifacts: [],
        status: 'completed',
        agentId: 'a1',
        agentName: 'Agent One',
      },
      {
        slotId: 'slot-2',
        slotType: 'agent',
        content: 'Streaming...',
        artifacts: [],
        status: 'streaming',
        agentId: 'a2',
        agentName: 'Agent Two',
      },
    ]

    const visible = getVisibleSlots(slots)

    expect(visible).toHaveLength(2)
    expect(visible[0].status).toBe('completed')
    expect(visible[1].status).toBe('streaming')
  })

  it('filters both hitl-pending and terminal slots', () => {
    const slots: ContentSlotView[] = [
      {
        slotId: 'hitl-pending:h1',
        slotType: 'hitl_record',
        content: '',
        artifacts: [],
        status: 'streaming',
        hitlPrompt: 'What color?',
        hitlSource: 'agent',
      },
      {
        slotId: 'slot-1',
        slotType: 'agent',
        content: 'Canceled',
        artifacts: [],
        status: 'canceled',
        agentId: 'a1',
        agentName: 'Agent One',
      },
      {
        slotId: 'slot-2',
        slotType: 'agent',
        content: 'Completed',
        artifacts: [],
        status: 'completed',
        agentId: 'a2',
        agentName: 'Agent Two',
      },
    ]

    const visible = getVisibleSlots(slots)

    // Only the completed slot should remain
    expect(visible).toHaveLength(1)
    expect(visible[0].slotId).toBe('slot-2')
    expect(visible[0].status).toBe('completed')
  })

  it('sorts hitl_record slots before agent slots', () => {
    const slots: ContentSlotView[] = [
      {
        slotId: 'agent-slot',
        slotType: 'agent',
        content: 'Agent response',
        artifacts: [],
        status: 'completed',
        agentId: 'a1',
        agentName: 'Excel Agent',
      },
      {
        slotId: 'hitl-record:hitl-1',
        slotType: 'hitl_record',
        content: '',
        artifacts: [],
        status: 'completed',
        hitlPrompt: 'What do you need?',
        hitlAnswer: 'A spreadsheet',
        agentName: 'Excel Agent',
      },
    ]

    const visible = getVisibleSlots(slots)
    expect(visible).toHaveLength(2)
    expect(visible[0].slotType).toBe('hitl_record')
    expect(visible[1].slotType).toBe('agent')
  })

  it('sorts hitl_record before agent even with journal event order (agent first)', () => {
    // Simulates journal hydration: agent slot_opened arrives before hitl_requested
    // so agent is at index 0, hitl_record at index 1. getVisibleSlots must reorder.
    let view = contentSlotsReducer.init()

    // Agent slot created first (chronological order from journal)
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_opened', seq: 1, slotId: 'agent-1', slotType: 'agent',
      agentId: 'excel-gen', agentName: 'Excel Generator Agent',
    }))
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_snapshot', seq: 2, slotId: 'agent-1',
      content: 'Here is your file', artifacts: [],
    }))

    // HITL comes after (chronological: agent started, then asked HITL)
    view = contentSlotsReducer.reduce(view, evt({
      type: 'hitl_requested', seq: 3,
      hitlId: 'hitl-1', source: 'agent', agentName: 'Excel Generator Agent',
      prompt: 'What do you need?', promptType: 'text',
    }))
    view = contentSlotsReducer.reduce(view, evt({
      type: 'hitl_answered', seq: 4, hitlId: 'hitl-1', answer: 'A spreadsheet',
    }))

    // Agent terminates after HITL resolved
    view = contentSlotsReducer.reduce(view, evt({
      type: 'slot_terminated', seq: 5, slotId: 'agent-1', status: 'completed',
    }))

    // Raw view has agent first (from event order)
    expect(view[0].slotType).toBe('agent')
    expect(view[1].slotType).toBe('hitl_record')

    // But getVisibleSlots sorts hitl_record before agent
    const visible = getVisibleSlots(view)
    expect(visible[0].slotType).toBe('hitl_record')
    expect(visible[0].hitlAnswer).toBe('A spreadsheet')
    expect(visible[1].slotType).toBe('agent')
    expect(visible[1].content).toBe('Here is your file')
  })
})
