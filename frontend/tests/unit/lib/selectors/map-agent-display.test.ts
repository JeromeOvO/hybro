import { describe, it, expect, beforeEach } from 'vitest'
import { mapAgentDisplayProps } from '@/lib/selectors/map-agent-display'
import { createAgentMessage, createTaskMessage, resetCounters } from '../../../fixtures'
import { TASK_STATE } from '@/lib/types/sse'
import type { MessageEntity } from '@/stores/message-store/types'

function asEntity(msg: ReturnType<typeof createAgentMessage>): MessageEntity {
  return {
    ...msg,
    source: 'db' as const,
    sourceVersion: 1,
    displayType: 'agent-bubble' as const,
    isEphemeral: false,
    createdAt: Date.now(),
    updatedAt: Date.now(),
  } as MessageEntity
}

describe('mapAgentDisplayProps', () => {
  beforeEach(() => resetCounters())

  it('returns Working for submitted status', () => {
    const entity = asEntity(createTaskMessage(TASK_STATE.SUBMITTED, { senderName: 'Analyst' }))
    const result = mapAgentDisplayProps(entity)
    expect(result.label).toBe('Working')
    expect(result.tone).toBe('accent')
    expect(result.isAnimated).toBe(true)
    expect(result.ariaLabel).toBe('Analyst — Working')
  })

  it('returns Working for working status without content', () => {
    const entity = asEntity(createTaskMessage(TASK_STATE.WORKING, { content: '' }))
    const result = mapAgentDisplayProps(entity)
    expect(result.label).toBe('Working')
    expect(result.isAnimated).toBe(true)
  })

  it('returns Streaming for working status with content', () => {
    const entity = asEntity(createTaskMessage(TASK_STATE.WORKING, { content: 'partial response...' }))
    const result = mapAgentDisplayProps(entity)
    expect(result.label).toBe('Streaming')
    expect(result.tone).toBe('accent')
    expect(result.isAnimated).toBe(true)
  })

  it('returns Completed with relative time for completed status', () => {
    const entity = asEntity(createTaskMessage(TASK_STATE.COMPLETED, {
      taskUpdatedAt: new Date(Date.now() - 120_000).toISOString(),
    }))
    const result = mapAgentDisplayProps(entity)
    expect(result.label).toMatch(/^Completed/)
    expect(result.tone).toBe('muted')
    expect(result.isAnimated).toBe(false)
  })

  it('returns Failed for failed status', () => {
    const entity = asEntity(createTaskMessage(TASK_STATE.FAILED))
    const result = mapAgentDisplayProps(entity)
    expect(result.label).toBe('Failed')
    expect(result.tone).toBe('danger')
  })

  it('returns Rejected for rejected status', () => {
    const entity = asEntity(createTaskMessage(TASK_STATE.REJECTED))
    const result = mapAgentDisplayProps(entity)
    expect(result.label).toBe('Rejected')
    expect(result.tone).toBe('danger')
  })

  it('returns Canceled with muted tone for canceled status', () => {
    const entity = asEntity(createTaskMessage(TASK_STATE.CANCELED))
    const result = mapAgentDisplayProps(entity)
    expect(result.label).toBe('Canceled')
    expect(result.tone).toBe('muted')
  })

  it('returns Needs Input for input-required status', () => {
    const entity = asEntity(createTaskMessage(TASK_STATE.INPUT_REQUIRED))
    const result = mapAgentDisplayProps(entity)
    expect(result.label).toBe('Needs Input')
    expect(result.tone).toBe('warning')
    expect(result.isAnimated).toBe(true)
  })

  it('returns Auth Required for auth-required status', () => {
    const entity = asEntity(createTaskMessage(TASK_STATE.AUTH_REQUIRED))
    const result = mapAgentDisplayProps(entity)
    expect(result.label).toBe('Auth Required')
    expect(result.tone).toBe('warning')
    expect(result.isAnimated).toBe(false)
  })

  it('returns Working for unknown status', () => {
    const entity = asEntity(createTaskMessage(TASK_STATE.UNKNOWN))
    const result = mapAgentDisplayProps(entity)
    expect(result.label).toBe('Working')
    expect(result.tone).toBe('accent')
  })
})
