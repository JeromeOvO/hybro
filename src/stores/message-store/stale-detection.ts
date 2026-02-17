import type { TaskState } from '@/lib/types/sse'
import { isTerminalState } from '@/lib/types/sse'
import type { IncomingMessage } from './types'

const STALE_TASK_THRESHOLD_MS = 10 * 60 * 1000 // 10 minutes, matches backend

/**
 * Detect tasks that are stuck in non-terminal state beyond the threshold.
 * These are marked as failed with a timeout error.
 *
 * This replaces the stale-task detection in the current messagesQuery.queryFn
 * post-processing. Runs at hydration and reconciliation time.
 */
export function detectAndMarkStaleTasks(messages: IncomingMessage[]): IncomingMessage[] {
  return messages.map(msg => {
    if (
      msg.messageType === 'agent' &&
      msg.taskStatus &&
      !isTerminalState(msg.taskStatus as TaskState) &&
      isStale(msg.taskUpdatedAt || msg.timestamp, STALE_TASK_THRESHOLD_MS)
    ) {
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

/**
 * Check if a timestamp is older than the given threshold.
 */
function isStale(timestamp: string, thresholdMs: number): boolean {
  const time = new Date(timestamp).getTime()
  if (isNaN(time)) return false
  return Date.now() - time > thresholdMs
}
