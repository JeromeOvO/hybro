/**
 * Tests for fixture helpers and type safety.
 *
 * Regression tests for:
 * - createTaskMessage no longer assigns nonexistent `taskId` on IncomingMessage
 * - All fixture helpers return objects conforming to IncomingMessage
 */
import { describe, it, expect, beforeEach } from 'vitest'
import type { IncomingMessage } from '@/stores/message-store/types'
import { TASK_STATE } from '@/lib/types/sse'
import {
  resetCounters,
  createMessage,
  createUserMessage,
  createAgentMessage,
  createTaskMessage,
  createWorkingTask,
  createCompletedTask,
} from '../../fixtures'

beforeEach(() => {
  resetCounters()
})

const INCOMING_MESSAGE_REQUIRED_KEYS: (keyof IncomingMessage)[] = [
  'id',
  'roomId',
  'messageType',
  'content',
  'senderName',
  'timestamp',
]

function assertValidIncomingMessage(msg: IncomingMessage) {
  for (const key of INCOMING_MESSAGE_REQUIRED_KEYS) {
    expect(msg).toHaveProperty(key)
    expect(msg[key]).toBeDefined()
  }
  expect(['user', 'agent']).toContain(msg.messageType)
}

describe('createMessage', () => {
  it('returns a valid IncomingMessage', () => {
    const msg = createMessage()
    assertValidIncomingMessage(msg)
  })

  it('applies overrides', () => {
    const msg = createMessage({ content: 'custom', roomId: 'room-42' })
    expect(msg.content).toBe('custom')
    expect(msg.roomId).toBe('room-42')
  })
})

describe('createUserMessage', () => {
  it('has messageType user', () => {
    const msg = createUserMessage()
    assertValidIncomingMessage(msg)
    expect(msg.messageType).toBe('user')
  })
})

describe('createAgentMessage', () => {
  it('has messageType agent and agentId', () => {
    const msg = createAgentMessage()
    assertValidIncomingMessage(msg)
    expect(msg.messageType).toBe('agent')
    expect(msg.agentId).toBeDefined()
  })
})

describe('createTaskMessage', () => {
  it('returns a valid IncomingMessage with taskStatus', () => {
    const msg = createTaskMessage(TASK_STATE.WORKING)
    assertValidIncomingMessage(msg)
    expect(msg.taskStatus).toBe(TASK_STATE.WORKING)
  })

  it('does not have a taskId property', () => {
    const msg = createTaskMessage(TASK_STATE.COMPLETED)
    expect(msg).not.toHaveProperty('taskId')
  })

  it('preserves overrides', () => {
    const msg = createTaskMessage(TASK_STATE.FAILED, { content: 'err' })
    expect(msg.content).toBe('err')
    expect(msg.taskStatus).toBe(TASK_STATE.FAILED)
  })
})

describe('createWorkingTask', () => {
  it('has WORKING taskStatus', () => {
    const msg = createWorkingTask()
    assertValidIncomingMessage(msg)
    expect(msg.taskStatus).toBe(TASK_STATE.WORKING)
  })
})

describe('createCompletedTask', () => {
  it('has COMPLETED taskStatus', () => {
    const msg = createCompletedTask()
    assertValidIncomingMessage(msg)
    expect(msg.taskStatus).toBe(TASK_STATE.COMPLETED)
  })
})
