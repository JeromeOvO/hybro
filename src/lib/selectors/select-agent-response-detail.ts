import type { MessageEntity } from '@/stores/message-store/types'
import type { StreamBuffer } from '@/stores/streaming-store'
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
  buffers: Record<string, StreamBuffer> = {},
): AgentResponseDetail | null {
  if (!messageId) return null
  const agent = entities[messageId]
  if (!agent || agent.roomId !== roomId || agent.messageType !== 'agent') return null

  const theme = agent.relatedMessageId || agent.clientRequestId
    ? getAgentTheme(agent.agentId, agent.senderName)
    : UNRESOLVED_THEME

  const buffer = buffers[agent.id]
  const content = buffer ? buffer.text : (agent.content ?? '').trim()
  // isStreaming: active buffer takes precedence; without a buffer fall back to
  // the entity's non-terminal task status so pre-stream "working" state still
  // shows as streaming in the detail pane.
  const isStreaming = buffer
    ? !buffer.isComplete
    : (agent.taskStatus == null || agent.taskStatus === 'working' || agent.taskStatus === 'submitted')
  // While a streaming buffer is active, suppress raw artifacts: buffer.text
  // already contains the extracted text so showing both would duplicate content.
  const artifacts = buffer ? undefined : agent.artifacts

  const isActivelyWorking = agent.taskStatus == null || agent.taskStatus === 'working' || agent.taskStatus === 'submitted'
  const staticDescription = agent.taskContent ?? agent.taskStatusMessage ?? ''
  const taskDescription = staticDescription || (isActivelyWorking ? 'Working on your request…' : '')

  const baseDisplay = mapAgentDisplayProps(agent)
  const display = buffer && !buffer.isComplete && agent.taskStatus === 'working'
    ? { ...baseDisplay, label: 'Streaming' }
    : baseDisplay

  return {
    messageId: agent.id,
    agentId: agent.agentId ?? agent.id,
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
