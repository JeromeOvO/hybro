'use client'

import { useLayoutEffect, useRef, type RefObject } from 'react'
import { isNearContentEnd } from '@/lib/conversation/content-end-scroll'
import { useStreamingStore } from '@/stores/streaming-store'

interface UsePrimaryStreamScrollOptions {
  scrollRef: RefObject<HTMLDivElement | null>
  primarySurfaceRef: RefObject<HTMLDivElement | null>
  primaryStreamMessageId?: string
  userPausedRef: RefObject<boolean>
  programmaticScrollRef: RefObject<boolean>
  /** When true, focus-scroll owns the viewport — tail-follow is disabled. */
  focusModeRef?: RefObject<boolean>
  enabled?: boolean
}

function scrollPrimaryTailIntoView(
  scrollEl: HTMLElement,
  primaryEl: HTMLElement,
  behavior: ScrollBehavior,
) {
  const scrollRect = scrollEl.getBoundingClientRect()
  const primaryRect = primaryEl.getBoundingClientRect()
  const delta = primaryRect.bottom - scrollRect.bottom
  if (delta > 0) {
    scrollEl.scrollBy({ top: delta, behavior })
  }
}

export function usePrimaryStreamScroll({
  scrollRef,
  primarySurfaceRef,
  primaryStreamMessageId,
  userPausedRef,
  programmaticScrollRef,
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
    const primaryEl = primarySurfaceRef.current
    if (!scrollEl || !primaryEl) return
    if (userPausedRef.current) return
    if (focusModeRef?.current) return
    if (!isNearContentEnd(scrollEl)) return

    programmaticScrollRef.current = true
    scrollPrimaryTailIntoView(scrollEl, primaryEl, behavior)
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
      if (userPausedRef.current) return
      if (focusModeRef?.current) return
      if (!isNearContentEnd(scrollEl)) return
      programmaticScrollRef.current = true
      scrollPrimaryTailIntoView(scrollEl, primaryEl, 'auto')
    })

    observer.observe(primaryEl)
    return () => observer.disconnect()
  }, [primaryStreamMessageId, scrollRef, primarySurfaceRef, userPausedRef, programmaticScrollRef, enabled])
}
