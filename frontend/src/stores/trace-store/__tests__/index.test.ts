import { beforeEach, describe, expect, it } from 'vitest'
import {
  isTraceRunEventType,
  selectTraceNodesForClientRequest,
  selectTraceNodesForRun,
  useTraceStore,
} from '../index'

function apply(type: string, payload: Record<string, unknown>, correlationId = 'crid-1') {
  useTraceStore.getState().applyRunEvent({
    eventId: `public:run-1:${type}:1`,
    runId: 'run-1',
    type,
    payload,
    correlationId,
  })
}

describe('trace-store', () => {
  beforeEach(() => {
    useTraceStore.getState().clearRoom()
  })

  it('recognizes only public trace run_event kinds', () => {
    expect(isTraceRunEventType('llm_call_completed')).toBe(true)
    expect(isTraceRunEventType('orchestrator_decision')).toBe(true)
    expect(isTraceRunEventType('tool_call_completed')).toBe(true)
    expect(isTraceRunEventType('run_completed')).toBe(false)
    expect(isTraceRunEventType(42)).toBe(false)
  })

  it('ignores non-trace run_event types', () => {
    apply('run_completed', {})
    expect(Object.keys(useTraceStore.getState().nodes)).toHaveLength(0)
  })

  it('folds llm_call_completed into an llm_call node', () => {
    apply('llm_call_completed', {
      model: 'gpt-4o',
      provider: 'openai',
      attempt: 1,
      outcome: 'completed',
      duration_ms: 812,
      usage: { input: 2100, output: 340 },
      finish_reason: 'stop',
    })
    const state = useTraceStore.getState()
    const node = Object.values(state.nodes)[0]
    expect(node).toMatchObject({
      kind: 'llm_call',
      runId: 'run-1',
      clientRequestId: 'crid-1',
      model: 'gpt-4o',
      provider: 'openai',
      attempt: 1,
      outcome: 'completed',
      durationMs: 812,
      usage: { input: 2100, output: 340 },
      finishReason: 'stop',
    })
    expect(state.runOrder['run-1']).toEqual([node.id])
  })

  it('folds retry events with redacted-safe fields', () => {
    apply('llm_retry_scheduled', {
      attempt: 2,
      error_class: 'rate_limit',
      retry_delay_ms: 1250,
      retryable: true,
    })
    const node = Object.values(useTraceStore.getState().nodes)[0]
    expect(node).toMatchObject({
      kind: 'retry',
      attempt: 2,
      errorClass: 'rate_limit',
      retryDelayMs: 1250,
    })
  })

  it('folds orchestrator decisions', () => {
    apply('orchestrator_decision', {
      chosen_agents: ['Weather Agent', 'Broker Agent'],
      plan_steps: [
        { agent: 'Weather Agent', summary: 'Fetch the forecast' },
        { agent: 'Broker Agent', summary: '' },
      ],
      reason: 'Two independent lookups.',
    })
    const node = Object.values(useTraceStore.getState().nodes)[0]
    expect(node).toMatchObject({
      kind: 'decision',
      chosenAgents: ['Weather Agent', 'Broker Agent'],
      planSteps: [
        { agent: 'Weather Agent', summary: 'Fetch the forecast' },
        { agent: 'Broker Agent', summary: '' },
      ],
      reason: 'Two independent lookups.',
    })
  })

  it('merges accepted and completed tool calls by tool name within a run', () => {
    apply('tool_call_accepted', {
      tool_name: 'weather_lookup',
      arg_summary: { city: 'Shanghai' },
    })
    apply('tool_call_completed', {
      tool_name: 'weather_lookup',
      result_summary: 'Sunny, 24C',
      exit_code: 0,
      duration_ms: 120,
    })
    const nodes = Object.values(useTraceStore.getState().nodes)
    expect(nodes).toHaveLength(1)
    expect(nodes[0]).toMatchObject({
      kind: 'tool_call',
      status: 'completed',
      toolName: 'weather_lookup',
      argSummary: { city: 'Shanghai' },
      resultSummary: 'Sunny, 24C',
      exitCode: 0,
      durationMs: 120,
    })
  })

  it('selects nodes for a client request in receive order', () => {
    apply('llm_call_completed', { model: 'gpt-4o' })
    apply('tool_call_accepted', { tool_name: 'weather_lookup' }, 'other-crid')
    const nodes = selectTraceNodesForClientRequest(
      useTraceStore.getState(),
      'crid-1',
    )
    expect(nodes.map((node) => node.kind)).toEqual(['llm_call'])
  })

  it('selects nodes for a run', () => {
    apply('llm_call_completed', { model: 'gpt-4o' })
    apply('orchestrator_decision', {
      plan_steps: [{ agent: 'Weather Agent', summary: 'Fetch' }],
    })
    const nodes = selectTraceNodesForRun(useTraceStore.getState(), 'run-1')
    expect(nodes.map((node) => node.kind)).toEqual(['llm_call', 'decision'])
    expect(selectTraceNodesForRun(useTraceStore.getState(), undefined)).toEqual([])
  })

  it('resets state on setRoom', () => {
    apply('llm_call_completed', { model: 'gpt-4o' })
    useTraceStore.getState().setRoom('room-2')
    const state = useTraceStore.getState()
    expect(state.roomId).toBe('room-2')
    expect(Object.keys(state.nodes)).toHaveLength(0)
  })

  it('records terminal run statuses', () => {
    useTraceStore.getState().setRunStatus('run-1', 'failed')
    expect(useTraceStore.getState().runStatuses['run-1']).toBe('failed')
  })

  it('hydrates trace trees and run statuses from a snapshot', () => {
    useTraceStore.getState().hydrateFromSnapshot('room-1', {
      trace: {
        'run-1': {
          run_id: 'run-1',
          client_request_id: 'crid-1',
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
            {
              id: 'run-1:tool_call:weather',
              kind: 'tool_call',
              tool_name: 'weather',
              status: 'completed',
              result_summary: 'Sunny',
              exit_code: 0,
              duration_ms: 100,
            },
          ],
          usage: { input: 10, output: 2 },
          duration_ms: 800,
        },
      },
      runs: [{ run_id: 'run-1', status: 'completed', client_request_id: 'crid-1' }],
    })

    const state = useTraceStore.getState()
    expect(state.runStatuses['run-1']).toBe('completed')
    expect(state.runOrder['run-1']).toEqual([
      'run-1:llm_call:e1',
      'run-1:tool_call:weather',
    ])
    expect(state.nodes['run-1:llm_call:e1']).toMatchObject({
      kind: 'llm_call',
      clientRequestId: 'crid-1',
      model: 'gpt-4o',
      durationMs: 800,
    })
    expect(state.nodes['run-1:tool_call:weather']).toMatchObject({
      kind: 'tool_call',
      clientRequestId: 'crid-1',
      toolName: 'weather',
      resultSummary: 'Sunny',
      exitCode: 0,
    })
  })

  it('ignores a delayed snapshot from the room being left', () => {
    useTraceStore.getState().setRoom('room-current')
    apply('llm_call_completed', { model: 'gpt-current' }, 'cr-current')
    const before = useTraceStore.getState().nodes

    useTraceStore.getState().hydrateFromSnapshot('room-stale', {
      trace: {},
      runs: [],
    })

    const state = useTraceStore.getState()
    expect(state.roomId).toBe('room-current')
    expect(state.nodes).toEqual(before)
  })
})
