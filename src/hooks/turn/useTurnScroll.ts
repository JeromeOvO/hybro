import { useRef, useEffect, useCallback, useState } from 'react'
import { useTurnEventStore } from '@/stores/turn-event-store'

export function useTurnScroll(scrollContainerRef: React.RefObject<HTMLDivElement | null>) {
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const prevTurnCountRef = useRef(0)

  const orderedTurnIds = useTurnEventStore(s => s.orderedTurnIds)

  const scrollToBottom = useCallback(() => {
    const container = scrollContainerRef.current
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' })
    }
  }, [scrollContainerRef])

  const checkIfNearBottom = useCallback(() => {
    const container = scrollContainerRef.current
    if (!container) return false
    const threshold = 100
    return container.scrollHeight - container.scrollTop - container.clientHeight < threshold
  }, [scrollContainerRef])

  const handleScroll = useCallback((event: React.UIEvent<HTMLDivElement>) => {
    if (event.currentTarget.dataset.programmaticScroll === 'true') {
      event.currentTarget.dataset.programmaticScroll = 'false'
      return
    }
    setShouldAutoScroll(checkIfNearBottom())
  }, [checkIfNearBottom])

  useEffect(() => {
    if (orderedTurnIds.length > prevTurnCountRef.current && shouldAutoScroll) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'auto' })
    }
    prevTurnCountRef.current = orderedTurnIds.length
  }, [orderedTurnIds.length, shouldAutoScroll])

  return {
    messagesEndRef,
    shouldAutoScroll,
    handleScroll,
    scrollToBottom,
  }
}
