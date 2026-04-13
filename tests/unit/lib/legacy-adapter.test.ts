import { describe, it, expect } from 'vitest'
import { convertLegacyMessagesToTurnEvents } from '@/lib/turn-event-store/legacy-adapter'

describe('convertLegacyMessagesToTurnEvents', () => {
  it('groups agent messages by related_message_id into turns', () => {
    const apiMessages = [
      {
        message_id: 'user_msg_1',
        message_type: 'user' as const,
        message_content: { message_text: 'Hello agents' },
        created_at: '2026-04-11T10:00:00Z',
      },
      {
        message_id: 'agent_msg_1',
        message_type: 'agent' as const,
        agent_id: 'agent_a',
        related_message_id: 'user_msg_1',
        message_content: { message_text: 'Response from A' },
        created_at: '2026-04-11T10:00:01Z',
      },
      {
        message_id: 'agent_msg_2',
        message_type: 'agent' as const,
        agent_id: 'agent_b',
        related_message_id: 'user_msg_1',
        message_content: { message_text: 'Response from B' },
        created_at: '2026-04-11T10:00:02Z',
      },
    ]

    const result = convertLegacyMessagesToTurnEvents(apiMessages)

    expect(result).toHaveLength(1)
    const turn = result[0]
    expect(turn.turnId).toBe('user_msg_1')
    expect(turn.events.length).toBeGreaterThanOrEqual(5)

    const types = turn.events.map(e => e.type)
    expect(types[0]).toBe('turn_started')
    expect(types[types.length - 1]).toBe('turn_completed')
    expect(types.filter(t => t === 'slot_opened')).toHaveLength(2)
  })

  it('handles empty message list', () => {
    const result = convertLegacyMessagesToTurnEvents([])
    expect(result).toHaveLength(0)
  })

  it('uses message_id as turn_id for user messages', () => {
    const apiMessages = [
      {
        message_id: 'user_42',
        message_type: 'user' as const,
        message_content: { message_text: 'Hi' },
        created_at: '2026-04-11T10:00:00Z',
      },
      {
        message_id: 'agent_99',
        message_type: 'agent' as const,
        agent_id: 'a1',
        related_message_id: 'user_42',
        message_content: { message_text: 'Reply' },
        created_at: '2026-04-11T10:00:01Z',
      },
    ]

    const result = convertLegacyMessagesToTurnEvents(apiMessages)
    expect(result[0].turnId).toBe('user_42')
  })
})
