'use client'

import { useLayoutEffect, useRef, type RefObject } from 'react'
import { scrollToContentEnd } from '@/lib/conversation/content-end-scroll'
import { useStreamingStore } from '@/stores/streaming-store'

interface UsePrimaryStreamScrollOptions {
  scrollRef: RefObject<HTMLDivElement | null>
  primarySurfaceRef: RefObject<HTMLDivElement | null>
  primaryStreamMessageId?: string
  tailFollowRef: RefObject<boolean>
  programmaticScrollRef: RefObject<boolean>
  markProgrammaticScroll: () => void
  /** When true, focus-scroll owns the viewport — tail-follow is disabled. */
  focusModeRef?: RefObject<boolean>
  enabled?: boolean
}

function followContentEndTail(
  scrollEl: HTMLElement,
  tailFollowRef: RefObject<boolean>,
  programmaticScrollRef: RefObject<boolean>,
  markProgrammaticScroll: () => void,
  focusModeRef?: RefObject<boolean>,
  behavior: ScrollBehavior = 'auto',
): void {
  if (!tailFollowRef.current) return
  if (focusModeRef?.current) return
  programmaticScrollRef.current = true
  markProgrammaticScroll()
  scrollToContentEnd(scrollEl, behavior)
}

export function usePrimaryStreamScroll({
  scrollRef,
  primarySurfaceRef,
  primaryStreamMessageId,
  tailFollowRef,
  programmaticScrollRef,
  markProgrammaticScroll,
  focusModeRef,
  enabled = true,
}: UsePrimaryStreamScrollOptions) {
  const bufferText = useStreamingStore(s =>
    primaryStreamMessageId ? s.buffers[primaryStreamMessageId]?.text : undefined,
  )
  const bufferUpdatedAt = useStreamingStore(s =>
    primaryStreamMessageId ? s.buffers[primaryStreamMessageId]?.lastUpdatedAt : undefined,
  )

  const rafRef = useRef<number | null>(null)

  const followPrimary = (behavior: ScrollBehavior = 'auto') => {
    const scrollEl = scrollRef.current
    if (!scrollEl) return
    followContentEndTail(
      scrollEl,
      tailFollowRef,
      programmaticScrollRef,
      markProgrammaticScroll,
      focusModeRef,
      behavior,
    )
  }

  useLayoutEffect(() => {
    if (!enabled || !primaryStreamMessageId) return

    if (rafRef.current != null) cancelAnimationFrame(rafRef.current)
    rafRef.current = requestAnimationFrame(() => {
      followPrimary('auto')
      rafRef.current = null
    })

    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current)
    }
  }, [bufferText, bufferUpdatedAt, primaryStreamMessageId, enabled])

  useLayoutEffect(() => {
    if (!enabled) return

    const primaryEl = primarySurfaceRef.current
    const scrollEl = scrollRef.current
    if (!primaryEl || !scrollEl) return

    const observer = new ResizeObserver(() => {
      followContentEndTail(
        scrollEl,
        tailFollowRef,
        programmaticScrollRef,
        markProgrammaticScroll,
        focusModeRef,
        'auto',
      )
    })

    observer.observe(primaryEl)
    return () => observer.disconnect()
  }, [
    primaryStreamMessageId,
    scrollRef,
    primarySurfaceRef,
    tailFollowRef,
    programmaticScrollRef,
    markProgrammaticScroll,
    focusModeRef,
    enabled,
  ])
}
