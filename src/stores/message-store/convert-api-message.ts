import type { A2ATaskStatus } from '@/lib/api/a2a-tasks'
import { extractTaskContent, extractTaskError } from '@/lib/api/a2a-tasks'
import type { RoomMessage } from '@/lib/types/response'
import type { TaskState } from '@/lib/types/sse'
import type { AttachmentData } from '@/lib/types/attachments'
import { normalizeTimestampOrNow } from '@/lib/time'
import type { ArtifactData, ArtifactPart, IncomingMessage } from './types'

/**
 * Parameters for converting API messages to IncomingMessage shape.
 */
export interface ConvertApiMessageOptions {
  userId?: string
  userName?: string
  getAgentName: (agentId: string) => Promise<string>
}

/**
 * Convert a RoomMessage (DB API format) to an IncomingMessage (normalized store format).
 *
 * Extracts content, task state, sender info, and timestamps from the API response.
 * Display-type resolution happens downstream in the store's upsert path via
 * resolveDisplayType — this function does not perform any type-conversion logic.
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

  // ── Extract persisted HITL user answer ───────────────────
  let hitlUserAnswer: string | undefined
  const maybeUserAnswer = messageTask?.metadata?.user_answer
  if (typeof maybeUserAnswer === 'string') {
    hitlUserAnswer = maybeUserAnswer
  }

  // ── Extract persisted HITL group metadata ───────────────
  let hitlGroupId: string | undefined
  let hitlGroupTotal: number | undefined
  let hitlGroupIndex: number | undefined
  const meta = messageTask?.metadata
  if (meta) {
    if (typeof meta.hitl_group_id === 'string') hitlGroupId = meta.hitl_group_id
    if (typeof meta.hitl_group_total === 'number') hitlGroupTotal = meta.hitl_group_total
    if (typeof meta.hitl_group_index === 'number') hitlGroupIndex = meta.hitl_group_index
  }

  // ── Extract user attachments ────────────────────────────
  let attachments: AttachmentData[] | undefined
  const rawAttachments = apiMessage.message_content?.attachments
  if (Array.isArray(rawAttachments) && rawAttachments.length > 0) {
    attachments = rawAttachments
      .filter((att: Record<string, unknown>) => typeof att.file_id === 'string' && typeof att.mime_type === 'string')
      .map((att: Record<string, unknown>) => ({
        fileId: att.file_id as string,
        fileUrl: (att.file_url as string) || undefined,
        mimeType: att.mime_type as string,
        fileName: (att.file_name as string) || 'unknown',
        sizeBytes: (att.size_bytes as number) || 0,
      }))
    if (attachments.length === 0) attachments = undefined
  }

  // ── Extract multimodal artifacts from task ───────────────
  let artifacts: ArtifactData[] | undefined
  const rawArtifacts = messageTask?.artifacts as Record<string, unknown>[] | undefined
  if (Array.isArray(rawArtifacts) && rawArtifacts.length > 0) {
    const mapped = rawArtifacts
      .map((a) => {
        const rawParts = a.parts as Record<string, unknown>[] | undefined
        if (!Array.isArray(rawParts) || rawParts.length === 0) return null

        const parts = rawParts
          .map((p) => {
            const root = (p.root ?? p) as Record<string, unknown>
            const kind = (root.kind as string) || 'text'
            if (kind === 'text') return null
            const fileData = root.file as Record<string, unknown> | undefined
            return {
              kind: kind as ArtifactPart['kind'],
              text: root.text as string | undefined,
              file: fileData ? {
                uri: fileData.uri as string | undefined,
                bytes: fileData.bytes as string | undefined,
                mime_type: (fileData.mime_type || fileData.mimeType) as string | undefined,
                name: fileData.name as string | undefined,
              } : undefined,
              data: root.data as Record<string, unknown> | undefined,
            }
          })
          .filter((p): p is NonNullable<typeof p> => p !== null) as ArtifactPart[]

        if (parts.length === 0) return null
        return {
          artifactId: (a.artifactId || a.artifact_id || `db-${apiMessage.message_id}`) as string,
          name: a.name as string | undefined,
          parts,
        }
      })
      .filter((a): a is NonNullable<typeof a> => a !== null) as ArtifactData[]
    artifacts = mapped
    if (artifacts.length === 0) artifacts = undefined
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
    taskError: messageTask ? (taskError || null) : undefined,
    taskContent,

    stepNumber: apiMessage.step_number ?? undefined,
    totalSteps: apiMessage.total_steps ?? undefined,
    relatedMessageId: apiMessage.related_message_id ?? undefined,

    taskUpdatedAt: apiMessage.task_updated_at
      ? normalizeTimestampOrNow(apiMessage.task_updated_at)
      : undefined,
    taskCreatedAt: apiMessage.message_created_at
      ? normalizeTimestampOrNow(apiMessage.message_created_at)
      : undefined,

    hitlUserAnswer,
    hitlGroupId,
    hitlGroupTotal,
    hitlGroupIndex,
    attachments,
    artifacts,
  }
}
