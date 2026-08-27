import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AnySSEFrame } from '@/lib/types/sse'
import {
  BOOTSTRAP_SNAPSHOT_MS,
  REORDER_WINDOW_MS,
  RoomReducer,
  applySnapshotToStores,
} from '../room-reducer'
import { useMessageStore } from '@/stores/message-store'
import { useStreamingStore } from '@/stores/streaming-store'
import { useTraceStore } from '@/stores/trace-store'
import { useTurnStore } from '@/stores/turn-store'
import { useTurnPresentationStore } from '@/stores/turn-presentation-store'

function frame(type: string, data: Record<string, unknown>, roomSeq?: number): AnySSEFrame {
  return {
    type,
    timestamp: '2026-07-02T00:00:00.000Z',
    room_id: 'room-1',
    data: roomSeq === undefined ? data : { ...data, room_seq: roomSeq },
  } as AnySSEFrame
}

function makeReducer(onDelta?: (f: AnySSEFrame) => Promise<void>, requestSnapshot?: () => void) {
  const reducer = new RoomReducer({
    roomId: 'room-1',
    onDelta: onDelta ?? (async () => {}),
    requestSnapshot: requestSnapshot ?? (() => {}),
  })
  return reducer
}

function emptySnapshot(roomSeq: number) {
  return {
    room_seq: roomSeq,
    messages: [],
    tasks: [],
    runs: [],
    hitl: { requests: [], resolved: [] },
    streaming: {},
    trace: {},
    turn_lifecycle_schema: 1,
    turns: [],
  }
}

