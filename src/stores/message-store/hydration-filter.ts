import type { IncomingMessage } from './types'

/**
 * Filter out empty/invalid agent messages during DB hydration and reconciliation.
 *
 * Without this filter, resolveDisplayType would resolve agent messages with
 * no content and no task status to 'agent-bubble', resulting in empty agent
 * bubbles in the UI.
 */
export function filterHydrationMessages(messages: IncomingMessage[]): IncomingMessage[] {
  return messages.filter(msg => {
    // Always keep user messages
    if (msg.messageType === 'user') return true

    // Agent messages must have content OR a meaningful task status
    const hasContent = !!msg.content && msg.content.trim().length > 0
    const hasTaskStatus = !!msg.taskStatus

    return hasContent || hasTaskStatus
  })
}
