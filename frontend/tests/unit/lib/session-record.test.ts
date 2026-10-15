import { describe, expect, it } from 'vitest'
import { foldSessionEvents, splitAgentLabel, type RoomEventRecord } from '@/lib/session-record'
import { formatSessionDuration, formatSessionTime } from '@/lib/session-format'

let seq = 0
function runEvent(type: string, runId: string, payload: Record<string, unknown>, ts = '2026-10-15T03:40:11Z'): RoomEventRecord {
  seq += 1
  return {
    room_seq: seq,
    kind: 'run_event',
    ts,
    payload_public: { type, run_id: runId, correlation_id: `cr-${runId}`, payload },
  }
}
function frame(kind: string, payload: Record<string, unknown>, ts = '2026-10-15T03:40:14Z'): RoomEventRecord {
  seq += 1
  return { room_seq: seq, kind, ts, payload_public: payload }
}

const AGENT = 'Travel Planner Agent - Trip planning and itineraries'

function approvedRun(runId = 'run-1'): RoomEventRecord[] {
  return [
    runEvent('run_started', runId, { user_message_id: 'msg-1', started_at: '2026-10-15T03:40:11Z', mode: 'passthrough' }),
    runEvent('tool_execution_start', runId, { tool_name: AGENT, execution_kind: 'agent', input: { task: 'Generate a travel plan' } }),
    frame('hitl_request', { request_id: 'q1', interaction_id: 'int-1', message_id: `orchestrator:${runId}:inv_1`, prompt: 'Which destination?', header: 'Destination' }),
    runEvent('run_waiting_input', runId, { interaction_id: 'int-1', request_ids: ['q1'] }),
    runEvent('tool_execution_start', runId, { tool_name: 'surface_agent_questions', execution_kind: 'tool', input: {} }),
    frame('hitl_response', { request_id: 'q1', interaction_id: 'int-1', run_id: runId, status: 'responded', answer_text: 'Kyoto' }, '2026-10-15T03:40:16Z'),
    runEvent('run_resumed', runId, { interaction_id: 'int-1' }),
    frame('agent_response', { message_id: 'final-1', agent_id: 'system:hybro', content: 'Day 1: Fushimi Inari' }, '2026-10-15T03:40:40Z'),
    runEvent('run_settled', runId, { status: 'completed', final_message_id: 'final-1', duration_ms: 28807, settled_at: '2026-10-15T03:40:40Z' }, '2026-10-15T03:40:40Z'),
  ]
}