describe('RoomReducer', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    useMessageStore.getState().clearRoom()
    useStreamingStore.getState().clearRoom('room-1')
    useTraceStore.getState().clearRoom()
    useTurnStore.getState().clear()
    useTurnPresentationStore.getState().clear()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('preserves the unsequenced legacy fold when the handshake has no room_seq', async () => {
    const deltas: string[] = []
    const requests: string[] = []
    const reducer = makeReducer(async (f) => {
      deltas.push(f.type)
    }, () => requests.push('snapshot'))
    await reducer.handle(frame('connected', { connection_id: 'conn-1' }))
    await reducer.handle(frame('task_update', { message_id: 'm1', status: 'working' }))
    expect(reducer.enabled).toBe(false)
    expect(deltas).toEqual(['task_update'])
    expect(requests).toEqual([])
  })

  it('buffers deltas before the first snapshot and replays them in order', async () => {
    const deltas: string[] = []
    const reducer = makeReducer(async (f) => {
      deltas.push(f.type)
    })
    await reducer.handle(frame('connected', { connection_id: 'c', room_seq: 5 }))
    await reducer.handle(frame('task_update', { message_id: 'm1' }, 6))
    await reducer.handle(frame('task_update', { message_id: 'm2' }, 7))
    expect(deltas).toEqual([])

    await reducer.handle(frame('snapshot', emptySnapshot(5)))
    expect(deltas).toEqual(['task_update', 'task_update'])
  })

  it('discards deltas at or below the snapshot watermark', async () => {
    const deltas: string[] = []
    const reducer = makeReducer(async (f) => {
      deltas.push(f.type)
    })
    await reducer.handle(frame('connected', { connection_id: 'c', room_seq: 5 }))
    await reducer.handle(frame('task_update', { message_id: 'stale' }, 4))
    await reducer.handle(frame('snapshot', emptySnapshot(5)))
    expect(deltas).toEqual([])

    await reducer.handle(frame('task_update', { message_id: 'm1' }, 6))
    expect(deltas).toEqual(['task_update'])
  })

  it('buffers out-of-order deltas in the reorder window and drains in order', async () => {
    const deltas: string[] = []
    const reducer = makeReducer(async (f) => {
      deltas.push(`${f.type}:${(f.data as { room_seq?: number }).room_seq}`)
    })
    await reducer.handle(frame('connected', { connection_id: 'c', room_seq: 0 }))
    await reducer.handle(frame('snapshot', emptySnapshot(0)))

    await reducer.handle(frame('task_update', { message_id: 'm2' }, 2))
    expect(deltas).toEqual([]) // gap: seq 1 missing → buffered
    await reducer.handle(frame('task_update', { message_id: 'm1' }, 1))
    expect(deltas).toEqual(['task_update:1', 'task_update:2'])
  })

  it('requests a forced snapshot when a gap outlasts the reorder window', async () => {
    const requests: string[] = []
    const reducer = makeReducer(async () => {}, () => requests.push('force'))
    await reducer.handle(frame('connected', { connection_id: 'c', room_seq: 0 }))
    await reducer.handle(frame('snapshot', emptySnapshot(0)))

    await reducer.handle(frame('task_update', { message_id: 'm2' }, 3))
    expect(requests).toEqual([])
    vi.advanceTimersByTime(REORDER_WINDOW_MS + 1)
    expect(requests).toEqual(['force'])
  })

  it('requests a snapshot when the bootstrap window passes without one', async () => {
    const requests: string[] = []
    const reducer = makeReducer(async () => {}, () => requests.push('force'))
    await reducer.handle(frame('connected', { connection_id: 'c', room_seq: 0 }))
    expect(requests).toEqual([])
    vi.advanceTimersByTime(BOOTSTRAP_SNAPSHOT_MS + 1)
    expect(requests).toEqual(['force'])
  })

  it('requests a snapshot on the first delta before any snapshot', async () => {
    const requests: string[] = []
    const reducer = makeReducer(async () => {}, () => requests.push('force'))
    await reducer.handle(frame('connected', { connection_id: 'c', room_seq: 0 }))
    await reducer.handle(frame('task_update', { message_id: 'm1' }, 1))
    expect(requests).toEqual(['force'])
  })

  it('requests a snapshot when a heartbeat reveals one lost tail event', async () => {
    const requests: string[] = []
    const reducer = makeReducer(async () => {}, () => requests.push('force'))
    await reducer.handle(frame('connected', { connection_id: 'c', room_seq: 3 }))
    await reducer.handle(frame('snapshot', emptySnapshot(3)))

    await reducer.handle(frame('heartbeat', { room_seq: 4 }))
    vi.advanceTimersByTime(400)
    expect(requests).toEqual(['force'])
  })

  it('rejects a stale replacement snapshot before it can regress stores', async () => {
    const requests: string[] = []
    const reducer = makeReducer(async (delta) => {
      const data = delta.data as { message_id?: string }
      if (data.message_id === 'm6') {
        useMessageStore.getState().upsertMessage({
          id: 'm6', roomId: 'room-1', messageType: 'agent', content: 'newer live value',
          senderName: 'Weather Agent', timestamp: '2030-01-01T00:00:06.000Z',
        }, 'sse')
      }
    }, () => requests.push('force'))
    await reducer.handle(frame('connected', { connection_id: 'c', room_seq: 5 }))
    await reducer.handle(frame('snapshot', emptySnapshot(5)))
    await reducer.handle(frame('task_update', { message_id: 'm6' }, 6))
    expect(useMessageStore.getState().entities.m6?.content).toBe('newer live value')

    await reducer.handle(frame('snapshot', emptySnapshot(4)))

    expect(requests).toEqual(['force'])
    expect(useMessageStore.getState().entities.m6?.content).toBe('newer live value')
    await reducer.handle(frame('task_update', { message_id: 'm7' }, 7))
    expect(requests).toEqual(['force'])
  })

  it('preserves the applied watermark across reconnects and rejects an older bootstrap snapshot', async () => {
    const requests: string[] = []
    const applied: string[] = []
    const reducer = makeReducer(async (delta) => {
      const data = delta.data as { message_id?: string }
      if (data.message_id) applied.push(data.message_id)
      if (data.message_id === 'm6') {
        useMessageStore.getState().upsertMessage({
          id: 'm6', roomId: 'room-1', messageType: 'agent', content: 'newer live value',
          senderName: 'Weather Agent', timestamp: '2030-01-01T00:00:06.000Z',
        }, 'sse')
      }
    }, () => requests.push('force'))
    await reducer.handle(frame('connected', { connection_id: 'c1', room_seq: 5 }))
    await reducer.handle(frame('snapshot', emptySnapshot(5)))
    await reducer.handle(frame('task_update', { message_id: 'm6' }, 6))

    await reducer.handle(frame('connected', { connection_id: 'c2', room_seq: 6 }))
    await reducer.handle(frame('snapshot', emptySnapshot(5)))

    expect(requests).toEqual(['force'])
    expect(useMessageStore.getState().entities.m6?.content).toBe('newer live value')
    await reducer.handle(frame('snapshot', emptySnapshot(6)))
    await reducer.handle(frame('task_update', { message_id: 'm7' }, 7))
    expect(applied).toEqual(['m6', 'm7'])
  })

  it('requests a snapshot when a heartbeat reveals a gap while idle', async () => {
    const requests: string[] = []
    const reducer = makeReducer(async () => {}, () => requests.push('force'))
    await reducer.handle(frame('connected', { connection_id: 'c', room_seq: 3 }))
    await reducer.handle(frame('snapshot', emptySnapshot(3)))

    await reducer.handle(frame('heartbeat', { room_seq: 6 }))
    expect(requests).toEqual([])
    vi.advanceTimersByTime(400)
    expect(requests).toEqual(['force'])
  })
})

