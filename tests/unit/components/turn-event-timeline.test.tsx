// tests/unit/components/turn-event-timeline.test.tsx
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { TurnEventTimeline } from '@/components/turn-event-timeline'
import type { TimelineEventViewModel } from '@/lib/room-timeline/types'

// Mock agent-colors to avoid dependency on the full color system
vi.mock('@/lib/agent-colors', () => ({
  getAgentColorClasses: () => ({
    bg: 'bg-blue-100',
    border: 'border-blue-300',
    accent: 'bg-blue-500',
    text: 'text-blue-700',
    content: 'text-blue-900',
  }),
}))

function makeEvent(overrides: Partial<TimelineEventViewModel> = {}): TimelineEventViewModel {
  return {
    id: `evt-${Math.random().toString(36).slice(2, 8)}`,
    kind: 'agent_started',
    timestamp: '2026-01-01T12:00:00.000Z',
    agentId: 'agent-1',
    agentName: 'Test Agent',
    label: 'Test Agent started',
    isLive: false,
    isHiddenInCompact: false,
    ...overrides,
  }
}

describe('TurnEventTimeline', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders visible events', () => {
    const events = [
      makeEvent({ id: 'e1', label: 'Agent A started' }),
      makeEvent({ id: 'e2', label: 'Agent A completed' }),
    ]

    render(<TurnEventTimeline events={events} />)

    expect(screen.getByText('Agent A started')).toBeTruthy()
    expect(screen.getByText('Agent A completed')).toBeTruthy()
  })

  it('hides compact events by default', () => {
    const events = [
      makeEvent({ id: 'e1', label: 'Visible event', isHiddenInCompact: false }),
      makeEvent({ id: 'e2', label: 'Hidden event', isHiddenInCompact: true }),
    ]

    render(<TurnEventTimeline events={events} />)

    expect(screen.getByText('Visible event')).toBeTruthy()
    expect(screen.queryByText('Hidden event')).toBeNull()
    expect(screen.getByTestId('show-process-toggle')).toBeTruthy()
  })

  it('show process toggle reveals hidden events', () => {
    const events = [
      makeEvent({ id: 'e1', label: 'Visible event', isHiddenInCompact: false }),
      makeEvent({ id: 'e2', label: 'Hidden progress event', isHiddenInCompact: true }),
    ]

    render(<TurnEventTimeline events={events} />)

    // Hidden by default
    expect(screen.queryByText('Hidden progress event')).toBeNull()

    // Click toggle
    fireEvent.click(screen.getByTestId('show-process-toggle'))

    // Now visible
    expect(screen.getByText('Hidden progress event')).toBeTruthy()
    expect(screen.getByText('Visible event')).toBeTruthy()
  })

  it('live event has breathing-glow class on dot', () => {
    const events = [
      makeEvent({ id: 'e1', label: 'Live agent working', isLive: true }),
    ]

    render(<TurnEventTimeline events={events} />)

    const liveDot = screen.getByTestId('live-dot')
    expect(liveDot).toBeTruthy()
    expect(liveDot.className).toContain('animate-breathing-glow')
  })

  it('empty events renders nothing', () => {
    const { container } = render(<TurnEventTimeline events={[]} />)
    expect(container.innerHTML).toBe('')
  })
})
