import type { MessageEntity } from '@/stores/message-store/types'
import type { ConversationTurnView, ConversationBlock } from './conversation-types'
import { getAgentTheme, UNRESOLVED_THEME } from './conversation-types'
import { routeAgentToTurn } from './route-agent'
import { mapAgentDisplayProps } from './map-agent-display'
import { selectAgentHitlState } from './select-hitl'

export function selectConversationTurns(
  roomId: string,
  entities: Record<string, MessageEntity>,
  orderedIds: string[],
): ConversationTurnView[] {
  const userMessageIds = new Set<string>()
  const userEntitiesOrdered: MessageEntity[] = []
  const agentEntities: MessageEntity[] = []
  const ephemeralByClientReqId = new Map<string, MessageEntity>()
  const userMessageIdByClientRequestId = new Map<string, string>()

  for (const id of orderedIds) {
    const e = entities[id]
    if (!e || e.roomId !== roomId) continue

    if (e.messageType === 'user') {
      userMessageIds.add(e.id)
      userEntitiesOrdered.push(e)
      if (e.clientRequestId && !userMessageIdByClientRequestId.has(e.clientRequestId)) {
        userMessageIdByClientRequestId.set(e.clientRequestId, e.id)
      }
    } else if (e.isEphemeral) {
      if (e.clientRequestId) ephemeralByClientReqId.set(e.clientRequestId, e)
    } else {
      agentEntities.push(e)
    }
  }

  // Route agents to turns
  const turnBlocks = new Map<string, ConversationBlock[]>()
  const unresolvedBlocks: ConversationBlock[] = []

  // Track which clientRequestIds have real agents (for dedup)
  const clientReqIdsWithRealAgent = new Set<string>()
  for (const agent of agentEntities) {
    if (agent.clientRequestId) clientReqIdsWithRealAgent.add(agent.clientRequestId)
  }

  for (const agent of agentEntities) {
    const targetTurn = routeAgentToTurn(
      agent,
      userMessageIds,
      entities,
      userMessageIdByClientRequestId,
    )

    const blocks = targetTurn === 'unresolved'
      ? unresolvedBlocks
      : (turnBlocks.get(targetTurn) ?? (() => { const b: ConversationBlock[] = []; turnBlocks.set(targetTurn, b); return b })())

    const theme = targetTurn === 'unresolved'
      ? UNRESOLVED_THEME
      : getAgentTheme(agent.agentId, agent.senderName)

    // Agent card
    blocks.push({
      type: 'agent_card',
      messageId: agent.id,
      agentId: agent.agentId ?? agent.id,
      agentName: agent.senderName,
      display: mapAgentDisplayProps(agent),
      taskDescription: agent.taskContent ?? agent.taskStatusMessage ?? '',
      theme,
      agentSource: agent.agentSource,
    })

    // Agent content (if non-empty content or has artifacts)
    const content = (agent.content ?? '').trim()
    const hasArtifacts = agent.artifacts && agent.artifacts.length > 0
    if (content || hasArtifacts) {
      const isStreaming = agent.taskStatus === 'working' && content.length > 0
      blocks.push({
        type: 'agent_content',
        agentId: agent.agentId ?? agent.id,
        agentName: agent.senderName,
        content,
        isStreaming,
        artifacts: agent.artifacts,
      })
    }

    // HITL user answer record
    const hitl = selectAgentHitlState(agent)
    if (hitl && hitl.resolved && hitl.answer) {
      blocks.push({
        type: 'user_answer',
        agentName: agent.senderName,
        question: hitl.question,
        answer: hitl.answer,
      })
    }
  }

  // Add synthetic working cards for ephemeral placeholders without real agents
  for (const [crId, eph] of ephemeralByClientReqId) {
    if (clientReqIdsWithRealAgent.has(crId)) continue

    const targetTurn = userMessageIdByClientRequestId.get(crId)
    if (!targetTurn) continue

    const blocks = turnBlocks.get(targetTurn) ?? (() => { const b: ConversationBlock[] = []; turnBlocks.set(targetTurn, b); return b })()
    blocks.push({
      type: 'agent_card',
      messageId: eph.id,
      agentId: eph.id,
      agentName: eph.senderName,
      display: { label: 'Working', tone: 'accent', isAnimated: true, ariaLabel: `${eph.senderName} — Working` },
      taskDescription: eph.taskContent ?? '',
      theme: getAgentTheme(undefined, eph.senderName),
    })
  }

  // Insert agent dividers between different agents in each turn
  for (const [, blocks] of turnBlocks) {
    insertDividers(blocks)
  }
  insertDividers(unresolvedBlocks)

  // Build turn views in user message order
  const turns: ConversationTurnView[] = []
  for (const user of userEntitiesOrdered) {
    turns.push({
      turnId: user.id,
      userMessage: user,
      blocks: turnBlocks.get(user.id) ?? [],
    })
  }

  if (unresolvedBlocks.length > 0) {
    turns.push({
      turnId: '__unresolved__',
      userMessage: null,
      blocks: unresolvedBlocks,
    })
  }

  return turns
}

function insertDividers(blocks: ConversationBlock[]): void {
  let lastAgentId: string | undefined
  let i = 0
  while (i < blocks.length) {
    const block = blocks[i]
    if (block.type === 'agent_card') {
      if (lastAgentId !== undefined && block.agentId !== lastAgentId) {
        blocks.splice(i, 0, { type: 'agent_divider' })
        i++
      }
      lastAgentId = block.agentId
    }
    i++
  }
}
