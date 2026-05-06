import type { MessageEntity } from '@/stores/message-store/types'
import { TASK_STATE } from '@/lib/types/sse'
import type { AgentResponseDetail } from './conversation-types'
import { getAgentTheme, UNRESOLVED_THEME } from './conversation-types'
import { mapAgentDisplayProps } from './map-agent-display'
import { buildClientRequestUserMessageIndex, routeAgentToTurn } from './route-agent'

function findRequestMessage(
  agent: MessageEntity,
  entities: Record<string, MessageEntity>,
  orderedIds: string[],
): MessageEntity | null {
  if (agent.relatedMessageId) {
    const related = entities[agent.relatedMessageId]
    if (related?.messageType === 'user' && related.roomId === agent.roomId) return related
  }

  if (agent.clientRequestId) {
    for (const id of orderedIds) {
      const candidate = entities[id]
      if (
        candidate?.roomId === agent.roomId &&
        candidate.messageType === 'user' &&
        candidate.clientRequestId === agent.clientRequestId
      ) {
        return candidate
      }
    }
  }

  const userMessageIds = new Set<string>()
  for (const id of orderedIds) {
    const candidate = entities[id]
    if (candidate?.roomId === agent.roomId && candidate.messageType === 'user') {
      userMessageIds.add(candidate.id)
    }
  }

  const routedTurnId = routeAgentToTurn(
    agent,
    userMessageIds,
    entities,
    buildClientRequestUserMessageIndex(userMessageIds, entities),
  )
  if (routedTurnId !== 'unresolved') {
    const routed = entities[routedTurnId]
    if (routed?.messageType === 'user' && routed.roomId === agent.roomId) return routed
  }

  return null
}

export function selectAgentResponseDetail(
  roomId: string,
  messageId: string | null | undefined,
  entities: Record<string, MessageEntity>,
  orderedIds: string[],
): AgentResponseDetail | null {
  if (!messageId) return null
  const agent = entities[messageId]
  if (!agent || agent.roomId !== roomId || agent.messageType !== 'agent') return null

  const theme = agent.relatedMessageId || agent.clientRequestId
    ? getAgentTheme(agent.agentId, agent.senderName)
    : UNRESOLVED_THEME

  return {
    messageId: agent.id,
    agentId: agent.agentId ?? agent.id,
    agentName: agent.senderName,
    display: mapAgentDisplayProps(agent),
    taskDescription: agent.taskContent ?? agent.taskStatusMessage ?? '',
    theme,
    content: (agent.content ?? '').trim(),
    isStreaming: agent.taskStatus === TASK_STATE.WORKING && (agent.content ?? '').trim().length > 0,
    artifacts: agent.artifacts,
    taskStatus: agent.taskStatus,
    taskStatusMessage: agent.taskStatusMessage,
    taskError: agent.taskError,
    requestMessage: findRequestMessage(agent, entities, orderedIds),
    agentSource: agent.agentSource,
  }
}
