import { describe, expect, it } from 'vitest'
import { mapResultDisplayProps } from '@/lib/room-timeline/map-result-display'
import type { AgentResultViewModel } from '@/lib/room-timeline/types'

function makeResult(overrides: Partial<AgentResultViewModel> = {}): AgentResultViewModel {
  return {
    messageId: 'msg-1',
    agentId: 'agent-1',
    agentName: 'Agent',
    status: 'working',
    content: '',
    artifacts: [],
    ...overrides,
  }
}

describe('mapResultDisplayProps', () => {
  it('returns Synthesizing for summary agent while working', () => {
    const result = makeResult({
      agentName: 'HYBRO AI',
      isSummaryAgent: true,
      status: 'working',
    })
    expect(mapResultDisplayProps(result, true, 'partial synthesis')).toEqual({
      label: 'Synthesizing',
      tone: 'accent',
      isAnimated: true,
      ariaLabel: 'HYBRO AI — Synthesizing',
    })
  })

  it('returns Streaming for regular agent when streaming with resolved content', () => {
    const result = makeResult({ content: '' })
    expect(mapResultDisplayProps(result, true, 'live tokens').label).toBe('Streaming')
  })

  it('returns Working for regular agent when streaming without visible content', () => {
    const result = makeResult({ content: '' })
    expect(mapResultDisplayProps(result, true, '').label).toBe('Working')
  })
})
