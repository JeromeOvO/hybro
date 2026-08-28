'use client'

import { useRef, useState, useCallback, useEffect, useLayoutEffect, useMemo } from 'react'
import { useMessageStore } from '@/stores/message-store'
import { useInitialHydrationSeq, useLocalSendSeq, useRoomProcessing, useRoomUiStore } from '@/stores/room-ui-store'
import { useTurnViewModels } from '@/hooks/useTurnViewModels'
import { useCanonicalTurns } from '@/stores/turn-store'
import type { TurnProjection } from '@/lib/pi-turn/types'
import type { TurnViewModel } from '@/lib/room-timeline/types'
import { usePrimaryStreamScroll } from '@/hooks/usePrimaryStreamScroll'
import { useScrollUserMessageOnSend } from '@/hooks/useScrollUserMessageOnSend'
import {
  readConversationScrollSnapshot,
  restoreConversationScrollWithRetry,
  shouldSkipInitialHydrationScrollRestore,
} from '@/lib/conversation/conversation-scroll'
import { contentEndScrollTop, isNearContentEnd, scrollToContentEnd } from '@/lib/conversation/content-end-scroll'
import { FOCUS_SCROLL_MIN_SPACER_PX } from '@/lib/conversation/focus-scroll'
import { CanonicalTurnRenderer } from './CanonicalTurnRenderer'
import { TurnRenderer } from './TurnRenderer'
import { TurnTracePanel } from './TurnTracePanel'
import { UserMessageBlock } from './UserMessageBlock'
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

/** Skip tail-follow re-enable inference briefly after programmatic scroll — never blocks user cancel. */
const PROGRAMMATIC_SCROLL_MS = 150

function readMetrics(el: HTMLElement): ScrollMetrics {
  return { scrollHeight: el.scrollHeight, scrollTop: el.scrollTop, clientHeight: el.clientHeight }
}

type ConversationTurn =
  | { kind: 'pending-user'; id: string; messageId: string }
  | { kind: 'legacy'; id: string; turn: TurnViewModel }
  | { kind: 'canonical'; id: string; turn: TurnProjection }

function PendingUserTurn({ messageId, live }: { messageId: string; live: boolean }) {
  const entity = useMessageStore((state) => state.entities[messageId])
  if (!entity || entity.messageType !== 'user') return null
  return (
    <div className="conversation-turn" data-turn-kind="pending-user">
      <div className="conversation-user-sticky">
        <UserMessageBlock entity={entity} />
      </div>
      {live ? (
        <div className="conversation-body-frame conversation-turn-content flex flex-col">
          <TurnTracePanel
            nodes={[]}
            statusEntries={entity.processingStatusLogs ?? []}
            isRunning
            startedAt={entity.timestamp}
          />
        </div>
      ) : null}
    </div>
  )
}

