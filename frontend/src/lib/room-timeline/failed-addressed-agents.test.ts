import { describe, expect, it } from 'vitest'
import type { TurnActivityItem, TurnProjection } from '@/lib/turn-lifecycle/types'
import { addressedAgentNames, failedAddressedAgents } from './failed-addressed-agents'

function call(targetName: string, status: 'completed' | 'failed', extra: Partial<TurnActivityItem> = {}): TurnActivityItem {
  return {
    kind: 'tool', id: `${targetName}-${status}`, internalTurnId: 'turn-1', toolCallId: `${targetName}-${status}`,
    label: targetName, input: {}, partialResult: '', updateIndex: 0, status,
    executionKind: 'agent', targetName, requestSummary: '', detailAvailable: false, order: 1,
    ...extra,
  } as TurnActivityItem
}

function turn(activity: TurnActivityItem[]): TurnProjection {
  return {
    id: 'run-1', runId: 'run-1', conversationId: 'room-1', userMessageId: 'user-1',
    clientRequestId: 'client-1', state: 'completed', startedAt: '2030-01-01T00:00:00Z',
    internalTurns: [], activity, finalCommitted: true, hitlInteractions: [], agentCallMessageIds: [],
  }
}

describe('failedAddressedAgents', () => {
  it('parses each mentioned agent once', () => {
    expect(addressedAgentNames('<@a1|Writer> and <@a2|Research> then <@a1|Writer>'))
      .toEqual(['Writer', 'Research'])
  })

  it('reports a mentioned agent only when every one of its calls failed', () => {
    const userText = '<@a1|Writer> <@a2|Research> <@a3|Idle>'
    const result = failedAddressedAgents(turn([
      call('Writer', 'failed', { errorCode: 'llm_quota_exceeded', errorMessage: 'Quota exceeded' }),
      call('Research', 'failed'),
      call('Research', 'completed'),
      call('Other', 'failed'),
    ]), userText)

    expect(result).toEqual([{ name: 'Writer', reason: 'Quota exceeded' }])
  })

  it('falls back to the error code as the reason', () => {
    expect(failedAddressedAgents(
      turn([call('Writer', 'failed', { errorCode: 'llm_quota_exceeded' })]),
      '<@a1|Writer>',
    )).toEqual([{ name: 'Writer', reason: 'llm_quota_exceeded' }])
  })
})
