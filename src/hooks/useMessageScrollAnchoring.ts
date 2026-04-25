import { useRef, useEffect, useLayoutEffect, useState, useCallback } from 'react'
import { escapeCssIdent } from '@/lib/room-timeline/message-groups'

interface AnchorEntity {
  messageType: string
  clientRequestId?: string
}

export interface ScrollAnchoringInput {
  scrollContainerRef: React.RefObject<HTMLDivElement | null>
  hydrated: boolean
  roomId: string
  renderedAnchorIds: string[]
  getEntityForAnchor: (id: string) => AnchorEntity | undefined
  contentVersion: number
}

function getLastUserSendKey(
  anchorIds: string[],
  getEntity: (id: string) => AnchorEntity | undefined,
): string | null {
  for (let i = anchorIds.length - 1; i >= 0; i--) {
    const entity = getEntity(anchorIds[i])
    if (entity?.messageType === 'user') {
      return entity.clientRequestId ?? anchorIds[i]
    }
  }
  return null
}

function findLastUserMessageId(
  anchorIds: string[],
  getEntity: (id: string) => AnchorEntity | undefined,
): string | null {
  for (let i = anchorIds.length - 1; i >= 0; i--) {
    const entity = getEntity(anchorIds[i])
    if (entity?.messageType === 'user') {
      return anchorIds[i]
    }
  }
  return null
}

export function useMessageScrollAnchoring({
  scrollContainerRef,
  hydrated,
  roomId,
  renderedAnchorIds,
  getEntityForAnchor,
  contentVersion,
}: ScrollAnchoringInput) {
  const didInitialAnchor = useRef(false)
  const prevLastUserSendKey = useRef<string | null>(null)
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true)

  const lastUserSendKey = getLastUserSendKey(renderedAnchorIds, getEntityForAnchor)

  const checkIfNearBottom = useCallback(() => {
    const container = scrollContainerRef.current
    if (!container) return false
    const threshold = 100
    return container.scrollHeight - container.scrollTop - container.clientHeight < threshold
  }, [scrollContainerRef])

  const scrollToBottom = useCallback(() => {
    const container = scrollContainerRef.current
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior: 'auto' })
    }
  }, [scrollContainerRef])

  const handleScroll = useCallback((event: React.UIEvent<HTMLDivElement>) => {
    if (event.currentTarget.dataset.programmaticScroll === 'true') {
      event.currentTarget.dataset.programmaticScroll = 'false'
      return
    }
    setShouldAutoScroll(checkIfNearBottom())
  }, [checkIfNearBottom])

  // P2: Initial anchor on hydration
  // Room reset is handled synchronously at the top of this effect
  // (not in a separate useEffect) to avoid timing issues —
  // useLayoutEffect runs before useEffect, so a separate reset
  // useEffect would fire AFTER P2 already saw stale didInitialAnchor=true.
  const prevRoomIdRef = useRef(roomId)
  useLayoutEffect(() => {
    let rafId: number | null = null
    let canceled = false

    const cleanup = () => {
      canceled = true
      if (rafId !== null) cancelAnimationFrame(rafId)
    }

    // Synchronous room reset — must happen before the anchor check below
    if (prevRoomIdRef.current !== roomId) {
      prevRoomIdRef.current = roomId
      didInitialAnchor.current = false
      prevLastUserSendKey.current = null
      setShouldAutoScroll(true)
    }

    if (!hydrated || didInitialAnchor.current) return cleanup

    if (renderedAnchorIds.length === 0) {
      didInitialAnchor.current = true
      prevLastUserSendKey.current = null
      setShouldAutoScroll(true)
      return cleanup
    }

    const lastUserMsgId = findLastUserMessageId(renderedAnchorIds, getEntityForAnchor)

    const completeAnchor = () => {
      didInitialAnchor.current = true
      prevLastUserSendKey.current = lastUserSendKey ?? null
      setShouldAutoScroll(checkIfNearBottom())
    }

    if (lastUserMsgId) {
      const el = scrollContainerRef.current?.querySelector(
        `[data-message-id="${escapeCssIdent(lastUserMsgId)}"]`
      )
      if (!el) {
        rafId = requestAnimationFrame(() => {
          if (canceled || didInitialAnchor.current) return
          const retryEl = scrollContainerRef.current?.querySelector(
            `[data-message-id="${escapeCssIdent(lastUserMsgId)}"]`
          )
          if (!retryEl) return
          retryEl.scrollIntoView({ block: 'start', behavior: 'auto' })
          completeAnchor()
        })
        return cleanup
      }
      el.scrollIntoView({ block: 'start', behavior: 'auto' })
    }

    completeAnchor()
    return cleanup
  }, [hydrated, roomId, renderedAnchorIds.length, lastUserSendKey, contentVersion, getEntityForAnchor, scrollContainerRef, checkIfNearBottom])

  // P1: Force scroll on new user send
  useEffect(() => {
    if (!hydrated || !didInitialAnchor.current) return
    if (!lastUserSendKey) return
    if (lastUserSendKey === prevLastUserSendKey.current) return

    scrollToBottom()
    prevLastUserSendKey.current = lastUserSendKey
    setShouldAutoScroll(true)
  }, [lastUserSendKey, hydrated, scrollToBottom])

  // AI streaming: scroll follow on contentVersion change
  useEffect(() => {
    if (!hydrated || !didInitialAnchor.current) return
    if (!shouldAutoScroll) return

    const container = scrollContainerRef.current
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior: 'auto' })
    }
  }, [contentVersion, hydrated, shouldAutoScroll, scrollContainerRef])

  return {
    shouldAutoScroll,
    handleScroll,
    scrollToBottom,
  }
}
