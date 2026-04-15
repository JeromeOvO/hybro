// tests/unit/components/turn-event-timeline-artifacts.test.tsx
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { TurnEventTimeline } from '@/components/turn-event-timeline'
import type { TimelineEventViewModel } from '@/lib/room-timeline/types'

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

describe('TurnEventTimeline - inline artifacts', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders artifact file card for artifact_emitted event', () => {
    const events = [
      makeEvent({
        id: 'e-art',
        kind: 'artifact_emitted',
        label: 'Report Agent emitted artifact',
        artifactPayload: {
          artifactId: 'art-1',
          name: 'quarterly-report.pdf',
          parts: [{ kind: 'file', file: { name: 'quarterly-report.pdf', mime_type: 'application/pdf' } }],
        },
      }),
    ]

    render(<TurnEventTimeline events={events} />)

    expect(screen.getByText('Report Agent emitted artifact')).toBeTruthy()
    expect(screen.getByText('quarterly-report.pdf')).toBeTruthy()
  })

  it('renders image thumbnail for image artifact', () => {
    const events = [
      makeEvent({
        id: 'e-img',
        kind: 'artifact_emitted',
        label: 'Chart Agent emitted artifact',
        artifactPayload: {
          artifactId: 'art-2',
          name: 'chart.png',
          parts: [{
            kind: 'file',
            file: {
              name: 'chart.png',
              mime_type: 'image/png',
              uri: 'data:image/png;base64,fakedata',
            },
          }],
        },
      }),
    ]

    render(<TurnEventTimeline events={events} />)

    const img = screen.getByAltText('chart.png')
    expect(img).toBeTruthy()
    expect(img.getAttribute('src')).toBe('data:image/png;base64,fakedata')
  })

  it('non-artifact events render without preview block', () => {
    const events = [
      makeEvent({ id: 'e1', kind: 'agent_started', label: 'Agent started' }),
      makeEvent({ id: 'e2', kind: 'agent_completed', label: 'Agent completed' }),
    ]

    render(<TurnEventTimeline events={events} />)

    // No artifact preview elements should be present
    expect(screen.queryByAltText(/./)).toBeNull()
  })
})
