'use client'

import React, { useRef, useEffect, useState, useCallback, useMemo } from 'react'
import { 
  ArrowDown,
  ChevronsDownUp, 
  ChevronsUpDown,
  MessageCirclePlus,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { EntityUserBubble, EntityAgentBubble, type QuoteData } from './message-bubble'
import { TaskStatusMessage } from './task-status-message'
import { type TaskState, TASK_STATE } from '@/lib/types/sse'
import { useAutoHideScroll } from '@/hooks/useAutoHideScroll'
import { useOrderedIds, useMessage, useMessageCount, useMessagesHydrated } from '@/hooks/useRoomMessages'
import { useMessageStore } from '@/stores/message-store'

// Empty state component
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

// Loading state component
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

// ── MemoizedMessage: per-message subscriber (Gap 4, Gap 8) ─────────────

interface MemoizedMessageProps {
  id: string
  isLatestAgent: boolean
  collapseSignal: number
  autoCollapseVersion: number
  isUserExpanded: boolean
  onUserToggle: (id: string, expanded: boolean) => void
  onQuote?: (data: QuoteData) => void
}

const MemoizedMessage = React.memo(function MemoizedMessage({
  id,
  isLatestAgent,
  collapseSignal,
  autoCollapseVersion,
  isUserExpanded,
  onUserToggle,
  onQuote,
}: MemoizedMessageProps) {
  const entity = useMessage(id)
  if (!entity) return null

  switch (entity.displayType) {
    case 'user-bubble':
      return <EntityUserBubble entity={entity} />

    case 'agent-bubble':
      return (
        <EntityAgentBubble
          entity={entity}
          defaultExpanded={isLatestAgent}
          collapseSignal={collapseSignal}
          autoCollapseVersion={autoCollapseVersion}
          isLatestAgent={isLatestAgent}
          isUserExpanded={isUserExpanded}
          onUserToggle={onUserToggle}
          onQuote={onQuote}
        />
      )

    case 'task-status':
      return (
        <TaskStatusMessage
          internalId={entity.id}
          agentId={entity.agentId}
          agentName={entity.senderName}
          initialStatus={(entity.taskStatus || TASK_STATE.WORKING) as TaskState}
          content={entity.content || null}
          error={entity.taskError}
          statusMessage={entity.taskStatusMessage}
          stepNumber={entity.stepNumber}
          totalSteps={entity.totalSteps}
          taskContent={entity.taskContent}
          taskCreatedAt={entity.taskCreatedAt || entity.timestamp}
          hitlPrompt={entity.hitlPrompt}
          hitlResolved={entity.hitlResolved}
          hitlUserAnswer={entity.hitlUserAnswer}
        />
      )
  }
})

// ── RoomMessages: main list component ──────────────────────────────────

interface RoomMessagesProps {
  onQuote?: (data: QuoteData) => void
}

export function RoomMessages({ onQuote }: RoomMessagesProps) {
  const orderedIds = useOrderedIds()
  const hydrated = useMessagesHydrated()
  const messageCount = useMessageCount()

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true)
  const prevCountRef = useRef(messageCount)

  // Auto-hide scrollbar when not scrolling
  useAutoHideScroll(scrollContainerRef)

  // ── Expand / collapse state (pure UI, not in message store — Gap 4) ──
  const [collapseSignal, setCollapseSignal] = useState(0)
  const [autoCollapseVersion, setAutoCollapseVersion] = useState(0)
  const [userExpandedIds, setUserExpandedIds] = useState<Set<string>>(new Set())
  const prevLatestAgentIdRef = useRef<string | null>(null)

  // Subscribe to store version so derived state recomputes on displayType transitions (Gap 15)
  const storeVersion = useMessageStore(s => s.version)

  // Compute which IDs are agent bubbles (for expand/collapse pill)
  const allAgentIds = useMemo(() => {
    const store = useMessageStore.getState()
    return orderedIds.filter(id => {
      const e = store.entities[id]
      return e && (e.displayType === 'agent-bubble')
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- storeVersion is an intentional invalidation signal (Gap 15)
  }, [orderedIds, storeVersion])

  const allExpanded = useMemo(
    () => allAgentIds.length > 0 && allAgentIds.every(id => userExpandedIds.has(id)),
    [allAgentIds, userExpandedIds]
  )

  // Find last agent message ID (for auto-expand)
  const lastAgentMessageId = useMemo(() => {
    const store = useMessageStore.getState()
    for (let i = orderedIds.length - 1; i >= 0; i--) {
      const e = store.entities[orderedIds[i]]
      if (e && e.displayType === 'agent-bubble') {
        return orderedIds[i]
      }
    }
    return null
    // eslint-disable-next-line react-hooks/exhaustive-deps -- storeVersion is an intentional invalidation signal (Gap 15)
  }, [orderedIds, storeVersion])

  // Track newest agent message to auto-collapse prior non-user-expanded responses
  useEffect(() => {
    if (lastAgentMessageId && lastAgentMessageId !== prevLatestAgentIdRef.current) {
      setAutoCollapseVersion((v) => v + 1)
    }
    prevLatestAgentIdRef.current = lastAgentMessageId
  }, [lastAgentMessageId])

  const handleUserToggle = useCallback((id: string, expanded: boolean) => {
    setUserExpandedIds((prev) => {
      const next = new Set(prev)
      if (expanded) next.add(id)
      else next.delete(id)
      return next
    })
  }, [])

  // Bulk collapse/expand for agent message bubbles
  const collapseAll = useCallback(() => {
    setCollapseSignal((v) => v + 1)
    setUserExpandedIds(new Set())
  }, [])

  const expandAll = useCallback(() => {
    setUserExpandedIds(new Set(allAgentIds))
  }, [allAgentIds])

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
    const isNearBottom = 
      container.scrollHeight - container.scrollTop - container.clientHeight < threshold
    return isNearBottom
  }, [])

  // Handle scroll to detect if user manually scrolls
  const handleScroll = useCallback((event: React.UIEvent<HTMLDivElement>) => {
    // Ignore scroll events we triggered ourselves (e.g., anchoring show less)
    if (event.currentTarget.dataset.programmaticScroll === 'true') {
      event.currentTarget.dataset.programmaticScroll = 'false'
      return
    }

    const isNearBottom = checkIfNearBottom()
    setShouldAutoScroll(isNearBottom)
  }, [checkIfNearBottom])

  // Auto scroll when new messages arrive (count-based, not reference-based)
  useEffect(() => {
    if (messageCount > prevCountRef.current) {
      const store = useMessageStore.getState()
      const lastId = store.orderedIds[store.orderedIds.length - 1]
      const lastEntity = lastId ? store.entities[lastId] : null

      if (lastEntity?.source === 'optimistic' && lastEntity.messageType === 'user') {
        // User just sent a message -> always scroll to bottom
        messagesEndRef.current?.scrollIntoView({ behavior: 'auto' })
      } else if (shouldAutoScroll) {
        // Agent message arrived while user is near bottom -> scroll
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
      {/* Main Content */}
      <div 
        ref={scrollContainerRef}
        data-message-scroll-container="true"
        onScroll={handleScroll}
        className="flex-1 h-full w-full overflow-y-auto"
      >
        <div className="py-4 min-h-full px-4 sm:px-6 max-w-4xl mx-auto">
          {orderedIds.length === 0 ? (
            <EmptyState />
          ) : (
            <>
              {/* Floating expand/collapse pill */}
              {allAgentIds.length > 0 && (
                <div className="sticky top-2 z-10 flex justify-end pointer-events-none">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={allExpanded ? collapseAll : expandAll}
                        className="h-8 w-8 p-0 pointer-events-auto rounded-full bg-muted/60 backdrop-blur-sm shadow-sm hover:bg-muted hover:shadow-md transition-all"
                        aria-label={allExpanded ? 'Collapse all messages' : 'Expand all messages'}
                      >
                        {allExpanded ? (
                          <ChevronsDownUp className="h-4 w-4" />
                        ) : (
                          <ChevronsUpDown className="h-4 w-4" />
                        )}
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p>{allExpanded ? 'Collapse all messages' : 'Expand all messages'}</p>
                    </TooltipContent>
                  </Tooltip>
                </div>
              )}

              {/* Messages Display - Timeline view */}
              <div className="space-y-4">
                {orderedIds.map(id => (
                  <MemoizedMessage
                    key={id}
                    id={id}
                    isLatestAgent={id === lastAgentMessageId}
                    collapseSignal={collapseSignal}
                    autoCollapseVersion={autoCollapseVersion}
                    isUserExpanded={userExpandedIds.has(id)}
                    onUserToggle={handleUserToggle}
                    onQuote={onQuote}
                  />
                ))}
              </div>
            
              <div ref={messagesEndRef} className="h-4" />
            </>
          )}
        </div>
      </div>
      <Button
        variant="ghost"
        size="sm"
        onClick={scrollToBottom}
        className={cn(
          "absolute bottom-4 left-1/2 -translate-x-1/2 h-9 w-9 p-0 rounded-full bg-muted/80 backdrop-blur-sm shadow-md hover:bg-muted hover:shadow-lg transition-all duration-200 z-10",
          shouldAutoScroll || orderedIds.length === 0
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
