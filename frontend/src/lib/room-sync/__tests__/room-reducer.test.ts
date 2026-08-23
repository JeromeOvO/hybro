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
  }
}

describe('RoomReducer', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    useMessageStore.getState().clearRoom()
    useStreamingStore.getState().clearRoom('room-1')
    useTraceStore.getState().clearRoom()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('disables sequencing when connected lacks room_seq (legacy fallback)', async () => {
    const deltas: string[] = []
    const reducer = makeReducer(async (f) => {
      deltas.push(f.type)
    })
    await reducer.handle(frame('connected', { connection_id: 'conn-1' }))
    await reducer.handle(frame('task_update', { message_id: 'm1', status: 'working' }))
    expect(reducer.enabled).toBe(false)
    expect(deltas).toEqual(['task_update'])
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
  })

  it('folds snapshot messages and streams into the stores', () => {
    applySnapshotToStores('room-1', {
      room_seq: 3,
      messages: [
        {
          message_id: 'm1',
          agent_id: 'a1',
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
      taskStatus: 'completed',
      clientRequestId: 'cr-1',
    })
    const buffers = useStreamingStore.getState().buffers
    expect(buffers['m2']?.text).toBe('Par')
  })

  it('retains turn logs when snapshot arrives before DB hydration', () => {
    applySnapshotToStores('room-1', {
      room_seq: 2,
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

  it('hydrates the trace store from snapshot trace and runs', () => {
    applySnapshotToStores('room-1', {
      room_seq: 2,
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
    expect(trace.runStatuses['run-1']).toBe('completed')
    expect(trace.nodes['run-1:llm_call:e1']).toMatchObject({
      kind: 'llm_call',
      clientRequestId: 'cr-1',
      model: 'gpt-4o',
      durationMs: 800,
    })
    expect(trace.runOrder['run-1']).toEqual(['run-1:llm_call:e1'])
  })
})
