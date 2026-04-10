// src/components/conversation-timeline.tsx
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
import { EntityUserBubble, EntityAgentBubble, type QuoteData } from './message-bubble'
import type { TurnViewModel } from '@/lib/room-timeline/types'

// ── Empty state ─────────────────────────────────────────────────

function EmptyState() {
  return (
    <div className="h-full flex items-center justify-center">
      <div className="text-center space-y-4 max-w-sm px-4">
        <div className="w-16 h-16 rounded-full bg-gradient-to-br from-primary/20 to-accent/20 dark:from-primary/10 dark:to-accent/10 flex items-center justify-center mx-auto">
          <MessageCirclePlus className="h-8 w-8 text-primary/60" />
        </div>
        <div className="space-y-2">
          <p className="text-lg font-medium text-foreground">Start the conversation</p>
          <p className="text-sm text-muted-foreground">
            Send a message and our AI agents will collaborate to help you.
          </p>
        </div>
      </div>
    </div>
  )
}

// ── Loading state ───────────────────────────────────────────────

function LoadingState() {
  return (
    <div className="h-full flex items-center justify-center">
      <div className="text-center space-y-4">
        <div className="flex justify-center gap-1.5">
          <div className="w-3 h-3 bg-primary/60 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
          <div className="w-3 h-3 bg-primary/60 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
          <div className="w-3 h-3 bg-primary/60 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
        </div>
        <p className="text-sm text-muted-foreground">Loading messages...</p>
      </div>
    </div>
  )
}

// ── Fallback flat message list (used by ErrorBoundary) ──────────

interface FallbackMessageProps {
  id: string
}

const FallbackMessage = React.memo(function FallbackMessage({ id }: FallbackMessageProps) {
  const entity = useMessage(id)
  if (!entity) return null

  switch (entity.displayType) {
    case 'user-bubble':
      return <EntityUserBubble entity={entity} />
    case 'agent-bubble':
      return (
        <EntityAgentBubble
          entity={entity}
          defaultExpanded={true}
          collapseSignal={0}
          autoCollapseVersion={0}
          isLatestAgent={false}
          isUserExpanded={false}
          onUserToggle={() => {}}
        />
      )
  }
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

export class TimelineErrorBoundary extends React.Component<
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
    console.error('[TimelineErrorBoundary] Caught error:', error, info)
  }

  render() {
    if (this.state.hasError) {
      return <FallbackMessageList />
    }
    return this.props.children
  }
}

// ── Placeholder MemoizedTurn (until Task 14 delivers the real one) ──

interface PlaceholderTurnProps {
  turn: TurnViewModel
  index: number
  isActive: boolean
  onQuote?: (data: QuoteData) => void
}

function PlaceholderTurn({ turn, index }: PlaceholderTurnProps) {
  const preview = turn.userContent
    ? turn.userContent.slice(0, 80) + (turn.userContent.length > 80 ? '...' : '')
    : 'System turn'

  return (
    <div
      data-testid="conversation-turn"
      aria-label={`Turn ${index + 1}: ${preview}`}
      className="px-4 py-2"
    >
      <span className="text-sm text-muted-foreground">
        Turn {index + 1}: {preview}
      </span>
    </div>
  )
}

// ── ConversationTimeline ────────────────────────────────────────

interface ConversationTimelineProps {
  onQuote?: (data: QuoteData) => void
}

export function ConversationTimeline({ onQuote }: ConversationTimelineProps) {
  const turns = useConversationTurns()
  const hydrated = useMessagesHydrated()
  const messageCount = useMessageCount()

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true)
  const prevCountRef = useRef(messageCount)

  // Auto-hide scrollbar when not scrolling
  useAutoHideScroll(scrollContainerRef)

  const scrollToBottom = useCallback(() => {
    const container = scrollContainerRef.current
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' })
    }
  }, [])

  // Track if user is near bottom of scroll
  const checkIfNearBottom = useCallback(() => {
    const container = scrollContainerRef.current
    if (!container) return false
    const threshold = 100
    return container.scrollHeight - container.scrollTop - container.clientHeight < threshold
  }, [])

  // Handle scroll to detect if user manually scrolls
  const handleScroll = useCallback((event: React.UIEvent<HTMLDivElement>) => {
    if (event.currentTarget.dataset.programmaticScroll === 'true') {
      event.currentTarget.dataset.programmaticScroll = 'false'
      return
    }
    setShouldAutoScroll(checkIfNearBottom())
  }, [checkIfNearBottom])

  // Auto scroll when new messages arrive
  useEffect(() => {
    if (messageCount > prevCountRef.current) {
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
  }, [messageCount, shouldAutoScroll])

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
        <div className="py-4 min-h-full max-w-4xl mx-auto">
          {turns.length === 0 ? (
            <EmptyState />
          ) : (
            <TimelineErrorBoundary>
              <div className="space-y-6">
                {turns.map((turn, index) => (
                  <React.Fragment key={turn.id}>
                    {index > 0 && (
                      <div
                        className="h-px bg-border/50 mx-4"
                        role="separator"
                        aria-hidden="true"
                      />
                    )}
                    <PlaceholderTurn
                      turn={turn}
                      index={index}
                      isActive={index === turns.length - 1}
                      onQuote={onQuote}
                    />
                  </React.Fragment>
                ))}
              </div>
              <div ref={messagesEndRef} className="h-4" />
            </TimelineErrorBoundary>
          )}
        </div>
      </div>
      <Button
        variant="ghost"
        size="sm"
        onClick={scrollToBottom}
        className={cn(
          "absolute bottom-4 left-1/2 -translate-x-1/2 h-9 w-9 p-0 rounded-full bg-muted/80 backdrop-blur-sm shadow-md hover:bg-muted hover:shadow-lg transition-all duration-200 z-10",
          shouldAutoScroll || turns.length === 0
            ? "opacity-0 scale-90 pointer-events-none"
            : "opacity-100 scale-100"
        )}
        aria-label="Scroll to bottom"
        tabIndex={shouldAutoScroll ? -1 : 0}
      >
        <ArrowDown className="h-4 w-4" />
      </Button>
    </div>
  )
}
