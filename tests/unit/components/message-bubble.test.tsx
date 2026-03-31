import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { EntityUserBubble, EntityAgentBubble, derivePhase } from '@/components/message-bubble'
import type { MessageEntity } from '@/stores/message-store'

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

function renderWithQueryClient(ui: React.ReactElement) {
  const queryClient = new QueryClient()
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>
  )
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
      messageType: 'agent',
      senderName: 'Coding Agent',
      agentId: 'agent-1',
      content: 'Here is your code.',
    })
    renderWithQueryClient(<EntityAgentBubble entity={entity} />)

    expect(screen.getByText('Coding Agent')).toBeTruthy()
  })

  it('should render agent initials in avatar', () => {
    const entity = makeEntity({
      messageType: 'agent',
      senderName: 'Coding Agent',
      agentId: 'agent-1',
      content: 'Response',
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)
    const avatar = container.querySelector('.rounded-full')
    expect(avatar).toBeTruthy()
  })

  it('should show "Show more" button for long messages', () => {
    const longContent = 'A'.repeat(600)
    const entity = makeEntity({
      messageType: 'agent',
      agentId: 'agent-1',
      content: longContent,
    })
    renderWithQueryClient(<EntityAgentBubble entity={entity} />)

    expect(screen.getByText(/Show more/)).toBeTruthy()
  })

  it('should not show expand button for very short messages', () => {
    const entity = makeEntity({
      messageType: 'agent',
      agentId: 'agent-1',
      content: 'Hi',
      source: 'db',
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)

    const expandBtn = container.querySelector('button')
    if (expandBtn) {
      expect(expandBtn.textContent).not.toContain('Show more')
    }
  })

  it('should link agent name to agent profile', () => {
    const entity = makeEntity({
      messageType: 'agent',
      senderName: 'Coding Agent',
      agentId: 'agent-1',
      content: 'Hi',
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)
    const link = container.querySelector('a[href="/c/agents/agent-1"]')
    expect(link).toBeTruthy()
  })

})

// ── derivePhase ─────────────────────────────────────────────────

describe('derivePhase', () => {
  it('returns "waiting" for entity with no content, no artifacts, no taskStatus', () => {
    const entity = makeEntity({ messageType: 'agent', content: '', taskStatus: undefined })
    expect(derivePhase(entity)).toBe('waiting')
  })

  it('returns "waiting" for entity with "working" taskStatus but no content', () => {
    const entity = makeEntity({ messageType: 'agent', content: '', taskStatus: 'working' })
    expect(derivePhase(entity)).toBe('waiting')
  })

  it('returns "waiting" for entity with "unknown" taskStatus', () => {
    const entity = makeEntity({ messageType: 'agent', content: '', taskStatus: 'unknown' as any })
    expect(derivePhase(entity)).toBe('waiting')
  })

  it('returns "streaming" for entity with streaming artifacts', () => {
    const entity = makeEntity({
      messageType: 'agent', content: '',
      artifacts: [{ artifactId: 'a1', parts: [{ kind: 'text', text: 'hi' }], isStreaming: true }],
    })
    expect(derivePhase(entity)).toBe('streaming')
  })

  it('returns "streaming" for working task with accumulated content (post-refresh)', () => {
    const entity = makeEntity({ messageType: 'agent', content: 'Partial result', taskStatus: 'working' })
    expect(derivePhase(entity)).toBe('streaming')
  })

  it('returns "streaming" for submitted task with content', () => {
    const entity = makeEntity({ messageType: 'agent', content: 'Some output', taskStatus: 'submitted' })
    expect(derivePhase(entity)).toBe('streaming')
  })

  it('returns "streaming" for working task with non-text artifacts', () => {
    const entity = makeEntity({
      messageType: 'agent', content: '', taskStatus: 'working',
      artifacts: [{ artifactId: 'a1', parts: [{ kind: 'file', file: { uri: 'https://example.com/img.png', mime_type: 'image/png' } }] }],
    })
    expect(derivePhase(entity)).toBe('streaming')
  })

  it('returns "streaming" for entity with streaming artifacts and no taskStatus', () => {
    const entity = makeEntity({
      messageType: 'agent', content: '',
      artifacts: [{ artifactId: 'a1', parts: [{ kind: 'text', text: 'hi' }], isStreaming: true }],
    })
    expect(derivePhase(entity)).toBe('streaming')
  })

  it('returns "complete" for entity with completed taskStatus and content', () => {
    const entity = makeEntity({ messageType: 'agent', content: 'Result', taskStatus: 'completed' })
    expect(derivePhase(entity)).toBe('complete')
  })

  it('returns "complete" for entity with content but no taskStatus', () => {
    const entity = makeEntity({ messageType: 'agent', content: 'Result', taskStatus: undefined })
    expect(derivePhase(entity)).toBe('complete')
  })

  it('returns "complete-empty" for completed task with no content and no artifacts', () => {
    const entity = makeEntity({ messageType: 'agent', content: '', taskStatus: 'completed' })
    expect(derivePhase(entity)).toBe('complete-empty')
  })

  it('returns "interactive" for input-required task', () => {
    const entity = makeEntity({ messageType: 'agent', content: '', taskStatus: 'input-required' })
    expect(derivePhase(entity)).toBe('interactive')
  })

  it('returns "interactive" for auth-required task', () => {
    const entity = makeEntity({ messageType: 'agent', content: '', taskStatus: 'auth-required' })
    expect(derivePhase(entity)).toBe('interactive')
  })

  it('returns "interactive" for resolved HITL (preserves display)', () => {
    const entity = makeEntity({
      messageType: 'agent', content: '', taskStatus: 'working',
      hitlResolved: true, hitlUserAnswer: 'Yes',
    })
    expect(derivePhase(entity)).toBe('interactive')
  })

  it('returns "failed" for failed task', () => {
    const entity = makeEntity({ messageType: 'agent', content: '', taskStatus: 'failed' })
    expect(derivePhase(entity)).toBe('failed')
  })

  it('returns "failed" for canceled task', () => {
    const entity = makeEntity({ messageType: 'agent', content: '', taskStatus: 'canceled' })
    expect(derivePhase(entity)).toBe('failed')
  })

  it('returns "failed" for rejected task', () => {
    const entity = makeEntity({ messageType: 'agent', content: '', taskStatus: 'rejected' })
    expect(derivePhase(entity)).toBe('failed')
  })

  it('failure takes precedence over resolved HITL (A2A-6)', () => {
    const entity = makeEntity({
      messageType: 'agent', content: '', taskStatus: 'failed',
      hitlResolved: true, hitlUserAnswer: 'Yes',
    })
    expect(derivePhase(entity)).toBe('failed')
  })
})

// ── Phase rendering ─────────────────────────────────────────────

describe('EntityAgentBubble phase rendering', () => {
  it('renders red styling for failed phase', () => {
    const entity = makeEntity({
      messageType: 'agent', agentId: 'a1', senderName: 'Bot',
      content: '', taskStatus: 'failed', taskError: 'Something went wrong',
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)
    expect(container.querySelector('.border-red-200')).toBeTruthy()
  })

  it('renders amber prompt for active interactive phase', () => {
    const entity = makeEntity({
      messageType: 'agent', agentId: 'a1', senderName: 'Bot',
      content: '', taskStatus: 'input-required',
      hitlPrompt: 'What is your name?', hitlResolved: false,
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)
    expect(container.querySelector('.border-amber-200')).toBeTruthy()
  })

  it('renders prompt + answer for resolved HITL', () => {
    const entity = makeEntity({
      messageType: 'agent', agentId: 'a1', senderName: 'Bot',
      content: '', taskStatus: 'working',
      hitlPrompt: 'What is your name?', hitlResolved: true, hitlUserAnswer: 'Alice',
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)
    expect(container.textContent).toContain('Your answer:')
    expect(container.textContent).toContain('Alice')
  })

  it('renders emerald badge for complete-empty phase', () => {
    const entity = makeEntity({
      messageType: 'agent', agentId: 'a1', senderName: 'Bot',
      content: '', taskStatus: 'completed',
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)
    expect(container.querySelector('.border-emerald-200')).toBeTruthy()
    expect(screen.getAllByText('Completed').length).toBeGreaterThanOrEqual(1)
  })
})
