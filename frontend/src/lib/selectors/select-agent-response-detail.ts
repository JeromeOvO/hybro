import type { MessageEntity } from '@/stores/message-store/types'
import type { StreamBuffer } from '@/stores/streaming-store'
import { isTerminalState } from '@/lib/types/sse'
import {
  isBufferStreaming,
  resolveDetailArtifacts,
  resolveEntityStreaming,
  resolveStreamText,
} from '@/lib/streaming/display'
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
  buffer?: StreamBuffer,
): AgentResponseDetail | null {
  if (!messageId) return null
  const agent = entities[messageId]
  if (!agent || agent.roomId !== roomId || agent.messageType !== 'agent') return null

  const theme = agent.relatedMessageId || agent.clientRequestId
    ? getAgentTheme(agent.agentId, agent.senderName)
    : UNRESOLVED_THEME

  const isTerminal = agent.taskStatus != null && isTerminalState(agent.taskStatus)
  const effectiveBuffer = isTerminal ? undefined : buffer

  const content = resolveStreamText(effectiveBuffer, (agent.content ?? '').trim())
  const isStreaming = resolveEntityStreaming(effectiveBuffer, agent.taskStatus)
  const artifacts = resolveDetailArtifacts(effectiveBuffer, agent.artifacts)

  const isActivelyWorking = agent.taskStatus == null || agent.taskStatus === 'working' || agent.taskStatus === 'submitted'
  const staticDescription = typeof agent.taskStatusMessage === 'string'
    ? agent.taskStatusMessage.trim()
    : ''
  const dispatchDescription = typeof agent.dispatchText === 'string'
    ? agent.dispatchText.trim()
    : ''
  const taskDescription = dispatchDescription
    || staticDescription
    || (isActivelyWorking ? 'Working on your request…' : '')

  const baseDisplay = mapAgentDisplayProps(agent)
  const display = isBufferStreaming(effectiveBuffer)
    ? { ...baseDisplay, label: 'Streaming' }
    : baseDisplay

  return {
    messageId: agent.id,
    // Only a catalog-backed Agent ID is profile identity. Sender labels and
    // opaque card/message IDs must never become profile routes.
    agentId: agent.agentId,
    agentName: agent.senderName,
    display,
    taskDescription,
    theme,
    content,
    isStreaming,
    artifacts,
    taskStatus: agent.taskStatus,
    taskStatusMessage: agent.taskStatusMessage,
    taskError: agent.taskError,
    requestMessage: findRequestMessage(agent, entities, orderedIds),
    agentSource: agent.agentSource,
  }
}