export function ConversationMessageList({ roomId, selectedAgentMessageId, enableAgentDetail = true }: ConversationMessageListProps) {
  const legacyTurns = useTurnViewModels(roomId)
  const canonicalTurns = useCanonicalTurns(roomId)
  const processing = useRoomProcessing(roomId)
  const turns = useMemo<ConversationTurn[]>(() => {
    const canonicalByUser = new Map(canonicalTurns.map((turn) => [turn.userMessageId, turn]))
    const included = new Set<string>()
    const ordered: ConversationTurn[] = []
    // Message-derived turns are used only to keep the optimistic User bubble
    // visible before its canonical run_started root arrives. They never own
    // Trace, Agent Cards, final content, or lifecycle status.
    for (const pending of legacyTurns) {
      if (!pending.userMessageId) {
        // HITL prompts belong exclusively to the composer questionnaire. A
        // message-derived orphan can otherwise duplicate the same request as
        // an "Unattributed responses" card above the canonical Turn.
        if (pending.finalAnswer.kind === 'hitl') continue
        ordered.push({ kind: 'legacy', id: `legacy:${pending.id}`, turn: pending })
        continue
      }
      const canonical = canonicalByUser.get(pending.userMessageId)
      if (canonical) {
        if (!included.has(canonical.id)) {
          included.add(canonical.id)
          ordered.push({ kind: 'canonical', id: canonical.id, turn: canonical })
        }
      } else {
        const entity = useMessageStore.getState().entities[pending.userMessageId]
        if (entity?.source === 'optimistic' && processing) {
          ordered.push({
            kind: 'pending-user',
            id: `pending:${pending.userMessageId}`,
            messageId: pending.userMessageId,
          })
        } else {
          ordered.push({ kind: 'legacy', id: `legacy:${pending.id}`, turn: pending })
        }
      }
    }
    for (const turn of canonicalTurns) {
      if (!included.has(turn.id)) ordered.push({ kind: 'canonical', id: turn.id, turn })
    }
    return ordered
  }, [canonicalTurns, legacyTurns, processing])
  const scrollRef = useRef<HTMLDivElement>(null)
  const frameRef = useRef<HTMLDivElement>(null)
  const [showScrollBtn, setShowScrollBtn] = useState(false)
  const [hasNewContent, setHasNewContent] = useState(false)

  const localSendSeq = useLocalSendSeq(roomId)
  const initialHydrationSeq = useInitialHydrationSeq(roomId)
  const prevRoomIdRef = useRef(roomId)
  const prevHydrationSeqRef = useRef(0)
  const initialScrollResolvedRef = useRef(false)

  const userPausedRef = useRef(false)
  const tailFollowRef = useRef(false)
  const pinnedToContentEndRef = useRef(false)
  const programmaticScrollRef = useRef(false)
  const suppressScrollUntilRef = useRef(0)
  const prevMetricsRef = useRef<ScrollMetrics | null>(null)
  const primarySurfaceRef = useRef<HTMLDivElement>(null)

  const markProgrammaticScroll = useCallback(() => {
    if (!tailFollowRef.current) return
    suppressScrollUntilRef.current = performance.now() + PROGRAMMATIC_SCROLL_MS
  }, [])

  const stopTailFollow = useCallback(() => {
    if (!tailFollowRef.current && userPausedRef.current) return
    tailFollowRef.current = false
    userPausedRef.current = true
    programmaticScrollRef.current = false
    suppressScrollUntilRef.current = 0
    setShowScrollBtn(true)
  }, [])

  const lastTurn = turns[turns.length - 1]
  const primaryStreamMessageId = lastTurn?.kind === 'canonical'
    ? lastTurn.turn.currentAssistant?.messageId
    : lastTurn?.kind === 'legacy'
      ? lastTurn.turn.primaryStreamMessageId
      : undefined
  const lastUserMessageId = lastTurn?.kind === 'canonical'
    ? lastTurn.turn.userMessageId
    : lastTurn?.kind === 'legacy'
      ? lastTurn.turn.userMessageId ?? undefined
      : lastTurn?.messageId
  const turnLive = lastTurn?.kind === 'canonical'
    ? lastTurn.turn.state === 'active'
    : processing && Boolean(lastUserMessageId)

  const storeVersion = useMessageStore(s => s.version)
  const hydratedFromDb = useMessageStore(s => s.hydratedFromDb)

  const saveConversationScroll = useCallback((targetRoomId: string) => {
    const el = scrollRef.current
    if (!el) return
    useRoomUiStore.getState().saveConversationScroll(
      targetRoomId,
      readConversationScrollSnapshot(el),
    )
  }, [])

  useScrollUserMessageOnSend({
    scrollRef,
    frameRef,
    lastUserMessageId,
    localSendSeq,
    programmaticScrollRef,
    userPausedRef,
    tailFollowRef,
  })

  usePrimaryStreamScroll({
    scrollRef,
    primarySurfaceRef,
    primaryStreamMessageId,
    tailFollowRef,
    programmaticScrollRef,
    markProgrammaticScroll,
    enabled: turnLive || Boolean(primaryStreamMessageId),
  })

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    const el = scrollRef.current
    if (!el) return
    programmaticScrollRef.current = true
    markProgrammaticScroll()
    const targetScrollTop = contentEndScrollTop(el)
    scrollToContentEnd(el, behavior)
    userPausedRef.current = false
    tailFollowRef.current = true
    pinnedToContentEndRef.current = true
    setHasNewContent(false)
    setShowScrollBtn(false)
    useRoomUiStore.getState().saveConversationScroll(roomId, { scrollTop: targetScrollTop, atBottom: true })

    if (behavior !== 'smooth') return

    const saveWhenSettled = () => {
      if (!isNearContentEnd(el)) return
      useRoomUiStore.getState().saveConversationScroll(roomId, {
        scrollTop: el.scrollTop,
        atBottom: true,
      })
    }

    if ('onscrollend' in el) {
      el.addEventListener('scrollend', saveWhenSettled, { once: true })
      return
    }

    requestAnimationFrame(() => requestAnimationFrame(saveWhenSettled))
  }, [roomId, markProgrammaticScroll])

  useEffect(() => {
    return () => {
      saveConversationScroll(roomId)
    }
  }, [roomId, saveConversationScroll])

  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return

    if (roomId !== prevRoomIdRef.current) {
      prevRoomIdRef.current = roomId
      prevHydrationSeqRef.current = 0
      initialScrollResolvedRef.current = false
      userPausedRef.current = false
      tailFollowRef.current = false
      pinnedToContentEndRef.current = false
      programmaticScrollRef.current = false
      suppressScrollUntilRef.current = 0
      prevMetricsRef.current = null
      setShowScrollBtn(false)
      setHasNewContent(false)
    }

    if (initialHydrationSeq !== prevHydrationSeqRef.current) {
      prevHydrationSeqRef.current = initialHydrationSeq
      initialScrollResolvedRef.current = true

      if (!userPausedRef.current && !shouldSkipInitialHydrationScrollRestore(localSendSeq)) {
        const saved = useRoomUiStore.getState().getConversationScroll(roomId)
        programmaticScrollRef.current = true
        restoreConversationScrollWithRetry(el, saved, (result) => {
          userPausedRef.current = result === 'restored-position'
          const metrics = readMetrics(el)
          prevMetricsRef.current = metrics
          pinnedToContentEndRef.current = isNearContentEnd(el)
          tailFollowRef.current = turnLive && pinnedToContentEndRef.current
          setShowScrollBtn(!pinnedToContentEndRef.current)
          requestAnimationFrame(() => {
            programmaticScrollRef.current = false
          })
        })
      } else {
        prevMetricsRef.current = readMetrics(el)
        requestAnimationFrame(() => {
          programmaticScrollRef.current = false
        })
      }

      return
    }

    if (!initialScrollResolvedRef.current) {
      prevMetricsRef.current = readMetrics(el)
      return
    }

    const prev = prevMetricsRef.current
    const curr = readMetrics(el)

    if (prev && curr.scrollHeight !== prev.scrollHeight) {
      if (turnLive) {
        if (tailFollowRef.current) {
          programmaticScrollRef.current = true
          markProgrammaticScroll()
          scrollToContentEnd(el, 'auto')
          prevMetricsRef.current = readMetrics(el)
          return
        }

        setHasNewContent(true)
        setShowScrollBtn(true)
        prevMetricsRef.current = curr
        return
      }

      if (!userPausedRef.current && pinnedToContentEndRef.current) {
        scrollToBottom('auto')
        prevMetricsRef.current = readMetrics(el)
        return
      }

      setHasNewContent(true)
      setShowScrollBtn(true)
    }

    prevMetricsRef.current = curr
  }, [roomId, storeVersion, initialHydrationSeq, localSendSeq, scrollToBottom, turnLive, markProgrammaticScroll])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return

    const onScroll = () => {
      const m = readMetrics(el)
      const atBottom = isNearContentEnd(el)
      pinnedToContentEndRef.current = atBottom

      if (turnLive) {
        const prevTop = prevMetricsRef.current?.scrollTop ?? null
        const userScrolledUp = prevTop !== null && m.scrollTop < prevTop - 1
        const inferFromScrollPosition = performance.now() >= suppressScrollUntilRef.current

        if (userScrolledUp && !programmaticScrollRef.current) {
          stopTailFollow()
        } else if (inferFromScrollPosition && atBottom) {
          tailFollowRef.current = true
          userPausedRef.current = false
          setShowScrollBtn(false)
          setHasNewContent(false)
        } else if (inferFromScrollPosition) {
          userPausedRef.current = !tailFollowRef.current
          setShowScrollBtn(!tailFollowRef.current)
        }

        if (inferFromScrollPosition) {
          useRoomUiStore.getState().saveConversationScroll(roomId, { scrollTop: m.scrollTop, atBottom })
        }

        if (programmaticScrollRef.current) {
          programmaticScrollRef.current = false
        }

        prevMetricsRef.current = m
        return
      }

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

      if (!programmaticScrollRef.current) {
        useRoomUiStore.getState().saveConversationScroll(roomId, { scrollTop: m.scrollTop, atBottom })
      }
    }

    const onWheel = (e: WheelEvent) => {
      if (!turnLive) return
      if (tailFollowRef.current && e.deltaY !== 0) {
        stopTailFollow()
        return
      }
      if (e.deltaY > 0 && isNearContentEnd(el)) {
        tailFollowRef.current = true
        userPausedRef.current = false
        setShowScrollBtn(false)
        setHasNewContent(false)
      }
    }

    const onTouchMove = () => {
      if (!turnLive || !tailFollowRef.current) return
      stopTailFollow()
    }

    el.addEventListener('scroll', onScroll, { passive: true })
    el.addEventListener('wheel', onWheel, { passive: true })
    el.addEventListener('touchmove', onTouchMove, { passive: true })
    return () => {
      el.removeEventListener('scroll', onScroll)
      el.removeEventListener('wheel', onWheel)
      el.removeEventListener('touchmove', onTouchMove)
    }
  }, [roomId, turnLive, stopTailFollow])

  const handleOpenAgentDetail = useCallback((messageId: string) => {
    const store = useRoomUiStore.getState()
    if (selectedAgentMessageId === messageId) {
      store.closeAgentDetail(roomId)
      return
    }
    store.openAgentDetail(roomId, messageId)
  }, [roomId, selectedAgentMessageId])

  return (
    <div className="relative h-full bg-background">
      <div className="conversation-top-cover" />

      <div
        ref={scrollRef}
        className="conversation-scroll-area h-full overflow-y-auto overscroll-contain"
      >
        <div className="conversation-gutter">
          <div
            ref={frameRef}
            className="conversation-frame"
            data-hydrated={hydratedFromDb || undefined}
            style={{ paddingTop: 'var(--conversation-sticky-top)', paddingBottom: 'calc(var(--conversation-dock-height, 120px) + 24px)' }}
          >
            {turns.map((entry, index) => (
              entry.kind === 'canonical' ? (
                <CanonicalTurnRenderer
                  key={entry.id}
                  turn={entry.turn}
                  selectedAgentMessageId={selectedAgentMessageId}
                  onOpenAgentDetail={enableAgentDetail ? handleOpenAgentDetail : undefined}
                  primarySurfaceRef={index === turns.length - 1 ? primarySurfaceRef : undefined}
                  isLastTurn={index === turns.length - 1}
                />
              ) : entry.kind === 'legacy' ? (
                <TurnRenderer
                  key={entry.id}
                  turn={entry.turn}
                  selectedAgentMessageId={selectedAgentMessageId}
                  onOpenAgentDetail={enableAgentDetail ? handleOpenAgentDetail : undefined}
                  primarySurfaceRef={index === turns.length - 1 ? primarySurfaceRef : undefined}
                  isLastTurn={index === turns.length - 1}
                />
              ) : (
                <PendingUserTurn
                  key={entry.id}
                  messageId={entry.messageId}
                  live={processing && index === turns.length - 1}
                />
              )
            ))}
            <div aria-hidden data-content-end />
            <div
              aria-hidden
              data-scroll-spacer
              style={{ height: FOCUS_SCROLL_MIN_SPACER_PX, flexShrink: 0 }}
            />
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