describe('applySnapshotToStores', () => {
  beforeEach(() => {
    useMessageStore.getState().clearRoom()
    useStreamingStore.getState().clearRoom('room-1')
    useTraceStore.getState().clearRoom()
    useTurnStore.getState().clear()
    useTurnPresentationStore.getState().clear()
  })

  it('hydrates a pure legacy snapshot without claiming canonical authority', () => {
    const snapshot = {
      room_seq: 2,
      messages: [{
        message_id: 'legacy-agent-1', agent_id: 'agent-1', agent_name: 'Weather Agent',
        content: 'Sunny', parts: null, related_message_id: 'user-1',
        client_request_id: 'client-1', status: null, task_status: 'completed',
        task_content: null, task_error: null, requires_input: false,
        requires_auth: false, step_number: null, total_steps: null,
        created_at: null, ts: '2030-01-01T00:00:01.000Z', artifacts: null,
        status_logs: [],
      }],
      tasks: [], runs: [], hitl: { requests: [], resolved: [] }, streaming: {},
      trace: {
        'legacy-run': {
          run_id: 'legacy-run', client_request_id: 'client-1', nodes: [],
          usage: null, duration_ms: 0,
        },
      },
    }

    expect(applySnapshotToStores('room-1', snapshot, new Map())).toBe(true)
    expect(useMessageStore.getState().entities['legacy-agent-1']?.content).toBe('Sunny')
    expect(useTraceStore.getState().runOrder['legacy-run']).toEqual([])
    expect(useTurnStore.getState().rooms['room-1']).toBeUndefined()
  })

  it('atomically replaces canonical Turns while preserving presentation state', () => {
    const canonicalSnapshot = {
      room_seq: 5,
      messages: [{
        message_id: 'orchestrator:run-1:inv_weather_0001',
        agent_id: null,
        content: '',
        parts: null,
        related_message_id: 'user-1',
        client_request_id: 'client-1',
        status: null,
        task_status: 'completed',
        task_content: null,
        task_error: null,
        requires_input: false,
        requires_auth: false,
        step_number: null,
        total_steps: null,
        created_at: null,
        ts: '2030-01-01T00:00:01.000Z',
        artifacts: null,
        status_logs: [],
      }],
      tasks: [],
      runs: [],
      hitl: { requests: [], resolved: [] },
      streaming: {},
      trace: {},
      turn_lifecycle_schema: 1 as const,
      turns: [{
        hybro_turn_id: 'run-1',
        run_id: 'run-1',
        user_message_id: 'user-1',
        client_request_id: 'client-1',
        state: 'active' as const,
        started_at: '2030-01-01T00:00:00.000Z',
        settled_at: null,
        duration_ms: null,
        terminal_code: null,
        terminal_summary: null,
        internal_turns: [],
        activity: [],
        current_assistant: null,
        final_answer: null,
        final_committed: false,
        hitl_interactions: [],
        active_interaction_id: null,
        agent_call_message_ids: ['orchestrator:run-1:inv_weather_0001'],
      }],
    }

    expect(applySnapshotToStores('room-1', canonicalSnapshot)).toBe(true)
    useTurnPresentationStore.getState().setExpanded('run-1', false)
    expect(applySnapshotToStores('room-1', { ...canonicalSnapshot, room_seq: 6 })).toBe(true)

    expect(useTurnStore.getState().rooms['room-1'].turns['run-1']).toMatchObject({
      id: 'run-1', userMessageId: 'user-1', clientRequestId: 'client-1',
      agentCallMessageIds: ['orchestrator:run-1:inv_weather_0001'],
    })
    expect(useTurnPresentationStore.getState().turns['run-1']).toMatchObject({
      expanded: false, manualAction: 'collapsed',
    })
  })

  it('restores canonical snapshot-only HITL requests before DB hydration', () => {
    const snapshot = {
      ...emptySnapshot(3),
      turn_lifecycle_schema: 1 as const,
      turns: [{
        hybro_turn_id: 'run-1', run_id: 'run-1', user_message_id: 'user-1',
        client_request_id: 'client-1', state: 'awaiting_input' as const,
        started_at: '2030-01-01T00:00:00.000Z', settled_at: null, duration_ms: null,
        terminal_code: null, terminal_summary: null, internal_turns: [], activity: [],
        current_assistant: null, final_answer: null, final_committed: false,
        hitl_interactions: [{
          interaction_id: 'interaction-1', state: 'awaiting_input' as const,
          request_ids: ['request-1'], requested_at: '2030-01-01T00:00:01.000Z', resumed_at: null,
          requests: [{
            request_id: 'request-1', message_id: 'hitl-message-1', question_index: 0,
            question_count: 1, prompt: 'Continue?', prompt_type: 'confirmation', choices: [],
            source: 'supervisor', agent_label: null, status: 'requested' as const, answer_ref: null,
          }],
        }],
        active_interaction_id: 'interaction-1', agent_call_message_ids: [],
      }],
      hitl: {
        requests: [{
          room_seq: 3, run_id: 'run-1', request_id: 'request-1', message_id: 'hitl-message-1',
          interaction_id: 'interaction-1', related_user_message_id: 'user-1',
          client_request_id: 'client-1', question_index: 0, question_count: 1,
          prompt: 'Continue?', prompt_type: 'confirmation', source: 'supervisor',
          ts: '2030-01-01T00:00:01.000Z',
        }],
        resolved: [],
      },
    }

    expect(applySnapshotToStores('room-1', snapshot, new Map())).toBe(true)
    expect(useMessageStore.getState().entities['hitl-message-1']).toMatchObject({
      hitlRequestId: 'request-1',
      hitlPrompt: 'Continue?',
      clientRequestId: 'client-1',
      relatedMessageId: 'user-1',
    })
  })

  it('restores a rolling-deploy legacy HITL request against an exact canonical Turn root', () => {
    const snapshot = {
      ...emptySnapshot(9),
      turn_lifecycle_schema: 1 as const,
      turns: [{
        hybro_turn_id: 'run-1', run_id: 'run-1', user_message_id: 'user-1',
        client_request_id: 'client-1', state: 'active' as const,
        started_at: '2030-01-01T00:00:00.000Z', settled_at: null, duration_ms: null,
        terminal_code: null, terminal_summary: null,
        internal_turns: [{
          internal_turn_id: 'turn-1', attempt: 1, message_ids: [],
          tool_call_ids: ['inv_travel_0001'], status: 'active' as const,
        }],
        activity: [{
          kind: 'tool' as const, id: 'inv_travel_0001', internal_turn_id: 'turn-1',
          tool_call_id: 'inv_travel_0001', label: 'Travel Planner Agent', input: {},
          partial_result: '', result: null, is_error: null, duration_ms: null,
          status: 'suspended' as const, update_index: 1, order: 8,
          execution_kind: 'agent' as const, target_name: 'Travel Planner Agent',
          request_summary: 'Plan a trip', detail_available: false,
        }],
        current_assistant: null, final_answer: null, final_committed: false,
        hitl_interactions: [], active_interaction_id: null, agent_call_message_ids: [],
      }],
      hitl: {
        requests: [{
          request_id: 'request-1', message_id: 'orchestrator:run-1:call_private',
          interaction_id: 'interaction-1', interaction_status: 'pending',
          interaction_version: 1, related_message_id: 'user-1',
          client_request_id: 'client-1', question_index: 0, question_count: 1,
          prompt: 'Which island?', prompt_type: 'text', source: 'agent',
          source_step_id: 'private-call-record', ts: '2030-01-01T00:00:01.000Z',
        }],
        resolved: [],
      },
    }

    expect(applySnapshotToStores('room-1', snapshot, new Map())).toBe(true)
    expect(useMessageStore.getState().entities['orchestrator:run-1:call_private']).toMatchObject({
      senderName: 'Travel Planner Agent',
      hitlRequestId: 'request-1',
      hitlPrompt: 'Which island?',
      clientRequestId: 'client-1',
      relatedMessageId: 'user-1',
    })
    expect(useTurnStore.getState().rooms['room-1'].turns['run-1'].state).toBe('active')
  })

  it('keeps canceled rolling-deploy HITL requests out of the composer projection', () => {
    const snapshot = {
      ...emptySnapshot(9),
      turn_lifecycle_schema: 1 as const,
      turns: [{
        hybro_turn_id: 'run-1', run_id: 'run-1', user_message_id: 'user-1',
        client_request_id: 'client-1', state: 'active' as const,
        started_at: '2030-01-01T00:00:00.000Z', settled_at: null, duration_ms: null,
        terminal_code: null, terminal_summary: null, internal_turns: [], activity: [],
        current_assistant: null, final_answer: null, final_committed: false,
        hitl_interactions: [], active_interaction_id: null, agent_call_message_ids: [],
      }],
      hitl: {
        requests: [{
          request_id: 'request-1', message_id: 'orchestrator:run-1:call_private',
          interaction_id: 'interaction-1', interaction_status: 'canceled',
          related_message_id: 'user-1', client_request_id: 'client-1',
          question_index: 0, question_count: 1, prompt: 'Which island?',
          prompt_type: 'text', source: 'agent', status: 'canceled',
          ts: '2030-01-01T00:00:01.000Z',
        }],
        resolved: [],
      },
    }

    expect(applySnapshotToStores('room-1', snapshot, new Map())).toBe(true)
    expect(useMessageStore.getState().entities['orchestrator:run-1:call_private']).toMatchObject({
      hitlRequestId: 'request-1', hitlResolved: true,
    })
  })

  it('does not apply a canonical HITL request that contradicts an existing entity root', () => {
    useMessageStore.getState().setRoom('room-1')
    useMessageStore.getState().upsertMessage({
      id: 'orchestrator:run-1:inv_request_0001', roomId: 'room-1',
      messageType: 'agent', content: '', senderName: 'Agent',
      timestamp: '2030-01-01T00:00:01.000Z', clientRequestId: 'client-1',
      relatedMessageId: 'user-1',
    }, 'sse')
    const snapshot = {
      ...emptySnapshot(3),
      turn_lifecycle_schema: 1 as const,
      turns: [{
        hybro_turn_id: 'run-1', run_id: 'run-1', user_message_id: 'other-user',
        client_request_id: 'other-client', state: 'active' as const,
        started_at: '2030-01-01T00:00:00.000Z', settled_at: null, duration_ms: null,
        terminal_code: null, terminal_summary: null, internal_turns: [], activity: [],
        current_assistant: null, final_answer: null, final_committed: false,
        hitl_interactions: [], active_interaction_id: null, agent_call_message_ids: [],
      }],
      hitl: {
        requests: [{
          room_seq: 3, run_id: 'run-1', request_id: 'request-1',
          message_id: 'orchestrator:run-1:inv_request_0001',
          interaction_id: 'interaction-1', related_user_message_id: 'other-user',
          client_request_id: 'other-client', question_index: 0, question_count: 1,
          prompt: 'Wrong root?', prompt_type: 'text', source: 'agent',
          ts: '2030-01-01T00:00:01.000Z',
        }],
        resolved: [],
      },
    }
    const index = new Map<string, string>()

    expect(applySnapshotToStores('room-1', snapshot, index)).toBe(true)
    expect(useMessageStore.getState().entities['orchestrator:run-1:inv_request_0001']?.hitlRequestId).toBeUndefined()
    expect(index.size).toBe(0)
  })

  it('rejects malformed canonical snapshot capability without mutating the Turn projection', () => {
    expect(applySnapshotToStores('room-1', {
      ...emptySnapshot(2),
      turns: undefined,
    } as never)).toBe(false)
    expect(useTurnStore.getState().rooms['room-1']).toBeUndefined()
  })

  it('folds snapshot messages and streams into the stores', () => {
    applySnapshotToStores('room-1', {
      room_seq: 3,
      turn_lifecycle_schema: 1,
      turns: [],
      messages: [
        {
          message_id: 'm1',
          agent_id: 'a1',
          agent_name: 'Weather Agent',
          content: 'Hello',
          parts: null,
          related_message_id: null,
          client_request_id: 'cr-1',
          status: null,
          task_status: 'completed',
          task_content: null,
          task_error: null,
          requires_input: false,
          requires_auth: false,
          step_number: null,
          total_steps: null,
          created_at: null,
          ts: '2026-07-02T00:00:00.000Z',
          artifacts: null,
          status_logs: [],
        },
      ],
      tasks: [],
      runs: [],
      hitl: { requests: [], resolved: [] },
      streaming: {
        m2: {
          message_id: 'm2',
          agent_id: 'a2',
          text: 'Par',
          artifacts: [],
          is_complete: false,
          client_request_id: 'cr-2',
          last_chunk: false,
        },
      },
      trace: {},
    })

    const messages = useMessageStore.getState().entities
    expect(messages['m1']).toMatchObject({
      messageType: 'agent',
      content: 'Hello',
      agentId: 'a1',
      senderName: 'Weather Agent',
      taskStatus: 'completed',
      clientRequestId: 'cr-1',
    })
    const buffers = useStreamingStore.getState().buffers
    expect(buffers['m2']?.text).toBe('Par')
  })

  it('retains turn logs when snapshot arrives before DB hydration', () => {
    applySnapshotToStores('room-1', {
      room_seq: 2,
      turn_lifecycle_schema: 1,
      turns: [],
      messages: [
        {
          message_id: 'user-1',
          agent_id: null,
          content: null,
          parts: null,
          related_message_id: null,
          client_request_id: 'cr-1',
          status: 'completed',
          task_status: null,
          task_content: null,
          task_error: null,
          requires_input: false,
          requires_auth: false,
          step_number: null,
          total_steps: null,
          created_at: null,
          ts: null,
          artifacts: null,
          status_logs: [
            {
              message: 'Planning the next actions',
              timestamp: '2026-07-02T00:00:01.000Z',
              turn_phase: 'collecting',
            },
          ],
        },
      ],
      tasks: [],
      runs: [],
      hitl: { requests: [], resolved: [] },
      streaming: {},
      trace: {},
    })

    expect(useMessageStore.getState().entities['user-1']).toMatchObject({
      messageType: 'user',
      clientRequestId: 'cr-1',
      turnTerminalStatus: 'completed',
      processingStatusLogs: [
        expect.objectContaining({
          message: 'Planning the next actions',
          turnPhase: 'collecting',
        }),
      ],
    })

    useMessageStore.getState().upsertMessage({
      id: 'user-1',
      roomId: 'room-1',
      messageType: 'user',
      content: 'Actual persisted prompt',
      senderName: 'Developer Local',
      timestamp: '2026-07-02T00:00:00.000Z',
    }, 'db')

    expect(useMessageStore.getState().entities['user-1']).toMatchObject({
      content: 'Actual persisted prompt',
      turnTerminalStatus: 'completed',
      processingStatusLogs: [
        expect.objectContaining({ message: 'Planning the next actions' }),
      ],
    })
  })

  it('does not hydrate the deprecated trace store from canonical snapshots', () => {
    applySnapshotToStores('room-1', {
      room_seq: 2,
      turn_lifecycle_schema: 1,
      turns: [],
      messages: [],
      tasks: [],
      runs: [{ run_id: 'run-1', status: 'completed', client_request_id: null, ts: '' }],
      hitl: { requests: [], resolved: [] },
      streaming: {},
      trace: {
        'run-1': {
          run_id: 'run-1',
          client_request_id: 'cr-1',
          nodes: [
            {
              id: 'run-1:llm_call:e1',
              kind: 'llm_call',
              model: 'gpt-4o',
              provider: 'openai',
              attempt: 1,
              outcome: 'completed',
              duration_ms: 800,
              usage: { input: 10, output: 2 },
              finish_reason: 'stop',
            },
          ],
          usage: { input: 10, output: 2 },
          duration_ms: 800,
        },
      },
    })

    const trace = useTraceStore.getState()
    expect(trace.runStatuses).toEqual({})
    expect(trace.nodes).toEqual({})
    expect(trace.runOrder).toEqual({})
  })
})
