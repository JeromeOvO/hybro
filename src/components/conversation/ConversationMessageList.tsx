'use client'

import { useRef, useState, useCallback, useEffect } from 'react'
import { useMessageStore } from '@/stores/message-store'
import { useConversationTurnViews } from '@/hooks/useConversationTurnViews'
import { ConversationTurn } from './ConversationTurn'
import { ScrollToBottomButton } from './ScrollToBottomButton'
import type { ConversationTurnView } from '@/lib/selectors/conversation-types'

interface ConversationMessageListProps {
  roomId: string
}

export function ConversationMessageList({ roomId }: ConversationMessageListProps) {
  const turns = useConversationTurnViews(roomId)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [showScrollBtn, setShowScrollBtn] = useState(false)
  const [hasNewContent, setHasNewContent] = useState(false)
  const sentinelRefs = useRef<Map<string, HTMLDivElement>>(new Map())
  const turnsRef = useRef(turns)
  turnsRef.current = turns

  const [stuckId, setStuckId] = useState<string | null>(null)

  const isNearBottom = useCallback(() => {
    const el = scrollRef.current
    if (!el) return true
    return el.scrollHeight - el.scrollTop - el.clientHeight < 100
  }, [])

  const scrollToBottom = useCallback(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
    setHasNewContent(false)
  }, [])

  const scrollFingerprint = useMessageStore(s => {
    const len = s.orderedIds.length
    if (len === 0) return '0:'
    const lastId = s.orderedIds[len - 1]
    const last = s.entities[lastId]
    return `${len}:${last?.content?.length ?? 0}:${last?.taskStatus ?? ''}`
  })

  const prevFingerprintRef = useRef(scrollFingerprint)
  useEffect(() => {
    if (scrollFingerprint !== prevFingerprintRef.current) {
      prevFingerprintRef.current = scrollFingerprint
      if (isNearBottom()) {
        scrollToBottom()
      } else {
        setHasNewContent(true)
      }
    }
  }, [scrollFingerprint, isNearBottom, scrollToBottom])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onScroll = () => {
      setShowScrollBtn(!isNearBottom())
      if (isNearBottom()) setHasNewContent(false)

      const containerTop = el.getBoundingClientRect().top
      let active: string | null = null
      for (const turn of turnsRef.current) {
        if (!turn.userMessage) continue
        const sentinel = sentinelRefs.current.get(turn.userMessage.id)
        if (!sentinel) continue
        if (sentinel.getBoundingClientRect().top < containerTop + 2) {
          active = turn.userMessage.id
        }
      }
      setStuckId(active)
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [isNearBottom])

  useEffect(() => {
    if (turns.length > 0) scrollToBottom()
  }, [roomId]) // eslint-disable-line react-hooks/exhaustive-deps

  const registerSentinel = useCallback((turnId: string) => (el: HTMLDivElement | null) => {
    if (el) sentinelRefs.current.set(turnId, el)
    else sentinelRefs.current.delete(turnId)
  }, [])

  const hasMultipleAgents = (turn: ConversationTurnView) => {
    const agentIds = new Set<string>()
    for (const b of turn.blocks) {
      if (b.type === 'agent_card') agentIds.add(b.agentId)
    }
    return agentIds.size > 1
  }

  return (
    <div className="relative h-full bg-background">
      <div
        ref={scrollRef}
        className="h-full overflow-y-auto overscroll-contain"
      >
        <div className="conversation-gutter">
          <div className="conversation-content-area">
            <div
              className="flex flex-col"
              style={{ gap: 'var(--conversation-gap-turn)', paddingTop: 24, paddingBottom: 120 }}
            >
              {turns.map(turn => (
                <ConversationTurn
                  key={turn.turnId}
                  turn={turn}
                  isStuck={turn.userMessage?.id === stuckId}
                  onSentinelRef={turn.userMessage ? registerSentinel(turn.userMessage.id) : undefined}
                  multiAgentTurn={hasMultipleAgents(turn)}
                />
              ))}
            </div>
          </div>
        </div>
      </div>

      <ScrollToBottomButton visible={showScrollBtn} hasNewContent={hasNewContent} onClick={scrollToBottom} />
    </div>
  )
}
