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

  // --- Interactive state (HITL) expiry tests ---

  it('does not mark input-required tasks with future hitlExpiresAt as stale', () => {
    const messages = [
      makeIncoming({
        id: 'hitl-active',
        taskStatus: TASK_STATE.INPUT_REQUIRED,
        timestamp: '2026-02-17T10:00:00Z', // 2 hours old — would fail generic 10m check
        hitlExpiresAt: '2026-02-18T10:00:00Z', // 22 hours from now
      }),
    ]
    const result = detectAndMarkStaleTasks(messages)
    expect(result[0].taskStatus).toBe(TASK_STATE.INPUT_REQUIRED)
  })

  it('marks input-required tasks with past hitlExpiresAt as expired', () => {
    const messages = [
      makeIncoming({
        id: 'hitl-expired',
        taskStatus: TASK_STATE.INPUT_REQUIRED,
        timestamp: '2026-02-16T12:00:00Z',
        hitlExpiresAt: '2026-02-17T11:00:00Z', // 1 hour ago
      }),
    ]
    const result = detectAndMarkStaleTasks(messages)
    expect(result[0].taskStatus).toBe(TASK_STATE.FAILED)
    expect(result[0].taskError).toContain('expired')
  })

  it('uses 24h fallback for input-required tasks without hitlExpiresAt', () => {
    const messages = [
      makeIncoming({
        id: 'hitl-no-expiry-recent',
        taskStatus: TASK_STATE.INPUT_REQUIRED,
        timestamp: '2026-02-17T11:00:00Z', // 1 hour old, well within 24h
      }),
    ]
    const result = detectAndMarkStaleTasks(messages)
    expect(result[0].taskStatus).toBe(TASK_STATE.INPUT_REQUIRED)
  })

  it('marks input-required tasks older than 24h (no hitlExpiresAt) as expired', () => {
    const messages = [
      makeIncoming({
        id: 'hitl-no-expiry-old',
        taskStatus: TASK_STATE.INPUT_REQUIRED,
        timestamp: '2026-02-16T11:00:00Z', // 25 hours old
      }),
    ]
    const result = detectAndMarkStaleTasks(messages)
    expect(result[0].taskStatus).toBe(TASK_STATE.FAILED)
    expect(result[0].taskError).toContain('expired')
  })

  it('does not mark auth-required tasks within 24h as stale', () => {
    const messages = [
      makeIncoming({
        id: 'auth-active',
        taskStatus: TASK_STATE.AUTH_REQUIRED,
        timestamp: '2026-02-17T06:00:00Z', // 6 hours old
      }),
    ]
    const result = detectAndMarkStaleTasks(messages)
    expect(result[0].taskStatus).toBe(TASK_STATE.AUTH_REQUIRED)
  })
})
