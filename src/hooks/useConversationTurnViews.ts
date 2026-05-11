import React from 'react'
import { useMessageStore } from '@/stores/message-store'
import { useStreamingStore } from '@/stores/streaming-store'
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
        && a.isStreaming === bc.isStreaming
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

/**
 * Subscribes to both messageStore (entity state) and streamingStore (live
 * chunk buffers) and assembles ConversationTurnView[] for rendering.
 *
 * During streaming, only streamingStore fires on every chunk — messageStore
 * is completely quiet. After task_update fires the checkpoint, both stores
 * update together in one re-render: buffer cleared, entity has canonical content.
 *
 * The useMemo ensures selectConversationTurns only re-runs when either store
 * actually changes. turnsEqual provides reference stability: a new array is
 * only returned (triggering downstream re-renders) when visible content changed.
 */
export function useConversationTurnViews(roomId: string): ConversationTurnView[] {
  const prev = React.useRef<ConversationTurnView[]>([])

  // Independent subscriptions — React re-renders when EITHER store changes.
  // streamingStore fires on every chunk; messageStore fires on entity writes.
  // Subscribe to each field individually to avoid inline-object reference churn
  // (an inline `s => ({ a, b })` creates a new object every call, which Zustand
  // treats as always-changed and produces an infinite re-render loop).
  const buffers = useStreamingStore(s => s.buffers)
  const entities = useMessageStore(s => s.entities)
  const orderedIds = useMessageStore(s => s.orderedIds)

  const next = React.useMemo(
    () => selectConversationTurns(roomId, entities, orderedIds, buffers),
    [roomId, entities, orderedIds, buffers],
  )

  // Reference-stable: only propagate a new array when content actually changed.
  const stableNext = turnsEqual(prev.current, next) ? prev.current : next
  prev.current = stableNext
  return stableNext
}
