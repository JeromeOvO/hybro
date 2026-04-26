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

type ScrollMode =
  | 'initial-anchor'
  | 'initial-settling'
  | 'user-anchor'
  | 'bottom-follow'
  | 'manual'

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Coordinate helpers (rect-based, no offsetTop)
// ---------------------------------------------------------------------------

function scrollElementToContainerTop(container: HTMLElement, el: HTMLElement) {
  const offset = el.getBoundingClientRect().top - container.getBoundingClientRect().top
  container.scrollTo({ top: Math.max(0, container.scrollTop + offset), behavior: 'auto' })
}

function scrollToContentEnd(container: HTMLElement) {
  const contentEnd = container.querySelector('[data-content-end]') as HTMLElement | null
  if (contentEnd) {
    const offset = contentEnd.getBoundingClientRect().top - container.getBoundingClientRect().top
    const target = container.scrollTop + offset - container.clientHeight
    container.scrollTo({ top: Math.max(0, target), behavior: 'auto' })
  } else {
    container.scrollTo({ top: Math.max(0, container.scrollHeight - container.clientHeight), behavior: 'auto' })
  }
}

function isNearContentEnd(container: HTMLElement): boolean {
  const contentEnd = container.querySelector('[data-content-end]') as HTMLElement | null
  if (!contentEnd) {
    return container.scrollHeight - container.scrollTop - container.clientHeight < 100
  }
  const offset = contentEnd.getBoundingClientRect().top - container.getBoundingClientRect().top
  return Math.abs(offset - container.clientHeight) < 100
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useMessageScrollAnchoring({
  scrollContainerRef,
  hydrated,
  roomId,
  renderedAnchorIds,
  getEntityForAnchor,
  contentVersion,
}: ScrollAnchoringInput) {
  const lastUserSendKey = getLastUserSendKey(renderedAnchorIds, getEntityForAnchor)

  // --- Refs ---
  const modeRef = useRef<ScrollMode>('initial-anchor')
  const initialPassDoneRef = useRef(false)
  const prevLastUserSendKey = useRef<string | null>(null)
  const prevRoomIdRef = useRef(roomId)
  const reflowRoRef = useRef<ResizeObserver | null>(null)
  const reflowTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const suppressScrollUntilRef = useRef(0)
  const pendingRafRef = useRef<number | null>(null)
  const latestLastUserSendKeyRef = useRef(lastUserSendKey)
  const latestGetEntityRef = useRef(getEntityForAnchor)
  const latestAnchorIdsRef = useRef(renderedAnchorIds)
  const [showScrollButton, setShowScrollButton] = useState(false)

  // Sync latest refs every render (before effects)
  latestLastUserSendKeyRef.current = lastUserSendKey
  latestGetEntityRef.current = getEntityForAnchor
  latestAnchorIdsRef.current = renderedAnchorIds

  // --- Internal helpers ---

  function killSettlingObserver() {
    if (reflowRoRef.current) { reflowRoRef.current.disconnect(); reflowRoRef.current = null }
    if (reflowTimerRef.current) { clearTimeout(reflowTimerRef.current); reflowTimerRef.current = null }
  }

  function markProgrammaticScroll() {
    suppressScrollUntilRef.current = performance.now() + 150
  }

  function updateButtonVisibility() {
    const container = scrollContainerRef.current
    const mode = modeRef.current
    const shouldShow = (mode === 'manual' || mode === 'user-anchor')
      && container != null
      && !isNearContentEnd(container)
    setShowScrollButton(shouldShow)
  }

  function transitionTo(next: ScrollMode) {
    if (next !== 'initial-settling') killSettlingObserver()
    modeRef.current = next
    updateButtonVisibility()
  }

  // ---------------------------------------------------------------------------
  // Effect 1 (useLayoutEffect) — Room reset + Initial Anchor
  // ---------------------------------------------------------------------------
  useLayoutEffect(() => {
    const cleanup = () => {
      if (pendingRafRef.current !== null) {
        cancelAnimationFrame(pendingRafRef.current)
        pendingRafRef.current = null
      }
    }

    // 1. Room switch detection (synchronous)
    if (prevRoomIdRef.current !== roomId) {
      killSettlingObserver()
      modeRef.current = 'initial-anchor'
      initialPassDoneRef.current = false
      prevLastUserSendKey.current = null
      prevRoomIdRef.current = roomId
    }

    // 2. Guard: not in initial-anchor mode
    if (modeRef.current !== 'initial-anchor') return cleanup

    // 3. Guard: not hydrated
    if (!hydrated) return cleanup

    // 4-5. Empty room handling
    if (renderedAnchorIds.length === 0) {
      if (!initialPassDoneRef.current) {
        initialPassDoneRef.current = true
      }
      return cleanup
    }

    // 6. Find last user message (use latest refs for callback closures)
    const lastUserMsgId = findLastUserMessageId(latestAnchorIdsRef.current, latestGetEntityRef.current)

    // 7. AI-only room (no user messages)
    if (lastUserMsgId === null) {
      prevLastUserSendKey.current = null
      initialPassDoneRef.current = true
      transitionTo('manual')
      return cleanup
    }

    const container = scrollContainerRef.current
    if (!container) return cleanup

    // Helper to complete initial anchoring and set up settling observer
    const completeInitialAnchor = () => {
      prevLastUserSendKey.current = latestLastUserSendKeyRef.current
      initialPassDoneRef.current = true
      transitionTo('initial-settling')

      if (typeof ResizeObserver !== 'undefined') {
        const anchorContainer = scrollContainerRef.current
        if (!anchorContainer) { transitionTo('manual'); return }

        const ro = new ResizeObserver(() => {
          if (modeRef.current !== 'initial-settling') return
          const c = scrollContainerRef.current
          if (!c) return
          const currentAnchorIds = latestAnchorIdsRef.current
          const currentGetEntity = latestGetEntityRef.current
          const msgId = findLastUserMessageId(currentAnchorIds, currentGetEntity)
          if (!msgId) return
          const el = c.querySelector(
            `[data-message-id="${escapeCssIdent(msgId)}"]`
          ) as HTMLElement | null
          if (!el) return
          markProgrammaticScroll()
          scrollElementToContainerTop(c, el)
        })

        const inner = anchorContainer.firstElementChild
        if (inner) ro.observe(inner)

        reflowRoRef.current = ro
        reflowTimerRef.current = setTimeout(() => {
          transitionTo('manual')
        }, 3000)
      } else {
        transitionTo('manual')
      }
    }

    // 8. Query DOM for user message element
    const el = container.querySelector(
      `[data-message-id="${escapeCssIdent(lastUserMsgId)}"]`
    ) as HTMLElement | null

    // 9. Element found: scroll and complete
    if (el) {
      markProgrammaticScroll()
      scrollElementToContainerTop(container, el)
      completeInitialAnchor()
      return cleanup
    }

    // 10. Element NOT found: rAF retry loop (max 5 frames)
    let retryCount = 0
    const maxRetries = 5

    const tryFind = () => {
      pendingRafRef.current = null
      retryCount++
      if (modeRef.current !== 'initial-anchor') return

      const c = scrollContainerRef.current
      if (!c) return

      const retryEl = c.querySelector(
        `[data-message-id="${escapeCssIdent(lastUserMsgId)}"]`
      ) as HTMLElement | null

      if (retryEl) {
        markProgrammaticScroll()
        scrollElementToContainerTop(c, retryEl)
        completeInitialAnchor()
        return
      }

      if (retryCount < maxRetries) {
        pendingRafRef.current = requestAnimationFrame(tryFind)
      } else {
        initialPassDoneRef.current = true
        transitionTo('manual')
      }
    }

    pendingRafRef.current = requestAnimationFrame(tryFind)
    return cleanup
  }, [hydrated, roomId, renderedAnchorIds.length, scrollContainerRef])

  // ---------------------------------------------------------------------------
  // Effect 2 (useEffect) — User Send (P1)
  // ---------------------------------------------------------------------------
  useEffect(() => {
    // 1. Guard: not hydrated
    if (!hydrated) return

    // 2. Guard: P2 not done yet
    if (modeRef.current === 'initial-anchor' && !initialPassDoneRef.current) return

    // 3. Guard: dedup (handles temp→real id swap)
    if (lastUserSendKey === prevLastUserSendKey.current) return

    // 4. Guard: no user send key
    if (!lastUserSendKey) return

    const container = scrollContainerRef.current
    if (!container) return

    // 5. Find last user message DOM element (use latest refs)
    const lastUserMsgId = findLastUserMessageId(latestAnchorIdsRef.current, latestGetEntityRef.current)

    if (lastUserMsgId) {
      const el = container.querySelector(
        `[data-message-id="${escapeCssIdent(lastUserMsgId)}"]`
      ) as HTMLElement | null

      if (el) {
        // 6. Element found
        markProgrammaticScroll()
        scrollElementToContainerTop(container, el)
      } else {
        // 7. Element not found: fallback
        markProgrammaticScroll()
        scrollToContentEnd(container)
      }
    } else {
      markProgrammaticScroll()
      scrollToContentEnd(container)
    }

    // 8. Transition to user-anchor
    transitionTo('user-anchor')

    // 9. Update send key
    prevLastUserSendKey.current = lastUserSendKey
  }, [lastUserSendKey, hydrated, scrollContainerRef, renderedAnchorIds, getEntityForAnchor])

  // ---------------------------------------------------------------------------
  // Effect 3 (useEffect) — Streaming + Button visibility
  // ---------------------------------------------------------------------------
  useEffect(() => {
    // 1. Guard: not hydrated
    if (!hydrated) return

    // 2. Bottom-follow: auto-scroll on content changes
    if (modeRef.current === 'bottom-follow') {
      const container = scrollContainerRef.current
      if (container) {
        markProgrammaticScroll()
        scrollToContentEnd(container)
      }
    }

    // 3. Always update button visibility
    updateButtonVisibility()
  }, [contentVersion, hydrated, scrollContainerRef])

  // ---------------------------------------------------------------------------
  // handleScroll — time-based suppression, no event arg needed
  // ---------------------------------------------------------------------------
  const handleScroll = useCallback(() => {
    if (performance.now() < suppressScrollUntilRef.current) return
    const container = scrollContainerRef.current
    if (!container) return

    const mode = modeRef.current
    if (mode === 'initial-anchor' || mode === 'initial-settling') {
      transitionTo('manual')
      return
    }

    if (isNearContentEnd(container)) {
      transitionTo('bottom-follow')
    } else {
      transitionTo('manual')
    }
  }, [scrollContainerRef])

  // ---------------------------------------------------------------------------
  // scrollToBottom
  // ---------------------------------------------------------------------------
  const scrollToBottom = useCallback(() => {
    const container = scrollContainerRef.current
    if (!container) return
    markProgrammaticScroll()
    scrollToContentEnd(container)
    transitionTo('bottom-follow')
  }, [scrollContainerRef])

  // ---------------------------------------------------------------------------
  // Unmount cleanup
  // ---------------------------------------------------------------------------
  useEffect(() => {
    return () => {
      killSettlingObserver()
      if (pendingRafRef.current !== null) cancelAnimationFrame(pendingRafRef.current)
    }
  }, [])

  return {
    shouldAutoScroll: !showScrollButton,
    handleScroll,
    scrollToBottom,
  }
}
