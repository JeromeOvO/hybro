import { describe, it, expect, vi } from 'vitest'
import { convertApiMessageToIncoming } from '../convert-api-message'
import type { RoomMessage } from '@/lib/types/response'
import type { ConvertApiMessageOptions } from '../convert-api-message'
import { TASK_STATE } from '@/lib/types/sse'

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

    it('maps persisted orchestration failure to turn terminal status', async () => {
      const apiMsg = makeApiMessage({
        message_type: 'user',
        extend_info: {
          orchestration_status: 'failed',
        },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.turnTerminalStatus).toBe('failed')
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
            kind: 'task',
            contextId: 'ctx-1',
            id: 'task-1',
          } as unknown as RoomMessage['message_content']['message_task'],
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
          } as unknown as RoomMessage['message_content']['message_task'],
        },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskStatus).toBe(TASK_STATE.WORKING)
    })

    it('ignores legacy top-level task_content without a public label', async () => {
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-1',
        task_content: 'PRIVATE_SENTINEL_top_level_task_content',
        message_content: { message_text: '' },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskContent).toBeUndefined()
      expect(JSON.stringify(result)).not.toContain('PRIVATE_SENTINEL')
    })

    it('ignores legacy task metadata task_content without a public label', async () => {
      const privateSentinel = 'PRIVATE_SENTINEL_task_metadata'
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-1',
        message_content: {
          message_text: '',
          message_task: {
            metadata: {
              task_content: privateSentinel,
              prompt: privateSentinel,
              hitl_prompt: privateSentinel,
              choices: [privateSentinel],
              hitl_choices: [privateSentinel],
              hitl_request_id: privateSentinel,
            },
            status: { state: 'working' },
            kind: 'task',
            contextId: 'ctx-1',
            id: 'task-1',
          } as unknown as RoomMessage['message_content']['message_task'],
        },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskContent).toBeUndefined()
      expect(JSON.stringify(result)).not.toContain(privateSentinel)
    })

    it('hydrates HITL fields only when backend provenance matches the request', async () => {
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-1',
        extend_info: {
          public_task_label: 'Requesting Claims Agent',
          hitl_request_id: 'local-hitl-request',
        },
        message_content: {
          message_text: '',
          message_task: {
            metadata: {
              hitl_request_id: 'local-hitl-request',
              hitl_prompt: 'Choose the approved option',
              hitl_prompt_type: 'choice',
              hitl_choices: ['Approve', 'Reject'],
              user_answer: 'Approve',
            },
            status: { state: 'completed' },
          } as unknown as RoomMessage['message_content']['message_task'],
        },
      })

      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.hitlRequestId).toBe('local-hitl-request')
      expect(result.hitlPrompt).toBe('Choose the approved option')
      expect(result.hitlPromptType).toBe('choice')
      expect(result.hitlChoices).toEqual(['Approve', 'Reject'])
      expect(result.hitlUserAnswer).toBe('Approve')
    })

    it('ignores all persisted HITL metadata when backend provenance is absent', async () => {
      const privateSentinel = 'PRIVATE_SENTINEL_untrusted_hitl_metadata'
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'system:clarifier',
        message_content: {
          message_text: '',
          message_task: {
            metadata: {
              hitl_request_id: 'spoofed-request',
              request_id: 'spoofed-request',
              hitl_prompt: privateSentinel,
              prompt: privateSentinel,
              hitl_prompt_type: 'choice',
              prompt_type: 'choice',
              hitl_choices: [privateSentinel],
              choices: [privateSentinel],
              user_answer: privateSentinel,
              hitl_interaction_id: privateSentinel,
              hitl_question_count: 2,
              hitl_question_index: 0,
            },
            status: { state: 'completed' },
          } as unknown as RoomMessage['message_content']['message_task'],
        },
      })

      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.hitlRequestId).toBeUndefined()
      expect(result.hitlPrompt).toBeUndefined()
      expect(result.hitlPromptType).toBeUndefined()
      expect(result.hitlChoices).toBeUndefined()
      expect(result.hitlUserAnswer).toBeUndefined()
      expect(result.hitlGroupId).toBeUndefined()
      expect(result.hitlGroupTotal).toBeUndefined()
      expect(result.hitlGroupIndex).toBeUndefined()
      expect(result.hitlResolved).toBeUndefined()
      expect(JSON.stringify(result)).not.toContain(privateSentinel)
    })

    it('preserves trusted locally projected supervisor answer and group context', async () => {
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'system:clarifier',
        extend_info: {
          hitl_request_id: 'supervisor-hitl-request',
        },
        message_content: {
          message_text: '',
          message_task: {
            metadata: {
              hitl_request_id: 'supervisor-hitl-request',
              hitl_prompt: 'Which account should be used?',
              hitl_prompt_type: 'text',
              user_answer: 'Use the enterprise account',
              hitl_interaction_id: 'supervisor-group',
              hitl_question_count: 2,
              hitl_question_index: 0,
            },
            status: { state: 'completed' },
          } as unknown as RoomMessage['message_content']['message_task'],
        },
      })

      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.hitlRequestId).toBe('supervisor-hitl-request')
      expect(result.hitlPrompt).toBe('Which account should be used?')
      expect(result.hitlUserAnswer).toBe('Use the enterprise account')
      expect(result.hitlGroupId).toBe('supervisor-group')
      expect(result.hitlGroupTotal).toBe(2)
      expect(result.hitlGroupIndex).toBe(0)
    })

    it('does not promote status message to content for non-terminal tasks', async () => {
      const privateSentinel = 'PRIVATE_SENTINEL_working_status_message'
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-1',
        message_content: {
          message_text: '',
          message_task: {
            status: {
              state: 'working',
              message: { parts: [{ text: privateSentinel }] },
            },
          } as RoomMessage['message_content']['message_task'],
        },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskStatus).toBe(TASK_STATE.WORKING)
      expect(result.content).toBe('')
      expect(result.taskError).toBeNull()
      expect(JSON.stringify(result)).not.toContain(privateSentinel)
    })

    it('ignores message_text for non-terminal agent tasks (user prompt seed)', async () => {
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-1',
        message_content: {
          message_text: 'What is the weather today?',
          message_task: {
            status: { state: 'working' },
          } as RoomMessage['message_content']['message_task'],
        },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskStatus).toBe(TASK_STATE.WORKING)
      expect(result.content).toBe('')
    })

    it('prefers public message_text for completed agent tasks with artifacts', async () => {
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-1',
        message_content: {
          message_text: 'The agent completed the request.',
          message_task: {
            status: { state: 'completed' },
            artifacts: [{
              artifactId: 'artifact-1',
              name: 'cyber_submission',
              parts: [{ kind: 'data', data: { company: 'Acme SaaS Inc.' } }],
            }],
          } as unknown as RoomMessage['message_content']['message_task'],
        },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskStatus).toBe(TASK_STATE.COMPLETED)
      expect(result.content).toBe('The agent completed the request.')
      expect(result.artifacts).toHaveLength(1)
      expect(result.artifacts?.[0]?.name).toBe('cyber_submission')
    })

    it('maps explicitly published dispatch text separately from the task label', async () => {
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-1',
        extend_info: {
          public_task_label: 'Requesting Insurer Agent',
          public_dispatch_text: 'Assess the supplied submission and return a quote.',
        },
        message_content: {
          message_text: 'The agent completed the request.',
          message_task: {
            status: { state: 'completed' },
          } as RoomMessage['message_content']['message_task'],
        },
      })

      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskStatusMessage).toBe('Requesting Insurer Agent')
      expect(result.dispatchText).toBe(
        'Assess the supplied submission and return a quote.',
      )
    })

    it('does not promote completed task status message to content or error', async () => {
      const privateSentinel = 'PRIVATE_SENTINEL_completed_status_message'
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-1',
        message_content: {
          message_text: '',
          message_task: {
            status: {
              state: 'completed',
              message: { parts: [{ text: privateSentinel }] },
            },
          } as RoomMessage['message_content']['message_task'],
        },
      })

      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskStatus).toBe(TASK_STATE.COMPLETED)
      expect(result.content).toBe('')
      expect(result.taskError).toBeNull()
      expect(JSON.stringify(result)).not.toContain(privateSentinel)
    })

    it('does not use completed task history as output', async () => {
      const privateSentinel = 'PRIVATE_SENTINEL_completed_history'
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-1',
        message_content: {
          message_text: '',
          message_task: {
            status: { state: 'completed' },
            history: [{
              role: 'agent',
              parts: [{ text: privateSentinel }],
            }],
          } as RoomMessage['message_content']['message_task'],
        },
      })

      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskStatus).toBe(TASK_STATE.COMPLETED)
      expect(result.content).toBe('')
      expect(JSON.stringify(result)).not.toContain(privateSentinel)
    })

    it('uses a stable public error for terminal failed tasks', async () => {
      const privateSentinel = 'PRIVATE_SENTINEL_failed_status_message'
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-1',
        message_content: {
          message_text: '',
          message_task: {
            status: {
              state: 'failed',
              message: { parts: [{ text: privateSentinel }] },
            },
          } as RoomMessage['message_content']['message_task'],
        },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskStatus).toBe(TASK_STATE.FAILED)
      expect(result.content).toBe('Task failed')
      expect(result.taskError).toBe('Task failed')
      expect(JSON.stringify(result)).not.toContain(privateSentinel)
    })

    it('ignores non-completed artifacts and inline file bytes', async () => {
      const privateSentinel = 'PRIVATE_SENTINEL_noncompleted_file_bytes'
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-1',
        message_content: {
          message_text: '',
          message_task: {
            status: { state: 'failed' },
            artifacts: [{
              artifactId: 'partial-artifact',
              name: 'partial',
              parts: [{
                kind: 'file',
                file: {
                  bytes: privateSentinel,
                  mimeType: 'text/plain',
                  name: 'partial.txt',
                },
              }],
            }],
          } as RoomMessage['message_content']['message_task'],
        },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskStatus).toBe(TASK_STATE.FAILED)
      expect(result.artifacts).toBeUndefined()
      expect(JSON.stringify(result)).not.toContain(privateSentinel)
    })

    it('hydrates safe unavailable markers for local output delivery failures', async () => {
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-1',
        message_content: {
          message_text: '',
          message_task: {
            status: { state: 'failed' },
            metadata: {
              output_failure_code: 'artifact_delivery_failed',
              remote_task_state: 'completed',
            },
            artifacts: [{
              artifactId: 'failed-file',
              parts: [{
                kind: 'data',
                data: {
                  type: 'file_unavailable',
                  reason: 'invalid_content',
                },
              }],
            }],
          } as unknown as RoomMessage['message_content']['message_task'],
        },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskStatus).toBe(TASK_STATE.FAILED)
      expect(result.artifacts?.[0]?.parts[0]?.data).toEqual({
        type: 'file_unavailable',
        reason: 'invalid_content',
      })
    })

    it('normalizes metadata-only room file artifacts', async () => {
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-1',
        message_content: {
          message_text: '',
          message_task: {
            status: { state: 'completed' },
            artifacts: [{
              artifactId: 'file-artifact',
              name: 'result-file',
              parts: [{
                kind: 'file',
                metadata: {
                  file_id: 'a'.repeat(32),
                  file_name: 'result.csv',
                  mime_type: 'text/csv',
                  size_bytes: 12,
                  sha256: 'hash',
                },
              }],
            }],
          } as unknown as RoomMessage['message_content']['message_task'],
        },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())

      expect(result.taskStatus).toBe(TASK_STATE.COMPLETED)
      expect(result.artifacts?.[0]?.parts[0]?.file).toEqual({
        uri: undefined,
        fileId: 'a'.repeat(32),
        mime_type: 'text/csv',
        name: 'result.csv',
        sizeBytes: 12,
        sha256: 'hash',
      })
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
        task_content: 'PRIVATE_SENTINEL_complete_entity_task_content',
        extend_info: { public_task_label: 'Analyzing data...' },
        step_number: 3,
        total_steps: 7,
        message_content: {
          message_text: 'Here are the results',
          message_task: {
            status: { state: 'completed' },
            artifacts: [{
              artifactId: 'artifact-complete',
              name: 'response',
              parts: [{ text: 'Here are the results' }],
            }],
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
        taskStatus: TASK_STATE.COMPLETED,
        taskContent: 'Analyzing data...',
        taskStatusMessage: 'Analyzing data...',
        stepNumber: 3,
        totalSteps: 7,
      }))
      expect(result.timestamp).toBeTruthy()
      expect(result.taskUpdatedAt).toBeTruthy()
      expect(result.taskCreatedAt).toBeTruthy()
    })

    it('serializes the public task label without leaking internal dispatch instructions', async () => {
      const privateSentinel = 'PRIVATE_SENTINEL_frontend_entity'
      const apiMsg = makeApiMessage({
        message_type: 'agent',
        agent_id: 'agent-1',
        task_content: privateSentinel,
        extend_info: { public_task_label: 'Requesting Insurer' },
        message_content: {
          message_text: privateSentinel,
          message_task: {
            status: { state: 'working' },
            metadata: {
              task_content: privateSentinel,
              internal_task_payload: { instructions: privateSentinel },
            },
          } as unknown as RoomMessage['message_content']['message_task'],
        },
      })
      const result = await convertApiMessageToIncoming(apiMsg, makeOptions())
      const serialized = JSON.stringify(result)

      expect(result.content).toBe('')
      expect(result.taskContent).toBe('Requesting Insurer')
      expect(result.taskStatusMessage).toBe('Requesting Insurer')
      expect(serialized).toContain('Requesting Insurer')
      expect(serialized).not.toContain(privateSentinel)
    })
  })
})
