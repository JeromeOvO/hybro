import type { IncomingMessage } from '@/stores/message-store/types'
import type { SSEMessage } from '@/lib/types/sse'
import { TASK_STATE } from '@/lib/types/sse'

let messageCounter = 0
let roomCounter = 0

export function resetCounters() {
  messageCounter = 0
  roomCounter = 0
}

export function createMessage(overrides: Partial<IncomingMessage> = {}): IncomingMessage {
  messageCounter++
  const id = overrides.id || `msg-${messageCounter}`
  return {
    id,
    roomId: 'room-1',
    messageType: 'agent',
    content: `Test message ${messageCounter}`,
    senderName: 'Test Agent',
    timestamp: new Date(Date.now() + messageCounter * 1000).toISOString(),
    ...overrides,
  }
}

export function createUserMessage(overrides: Partial<IncomingMessage> = {}): IncomingMessage {
  return createMessage({
    messageType: 'user',
    senderName: 'Test User',
    ...overrides,
  })
}

export function createAgentMessage(overrides: Partial<IncomingMessage> = {}): IncomingMessage {
  return createMessage({
    messageType: 'agent',
    senderName: 'Test Agent',
    agentId: 'agent-1',
    ...overrides,
  })
}

export function createTaskMessage(
  taskStatus: typeof TASK_STATE[keyof typeof TASK_STATE],
  overrides: Partial<IncomingMessage> = {}
): IncomingMessage {
  const msg = createAgentMessage({
    taskStatus,
    ...overrides,
  })
  if (!msg.taskId) {
    msg.taskId = `task-${msg.id}`
  }
  return msg
}

export function createWorkingTask(overrides: Partial<IncomingMessage> = {}): IncomingMessage {
  return createTaskMessage(TASK_STATE.WORKING, overrides)
}

export function createCompletedTask(overrides: Partial<IncomingMessage> = {}): IncomingMessage {
  return createTaskMessage(TASK_STATE.COMPLETED, {
    content: 'Task completed successfully',
    ...overrides,
  })
}

export function createFailedTask(overrides: Partial<IncomingMessage> = {}): IncomingMessage {
  return createTaskMessage(TASK_STATE.FAILED, {
    content: 'Task failed',
    ...overrides,
  })
}

export function createCanceledTask(overrides: Partial<IncomingMessage> = {}): IncomingMessage {
  return createTaskMessage(TASK_STATE.CANCELED, {
    content: 'Task was canceled',
    ...overrides,
  })
}

export function createEphemeralMessage(overrides: Partial<IncomingMessage> = {}): IncomingMessage {
  return createMessage({
    isEphemeral: true,
    ...overrides,
  })
}

export function createRoom(overrides: Partial<{
  room_id: string
  room_name: string
  room_owner_id: string
  room_owner_name: string
  room_agent_set: Record<string, string>
  room_created_at: string
}> = {}) {
  roomCounter++
  return {
    room_id: `room-${roomCounter}`,
    room_name: `Test Room ${roomCounter}`,
    room_owner_id: 'test-user',
    room_owner_name: 'Test User',
    room_agent_set: {},
    room_created_at: new Date().toISOString(),
    ...overrides,
  }
}

export function createAgent(overrides: Partial<{
  agent_id: string
  provider_id: string
  agent_status: string
  agent_card: {
    name: string
    description: string
    url: string
    version: string
  }
}> = {}) {
  return {
    agent_id: 'agent-1',
    provider_id: 'provider-1',
    agent_status: 'active',
    agent_card: {
      name: 'Test Agent',
      description: 'A test agent',
      url: 'http://localhost:8001',
      version: '1.0.0',
    },
    ...overrides,
  }
}

export function createSSEMessage(
  type: SSEMessage['type'],
  data: SSEMessage['data'] = {}
): SSEMessage {
  return {
    type,
    room_id: 'room-1',
    timestamp: new Date().toISOString(),
    data,
  }
}

export function createTaskSubmittedSSE(
  messageId: string,
  agentName: string = 'Test Agent'
): SSEMessage {
  return createSSEMessage('task_submitted', {
    message_id: messageId,
    task_id: `task-${messageId}`,
    agent_name: agentName,
    status: 'submitted',
  })
}

export function createTaskUpdateSSE(
  messageId: string,
  status: string,
  content?: string
): SSEMessage {
  return createSSEMessage('task_update', {
    message_id: messageId,
    status,
    content,
  })
}

export function createAgentTokenSSE(messageId: string, token: string): SSEMessage {
  return createSSEMessage('agent_token', {
    message_id: messageId,
    token,
  })
}

export function createProcessingStatusSSE(
  status: 'processing' | 'completed' | 'canceled' | 'failed'
): SSEMessage {
  return createSSEMessage('processing_status', {
    status,
  })
}

export function createMessageBatch(count: number, baseOverrides: Partial<IncomingMessage> = {}): IncomingMessage[] {
  return Array.from({ length: count }, (_, i) =>
    createMessage({
      ...baseOverrides,
      timestamp: new Date(Date.now() + i * 1000).toISOString(),
    })
  )
}
