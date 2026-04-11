// tests/unit/lib/system-agents.test.ts
import { describe, it, expect } from 'vitest'
import {
  isSystemAgent,
  isSupervisorSystemAgent,
  isSummarySystemAgent,
} from '@/lib/system-agents'

describe('isSupervisorSystemAgent', () => {
  it('returns true for supervisor_hitl', () => {
    expect(isSupervisorSystemAgent('supervisor_hitl')).toBe(true)
  })

  it('returns true for supervisor_synthesis', () => {
    expect(isSupervisorSystemAgent('supervisor_synthesis')).toBe(true)
  })

  it('returns false for debate_summary', () => {
    expect(isSupervisorSystemAgent('debate_summary')).toBe(false)
  })

  it('returns false for non_debate_summary', () => {
    expect(isSupervisorSystemAgent('non_debate_summary')).toBe(false)
  })

  it('returns false for summary', () => {
    expect(isSupervisorSystemAgent('summary')).toBe(false)
  })

  it('returns false for undefined', () => {
    expect(isSupervisorSystemAgent(undefined)).toBe(false)
  })

  it('returns false for random agent id', () => {
    expect(isSupervisorSystemAgent('agent-123')).toBe(false)
  })
})

describe('isSummarySystemAgent', () => {
  it('returns true for supervisor_synthesis', () => {
    expect(isSummarySystemAgent('supervisor_synthesis')).toBe(true)
  })

  it('returns true for debate_summary', () => {
    expect(isSummarySystemAgent('debate_summary')).toBe(true)
  })

  it('returns true for non_debate_summary', () => {
    expect(isSummarySystemAgent('non_debate_summary')).toBe(true)
  })

  it('returns true for summary', () => {
    expect(isSummarySystemAgent('summary')).toBe(true)
  })

  it('returns false for supervisor_hitl', () => {
    expect(isSummarySystemAgent('supervisor_hitl')).toBe(false)
  })

  it('returns false for undefined', () => {
    expect(isSummarySystemAgent(undefined)).toBe(false)
  })

  it('returns false for random agent id', () => {
    expect(isSummarySystemAgent('agent-123')).toBe(false)
  })
})
