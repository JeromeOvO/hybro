'use client'

import { useCallback, useLayoutEffect, useRef, useState, type RefObject } from 'react'
import { escapeCssIdent } from '@/lib/room-timeline/message-groups'
import {
  computeFocusSpacerHeight,
  FOCUS_SCROLL_MIN_SPACER_PX,
  scrollUserMessageToFocus,
} from '@/lib/conversation/focus-scroll'

interface UseTurnFocusScrollOptions {
  scrollRef: RefObject<HTMLDivElement | null>
  frameRef: RefObject<HTMLDivElement | null>
  lastUserMessageId: string | undefined
  localSendSeq: number
  initialHydrationSeq: number
  turnLive: boolean
  contentVersion: number
  userPausedRef: RefObject<boolean>
  programmaticScrollRef: RefObject<boolean>
}

interface SpacerUpdateOptions {
  /** When true, keep the dynamic bottom spacer even if turnLive is still false. */
  focusMode?: boolean
  liveTurn?: boolean
}

export function useTurnFocusScroll({
  scrollRef,
  frameRef,
  lastUserMessageId,
  localSendSeq,
  initialHydrationSeq,
  turnLive,
  contentVersion,
  userPausedRef,
  programmaticScrollRef,
}: UseTurnFocusScrollOptions) {
  const [spacerHeight, setSpacerHeight] = useState(FOCUS_SCROLL_MIN_SPACER_PX)
  const focusModeRef = useRef(false)
  const turnLiveRef = useRef(turnLive)
  const prevLocalSendSeqRef = useRef(localSendSeq)
  const prevInitialHydrationSeqRef = useRef(initialHydrationSeq)
  const prevTurnLiveRef = useRef(turnLive)

  turnLiveRef.current = turnLive

  const updateSpacer = useCallback((options: SpacerUpdateOptions = {}) => {
    const container = scrollRef.current
    const frame = frameRef.current
    if (!container || !frame || !lastUserMessageId) return

    const contentEnd = frame.querySelector('[data-content-end]') as HTMLElement | null
    const userEl = frame.querySelector(
      `[data-message-id="${escapeCssIdent(lastUserMessageId)}"]`,
    ) as HTMLElement | null

    if (!contentEnd || !userEl) return

    const focusMode = options.focusMode ?? focusModeRef.current
    const liveTurn = options.liveTurn ?? turnLiveRef.current

    if (!focusMode && !liveTurn) {
      setSpacerHeight(FOCUS_SCROLL_MIN_SPACER_PX)
      return
    }

    setSpacerHeight(computeFocusSpacerHeight(container, userEl, contentEnd))
  }, [scrollRef, frameRef, lastUserMessageId])

  const focusLastUserMessage = useCallback((options: SpacerUpdateOptions = {}) => {
    const container = scrollRef.current
    const frame = frameRef.current
    if (!container || !frame || !lastUserMessageId) return false

    const userEl = frame.querySelector(
      `[data-message-id="${escapeCssIdent(lastUserMessageId)}"]`,
    ) as HTMLElement | null
    if (!userEl) return false

    programmaticScrollRef.current = true
    scrollUserMessageToFocus(container, userEl)
    updateSpacer({
      focusMode: options.focusMode ?? true,
      liveTurn: options.liveTurn ?? turnLiveRef.current,
    })
    return true
  }, [scrollRef, frameRef, lastUserMessageId, programmaticScrollRef, updateSpacer])

  const scheduleFocusRetries = useCallback((options: SpacerUpdateOptions) => {
    let retries = 0
    const tryFocus = () => {
      retries += 1
      if (focusLastUserMessage(options) || retries >= 5) return
      requestAnimationFrame(tryFocus)
    }
    requestAnimationFrame(tryFocus)
  }, [focusLastUserMessage])

  useLayoutEffect(() => {
    if (localSendSeq === prevLocalSendSeqRef.current) return
    prevLocalSendSeqRef.current = localSendSeq

    focusModeRef.current = true
    userPausedRef.current = false

    const focusOptions: SpacerUpdateOptions = { focusMode: true, liveTurn: turnLiveRef.current }
    if (!focusLastUserMessage(focusOptions)) {
      scheduleFocusRetries(focusOptions)
    }
  }, [localSendSeq, lastUserMessageId, focusLastUserMessage, scheduleFocusRetries, userPausedRef])

  useLayoutEffect(() => {
    if (initialHydrationSeq === prevInitialHydrationSeqRef.current) return
    prevInitialHydrationSeqRef.current = initialHydrationSeq
    if (initialHydrationSeq === 0 || localSendSeq === 0) return

    focusModeRef.current = true
    userPausedRef.current = false
    const focusOptions: SpacerUpdateOptions = { focusMode: true, liveTurn: turnLiveRef.current }
    if (!focusLastUserMessage(focusOptions)) {
      scheduleFocusRetries(focusOptions)
    }
  }, [
    initialHydrationSeq,
    localSendSeq,
    lastUserMessageId,
    focusLastUserMessage,
    scheduleFocusRetries,
    userPausedRef,
  ])

  useLayoutEffect(() => {
    if (turnLive) {
      focusModeRef.current = true
    } else if (prevTurnLiveRef.current && !turnLive) {
      focusModeRef.current = false
    }
    prevTurnLiveRef.current = turnLive
    updateSpacer({ focusMode: focusModeRef.current, liveTurn: turnLive })
  }, [turnLive, contentVersion, lastUserMessageId, updateSpacer])

  useLayoutEffect(() => {
    const frame = frameRef.current
    if (!frame || typeof ResizeObserver === 'undefined') return

    const observer = new ResizeObserver(() => {
      if (!focusModeRef.current && !turnLiveRef.current) return
      if (userPausedRef.current) return
      updateSpacer({ focusMode: focusModeRef.current, liveTurn: turnLiveRef.current })
    })

    observer.observe(frame)
    return () => observer.disconnect()
  }, [frameRef, updateSpacer, userPausedRef])

  return { spacerHeight, focusModeRef }
}
