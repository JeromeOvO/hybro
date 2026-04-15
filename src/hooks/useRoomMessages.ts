import { useRef } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { useMessageStore } from '@/stores/message-store'
import type { MessageEntity } from '@/stores/message-store'
import { buildTurnsIncremental } from '@/lib/room-timeline/build-turns'
import { getEvents } from '@/lib/room-timeline/event-log'
import type { TurnViewModel } from '@/lib/room-timeline/types'

/**
 * Ordered message IDs only.
 * Only re-renders when IDs are added/removed/reordered.
 * Use this for the primary render path (RoomMessages list).
 */
export function useOrderedIds(): string[] {
  return useMessageStore(s => s.orderedIds)
}

/**
 * Full ordered message list (convenience).
 * Re-renders on any entity change — use for derived computations
 * (e.g. lastAgentMessageId), NOT for the primary render path.
 */
export function useOrderedMessages(): MessageEntity[] {
  return useMessageStore(
    useShallow(s => s.orderedIds.map(id => s.entities[id]).filter(Boolean))
  )
}

/**
 * Single message by ID.
 * Only re-renders when that specific entity changes.
 * Use this inside MemoizedMessage for per-message isolation.
 */
export function useMessage(id: string): MessageEntity | undefined {
  return useMessageStore(s => s.entities[id])
}

/**
 * Message count only (for auto-scroll logic).
 * Only re-renders when messages are added or removed.
 */
export function useMessageCount(): number {
  return useMessageStore(s => s.orderedIds.length)
}

/**
 * Whether initial DB load is complete.
 * Use to show loading state before messages are available.
 */
export function useMessagesHydrated(): boolean {
  return useMessageStore(s => s.hydratedFromDb)
}

/**
 * Active HITL requests — entities with unresolved HITL state.
 * Re-renders only when the set of active HITL entities changes.
 */
export function useActiveHitlRequests(): MessageEntity[] {
  return useMessageStore(
    useShallow(s => {
      const all = s.orderedIds
        .map(id => s.entities[id])
        .filter((e): e is MessageEntity => !!e && !!e.hitlRequestId)

      // Collect group IDs that still have at least one unanswered question
      const activeGroupIds = new Set<string>()
      for (const e of all) {
        if (e.hitlGroupId && !e.hitlResolved && !e.hitlUserAnswer) {
          activeGroupIds.add(e.hitlGroupId)
        }
      }

      return all.filter(e => {
        // Non-grouped: show only if unresolved
        if (!e.hitlGroupId) return !e.hitlResolved
        // Grouped: show all questions in groups that still have unanswered ones
        return activeGroupIds.has(e.hitlGroupId)
      })
    })
  )
}

/**
 * The current room ID tracked by the message store.
 */
export function useMessageStoreRoomId(): string | null {
  return useMessageStore(s => s.roomId)
}

/**
 * Derive conversation turns from the message store.
 * Uses incremental derivation to preserve referential identity.
 */
export function useConversationTurns(): TurnViewModel[] {
  const prevTurnsRef = useRef<TurnViewModel[]>([])

  return useMessageStore(
    useShallow(s => {
      const events = s.roomId ? getEvents(s.roomId) : []
      const turns = buildTurnsIncremental(
        prevTurnsRef.current,
        s.entities,
        s.orderedIds,
        events,
      )
      prevTurnsRef.current = turns
      return turns
    }),
  )
}

export function useActiveTurn(): TurnViewModel | undefined {
  const turns = useConversationTurns()
  return turns.length > 0 ? turns[turns.length - 1] : undefined
}

export function useTurnById(turnId: string): TurnViewModel | undefined {
  const turns = useConversationTurns()
  return turns.find(t => t.id === turnId)
}

export function useHitlTurnContext(hitlMessageId: string | null): {
  turnId: string
  turnIndex: number
  turnLabel: string
} | null {
  const turns = useConversationTurns()
  if (!hitlMessageId) return null

  for (let i = 0; i < turns.length; i++) {
    const turn = turns[i]
    const ownsHitl = turn.agentResults.some(r => r.messageId === hitlMessageId)
    if (ownsHitl) {
      const preview = turn.userContent
        ? turn.userContent.slice(0, 40) + (turn.userContent.length > 40 ? '...' : '')
        : 'System turn'
      return {
        turnId: turn.id,
        turnIndex: i,
        turnLabel: `Turn ${i + 1}: ${preview}`,
      }
    }
  }
  return null
}
