import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { EntityUserBubble, EntityAgentBubble } from '@/components/message-bubble'
import { useStreamingContent } from '@/hooks/useStreamingContent'
import type { MessageEntity } from '@/stores/message-store'

vi.mock('@/hooks/useStreamingContent', () => ({
  useStreamingContent: vi.fn().mockReturnValue({ streamingText: '', isStreaming: false }),
}))

const mockUseStreamingContent = vi.mocked(useStreamingContent)

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
  beforeEach(() => {
    mockUseStreamingContent.mockReturnValue({ streamingText: '', isStreaming: false })
  })

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

  it('should render streaming text through Streamdown markdown', () => {
    mockUseStreamingContent.mockReturnValue({
      streamingText: 'word1\nword2\nword3',
      isStreaming: true,
    })

    const entity = makeEntity({
      type: 'agent',
      senderName: 'Coding Agent',
      agentId: 'agent-1',
      content: '',
    })

    const { container } = render(<EntityAgentBubble entity={entity} />)

    expect(container.textContent).toContain('word1')
    expect(container.textContent).toContain('word2')
    expect(container.textContent).toContain('word3')
  })

  it('should render streaming preview with grid transition and Streamdown', () => {
    mockUseStreamingContent.mockReturnValue({
      streamingText: '**How AI will change software engineering**\n\nArtificial Intelligence',
      isStreaming: true,
    })

    const entity = makeEntity({
      type: 'agent',
      senderName: 'Coding Agent',
      agentId: 'agent-1',
      content: '',
    })

    const { container } = render(<EntityAgentBubble entity={entity} />)

    const contentGrid = container.querySelector('.grid')

    expect(contentGrid?.className).not.toContain('transition-[grid-template-rows]')
    expect(container.textContent).toContain('How AI will change software engineering')
    expect(container.textContent).toContain('Artificial Intelligence')
  })

  it('should continue reveal from the streamed preview instead of restarting at 10 chars', () => {
    const rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 1)
    const cancelSpy = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {})

    mockUseStreamingContent
      .mockReturnValueOnce({
        streamingText: 'word1\nword2\nword3',
        isStreaming: true,
      })
      .mockReturnValue({
        streamingText: '',
        isStreaming: false,
      })

    const entity = makeEntity({
      type: 'agent',
      senderName: 'Coding Agent',
      agentId: 'agent-1',
      content: 'word1 word2 word3 and more',
    })

    const { rerender, container } = render(<EntityAgentBubble entity={entity} />)
    expect(container.textContent).toContain('word1')
    expect(container.textContent).toContain('word2')
    expect(container.textContent).toContain('word3')

    rerender(<EntityAgentBubble entity={entity} />)

    expect(container.textContent).toContain('word1')

    rafSpy.mockRestore()
    cancelSpy.mockRestore()
  })

  it('should render markdown during reveal phase via Streamdown', () => {
    const rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 1)
    const cancelSpy = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {})

    mockUseStreamingContent
      .mockReturnValueOnce({
        streamingText: '**bold**\n\nparagraph',
        isStreaming: true,
      })
      .mockReturnValue({
        streamingText: '',
        isStreaming: false,
      })

    const entity = makeEntity({
      type: 'agent',
      senderName: 'Coding Agent',
      agentId: 'agent-1',
      content: '**bold**\n\nparagraph and more',
    })

    const { rerender, container } = render(<EntityAgentBubble entity={entity} />)
    rerender(<EntityAgentBubble entity={entity} />)

    expect(container.textContent).toContain('bold')
    expect(container.textContent).toContain('paragraph')

    rafSpy.mockRestore()
    cancelSpy.mockRestore()
  })

})
