import { describe, it, expect } from 'vitest'
import { resolveDisplayType } from '../resolve-display-type'
import { TASK_STATE } from '@/lib/types/sse'

describe('resolveDisplayType', () => {
  // ── User messages ──────────────────────────────────────────
  it('returns user-bubble for user messages', () => {
    expect(resolveDisplayType({ messageType: 'user' })).toBe('user-bubble')
  })

  it('returns user-bubble for user messages even with taskStatus', () => {
    expect(resolveDisplayType({ messageType: 'user', taskStatus: TASK_STATE.WORKING })).toBe('user-bubble')
  })

  // ── Agent messages without task ────────────────────────────
  it('returns agent-bubble for agent messages without taskStatus', () => {
    expect(resolveDisplayType({ messageType: 'agent', content: 'Hello' })).toBe('agent-bubble')
  })

  it('returns agent-bubble for agent messages with no taskStatus and no content', () => {
    expect(resolveDisplayType({ messageType: 'agent' })).toBe('agent-bubble')
  })

  // ── Completed tasks with content ──────────────────────────
  it('returns agent-bubble for completed task with content', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.COMPLETED,
      content: 'Here is your result',
    })).toBe('agent-bubble')
  })

  it('returns task-status for completed task with empty content', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.COMPLETED,
      content: '',
    })).toBe('task-status')
  })

  it('returns task-status for completed task with whitespace-only content', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.COMPLETED,
      content: '   ',
    })).toBe('task-status')
  })

  it('returns task-status for completed task with no content', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.COMPLETED,
    })).toBe('task-status')
  })

  // ── Non-terminal task states ──────────────────────────────
  it('returns task-status for working task', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.WORKING,
    })).toBe('task-status')
  })

  it('returns task-status for submitted task', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.SUBMITTED,
    })).toBe('task-status')
  })

  it('returns task-status for input-required task', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.INPUT_REQUIRED,
    })).toBe('task-status')
  })

  it('returns task-status for auth-required task', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.AUTH_REQUIRED,
    })).toBe('task-status')
  })

  // ── Terminal failure states ───────────────────────────────
  it('returns task-status for failed task', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.FAILED,
      content: 'Error occurred',
    })).toBe('task-status')
  })

  it('returns task-status for canceled task', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.CANCELED,
    })).toBe('task-status')
  })

  it('returns task-status for rejected task', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.REJECTED,
    })).toBe('task-status')
  })
})
