import { describe, it, expect } from 'vitest'
import { filterHydrationMessages } from '../hydration-filter'
import type { IncomingMessage } from '../types'

function makeIncoming(overrides: Partial<IncomingMessage> = {}): IncomingMessage {
  return {
    id: 'msg-1',
    roomId: 'room-1',
    messageType: 'agent',
    content: '',
    senderName: 'Agent',
    timestamp: '2026-02-17T10:00:00Z',
    ...overrides,
  }
}

describe('filterHydrationMessages', () => {
  it('keeps user messages regardless of content', () => {
    const messages = [
      makeIncoming({ id: 'user-1', messageType: 'user', content: '' }),
      makeIncoming({ id: 'user-2', messageType: 'user', content: 'Hello' }),
    ]
    const result = filterHydrationMessages(messages)
    expect(result).toHaveLength(2)
  })

  it('keeps agent messages with content', () => {
    const messages = [
      makeIncoming({ id: 'agent-1', content: 'Some response' }),
    ]
    const result = filterHydrationMessages(messages)
    expect(result).toHaveLength(1)
  })

  it('keeps agent messages with taskStatus (even without content)', () => {
    const messages = [
      makeIncoming({ id: 'task-1', content: '', taskStatus: 'working' }),
    ]
    const result = filterHydrationMessages(messages)
    expect(result).toHaveLength(1)
  })

  it('filters out agent messages with no content and no taskStatus', () => {
    const messages = [
      makeIncoming({ id: 'empty-1', content: '' }),
      makeIncoming({ id: 'empty-2', content: '   ' }),
    ]
    const result = filterHydrationMessages(messages)
    expect(result).toHaveLength(0)
  })

  it('handles mixed messages correctly', () => {
    const messages = [
      makeIncoming({ id: 'user', messageType: 'user', content: 'Hi' }),
      makeIncoming({ id: 'agent-good', content: 'Response' }),
      makeIncoming({ id: 'agent-empty', content: '' }),
      makeIncoming({ id: 'task', content: '', taskStatus: 'working' }),
    ]
    const result = filterHydrationMessages(messages)
    expect(result.map(m => m.id)).toEqual(['user', 'agent-good', 'task'])
  })
})
