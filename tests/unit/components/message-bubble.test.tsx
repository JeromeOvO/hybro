import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, act, within } from '@testing-library/react'
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

  it('shows task spinner beside hub/cloud when task is in progress', () => {
    const entity = makeEntity({
      messageType: 'agent',
      senderName: 'Coding Agent',
      agentId: 'agent-1',
      content: 'Partial',
      agentSource: 'hub',
      taskStatus: 'working',
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)
    expect(within(container).getByLabelText('Task in progress')).toBeTruthy()
  })

  it('hides task spinner beside hub/cloud when task is terminal', () => {
    const entity = makeEntity({
      messageType: 'agent',
      senderName: 'Coding Agent',
      agentId: 'agent-1',
      content: 'Done',
      agentSource: 'cloud',
      taskStatus: 'completed',
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)
    expect(within(container).queryByLabelText('Task in progress')).toBeNull()
  })

  it('hides task spinner during interactive task state', () => {
    const entity = makeEntity({
      messageType: 'agent',
      senderName: 'Coding Agent',
      agentId: 'agent-1',
      content: 'Need input',
      agentSource: 'hub',
      taskStatus: 'input-required',
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)
    expect(within(container).queryByLabelText('Task in progress')).toBeNull()
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

// ── JSON content rendering ──────────────────────────────────────

describe('EntityAgentBubble JSON content rendering', () => {
  it('renders JSON entity.content as CollapsibleJsonBlock, not plain text', () => {
    const jsonContent = JSON.stringify({ foo: 'bar', count: 42 })
    const entity = makeEntity({
      messageType: 'agent', agentId: 'a1', senderName: 'Bot',
      content: jsonContent,
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)

    // Should render a collapsible trigger, not raw JSON text
    const trigger = container.querySelector('[data-slot="collapsible-trigger"]')
    expect(trigger).toBeTruthy()
    expect(trigger!.textContent).toContain('JSON')
  })

  it('renders JSON object entity.content as CollapsibleJsonBlock for completed task', () => {
    const jsonContent = JSON.stringify({ status: 'ok', data: [1, 2, 3] })
    const entity = makeEntity({
      messageType: 'agent', agentId: 'a1', senderName: 'Bot',
      content: jsonContent, taskStatus: 'completed',
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)

    const trigger = container.querySelector('[data-slot="collapsible-trigger"]')
    expect(trigger).toBeTruthy()
    expect(trigger!.textContent).toContain('JSON')
  })

  it('renders JSON array entity.content as CollapsibleJsonBlock', () => {
    const jsonContent = JSON.stringify([{ id: 1 }, { id: 2 }])
    const entity = makeEntity({
      messageType: 'agent', agentId: 'a1', senderName: 'Bot',
      content: jsonContent,
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)

    const trigger = container.querySelector('[data-slot="collapsible-trigger"]')
    expect(trigger).toBeTruthy()
    expect(trigger!.textContent).toContain('JSON')
  })

  it('CollapsibleJsonBlock trigger click toggles open state', () => {
    const jsonContent = JSON.stringify({ foo: 'bar', count: 42 })
    const entity = makeEntity({
      messageType: 'agent', agentId: 'a1', senderName: 'Bot',
      content: jsonContent,
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)

    const trigger = container.querySelector('[data-slot="collapsible-trigger"]') as HTMLElement
    expect(trigger).toBeTruthy()

    // Initially closed
    const collapsible = container.querySelector('[data-slot="collapsible"]')
    expect(collapsible?.getAttribute('data-state')).toBe('closed')

    // Click to open
    fireEvent.click(trigger)
    expect(collapsible?.getAttribute('data-state')).toBe('open')

    // Click to close again
    fireEvent.click(trigger)
    expect(collapsible?.getAttribute('data-state')).toBe('closed')
  })

  it('renders JSON code block within markdown message as CollapsibleJsonBlock', () => {
    const content = [
      "Here's your data:",
      '```json',
      '{"foo": "bar", "count": 42}',
      '```',
      'Hope that helps!',
    ].join('\n')
    const entity = makeEntity({
      messageType: 'agent', agentId: 'a1', senderName: 'Bot',
      content,
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)

    // The JSON fenced code block should render as a collapsible, not a plain code block
    const trigger = container.querySelector('[data-slot="collapsible-trigger"]')
    expect(trigger).toBeTruthy()
    expect(trigger!.textContent).toContain('JSON')
  })

  it('does NOT render non-JSON fenced code blocks (e.g. python) as collapsible', () => {
    const content = [
      'Here is some Python:',
      '```python',
      'print("hello")',
      '```',
    ].join('\n')
    const entity = makeEntity({
      messageType: 'agent', agentId: 'a1', senderName: 'Bot',
      content,
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)

    const trigger = container.querySelector('[data-slot="collapsible-trigger"]')
    expect(trigger).toBeNull()
  })

  it('does NOT render CollapsibleJsonBlock for plain text content', () => {
    const entity = makeEntity({
      messageType: 'agent', agentId: 'a1', senderName: 'Bot',
      content: 'Hello, this is a normal message.',
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)

    const trigger = container.querySelector('[data-slot="collapsible-trigger"]')
    expect(trigger).toBeNull()
  })
})

// ── Typewriter animation ───────────────────────────────────────

describe('Typewriter animation', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('source: "db" renders full content immediately without animation', () => {
    const entity = makeEntity({
      messageType: 'agent', agentId: 'a1', senderName: 'Bot',
      content: 'Hello, this is a completed response from the agent.',
      source: 'db', taskStatus: 'completed',
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)
    expect(container.textContent).toContain('Hello, this is a completed response from the agent.')
  })

  it('source: "sse" agent message animates then completes after timers advance', () => {
    vi.useFakeTimers()
    const content = 'Hello, this is a live streaming response from the agent.'
    const entity = makeEntity({
      messageType: 'agent', agentId: 'a1', senderName: 'Bot',
      content, source: 'sse', taskStatus: 'completed',
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)

    // Phase 1: immediately after render, full content should NOT be present (typewriter starts at 0)
    expect(container.textContent).not.toContain(content)

    // Phase 2: advance through all typewriter ticks — each tick schedules
    // the next via setTimeout, so we need multiple act() flush cycles
    // TYPEWRITER_CHARS_PER_TICK=3, TYPEWRITER_INTERVAL_MS=12
    const ticks = Math.ceil(content.length / 3) + 5
    for (let i = 0; i < ticks; i++) {
      act(() => { vi.advanceTimersByTime(12) })
    }

    // Now the full content should be visible
    expect(container.textContent).toContain(content)
  })

  it('source: "optimistic" does not animate', () => {
    const entity = makeEntity({
      messageType: 'agent', agentId: 'a1', senderName: 'System',
      content: 'Processing was stopped by the user.',
      source: 'optimistic', taskStatus: 'canceled',
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)
    expect(container.textContent).toContain('Processing was stopped by the user.')
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

  it('renders amber minimal state for complete-empty phase', () => {
    const entity = makeEntity({
      messageType: 'agent', agentId: 'a1', senderName: 'Bot',
      content: '', taskStatus: 'completed',
    })
    const { container } = renderWithQueryClient(<EntityAgentBubble entity={entity} />)
    expect(container.querySelector('.text-amber-700')).toBeTruthy()
    const badges = within(container).getAllByText('No visible output')
    expect(badges.length).toBeGreaterThanOrEqual(1)
  })
})
