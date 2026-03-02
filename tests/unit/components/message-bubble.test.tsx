import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { EntityUserBubble, EntityAgentBubble } from '@/components/message-bubble'
import type { MessageEntity } from '@/stores/message-store'

vi.mock('@/hooks/useStreamingContent', () => ({
  useStreamingContent: vi.fn().mockReturnValue({ streamingText: '', isStreaming: false }),
}))

function makeEntity(overrides: Partial<MessageEntity> = {}): MessageEntity {
  return {
    id: 'msg-1',
    content: 'Hello, world!',
    senderName: 'Test User',
    timestamp: new Date().toISOString(),
    messageType: 'user',
    source: 'db',
    sourceVersion: 1,
    displayType: 'user-bubble',
    isEphemeral: false,
    createdAt: Date.now(),
    updatedAt: Date.now(),
    agentId: undefined,
    taskStatus: undefined,
    roomId: 'room-1',
    ...overrides,
  }
}

describe('EntityUserBubble', () => {
  it('should render user message content', () => {
    const entity = makeEntity({ content: 'Hello from user', senderName: 'Alice' })
    render(<EntityUserBubble entity={entity} />)

    expect(screen.getByText('Hello from user')).toBeTruthy()
    expect(screen.getByText('Alice')).toBeTruthy()
  })

  it('should display "No message content" when content is empty', () => {
    const entity = makeEntity({ content: '' })
    render(<EntityUserBubble entity={entity} />)

    expect(screen.getByText('No message content')).toBeTruthy()
  })

  it('should render with message-bubble class', () => {
    const entity = makeEntity()
    const { container } = render(<EntityUserBubble entity={entity} />)
    expect(container.querySelector('.message-bubble')).toBeTruthy()
  })
})

describe('EntityAgentBubble', () => {
  it('should render agent message content', () => {
    const entity = makeEntity({
      type: 'agent',
      senderName: 'Coding Agent',
      agentId: 'agent-1',
      content: 'Here is your code.',
    })
    render(<EntityAgentBubble entity={entity} />)

    expect(screen.getByText('Coding Agent')).toBeTruthy()
  })

  it('should render agent initials in avatar', () => {
    const entity = makeEntity({
      type: 'agent',
      senderName: 'Coding Agent',
      agentId: 'agent-1',
      content: 'Response',
    })
    const { container } = render(<EntityAgentBubble entity={entity} />)
    const avatar = container.querySelector('.rounded-full')
    expect(avatar).toBeTruthy()
  })

  it('should show "Show more" button for long messages', () => {
    const longContent = 'A'.repeat(600)
    const entity = makeEntity({
      type: 'agent',
      agentId: 'agent-1',
      content: longContent,
    })
    render(<EntityAgentBubble entity={entity} />)

    expect(screen.getByText('Show more')).toBeTruthy()
  })

  it('should not show expand button for very short messages', () => {
    const entity = makeEntity({
      type: 'agent',
      agentId: 'agent-1',
      content: 'Hi',
      source: 'db',
    })
    const { container } = render(<EntityAgentBubble entity={entity} />)

    const expandBtn = container.querySelector('button')
    if (expandBtn) {
      expect(expandBtn.textContent).not.toContain('Show more')
    }
  })

  it('should link agent name to agent profile', () => {
    const entity = makeEntity({
      type: 'agent',
      senderName: 'Coding Agent',
      agentId: 'agent-1',
      content: 'Hi',
    })
    const { container } = render(<EntityAgentBubble entity={entity} />)
    const link = container.querySelector('a[href="/c/agents/agent-1"]')
    expect(link).toBeTruthy()
  })
})
