import React from 'react'
import { useMessageStore } from '@/stores/message-store'
import { selectConversationTurns } from '@/lib/selectors'
import type { ConversationTurnView, ConversationBlock } from '@/lib/selectors/conversation-types'

function blocksEqual(a: ConversationBlock, b: ConversationBlock): boolean {
  if (a.type !== b.type) return false
  switch (a.type) {
    case 'agent_card': {
      const bc = b as typeof a
      return a.agentId === bc.agentId
        && a.taskDescription === bc.taskDescription
        && a.display.label === bc.display.label
        && a.display.tone === bc.display.tone
        && a.display.isAnimated === bc.display.isAnimated
        && a.agentSource === bc.agentSource
    }
    case 'agent_content': {
      const bc = b as typeof a
      if (a.content !== bc.content) return false
      if (a.isStreaming !== bc.isStreaming) return false
      if ((a.artifacts?.length ?? 0) !== (bc.artifacts?.length ?? 0)) return false
      // Compare artifact IDs for stability
      if (a.artifacts && bc.artifacts) {
        for (let i = 0; i < a.artifacts.length; i++) {
          if (a.artifacts[i].artifactId !== bc.artifacts[i].artifactId) return false
        }
      }
      return true
    }
    case 'user_answer': {
      const bc = b as typeof a
      return a.question === bc.question && a.answer === bc.answer
    }
    case 'unresolved_content': {
      const bc = b as typeof a
      return a.entity.id === bc.entity.id && a.entity.content === bc.entity.content
    }
    case 'agent_divider':
      return true
  }
}

function turnsEqual(a: ConversationTurnView[], b: ConversationTurnView[]): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) {
    if (a[i].turnId !== b[i].turnId) return false
    if (a[i].blocks.length !== b[i].blocks.length) return false
    const um1 = a[i].userMessage
    const um2 = b[i].userMessage
    if (um1?.id !== um2?.id) return false
    if (um1?.content !== um2?.content) return false
    if ((um1?.attachments?.length ?? 0) !== (um2?.attachments?.length ?? 0)) return false
    for (let j = 0; j < a[i].blocks.length; j++) {
      if (!blocksEqual(a[i].blocks[j], b[i].blocks[j])) return false
    }
  }
  return true
}

export function useConversationTurnViews(roomId: string): ConversationTurnView[] {
  const prev = React.useRef<ConversationTurnView[]>([])
  return useMessageStore(s => {
    const next = selectConversationTurns(roomId, s.entities, s.orderedIds)
    if (turnsEqual(prev.current, next)) return prev.current
    prev.current = next
    return next
  })
}
