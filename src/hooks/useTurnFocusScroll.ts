'use client'

import { useLayoutEffect, useRef, useState, type RefObject } from 'react'
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
  turnLive: boolean
  contentVersion: number
  userPausedRef: RefObject<boolean>
  programmaticScrollRef: RefObject<boolean>
}

export function useTurnFocusScroll({
  scrollRef,
  frameRef,
  lastUserMessageId,
  localSendSeq,
  turnLive,
  contentVersion,
  userPausedRef,
  programmaticScrollRef,
}: UseTurnFocusScrollOptions) {
  const [spacerHeight, setSpacerHeight] = useState(FOCUS_SCROLL_MIN_SPACER_PX)
  const focusModeRef = useRef(false)
  const prevLocalSendSeqRef = useRef(localSendSeq)
  const prevTurnLiveRef = useRef(turnLive)

  const updateSpacer = () => {
    const container = scrollRef.current
    const frame = frameRef.current
    if (!container || !frame || !lastUserMessageId) return

    const contentEnd = frame.querySelector('[data-content-end]') as HTMLElement | null
    const userEl = frame.querySelector(
      `[data-message-id="${escapeCssIdent(lastUserMessageId)}"]`,
    ) as HTMLElement | null

    if (!contentEnd || !userEl) return

    if (!focusModeRef.current && !turnLive) {
      setSpacerHeight(FOCUS_SCROLL_MIN_SPACER_PX)
      return
    }

    setSpacerHeight(computeFocusSpacerHeight(container, userEl, contentEnd))
  }

  const focusLastUserMessage = () => {
    const container = scrollRef.current
    const frame = frameRef.current
    if (!container || !frame || !lastUserMessageId) return false

    const userEl = frame.querySelector(
      `[data-message-id="${escapeCssIdent(lastUserMessageId)}"]`,
    ) as HTMLElement | null
    if (!userEl) return false

    programmaticScrollRef.current = true
    scrollUserMessageToFocus(container, userEl)
    updateSpacer()
    return true
  }

  useLayoutEffect(() => {
    if (localSendSeq === prevLocalSendSeqRef.current) return
    prevLocalSendSeqRef.current = localSendSeq

    focusModeRef.current = true
    userPausedRef.current = false

    if (!focusLastUserMessage()) {
      let retries = 0
      const tryFocus = () => {
        retries += 1
        if (focusLastUserMessage() || retries >= 5) return
        requestAnimationFrame(tryFocus)
      }
      requestAnimationFrame(tryFocus)
    }
  }, [localSendSeq, lastUserMessageId])

  useLayoutEffect(() => {
    if (turnLive) {
      focusModeRef.current = true
    } else if (prevTurnLiveRef.current && !turnLive) {
      focusModeRef.current = false
    }
    prevTurnLiveRef.current = turnLive
    updateSpacer()
  }, [turnLive, contentVersion, lastUserMessageId])

  useLayoutEffect(() => {
    const frame = frameRef.current
    if (!frame || typeof ResizeObserver === 'undefined') return

    const observer = new ResizeObserver(() => {
      if (!focusModeRef.current && !turnLive) return
      if (userPausedRef.current) return
      updateSpacer()
    })

    observer.observe(frame)
    return () => observer.disconnect()
  }, [frameRef, turnLive, lastUserMessageId])

  return { spacerHeight, focusModeRef }
}
