import { describe, expect, it } from 'vitest'
import { buildPendingHitlIncomingMessage } from '@/lib/hitl/hitl-message-projection'
import { applyUpsert } from '@/stores/message-store/upsert'

function supervisorHitl() {
  return buildPendingHitlIncomingMessage({
    roomId: 'room-1',
    messageId: 'message-1',
    requestId: 'request-1',
    source: 'supervisor',
    prompt: 'Choose a market',
    promptType: 'choice',
    choices: ['A', 'B'],
    timestamp: '2026-08-17T00:00:00Z',
    agentId: null,
    agentName: null,
    agentSource: undefined,
    expiresAt: null,
    groupId: 'group-1',
    groupTotal: 1,
    groupIndex: 0,
    stepNumber: null,
    totalSteps: null,
    relatedMessageId: 'user-message-1',
    clientRequestId: 'client-1',
  })
}

describe('message store HITL source projection', () => {
  it('preserves supervisor ownership when hydrating a new entity', () => {
    const result = applyUpsert({}, [], supervisorHitl(), 'db')

    expect(result?.entities['message-1'].hitlSource).toBe('supervisor')
  })

  it('updates source ownership on an existing entity', () => {
    const initial = applyUpsert({}, [], {
      ...supervisorHitl(),
      hitlSource: 'agent',
    }, 'sse')
    expect(initial).not.toBeNull()

    const updated = applyUpsert(
      initial!.entities,
      ['message-1'],
      supervisorHitl(),
      'sse',
    )

    expect(updated?.entities['message-1'].hitlSource).toBe('supervisor')
  })
})
