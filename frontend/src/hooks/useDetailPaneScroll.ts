'use client'

import { useCallback, useEffect, useLayoutEffect, useRef, type RefObject } from 'react'
import { readConversationScrollSnapshot } from '@/lib/conversation/conversation-scroll'
import {
  isNearScrollBottom,
  restoreDetailPaneScrollWithRetry,
  scrollElementToBottom,
} from '@/lib/streaming/detail-pane-scroll'
import { useRoomUiStore } from '@/stores/room-ui-store'

const PROGRAMMATIC_SCROLL_MS = 150
const BOTTOM_FOLLOW_THRESHOLD = 48

function saveDetailScroll(messageId: string, body: HTMLElement): void {
  useRoomUiStore.getState().saveDetailPaneScroll(
    messageId,
    readConversationScrollSnapshot(body),
  )
}

/**
 * Detail pane scroll behavior (S7, ChatGPT-aligned):
 * - First open for a message → scroll to top
 * - Reopen same message → restore saved scroll position
 * - During stream → tail-follow only when user is already near the bottom
 * - Stream completes → leave scroll position unchanged
 */
export function useDetailPaneScroll(
  bodyRef: RefObject<HTMLElement | null>,
  messageId: string,
  isStreaming: boolean,
  contentLength: number,
): void {
  const followBottomRef = useRef(false)
  const prevMessageIdRef = useRef<string | undefined>(undefined)
  const suppressScrollUntilRef = useRef(0)

  const markProgrammaticScroll = useCallback(() => {
    suppressScrollUntilRef.current = performance.now() + PROGRAMMATIC_SCROLL_MS
  }, [])

  useLayoutEffect(() => {
    if (prevMessageIdRef.current === messageId) return

    const body = bodyRef.current
    const previousMessageId = prevMessageIdRef.current
    prevMessageIdRef.current = messageId

    if (body && previousMessageId) {
      saveDetailScroll(previousMessageId, body)
    }

    if (!body) return

    const saved = useRoomUiStore.getState().getDetailPaneScroll(messageId)
    markProgrammaticScroll()
    restoreDetailPaneScrollWithRetry(body, saved, (result) => {
      followBottomRef.current = result === 'restored-bottom'
        || (isStreaming && isNearScrollBottom(body, BOTTOM_FOLLOW_THRESHOLD))
    })
  }, [bodyRef, messageId, isStreaming, markProgrammaticScroll])

  useLayoutEffect(() => {
    if (!isStreaming || !followBottomRef.current) return
    const body = bodyRef.current
    if (!body) return
    markProgrammaticScroll()
    scrollElementToBottom(body)
    saveDetailScroll(messageId, body)
  }, [bodyRef, contentLength, isStreaming, messageId, markProgrammaticScroll])

  useEffect(() => {
    const body = bodyRef.current
    if (!body) return

    const persistScroll = () => {
      if (performance.now() < suppressScrollUntilRef.current) return
      const snapshot = readConversationScrollSnapshot(body)
      useRoomUiStore.getState().saveDetailPaneScroll(messageId, snapshot)
      return snapshot
    }

    const onScroll = () => {
      const snapshot = persistScroll()
      if (!snapshot || !isStreaming) return
      followBottomRef.current = snapshot.atBottom
    }

    const onUserScrollIntent = () => {
      if (isStreaming) {
        followBottomRef.current = isNearScrollBottom(body, BOTTOM_FOLLOW_THRESHOLD)
      }
      persistScroll()
    }

    body.addEventListener('scroll', onScroll, { passive: true })
    body.addEventListener('wheel', onUserScrollIntent, { passive: true })
    body.addEventListener('touchstart', onUserScrollIntent, { passive: true })

    return () => {
      body.removeEventListener('scroll', onScroll)
      body.removeEventListener('wheel', onUserScrollIntent)
      body.removeEventListener('touchstart', onUserScrollIntent)
    }
  }, [bodyRef, isStreaming, messageId])

  useEffect(() => {
    return () => {
      const body = bodyRef.current
      const id = prevMessageIdRef.current
      if (!body || !id) return
      saveDetailScroll(id, body)
    }
  }, [bodyRef])
}
