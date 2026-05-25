import { isSummarySystemAgent } from '@/lib/system-agents'
import { isTerminalState } from '@/lib/types/sse'
import type { MessageEntity } from '@/stores/message-store/types'

/** True when every non-summary agent for this user message has a terminal task status. */
export function allAgentsTerminalForUserMessage(
  entities: Record<string, MessageEntity>,
  roomId: string,
  userMessageId: string,
): boolean {
  const agents = Object.values(entities).filter(
    e =>
      e.roomId === roomId
      && e.messageType === 'agent'
      && !e.isEphemeral
      && e.relatedMessageId === userMessageId
      && !isSummarySystemAgent(e.agentId),
  )
  if (agents.length === 0) return false
  return agents.every(e => e.taskStatus != null && isTerminalState(e.taskStatus))
}
