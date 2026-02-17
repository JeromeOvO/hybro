import type { A2ATaskStatus } from '@/lib/api/a2a-tasks'
import { extractTaskContent, extractTaskError } from '@/lib/api/a2a-tasks'
import type { RoomMessage } from '@/lib/types/response'
import type { TaskState } from '@/lib/types/sse'
import { normalizeTimestampOrNow } from '@/lib/time'
import type { IncomingMessage } from './types'

/**
 * Parameters for converting API messages to IncomingMessage shape.
 * These are the same dependencies that convertApiMessageToMessageData uses
 * (userId, userName, getAgentName) but passed explicitly instead of via closure.
 */
export interface ConvertApiMessageOptions {
  userId?: string
  userName?: string
  getAgentName: (agentId: string) => Promise<string>
}

/**
 * Convert a RoomMessage (DB API format) to an IncomingMessage (normalized store format).
 *
 * This is the normalized-store equivalent of convertApiMessageToMessageData.
 * It extracts the same fields but produces an IncomingMessage rather than
 * a MessageData. The display-type resolution happens downstream in the
 * store's upsert path via resolveDisplayType — this function does not
 * perform any type-conversion logic.
 */
export async function convertApiMessageToIncoming(
  apiMessage: RoomMessage,
  options: ConvertApiMessageOptions,
): Promise<IncomingMessage> {
  const { userId, userName, getAgentName } = options

  // ── Extract content ──────────────────────────────────────────
  let content = ''
  let taskError: string | undefined
  let taskContent: string | undefined

  if (apiMessage.message_content?.message_text) {
    content = apiMessage.message_content.message_text
  }

  const messageTask = apiMessage.message_content?.message_task
  if (messageTask) {
    const messageTaskTyped = messageTask as A2ATaskStatus['task']
    const extractedError = extractTaskError(messageTaskTyped)
    if (extractedError) {
      taskError = extractedError
    }
    if (!content) {
      const extractedContent = extractTaskContent(messageTaskTyped)
      if (extractedContent) {
        content = extractedContent
      } else if (extractedError) {
        content = extractedError
      }
    }
  }

  // ── Extract task status ──────────────────────────────────────
  let taskStatus: TaskState | undefined
  const maybeStatus = messageTask?.status?.state
  if (typeof maybeStatus === 'string') {
    taskStatus = maybeStatus as TaskState
  }

  // ── Extract task_content ─────────────────────────────────────
  if (apiMessage.task_content) {
    taskContent = apiMessage.task_content
  } else {
    const maybeTaskContent = messageTask?.metadata?.task_content
    if (typeof maybeTaskContent === 'string') {
      taskContent = maybeTaskContent
    }
  }

  // ── Resolve sender name ──────────────────────────────────────
  let senderName: string
  let agentId: string | undefined

  if (apiMessage.message_type === 'user') {
    senderName = userName ?? userId ?? 'User'
  } else if (apiMessage.message_type === 'agent') {
    if (apiMessage.agent_id) {
      agentId = apiMessage.agent_id
    } else if (apiMessage.message_content?.message_task?.metadata?.agent_id) {
      agentId = apiMessage.message_content.message_task.metadata.agent_id as string
    }

    if (agentId) {
      try {
        senderName = await getAgentName(agentId)
      } catch {
        senderName = 'Agent'
      }
    } else {
      senderName = 'Agent'
    }
  } else {
    senderName = 'Unknown'
  }

  // ── Build IncomingMessage ────────────────────────────────────
  return {
    id: apiMessage.message_id,
    roomId: apiMessage.room_id,
    messageType: apiMessage.message_type as 'user' | 'agent',
    content,
    senderName,
    timestamp: normalizeTimestampOrNow(apiMessage.message_created_at),

    agentId: apiMessage.message_type === 'agent' ? (agentId || undefined) : undefined,
    userId: apiMessage.message_type === 'user' ? userId : undefined,

    taskStatus,
    // Gap 20: When a task is present, explicitly send null for no-error so DB
    // reconciliation can clear stale errors. When no task exists, send undefined
    // so mergeIncoming preserves whatever value the entity already has.
    taskError: messageTask ? (taskError || null) : undefined,
    taskContent,

    stepNumber: apiMessage.step_number ?? undefined,
    totalSteps: apiMessage.total_steps ?? undefined,

    taskUpdatedAt: apiMessage.task_updated_at
      ? normalizeTimestampOrNow(apiMessage.task_updated_at)
      : undefined,
    taskCreatedAt: apiMessage.message_created_at
      ? normalizeTimestampOrNow(apiMessage.message_created_at)
      : undefined,
  }
}