describe('foldSessionEvents', () => {
  it('builds one exchange per run with its agent, approval, decision, and reply', () => {
    const [exchange] = foldSessionEvents(approvedRun())

    expect(exchange).toMatchObject({
      runId: 'run-1',
      userMessageId: 'msg-1',
      agentName: 'Travel Planner Agent',
      skill: 'Trip planning and itineraries',
      task: 'Generate a travel plan',
      status: 'completed',
      reply: 'Day 1: Fushimi Inari',
      durationMs: 28807,
    })
    expect(exchange.steps.map(step => step.kind)).toEqual(['sent', 'asked', 'answered', 'replied'])
    expect(exchange.steps[1]).toMatchObject({ kind: 'asked', items: [{ label: 'Destination', text: 'Which destination?' }] })
    expect(exchange.steps[2]).toMatchObject({ kind: 'answered', items: [{ label: 'Destination', text: 'Kyoto' }] })
  })

  it('collects every question and answer of one interaction into one step each', () => {
    const runId = 'run-q'
    const events = [
      runEvent('run_started', runId, { user_message_id: 'msg-q' }),
      runEvent('tool_execution_start', runId, { tool_name: AGENT, execution_kind: 'agent', input: { task: 'Plan a trip' } }),
      frame('hitl_request', { request_id: 'dest', interaction_id: 'int-q', message_id: `orchestrator:${runId}:inv_1`, prompt: 'Where?', header: 'Destination' }),
      frame('hitl_request', { request_id: 'days', interaction_id: 'int-q', message_id: `orchestrator:${runId}:inv_1`, prompt: 'How long?' }),
      frame('hitl_response', { request_id: 'dest', interaction_id: 'int-q', run_id: runId, status: 'responded', answer_text: 'Lisbon' }),
      frame('hitl_response', { request_id: 'days', interaction_id: 'int-q', run_id: runId, status: 'responded' }),
    ]
    const [exchange] = foldSessionEvents(events)
    expect(exchange.steps.map(step => step.kind)).toEqual(['sent', 'asked', 'answered'])
    expect(exchange.steps[1].items).toEqual([
      { label: 'Destination', text: 'Where?' },
      { text: 'How long?' },
    ])
    // A skipped question has no answer text.
    expect(exchange.steps[2].items).toEqual([{ label: 'Destination', text: 'Lisbon' }])
  })

  it('ignores the internal surface tool and lifecycle noise', () => {
    const [exchange] = foldSessionEvents(approvedRun())
    expect(exchange.steps.filter(step => step.kind === 'sent')).toHaveLength(1)
  })

  it('reports an unanswered approval as waiting', () => {
    const events = approvedRun('run-2').slice(0, 4)
    const [exchange] = foldSessionEvents(events)
    expect(exchange.status).toBe('waiting')
    expect(exchange.reply).toBeNull()
  })

  it('reports work after an answer as working', () => {
    const events = approvedRun('run-3').slice(0, 7)
    expect(foldSessionEvents(events)[0].status).toBe('working')
  })

  it('maps an owner reject before a canceled settlement to rejected', () => {
    const runId = 'run-4'
    const events = [
      ...approvedRun(runId).slice(0, 4),
      frame('hitl_response', { request_id: 'q1', interaction_id: 'int-1', run_id: runId, status: 'canceled' }),
      runEvent('run_settled', runId, { status: 'canceled', cancellation_code: 'user_requested' }),
    ]
    const [exchange] = foldSessionEvents(events)
    expect(exchange.status).toBe('rejected')
    expect(exchange.steps.map(step => step.kind)).toEqual(['sent', 'asked', 'rejected'])
  })

  it('carries the public failure summary', () => {
    const runId = 'run-5'
    const events = [
      runEvent('run_started', runId, { user_message_id: 'msg-5' }),
      runEvent('run_settled', runId, { status: 'failed', error_summary: 'The run could not be completed.' }),
    ]
    const [exchange] = foldSessionEvents(events)
    expect(exchange.status).toBe('failed')
    expect(exchange.failure).toBe('The run could not be completed.')
    expect(exchange.steps.at(-1)).toMatchObject({ kind: 'failed' })
  })

  it('keeps requests in order and ignores unrelated frames', () => {
    const events = [
      ...approvedRun('run-a'),
      frame('processing_status', { status: 'processing' }),
      ...approvedRun('run-b'),
    ]
    expect(foldSessionEvents(events).map(exchange => exchange.runId)).toEqual(['run-a', 'run-b'])
  })
})

describe('splitAgentLabel', () => {
  it('splits the agent and skill', () => {
    expect(splitAgentLabel(AGENT)).toEqual({ agentName: 'Travel Planner Agent', skill: 'Trip planning and itineraries' })
    expect(splitAgentLabel('Weather Agent')).toEqual({ agentName: 'Weather Agent', skill: null })
  })
})

describe('session formatting', () => {
  it('formats times in English regardless of the browser locale', () => {
    expect(formatSessionTime('2026-10-14T18:04:58Z', 'UTC')).toBe('Oct 14, 6:04 PM')
    expect(formatSessionTime('not a date', 'UTC')).toBe('')
    expect(formatSessionTime(null, 'UTC')).toBe('')
  })

  it('formats durations compactly', () => {
    expect(formatSessionDuration(28807)).toBe('29s')
    expect(formatSessionDuration(130000)).toBe('2m 10s')
    expect(formatSessionDuration(null)).toBe('')
  })
})
