import { describe, it, expect, vi } from 'vitest'
import { convertApiMessageToIncoming } from '../convert-api-message'
import type { RoomMessage } from '@/lib/types/response'
import type { ConvertApiMessageOptions } from '../convert-api-message'

// ── Helpers ──────────────────────────────────────────────────

function makeApiMessage(overrides: Partial<RoomMessage> = {}): RoomMessage {
  return {
    room_id: 'room-1',
    message_id: 'msg-1',
    message_created_at: '2026-02-17T10:00:00Z',
    message_type: 'user',
    message_content: {
      message_text: 'Hello world',
    },
    ...overrides,
  }
}

function makeOptions(overrides: Partial<ConvertApiMessageOptions> = {}): ConvertApiMessageOptions {
  return {
    userId: 'user-123',
    userName: 'Alice',
    getAgentName: vi.fn().mockResolvedValue('Test Agent'),
    ...overrides,
  }
}

describe('convertApiMessageToIncoming', () => {
  describe('user messages', () => {
    it('converts a basic user message', async () => {
      const apiMsg = makeApiMessage({
        message_type: 'user',
        message_content: { message_text: 'Hello!' },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.id).toBe('msg-1')
      expect(result.roomId).toBe('room-1')
      expect(result.messageType).toBe('user')
      expect(result.content).toBe('Hello!')
      expect(result.senderName).toBe('Alice')
      expect(result.userId).toBe('user-123')
      expect(result.agentId).toBeUndefined()
      expect(result.taskStatus).toBeUndefined()
    })

    it('uses userId as fallback when userName is missing', async () => {
      const apiMsg = makeApiMessage({ message_type: 'user' })
      const options = makeOptions({ userName: undefined })
      const result = await convertApiMessageToIncoming(apiMsg, options)

      expect(result.senderName).toBe('user-123')
    })

    it('uses "User" as fallback when both userName and userId are missing', async () => {
      const apiMsg = makeApiMessage({ message_type: 'user' })
      const options = makeOptions({ userName: undefined, userId: undefined })
      const result = await convertApiMessageToIncoming(apiMsg, options)

      expect(result.senderName).toBe('User')
    })
  })

  describe('agent messages', () => {
    it('converts a basic agent message with agent_id', async () => {
      const getAgentName = vi.fn().mockResolvedValue('Smart Agent')
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-42',
        message_content: { message_text: 'I can help with that.' },
      })
      const result = await convertApiMessageToIncoming(
        apiMsg,
        makeOptions({ getAgentName }),
      )

      expect(result.messageType).toBe('agent')
      expect(result.content).toBe('I can help with that.')
      expect(result.senderName).toBe('Smart Agent')
      expect(result.agentId).toBe('agent-42')
      expect(result.userId).toBeUndefined()
      expect(getAgentName).toHaveBeenCalledWith('agent-42')
    })

    it('falls back to "Agent" when getAgentName throws', async () => {
      const getAgentName = vi.fn().mockRejectedValue(new Error('not found'))
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-unknown',
        message_content: { message_text: 'response' },
      })
      const result = await convertApiMessageToIncoming(
        apiMsg,
        makeOptions({ getAgentName }),
      )

      expect(result.senderName).toBe('Agent')
    })

    it('falls back to "Agent" when no agent_id is available', async () => {
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: undefined,
        message_content: { message_text: 'response' },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.senderName).toBe('Agent')
      expect(result.agentId).toBeUndefined()
    })

    it('extracts agent_id from task metadata when not on top-level', async () => {
      const getAgentName = vi.fn().mockResolvedValue('Meta Agent')
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: undefined,
        message_content: {
          message_text: '',
          message_task: {
            metadata: { agent_id: 'meta-agent-1' },
            status: { state: 'working' },
          } as RoomMessage['message_content']['message_task'],
        },
      })
      const result = await convertApiMessageToIncoming(
        apiMsg,
        makeOptions({ getAgentName }),
      )

      expect(result.agentId).toBe('meta-agent-1')
      expect(getAgentName).toHaveBeenCalledWith('meta-agent-1')
    })
  })

  describe('content extraction', () => {
    it('uses message_text as primary content', async () => {
      const apiMsg = makeApiMessage({
        message_content: { message_text: 'Primary content' },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.content).toBe('Primary content')
    })

    it('defaults to empty string when message_text is null', async () => {
      const apiMsg = makeApiMessage({
        message_content: { message_text: null },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.content).toBe('')
    })
  })

  describe('task fields', () => {
    it('extracts task status from message_task', async () => {
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-1',
        message_content: {
          message_text: '',
          message_task: {
            status: { state: 'working' },
          } as RoomMessage['message_content']['message_task'],
        },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskStatus).toBe('working')
    })

    it('extracts task_content from top-level field', async () => {
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-1',
        task_content: 'Researching your question...',
        message_content: { message_text: '' },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskContent).toBe('Researching your question...')
    })

    it('extracts task_content from metadata fallback', async () => {
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-1',
        message_content: {
          message_text: '',
          message_task: {
            metadata: { task_content: 'From metadata' },
            status: { state: 'working' },
          } as RoomMessage['message_content']['message_task'],
        },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskContent).toBe('From metadata')
    })

    it('has no taskStatus when message_task is absent', async () => {
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-1',
        message_content: { message_text: 'No task here' },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskStatus).toBeUndefined()
    })
  })

  describe('ordering fields', () => {
    it('maps step_number and total_steps', async () => {
      const apiMsg = makeApiMessage({
        step_number: 2,
        total_steps: 5,
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.stepNumber).toBe(2)
      expect(result.totalSteps).toBe(5)
    })

    it('treats null step_number and total_steps as undefined', async () => {
      const apiMsg = makeApiMessage({
        step_number: null,
        total_steps: null,
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.stepNumber).toBeUndefined()
      expect(result.totalSteps).toBeUndefined()
    })
  })

  describe('timestamps', () => {
    it('normalizes message_created_at to timestamp', async () => {
      const apiMsg = makeApiMessage({
        message_created_at: '2026-02-17T10:00:00Z',
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.timestamp).toBeTruthy()
      expect(typeof result.timestamp).toBe('string')
    })

    it('maps task_updated_at when present', async () => {
      const apiMsg = makeApiMessage({
        task_updated_at: '2026-02-17T10:05:00Z',
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskUpdatedAt).toBeTruthy()
    })

    it('omits taskUpdatedAt when task_updated_at is null', async () => {
      const apiMsg = makeApiMessage({
        task_updated_at: null,
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskUpdatedAt).toBeUndefined()
    })

    it('sets taskCreatedAt from message_created_at', async () => {
      const apiMsg = makeApiMessage({
        message_created_at: '2026-02-17T10:00:00Z',
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskCreatedAt).toBeTruthy()
    })

    it('omits taskCreatedAt when message_created_at is missing', async () => {
      const apiMsg = makeApiMessage({
        message_created_at: undefined,
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskCreatedAt).toBeUndefined()
    })
  })

  describe('field mapping completeness', () => {
    it('maps all fields for a complete agent task message', async () => {
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-1',
        message_created_at: '2026-02-17T10:00:00Z',
        task_updated_at: '2026-02-17T10:05:00Z',
        task_content: 'Analyzing data...',
        step_number: 3,
        total_steps: 7,
        message_content: {
          message_text: 'Here are the results',
          message_task: {
            status: { state: 'completed' },
          } as RoomMessage['message_content']['message_task'],
        },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result).toEqual(expect.objectContaining({
        id: 'msg-1',
        roomId: 'room-1',
        messageType: 'agent',
        content: 'Here are the results',
        agentId: 'agent-1',
        taskStatus: 'completed',
        taskContent: 'Analyzing data...',
        stepNumber: 3,
        totalSteps: 7,
      }))
      expect(result.timestamp).toBeTruthy()
      expect(result.taskUpdatedAt).toBeTruthy()
      expect(result.taskCreatedAt).toBeTruthy()
    })
  })
})
