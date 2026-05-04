import { describe, expect, it } from 'vitest'
import { render } from '../../../utils/test-utils'
import { UserMessageBlock } from '@/components/conversation/UserMessageBlock'
import type { MessageEntity } from '@/stores/message-store/types'

function makeUserMessage(content = 'Hello'): MessageEntity {
  return {
    id: 'msg-1',
    roomId: 'room-1',
    messageType: 'user',
    content,
    senderName: 'User',
    timestamp: '2026-04-30T00:00:00.000Z',
    source: 'db',
    sourceVersion: 1,
    displayType: 'user-bubble',
    isEphemeral: false,
    createdAt: 0,
    updatedAt: 0,
  }
}

describe('UserMessageBlock', () => {
  it('uses conversation density classes for the user anchor', () => {
    const { container } = render(<UserMessageBlock entity={makeUserMessage('Hello')} />)

    expect(container.querySelector('.conversation-user-message')).toBeTruthy()
    expect(container.querySelector('.conversation-user-message-inner')).toBeTruthy()
    expect(container.querySelector('.conversation-user-message-text')).toBeTruthy()
  })
})
