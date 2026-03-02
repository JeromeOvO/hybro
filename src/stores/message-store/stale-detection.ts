import type { TaskState } from '@/lib/types/sse'
import { isTerminalState, isInteractiveState } from '@/lib/types/sse'
import type { IncomingMessage } from './types'

const STALE_TASK_THRESHOLD_MS = 10 * 60 * 1000 // 10 minutes, matches backend
const INTERACTIVE_FALLBACK_THRESHOLD_MS = 24 * 60 * 60 * 1000 // 24h, matches backend HITL expiry default

/**
 * Detect tasks that are stuck in non-terminal state beyond the threshold.
 *
 * - For interactive states (input-required, auth-required): use the backend-provided
 *   `hitlExpiresAt` when available, otherwise fall back to 24 hours.
 * - For all other non-terminal states (submitted, working): 10-minute timeout.
 *
 * Runs at hydration and reconciliation time.
 */
export function detectAndMarkStaleTasks(messages: IncomingMessage[]): IncomingMessage[] {
  const now = Date.now()
  return messages.map(msg => {
    if (
      msg.messageType !== 'agent' ||
      !msg.taskStatus ||
      isTerminalState(msg.taskStatus as TaskState)
    ) {
      return msg
    }

    // Already-answered HITL: the user has responded; this is logically complete
    // even though taskStatus is still 'input-required'.  Skip stale detection
    // to prevent marking them as expired on page refresh.
    if (msg.hitlUserAnswer !== undefined) {
      return msg
    }

    const state = msg.taskStatus as TaskState

    if (isInteractiveState(state)) {
      if (msg.hitlExpiresAt) {
        const expiry = new Date(msg.hitlExpiresAt).getTime()
        if (isNaN(expiry) || now <= expiry) return msg
      } else {
        const anchor = new Date(msg.taskUpdatedAt || msg.timestamp).getTime()
        if (isNaN(anchor) || now - anchor <= INTERACTIVE_FALLBACK_THRESHOLD_MS) return msg
      }
      return {
        ...msg,
        taskStatus: 'failed' as TaskState,
        taskError: 'Request expired — no response was received before the deadline',
        content: msg.content || 'Request expired',
      }
    }

    if (isStale(msg.taskUpdatedAt || msg.timestamp, STALE_TASK_THRESHOLD_MS)) {
      return {
        ...msg,
        taskStatus: 'failed' as TaskState,
        taskError: 'Task timed out — no updates received within the expected timeframe',
        content: msg.content || 'Task failed due to timeout',
      }
    }

    return msg
  })
}

function isStale(timestamp: string, thresholdMs: number): boolean {
  const time = new Date(timestamp).getTime()
  if (isNaN(time)) return false
  return Date.now() - time > thresholdMs
}
