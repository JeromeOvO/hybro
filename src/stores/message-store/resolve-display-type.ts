import type { TaskState } from '@/lib/types/sse'
import { TASK_STATE, PENDING_STATES } from '@/lib/types/sse'
import type { ArtifactData, DisplayType } from './types'

/**
 * Resolve the display type for a message — the single source of truth for which
 * React component renders a given message.
 *
 * Working/submitted tasks render as 'agent-bubble' so the unified component
 * handles all phases (waiting → streaming → revealing → static) without
 * unmount/remount. TaskStatusMessage is reserved for interactive states
 * (input-required, auth-required) and terminal failures without content.
 */
export function resolveDisplayType(msg: {
  messageType: 'user' | 'agent'
  taskStatus?: TaskState
  content?: string
  isEphemeral?: boolean
  artifacts?: ArtifactData[]
}): DisplayType {
  if (msg.messageType === 'user') return 'user-bubble'

  // Ephemeral agent messages are streaming/typewriter placeholders
  if (msg.isEphemeral) return 'agent-bubble'

  // Agent message with no task → regular agent bubble
  if (!msg.taskStatus) return 'agent-bubble'

  const hasContent = !!msg.content?.trim()
  const hasArtifacts = !!msg.artifacts && msg.artifacts.length > 0

  // Completed task with text content or multimodal artifacts → agent bubble
  if (msg.taskStatus === TASK_STATE.COMPLETED && (hasContent || hasArtifacts)) {
    return 'agent-bubble'
  }

  // Working/submitted → agent-bubble so the unified component renders the
  // waiting indicator inline and transitions smoothly to streaming/reveal.
  if (PENDING_STATES.includes(msg.taskStatus)) {
    return 'agent-bubble'
  }

  // Interactive states (input-required, auth-required) and terminal failures
  // without content remain as standalone task-status cards.
  return 'task-status'
}
