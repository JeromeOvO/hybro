'use client'

import React, { useRef, useEffect, useState, useCallback } from 'react'
import { ArrowDown, MessageCirclePlus } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { useAutoHideScroll } from '@/hooks/useAutoHideScroll'
import {
  useConversationTurns,
  useMessagesHydrated,
  useOrderedIds,
  useMessage,
  useMessageCount,
} from '@/hooks/useRoomMessages'
import { useMessageStore } from '@/stores/message-store'
import type { QuoteData } from '@/components/message-bubble'
import { CursorUserMessage } from './cursor-user-message'
import { CursorAgentMessage } from './cursor-agent-message'
import { MemoizedCursorTurn } from './cursor-turn'

// ── Empty state ─────────────────────────────────────────────────

function EmptyState() {
  return (
    <div className="h-full flex items-center justify-center px-4">
      <div className="text-center max-w-md space-y-2">
        <MessageCirclePlus className="h-5 w-5 text-muted-foreground/50 mx-auto" aria-hidden />
        <p className="text-sm font-medium text-foreground">No messages yet</p>
        <p className="text-xs text-muted-foreground leading-relaxed">
          Type below to start. Agents will respond in the thread.
        </p>
      </div>
    </div>
  )
}

// ── Loading state ───────────────────────────────────────────────

function LoadingState() {
  return (
    <div className="h-full flex items-center justify-center">
      <div className="text-center space-y-3">
        <div className="flex justify-center gap-1">
          <div className="w-2 h-2 bg-muted-foreground/40 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
          <div className="w-2 h-2 bg-muted-foreground/40 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
          <div className="w-2 h-2 bg-muted-foreground/40 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
        </div>
        <p className="text-sm text-muted-foreground">Loading messages...</p>
      </div>
    </div>
  )
}

// ── Fallback flat message list (ErrorBoundary) ──────────────────

interface FallbackMessageProps {
  id: string
}

const FallbackMessage = React.memo(function FallbackMessage({ id }: FallbackMessageProps) {
  const entity = useMessage(id)
  if (!entity) return null

  if (entity.displayType === 'user-bubble') {
    return <CursorUserMessage entity={entity} />
  }
  return <CursorAgentMessage entity={entity} />
})

function FallbackMessageList() {
  const orderedIds = useOrderedIds()
  return (
    <div className="space-y-4">
      {orderedIds.map(id => (
        <FallbackMessage key={id} id={id} />
      ))}
    </div>
  )
}

// ── Error boundary ──────────────────────────────────────────────

interface ErrorBoundaryState {
  hasError: boolean
}

class TimelineErrorBoundary extends React.Component<
  { children: React.ReactNode },
  ErrorBoundaryState
> {
  constructor(props: { children: React.ReactNode }) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[CursorTimeline ErrorBoundary] Caught error:', error, info)
  }

  render() {
    if (this.state.hasError) {
      return <FallbackMessageList />
    }
    return this.props.children
  }
}

// ── CursorTimeline ──────────────────────────────────────────────

const EMPTY_AGENTS: { agentId: string; agentName: string }[] = []

interface CursorTimelineProps {
  roomAgentList?: { agentId: string; agentName: string }[]
  onQuote?: (data: QuoteData) => void
}

export function CursorTimeline({ roomAgentList, onQuote }: CursorTimelineProps) {
  const turns = useConversationTurns()
  const hydrated = useMessagesHydrated()
  const messageCount = useMessageCount()

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true)
  const prevCountRef = useRef(messageCount)

  // Auto-hide scrollbar
  useAutoHideScroll(scrollContainerRef)

  const scrollToBottom = useCallback(() => {
    const container = scrollContainerRef.current
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' })
    }
  }, [])

  const checkIfNearBottom = useCallback(() => {
    const container = scrollContainerRef.current
    if (!container) return false
    const threshold = 100
    return container.scrollHeight - container.scrollTop - container.clientHeight < threshold
  }, [])

  const handleScroll = useCallback(
    (event: React.UIEvent<HTMLDivElement>) => {
      if (event.currentTarget.dataset.programmaticScroll === 'true') {
        event.currentTarget.dataset.programmaticScroll = 'false'
        return
      }
      setShouldAutoScroll(checkIfNearBottom())
    },
    [checkIfNearBottom],
  )

  // Track the active turn's ID for scroll anchoring
  const prevActiveTurnIdRef = useRef<string | null>(null)

  // Auto scroll on new messages or active turn change
  useEffect(() => {
    const activeTurnId = turns.length > 0 ? turns[turns.length - 1].id : null

    if (messageCount > prevCountRef.current || activeTurnId !== prevActiveTurnIdRef.current) {
      const store = useMessageStore.getState()
      const lastId = store.orderedIds[store.orderedIds.length - 1]
      const lastEntity = lastId ? store.entities[lastId] : null

      if (lastEntity?.source === 'optimistic' && lastEntity.messageType === 'user') {
        messagesEndRef.current?.scrollIntoView({ behavior: 'auto' })
      } else if (shouldAutoScroll) {
        messagesEndRef.current?.scrollIntoView({ behavior: 'auto' })
      }
    }

    prevCountRef.current = messageCount
    prevActiveTurnIdRef.current = activeTurnId
  }, [messageCount, shouldAutoScroll, turns])

  if (!hydrated) {
    return <LoadingState />
  }

  return (
    <div className="h-full flex relative">
      <div
        ref={scrollContainerRef}
        data-message-scroll-container="true"
        onScroll={handleScroll}
        className="flex-1 h-full w-full overflow-y-auto"
      >
        <div className="py-4 sm:py-6 min-h-full max-w-3xl mx-auto px-3 sm:px-5">
          {turns.length === 0 ? (
            <EmptyState />
          ) : (
            <TimelineErrorBoundary>
              <div className="space-y-0">
                {turns.map((turn, index) => {
                  const isLastTurn = index === turns.length - 1
                  const turnStillProcessing =
                    turn.status === 'active' || turn.status === 'awaiting_input'
                  const pendingAgents =
                    isLastTurn && turnStillProcessing && roomAgentList
                      ? roomAgentList.filter(
                          a => !turn.agentResults.some(r => r.agentId === a.agentId),
                        )
                      : EMPTY_AGENTS
                  return (
                    <MemoizedCursorTurn
                      key={turn.id}
                      turn={turn}
                      index={index}
                      isActive={isLastTurn}
                      pendingAgents={pendingAgents}
                      onQuote={onQuote}
                    />
                  )
                })}
              </div>
              <div ref={messagesEndRef} className="h-4" />
            </TimelineErrorBoundary>
          )}
        </div>
      </div>

      {/* Scroll-to-bottom button */}
      <Button
        variant="ghost"
        size="sm"
        onClick={scrollToBottom}
        className={cn(
          'absolute bottom-4 left-1/2 -translate-x-1/2 h-9 w-9 p-0 rounded-full',
          'bg-muted/80 backdrop-blur-sm shadow-md hover:bg-muted hover:shadow-lg',
          'transition-all duration-200 z-10',
          shouldAutoScroll || turns.length === 0
            ? 'opacity-0 scale-90 pointer-events-none'
            : 'opacity-100 scale-100',
        )}
        aria-label="Scroll to bottom"
        tabIndex={shouldAutoScroll ? -1 : 0}
      >
        <ArrowDown className="h-4 w-4" />
      </Button>
    </div>
  )
}
