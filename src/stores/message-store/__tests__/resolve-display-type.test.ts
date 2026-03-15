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

  it('returns agent-bubble for completed task with artifacts but no content', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.COMPLETED,
      content: '',
      artifacts: [{ artifactId: 'a-1', parts: [{ kind: 'file', file: { uri: 'https://s3/img.png', mime_type: 'image/png' } }] }],
    })).toBe('agent-bubble')
  })

  it('returns agent-bubble for completed task with artifacts and no content field', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.COMPLETED,
      artifacts: [{ artifactId: 'a-2', parts: [{ kind: 'data', data: { key: 'value' } }] }],
    })).toBe('agent-bubble')
  })

  it('returns task-status for completed task with empty artifacts array', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.COMPLETED,
      content: '',
      artifacts: [],
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
  it('returns agent-bubble for working task', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.WORKING,
    })).toBe('agent-bubble')
  })

  it('returns agent-bubble for working task with content', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.WORKING,
      content: 'Streaming content already landed',
    })).toBe('agent-bubble')
  })

  it('returns agent-bubble for working task with artifacts', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.WORKING,
      artifacts: [{ artifactId: 'w-1', parts: [{ kind: 'data', data: { ok: true } }] }],
    })).toBe('agent-bubble')
  })

  it('returns agent-bubble for submitted task', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.SUBMITTED,
    })).toBe('agent-bubble')
  })

  it('returns agent-bubble for submitted task with content', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.SUBMITTED,
      content: 'Payload arrived before terminal status',
    })).toBe('agent-bubble')
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

  // ── Ephemeral streaming entities ─────────────────────────
  it('returns agent-bubble for ephemeral agent without taskStatus (streaming placeholder)', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      isEphemeral: true,
      content: '',
    })).toBe('agent-bubble')
  })

  it('returns agent-bubble for ephemeral agent with non-terminal taskStatus (processing placeholder)', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.WORKING,
      isEphemeral: true,
    })).toBe('agent-bubble')
  })

  it('returns agent-bubble for ephemeral agent with input-required taskStatus', () => {
    expect(resolveDisplayType({
      messageType: 'agent',
      taskStatus: TASK_STATE.INPUT_REQUIRED,
      isEphemeral: true,
    })).toBe('agent-bubble')
  })

  it('returns user-bubble for user messages even with isEphemeral', () => {
    expect(resolveDisplayType({
      messageType: 'user',
      isEphemeral: true,
    })).toBe('user-bubble')
  })
})
