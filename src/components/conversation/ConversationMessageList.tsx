'use client'

import { useRef, useState, useCallback, useEffect, useLayoutEffect } from 'react'
import { useMessageStore } from '@/stores/message-store'
import { useInitialHydrationSeq, useLocalSendSeq, useRoomUiStore } from '@/stores/room-ui-store'
import { useTurnViewModels } from '@/hooks/useTurnViewModels'
import { usePrimaryStreamScroll } from '@/hooks/usePrimaryStreamScroll'
import { TurnRenderer } from './TurnRenderer'
import { ScrollToBottomButton } from './ScrollToBottomButton'
import { resolveScrollStateAfterEvent } from './scroll-state'
interface ConversationMessageListProps {
  roomId: string
  selectedAgentMessageId?: string
  enableAgentDetail?: boolean
}

interface ScrollMetrics {
  scrollHeight: number
  scrollTop: number
  clientHeight: number
}

function readMetrics(el: HTMLElement): ScrollMetrics {
  return { scrollHeight: el.scrollHeight, scrollTop: el.scrollTop, clientHeight: el.clientHeight }
}

function isAtBottom(m: ScrollMetrics): boolean {
  return m.scrollHeight - m.scrollTop - m.clientHeight < 100
}

export function ConversationMessageList({ roomId, selectedAgentMessageId, enableAgentDetail = true }: ConversationMessageListProps) {
  const turns = useTurnViewModels(roomId)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [showScrollBtn, setShowScrollBtn] = useState(false)
  const [hasNewContent, setHasNewContent] = useState(false)

  const localSendSeq = useLocalSendSeq(roomId)
  const initialHydrationSeq = useInitialHydrationSeq(roomId)
  const prevRoomIdRef = useRef(roomId)
  const prevLocalSendSeqRef = useRef(localSendSeq)
  const prevHydrationSeqRef = useRef(0)
  const initialScrollResolvedRef = useRef(false)

  const userPausedRef = useRef(false)
  const programmaticScrollRef = useRef(false)
  const prevMetricsRef = useRef<ScrollMetrics | null>(null)
  const primarySurfaceRef = useRef<HTMLDivElement>(null)

  const lastTurn = turns[turns.length - 1]
  const primaryStreamMessageId = lastTurn?.primaryStreamMessageId

  usePrimaryStreamScroll({
    scrollRef,
    primarySurfaceRef,
    primaryStreamMessageId,
    userPausedRef,
    programmaticScrollRef,
  })

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    const el = scrollRef.current
    if (!el) return
    programmaticScrollRef.current = true
    el.scrollTo({ top: el.scrollHeight, behavior })
    userPausedRef.current = false
    setHasNewContent(false)
    setShowScrollBtn(false)
  }, [])

  const storeVersion = useMessageStore(s => s.version)
  const hydratedFromDb = useMessageStore(s => s.hydratedFromDb)

  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return

    if (roomId !== prevRoomIdRef.current) {
      prevRoomIdRef.current = roomId
      prevLocalSendSeqRef.current = localSendSeq
      prevHydrationSeqRef.current = 0
      initialScrollResolvedRef.current = false
      userPausedRef.current = false
      programmaticScrollRef.current = false
      prevMetricsRef.current = null
      setShowScrollBtn(false)
      setHasNewContent(false)
    }

    if (localSendSeq !== prevLocalSendSeqRef.current) {
      prevLocalSendSeqRef.current = localSendSeq
      initialScrollResolvedRef.current = true
      scrollToBottom('auto')
      prevMetricsRef.current = readMetrics(el)
      return
    }

    if (initialHydrationSeq !== prevHydrationSeqRef.current) {
      prevHydrationSeqRef.current = initialHydrationSeq
      initialScrollResolvedRef.current = true

      if (!userPausedRef.current) {
        scrollToBottom('auto')
      }

      prevMetricsRef.current = readMetrics(el)
      return
    }

    if (!initialScrollResolvedRef.current) {
      prevMetricsRef.current = readMetrics(el)
      return
    }

    const prev = prevMetricsRef.current
    const curr = readMetrics(el)

    if (prev && curr.scrollHeight !== prev.scrollHeight) {
      if (!userPausedRef.current && isAtBottom(prev)) {
        scrollToBottom('auto')
        prevMetricsRef.current = readMetrics(el)
        return
      } else {
        setHasNewContent(true)
        setShowScrollBtn(true)
      }
    }

    prevMetricsRef.current = curr
  }, [roomId, storeVersion, localSendSeq, initialHydrationSeq, scrollToBottom])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onScroll = () => {
      const m = readMetrics(el)
      const atBottom = isAtBottom(m)
      const next = resolveScrollStateAfterEvent({
        atBottom,
        programmatic: programmaticScrollRef.current,
        previousScrollTop: prevMetricsRef.current?.scrollTop ?? null,
        currentScrollTop: m.scrollTop,
        wasPaused: userPausedRef.current,
      })

      programmaticScrollRef.current = next.programmatic
      userPausedRef.current = next.paused
      if (next.clearNewContent) setHasNewContent(false)

      setShowScrollBtn(!atBottom)
      prevMetricsRef.current = m
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  const handleOpenAgentDetail = useCallback((messageId: string) => {
    useRoomUiStore.getState().openAgentDetail(roomId, messageId)
  }, [roomId])

  return (
    <div className="relative h-full bg-background">
      <div className="conversation-top-cover" />

      <div
        ref={scrollRef}
        className="conversation-scroll-area h-full overflow-y-auto overscroll-contain"
      >
        <div className="conversation-gutter">
          <div
            className="conversation-frame"
            data-hydrated={hydratedFromDb || undefined}
            style={{ paddingTop: 'var(--conversation-sticky-top)', paddingBottom: 'calc(var(--conversation-dock-height, 120px) + 24px)' }}
          >
            {turns.map((turn, index) => (
              <TurnRenderer
                key={turn.id}
                turn={turn}
                selectedAgentMessageId={selectedAgentMessageId}
                onOpenAgentDetail={enableAgentDetail ? handleOpenAgentDetail : undefined}
                primarySurfaceRef={index === turns.length - 1 ? primarySurfaceRef : undefined}
                isLastTurn={index === turns.length - 1}
              />
            ))}
          </div>
        </div>
      </div>

      <ScrollToBottomButton
        visible={showScrollBtn}
        hasNewContent={hasNewContent}
        onClick={() => scrollToBottom('smooth')}
      />
    </div>
  )
}
