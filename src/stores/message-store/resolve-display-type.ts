import type { TaskState } from '@/lib/types/sse'
import { TASK_STATE } from '@/lib/types/sse'
import type { ArtifactData, DisplayType } from './types'

/**
 * Resolve the display type for a message — the single source of truth for which
 * React component renders a given message.
 *
 * Display type transitions are expected during the task lifecycle:
 *   1. task_submitted → 'task-status' (working, no content)
 *   2. task_update completed with content → 'agent-bubble'
 * The transition happens once via SSE; subsequent DB reconciliation sees the
 * entity is already the correct display type and isNoOpUpdate returns true.
 */
export function resolveDisplayType(msg: {
  messageType: 'user' | 'agent'
  taskStatus?: TaskState
  content?: string
  isEphemeral?: boolean
  artifacts?: ArtifactData[]
}): DisplayType {
  // User messages are always user bubbles
  if (msg.messageType === 'user') return 'user-bubble'

  // Ephemeral agent messages WITHOUT a task status are streaming/typewriter
  // placeholders — always render as agent-bubble so useStreamingContent works.
  // Ephemeral messages WITH a non-terminal task status (e.g. processing
  // placeholders at WORKING) must still render as task-status cards.
  if (msg.isEphemeral && !msg.taskStatus) return 'agent-bubble'

  // Agent message with no task → regular agent bubble
  if (!msg.taskStatus) return 'agent-bubble'

  // Completed task with text content or multimodal artifacts → agent bubble
  const hasContent = !!msg.content?.trim()
  const hasArtifacts = !!msg.artifacts && msg.artifacts.length > 0
  if (msg.taskStatus === TASK_STATE.COMPLETED && (hasContent || hasArtifacts)) {
    return 'agent-bubble'
  }

  // Everything else: working, failed, canceled, input-required, etc.
  return 'task-status'
}
