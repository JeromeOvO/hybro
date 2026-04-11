// tests/unit/components/conversation-turn.test.tsx
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { MemoizedTurn } from '@/components/conversation-turn'
import type { TurnViewModel } from '@/lib/room-timeline/types'

// Mock agent-colors
vi.mock('@/lib/agent-colors', () => ({
  getAgentColorClasses: () => ({
    bg: 'bg-blue-100',
    border: 'border-blue-300',
    accent: 'bg-blue-500',
    text: 'text-blue-700',
    content: 'text-blue-900',
  }),
}))

vi.mock('@/components/message-bubble', () => ({
  UserAttachmentCard: ({ attachment }: { attachment: { fileId: string; fileName: string } }) => (
    <div data-testid={`attachment-card-${attachment.fileId}`}>{attachment.fileName}</div>
  ),
}))

vi.mock('@/components/agent-source-badge', () => ({
  AgentSourceBadge: ({ source }: { source: string }) => (
    <span data-testid={`source-badge-${source}`} />
  ),
}))

vi.mock('@/components/ui/tooltip', () => ({
  TooltipProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

vi.mock('@/lib/agent-avatar', () => ({
  getAgentAvatarUri: (seed: string) => `data:image/svg+xml;seed=${seed}`,
}))

vi.mock('@/lib/system-agents', () => ({
  isSummarySystemAgent: () => false,
}))

vi.mock('@/components/agent-placeholder-row', () => ({
  AgentPlaceholderRow: ({ agentId, agentName }: { agentId: string; agentName: string }) => (
    <div data-testid={`placeholder-${agentId}`}>{agentName} — Thinking</div>
  ),
}))

vi.mock('@/components/supervisor-header', () => ({
  SupervisorHeader: ({ isCompleted }: { isCompleted: boolean }) => (
    <div data-testid="supervisor-header">{isCompleted ? 'Completed' : 'Processing'}</div>
  ),
}))

function makeTurn(overrides: Partial<TurnViewModel> = {}): TurnViewModel {
  return {
    id: 'turn-1',
    roomId: 'room-1',
    userMessageId: 'u1',
    userContent: 'What is the weather?',
    userAttachments: [],
    timestamp: '2026-01-01T00:00:00Z',
    status: 'completed',
    events: [],
    summary: null,
    agentResults: [
      {
        agentId: 'agent-1',
        agentName: 'Weather Agent',
        messageId: 'a1',
        status: 'completed',
        content: 'The weather is sunny and 22C.',
        artifacts: [],
        isSummaryAgent: false,
      },
    ],
    activeAgentIds: [],
    isSupervisorTurn: false,
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
})

describe('ConversationTurn', () => {
  it('active turn is fully expanded', () => {
    render(<MemoizedTurn turn={makeTurn()} index={0} isActive={true} />)

    // User prompt visible
    expect(screen.getByText('What is the weather?')).toBeTruthy()
    // Agent result visible (expanded)
    expect(screen.getByText('Weather Agent')).toBeTruthy()
    expect(screen.getByText('The weather is sunny and 22C.')).toBeTruthy()
    // No collapse button on active turn
    expect(screen.queryByText('Collapse')).toBeNull()
  })

  it('completed non-active turn shows collapsed with summary', () => {
    const turn = makeTurn({
      summary: {
        sourceAgentId: 'agent-1',
        sourceAgentName: 'Weather Agent',
        title: 'Sunny weather today',
        body: 'The forecast shows clear skies.',
      },
    })

    render(<MemoizedTurn turn={turn} index={0} isActive={false} />)

    // User prompt visible
    expect(screen.getByText('What is the weather?')).toBeTruthy()
    // Summary visible in collapsed state
    expect(screen.getByText('Sunny weather today')).toBeTruthy()
    // Full agent result NOT visible (collapsed)
    expect(screen.queryByText('The weather is sunny and 22C.')).toBeNull()
  })

  it('click expands a collapsed non-active turn', () => {
    const turn = makeTurn()

    render(<MemoizedTurn turn={turn} index={0} isActive={false} />)

    // Should show the expand indicator
    expect(screen.getByText(/1 agent responded/)).toBeTruthy()

    // Click to expand
    fireEvent.click(screen.getByText(/1 agent responded/))

    // Now the full content should be visible
    expect(screen.getByText('Weather Agent')).toBeTruthy()
    expect(screen.getByText('The weather is sunny and 22C.')).toBeTruthy()
    expect(screen.getByText('Collapse')).toBeTruthy()
  })

  it('failed turn shows warning line', () => {
    const turn = makeTurn({
      status: 'failed',
      agentResults: [
        {
          agentId: 'agent-1',
          agentName: 'Broken Agent',
          messageId: 'a1',
          status: 'failed',
          content: 'Connection error',
          artifacts: [],
        },
      ],
    })

    render(<MemoizedTurn turn={turn} index={0} isActive={true} />)

    expect(screen.getByText('One or more agents failed in this turn')).toBeTruthy()
  })

  it('hides summary when turn is expanded (active)', () => {
    const turn = makeTurn({
      summary: {
        sourceAgentId: 'agent-1',
        sourceAgentName: 'Weather Agent',
        title: 'Clear skies ahead',
        body: 'Detailed weather summary.',
      },
    })

    render(<MemoizedTurn turn={turn} index={0} isActive={true} />)

    // V2: summary is hidden in expanded state
    expect(screen.queryByTestId('turn-summary')).toBeNull()
  })

  it('renders user prompt with attachments', () => {
    const turn = makeTurn({
      userAttachments: [
        { fileId: 'f1', fileName: 'screenshot.png', mimeType: 'image/png', sizeBytes: 1024 },
      ],
    })

    render(<MemoizedTurn turn={turn} index={0} isActive={true} />)

    expect(screen.getByText('What is the weather?')).toBeTruthy()
    expect(screen.getByText('screenshot.png')).toBeTruthy()
  })

  it('renders user prompt right-aligned', () => {
    const turn = makeTurn({ userContent: 'Hello world' })
    render(<MemoizedTurn turn={turn} index={0} isActive={true} />)
    const wrapper = screen.getByTestId('user-prompt-wrapper')
    expect(wrapper.className).toContain('justify-end')
    expect(screen.getByText('Hello world')).toBeTruthy()
  })

  it('renders mentions as clickable links in user prompt', () => {
    const turn = makeTurn({ userContent: 'Ask <@agent-1|CodeBot> for help' })
    render(<MemoizedTurn turn={turn} index={0} isActive={true} />)
    const link = screen.getByText('@CodeBot')
    expect(link.tagName).toBe('A')
    expect(link.getAttribute('href')).toBe('/c/agents/agent-1')
  })

  it('renders attachments via UserAttachmentCard', () => {
    const turn = makeTurn({
      userContent: 'See this',
      userAttachments: [
        { fileId: 'f1', fileName: 'photo.png', mimeType: 'image/png', sizeBytes: 2048, fileUrl: 'https://example.com/photo.png' },
        { fileId: 'f2', fileName: 'report.pdf', mimeType: 'application/pdf', sizeBytes: 51200, fileUrl: 'https://example.com/report.pdf' },
      ],
    })
    render(<MemoizedTurn turn={turn} index={0} isActive={true} />)
    expect(screen.getByTestId('attachment-card-f1')).toBeTruthy()
    expect(screen.getByTestId('attachment-card-f2')).toBeTruthy()
    expect(screen.getByText('photo.png')).toBeTruthy()
    expect(screen.getByText('report.pdf')).toBeTruthy()
  })

  it('summary badge does NOT show source icon (collapsed state)', () => {
    const turn = makeTurn({
      summary: {
        sourceAgentId: 'agent-1',
        sourceAgentName: 'Weather Agent',
        title: 'Sunny',
        body: 'Clear skies.',
      },
    })
    // Summary only shows in collapsed (non-active) state
    render(<MemoizedTurn turn={turn} index={0} isActive={false} />)
    const summaryBlock = screen.getByTestId('turn-summary')
    expect(summaryBlock.querySelector('[data-testid^="source-badge-"]')).toBeNull()
  })

  it('summary badge does NOT show (deleted) even without sourceAgentId (collapsed state)', () => {
    const turn = makeTurn({
      summary: {
        sourceAgentId: undefined,
        sourceAgentName: 'System Summary',
        title: 'Overview',
        body: 'A synthesis.',
      },
    })
    // Summary only shows in collapsed (non-active) state
    render(<MemoizedTurn turn={turn} index={0} isActive={false} />)
    expect(screen.getByText('System Summary')).toBeTruthy()
    expect(screen.queryByText(/deleted/i)).toBeNull()
  })

  // --- V2: Placeholders ---

  it('renders AgentPlaceholderRow for pending agents', () => {
    const turn = makeTurn({ status: 'active' })
    const pendingAgents = [
      { agentId: 'a2', agentName: 'Data Bot' },
      { agentId: 'a3', agentName: 'Image Bot' },
    ]
    render(<MemoizedTurn turn={turn} index={0} isActive={true} pendingAgents={pendingAgents} />)
    expect(screen.getByTestId('placeholder-a2')).toBeTruthy()
    expect(screen.getByTestId('placeholder-a3')).toBeTruthy()
    expect(screen.getByText('Data Bot — Thinking')).toBeTruthy()
  })

  it('does NOT render placeholders for non-active turns', () => {
    const turn = makeTurn({ status: 'completed' })
    const pendingAgents = [{ agentId: 'a2', agentName: 'Bot' }]
    render(<MemoizedTurn turn={turn} index={0} isActive={false} pendingAgents={pendingAgents} />)
    expect(screen.queryByTestId('placeholder-a2')).toBeNull()
  })

  // --- V2: SupervisorHeader ---

  it('renders SupervisorHeader when isSupervisorTurn=true and expanded', () => {
    const turn = makeTurn({ isSupervisorTurn: true })
    render(<MemoizedTurn turn={turn} index={0} isActive={true} />)
    expect(screen.getByTestId('supervisor-header')).toBeTruthy()
  })

  it('does NOT render SupervisorHeader when isSupervisorTurn=false', () => {
    const turn = makeTurn({ isSupervisorTurn: false })
    render(<MemoizedTurn turn={turn} index={0} isActive={true} />)
    expect(screen.queryByTestId('supervisor-header')).toBeNull()
  })

  // --- V2: Summary hidden when expanded ---

  it('shows SummaryBlock in collapsed state', () => {
    const turn = makeTurn({
      summary: {
        sourceAgentId: 'agent-1',
        sourceAgentName: 'Bot',
        title: 'Summary title',
        body: 'Summary body',
      },
    })
    render(<MemoizedTurn turn={turn} index={0} isActive={false} />)
    expect(screen.getByTestId('turn-summary')).toBeTruthy()
  })

  // --- V2: No TurnEventTimeline ---

  it('does NOT render TurnEventTimeline even when events are present', () => {
    const turn = makeTurn({
      events: [{
        id: 'e1', kind: 'agent_started', timestamp: '2026-01-01T00:00:00Z',
        agentId: 'a1', agentName: 'Bot', label: 'Started', isLive: false, isHiddenInCompact: false,
      }],
    })
    render(<MemoizedTurn turn={turn} index={0} isActive={true} />)
    expect(screen.queryByTestId('live-dot')).toBeNull()
    expect(screen.queryByTestId('show-process-toggle')).toBeNull()
  })
})
