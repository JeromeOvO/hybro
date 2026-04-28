'use client'

import { useRef, useState, useCallback, useEffect } from 'react'
import { useMessageStore } from '@/stores/message-store'
import { useConversationTurnViews } from '@/hooks/useConversationTurnViews'
import { ConversationTurn } from './ConversationTurn'
import { ScrollToBottomButton } from './ScrollToBottomButton'
import { UserMessageBlock } from './UserMessageBlock'
import type { ConversationTurnView } from '@/lib/selectors/conversation-types'

interface ConversationMessageListProps {
  roomId: string
}

export function ConversationMessageList({ roomId }: ConversationMessageListProps) {
  const turns = useConversationTurnViews(roomId)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [showScrollBtn, setShowScrollBtn] = useState(false)
  const [hasNewContent, setHasNewContent] = useState(false)
  const [stickyTurn, setStickyTurn] = useState<ConversationTurnView | null>(null)
  const [stickyVisible, setStickyVisible] = useState(false)
  const sentinelRefs = useRef<Map<string, HTMLDivElement>>(new Map())
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
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [isNearBottom])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const observer = new IntersectionObserver(
      (entries) => {
        let lastVisibleTurn: ConversationTurnView | null = null
        for (const entry of entries) {
          const turnId = (entry.target as HTMLElement).dataset.messageId
          if (!turnId) continue
          const turn = turns.find(t => t.userMessage?.id === turnId)
          if (turn && !entry.isIntersecting) {
            lastVisibleTurn = turn
          }
        }
        if (lastVisibleTurn) {
          setStickyTurn(prev => {
            if (prev?.turnId === lastVisibleTurn!.turnId) return prev
            setStickyVisible(false)
            setTimeout(() => setStickyVisible(true), 20)
            return lastVisibleTurn
          })
        }
      },
      { root: el, rootMargin: `-${parseInt(getComputedStyle(document.documentElement).getPropertyValue('--conversation-sticky-top') || '12')}px 0px 0px 0px` }
    )

    for (const ref of sentinelRefs.current.values()) {
      observer.observe(ref)
    }
    return () => observer.disconnect()
  }, [turns])

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
    <div className="relative h-full" style={{ backgroundColor: 'var(--conversation-bg)' }}>
      {stickyTurn?.userMessage && (
        <div
          className="sticky z-20 transition-opacity"
          style={{
            top: 'var(--conversation-sticky-top)',
            opacity: stickyVisible ? 1 : 0,
            transitionDuration: 'var(--conversation-fade-duration)',
            borderBottom: '1px solid var(--conversation-border)',
          }}
        >
          <div style={{ maxWidth: 'var(--conversation-max-width)', margin: '0 auto' }}>
            <UserMessageBlock entity={stickyTurn.userMessage} />
          </div>
        </div>
      )}

      <div
        ref={scrollRef}
        className="h-full overflow-y-auto"
        style={{ scrollBehavior: 'smooth' }}
      >
        <div style={{ maxWidth: 'var(--conversation-max-width)', margin: '0 auto' }}>
          <div className="flex flex-col" style={{ gap: 'var(--conversation-gap-turn)', paddingTop: '48px' }}>
            {turns.map(turn => (
              <ConversationTurn
                key={turn.turnId}
                turn={turn}
                onUserSentinelRef={turn.userMessage ? registerSentinel(turn.userMessage.id) : undefined}
                multiAgentTurn={hasMultipleAgents(turn)}
              />
            ))}
          </div>
          <div style={{ minHeight: '60vh' }} />
        </div>
      </div>

      <ScrollToBottomButton visible={showScrollBtn} hasNewContent={hasNewContent} onClick={scrollToBottom} />
    </div>
  )
}
