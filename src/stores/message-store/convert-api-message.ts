import type { A2ATaskStatus } from '@/lib/api/a2a-tasks'
import { extractTaskContent, extractTaskError } from '@/lib/api/a2a-tasks'
import type { RoomMessage } from '@/lib/types/response'
import type { TaskState } from '@/lib/types/sse'
import { isInteractiveState, isTerminalState, TASK_STATE } from '@/lib/types/sse'
import { isSupervisorClarifyAgent } from '@/lib/system-agents'
import type { AttachmentData } from '@/lib/types/attachments'
import { normalizeTimestampOrNow } from '@/lib/time'
import { parseSummaryOrigin } from '@/lib/room-timeline/derive-final-answer'
import type { ArtifactData, ArtifactPart, IncomingMessage } from './types'

/**
 * Parameters for converting API messages to IncomingMessage shape.
 */
export interface ConvertApiMessageOptions {
  userId?: string
  userName?: string
  getAgentName: (agentId: string) => Promise<string>
  getAgentSource?: (agentId: string) => 'cloud' | 'hub' | undefined
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
  const { userId, userName, getAgentName, getAgentSource } = options

  // ── Extract task status (before content, so we can gate error fallback) ──
  const messageTask = apiMessage.message_content?.message_task
  let taskStatus: TaskState | undefined
  if (messageTask) {
    const maybeStatus = (messageTask as A2ATaskStatus['task']).status?.state
    if (typeof maybeStatus === 'string') {
      taskStatus = maybeStatus as TaskState
    }
  }

  // ── Extract content ──────────────────────────────────────────
  let content = ''
  let taskError: string | undefined
  let taskContent: string | undefined

  if (apiMessage.message_content?.message_text) {
    // For non-terminal agent tasks, message_text may contain the user's original
    // prompt (seeded at task creation). Only use it as display content for
    // terminal states, non-task messages, or user messages.
    const isNonTerminalAgentTask = apiMessage.message_type === 'agent'
      && taskStatus && !isTerminalState(taskStatus)
    if (!isNonTerminalAgentTask) {
      content = apiMessage.message_content.message_text
    }
  }

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
      } else if (extractedError && (!taskStatus || isTerminalState(taskStatus))) {
        content = extractedError
      }
    }
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

  // ── Extract persisted HITL request metadata ─────────────
  let hitlRequestId: string | undefined
  let hitlPrompt: string | undefined
  let hitlPromptType: 'text' | 'choice' | 'confirmation' | undefined
  let hitlChoices: string[] | null | undefined

  // ── Extract persisted HITL group metadata ───────────────
  let hitlGroupId: string | undefined
  let hitlGroupTotal: number | undefined
  let hitlGroupIndex: number | undefined
  const meta = messageTask?.metadata
  if (meta) {
    if (typeof meta.hitl_group_id === 'string') hitlGroupId = meta.hitl_group_id
    if (typeof meta.hitl_group_total === 'number') hitlGroupTotal = meta.hitl_group_total
    if (typeof meta.hitl_group_index === 'number') hitlGroupIndex = meta.hitl_group_index

    const rid = meta.hitl_request_id ?? meta.request_id
    if (typeof rid === 'string') hitlRequestId = rid
    const hp = meta.hitl_prompt ?? meta.prompt
    if (typeof hp === 'string') hitlPrompt = hp
    const hpt = meta.hitl_prompt_type ?? meta.prompt_type
    if (typeof hpt === 'string') hitlPromptType = hpt as 'text' | 'choice' | 'confirmation'
    if (Array.isArray(meta.hitl_choices)) hitlChoices = meta.hitl_choices as string[]
    else if (Array.isArray(meta.choices)) hitlChoices = meta.choices as string[]
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

  // Answered HITL from DB: supervisor clarify is done; real agents may still be working.
  let hitlResolved: boolean | undefined
  if (hitlUserAnswer !== undefined) {
    hitlResolved = true
    if (
      apiMessage.message_type === 'agent'
      && taskStatus
      && isInteractiveState(taskStatus)
      && isSupervisorClarifyAgent(agentId)
    ) {
      taskStatus = TASK_STATE.COMPLETED
    }
  }

  // ── Build IncomingMessage ────────────────────────────────────
  const extendInfo = apiMessage.extend_info as Record<string, unknown> | null | undefined
  const summaryOrigin = parseSummaryOrigin(extendInfo?.summary_origin)
  const quotedText = typeof extendInfo?.quoted_text === 'string' ? extendInfo.quoted_text : undefined
  const quotedSenderName = typeof extendInfo?.quoted_sender_name === 'string' ? extendInfo.quoted_sender_name : undefined
  const extQuoteId = typeof extendInfo?.quote_id === 'string' ? extendInfo.quote_id : undefined
  const topQuoteId = typeof (apiMessage as { quote_id?: unknown }).quote_id === 'string'
    ? (apiMessage as { quote_id: string }).quote_id
    : undefined
  const quoteId = topQuoteId || extQuoteId

  return {
    id: apiMessage.message_id,
    clientRequestId: apiMessage.client_request_id ?? undefined,
    roomId: apiMessage.room_id,
    messageType: apiMessage.message_type as 'user' | 'agent',
    content,
    senderName,
    timestamp: normalizeTimestampOrNow(apiMessage.message_created_at),

    agentId: apiMessage.message_type === 'agent' ? (agentId || undefined) : undefined,
    agentSource: apiMessage.message_type === 'agent' && agentId && getAgentSource
      ? getAgentSource(agentId)
      : undefined,
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

    hitlRequestId,
    hitlPrompt,
    hitlPromptType,
    hitlChoices,
    hitlResolved,
    hitlUserAnswer,
    hitlGroupId,
    hitlGroupTotal,
    hitlGroupIndex,
    attachments,
    artifacts,
    summaryOrigin,
    quotedText,
    quotedSenderName,
    quoteId,
  }
}
