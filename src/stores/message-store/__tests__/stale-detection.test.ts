import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { detectAndMarkStaleTasks } from '../stale-detection'
import type { IncomingMessage } from '../types'
import { TASK_STATE } from '@/lib/types/sse'

function makeIncoming(overrides: Partial<IncomingMessage> = {}): IncomingMessage {
  return {
    id: 'msg-1',
    roomId: 'room-1',
    messageType: 'agent',
    content: '',
    senderName: 'Agent',
    timestamp: new Date().toISOString(),
    ...overrides,
  }
}

describe('detectAndMarkStaleTasks', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-02-17T12:00:00Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('marks non-terminal tasks older than 10 minutes as failed', () => {
    const messages = [
      makeIncoming({
        id: 'stale',
        taskStatus: TASK_STATE.WORKING,
        timestamp: '2026-02-17T11:45:00Z', // 15 minutes old
      }),
    ]
    const result = detectAndMarkStaleTasks(messages)
    expect(result[0].taskStatus).toBe(TASK_STATE.FAILED)
    expect(result[0].taskError).toContain('timed out')
  })

  it('does not mark tasks younger than 10 minutes', () => {
    const messages = [
      makeIncoming({
        id: 'fresh',
        taskStatus: TASK_STATE.WORKING,
        timestamp: '2026-02-17T11:55:00Z', // 5 minutes old
      }),
    ]
    const result = detectAndMarkStaleTasks(messages)
    expect(result[0].taskStatus).toBe(TASK_STATE.WORKING)
  })

  it('does not mark terminal tasks as stale', () => {
    const messages = [
      makeIncoming({
        id: 'completed',
        taskStatus: TASK_STATE.COMPLETED,
        content: 'Done',
        timestamp: '2026-02-17T10:00:00Z', // 2 hours old
      }),
    ]
    const result = detectAndMarkStaleTasks(messages)
    expect(result[0].taskStatus).toBe(TASK_STATE.COMPLETED)
  })

  it('does not mark user messages', () => {
    const messages = [
      makeIncoming({
        id: 'user',
        messageType: 'user',
        timestamp: '2026-02-17T10:00:00Z',
      }),
    ]
    const result = detectAndMarkStaleTasks(messages)
    expect(result[0].messageType).toBe('user')
    expect(result[0].taskStatus).toBeUndefined()
  })

  it('does not mark agent messages without taskStatus', () => {
    const messages = [
      makeIncoming({
        id: 'no-task',
        content: 'Just a message',
        timestamp: '2026-02-17T10:00:00Z',
      }),
    ]
    const result = detectAndMarkStaleTasks(messages)
    expect(result[0].taskStatus).toBeUndefined()
  })

  it('uses taskUpdatedAt over timestamp when available', () => {
    const messages = [
      makeIncoming({
        id: 'updated-recently',
        taskStatus: TASK_STATE.WORKING,
        timestamp: '2026-02-17T10:00:00Z', // 2 hours old
        taskUpdatedAt: '2026-02-17T11:55:00Z', // 5 minutes old
      }),
    ]
    const result = detectAndMarkStaleTasks(messages)
    expect(result[0].taskStatus).toBe(TASK_STATE.WORKING) // not stale because taskUpdatedAt is fresh
  })

  it('provides default content for stale tasks with no content', () => {
    const messages = [
      makeIncoming({
        id: 'stale-no-content',
        taskStatus: TASK_STATE.WORKING,
        content: '',
        timestamp: '2026-02-17T11:40:00Z',
      }),
    ]
    const result = detectAndMarkStaleTasks(messages)
    expect(result[0].content).toBe('Task failed due to timeout')
  })

  it('preserves existing content for stale tasks', () => {
    const messages = [
      makeIncoming({
        id: 'stale-with-content',
        taskStatus: TASK_STATE.WORKING,
        content: 'Was working on analysis',
        timestamp: '2026-02-17T11:40:00Z',
      }),
    ]
    const result = detectAndMarkStaleTasks(messages)
    expect(result[0].content).toBe('Was working on analysis')
  })
})
