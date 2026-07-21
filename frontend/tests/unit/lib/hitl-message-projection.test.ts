import { describe, expect, it } from 'vitest'
import { buildPendingHitlIncomingMessage } from '@/lib/hitl/hitl-message-projection'

describe('buildPendingHitlIncomingMessage', () => {
  it('maps durable HITL payload fields to one agent message projection', () => {
    const incoming = buildPendingHitlIncomingMessage({
      roomId: 'room-1',
      messageId: 'agent-msg-1',
      requestId: 'hitl-1',
      source: 'agent',
      prompt: 'Need revenue',
      promptType: 'text',
      choices: null,
      timestamp: '2026-07-02T00:00:00.000Z',
      agentId: 'agent-1',
      agentName: 'Broker',
      agentSource: 'cloud',
      expiresAt: '2026-07-03T00:00:00.000Z',
      groupId: 'group-1',
      groupTotal: 2,
      groupIndex: 0,
      stepNumber: 1,
      totalSteps: 3,
      relatedMessageId: 'user-msg-1',
      clientRequestId: 'cr-1',
    })

    expect(incoming).toMatchObject({
      id: 'agent-msg-1',
      roomId: 'room-1',
      messageType: 'agent',
      content: 'Need revenue',
      senderName: 'Broker',
      agentId: 'agent-1',
      agentSource: 'cloud',
      taskStatus: 'input-required',
      hitlRequestId: 'hitl-1',
      hitlSource: 'agent',
      hitlPrompt: 'Need revenue',
      hitlPromptType: 'text',
      hitlChoices: null,
      hitlExpiresAt: '2026-07-03T00:00:00.000Z',
      hitlResolved: false,
      hitlUserAnswer: '',
      hitlGroupId: 'group-1',
      hitlGroupTotal: 2,
      hitlGroupIndex: 0,
      stepNumber: 1,
      totalSteps: 3,
      relatedMessageId: 'user-msg-1',
      clientRequestId: 'cr-1',
    })
  })

  it('uses stable defaults for missing optional fields', () => {
    const incoming = buildPendingHitlIncomingMessage({
      roomId: 'room-1',
      messageId: 'agent-msg-1',
      requestId: 'hitl-1',
      prompt: '',
      promptType: undefined,
      choices: undefined,
      timestamp: undefined,
      agentId: undefined,
      agentName: undefined,
      agentSource: undefined,
      expiresAt: undefined,
      groupId: undefined,
      groupTotal: undefined,
      groupIndex: undefined,
      stepNumber: undefined,
      totalSteps: undefined,
      relatedMessageId: undefined,
      clientRequestId: undefined,
    })

    expect(incoming.senderName).toBe('Agent')
    expect(incoming.content).toBe('')
    expect(incoming.hitlPromptType).toBe('text')
    expect(incoming.hitlChoices).toBeNull()
    expect(incoming.hitlGroupId).toBeNull()
    expect(incoming.hitlGroupTotal).toBeNull()
    expect(incoming.hitlGroupIndex).toBeNull()
    expect(incoming.hitlResolved).toBe(false)
    expect(incoming.taskStatus).toBe('input-required')
  })
})
