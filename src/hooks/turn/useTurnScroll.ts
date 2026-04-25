import { useEffect, useState, useCallback } from 'react'
import { useTurnEventStore } from '@/stores/turn-event-store'
import { useMessageStore } from '@/stores/message-store'
import { useMessageScrollAnchoring } from '@/hooks/useMessageScrollAnchoring'

export function useTurnScroll(scrollContainerRef: React.RefObject<HTMLDivElement | null>) {
  const orderedTurnIds = useTurnEventStore(s => s.orderedTurnIds)
  const turnLogs = useTurnEventStore(s => s.turnLogs)
  const hydrated = useTurnEventStore(s => s.hydrated)
  const roomId = useMessageStore(s => s.roomId) ?? ''

  const [contentVersion, setContentVersion] = useState(0)

  const activeTurnId = orderedTurnIds[orderedTurnIds.length - 1]

  useEffect(() => {
    if (!activeTurnId) return

    const turnLog = turnLogs.get(activeTurnId)
    if (!turnLog) return

    const unsubscribe = turnLog.subscribe(() => {
      setContentVersion(v => v + 1)
    })
    return unsubscribe
  }, [activeTurnId, turnLogs])

  const getEntityForAnchor = useCallback(
    (turnId: string) => {
      const turnLog = turnLogs.get(turnId)
      if (!turnLog) return undefined
      const startEvent = turnLog.getEvents().find(e => e.type === 'turn_started')
      return {
        messageType: 'user' as const,
        clientRequestId: startEvent?.clientRequestId ?? turnId,
      }
    },
    [turnLogs],
  )

  const { shouldAutoScroll, handleScroll, scrollToBottom } = useMessageScrollAnchoring({
    scrollContainerRef,
    hydrated,
    roomId,
    renderedAnchorIds: orderedTurnIds,
    getEntityForAnchor,
    contentVersion,
  })

  return {
    shouldAutoScroll,
    handleScroll,
    scrollToBottom,
  }
}
