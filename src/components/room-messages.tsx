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
import { EntityUserBubble, EntityAgentBubble, derivePhase, type QuoteData } from './message-bubble'
import { ScrollRangeSpacer } from './scroll-range-spacer'
import { useAutoHideScroll } from '@/hooks/useAutoHideScroll'
import { useMessageScrollAnchoring } from '@/hooks/useMessageScrollAnchoring'
import { groupMessagesByUserTurn } from '@/lib/room-timeline/message-groups'
import { useOrderedIds, useMessage, useMessagesHydrated } from '@/hooks/useRoomMessages'
import { useMessageStore } from '@/stores/message-store'
import { useShallow } from 'zustand/react/shallow'

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
          collapseSignal={collapseSignal}
          autoCollapseVersion={autoCollapseVersion}
          isLatestAgent={isLatestAgent}
          isUserExpanded={isUserExpanded}
          onUserToggle={onUserToggle}
          onQuote={onQuote}
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

  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const roomId = useMessageStore(s => s.roomId) ?? ''
  const version = useMessageStore(s => s.version)
  const entities = useMessageStore(s => s.entities)

  const getEntityForAnchor = useCallback(
    (id: string) => {
      const e = entities[id]
      if (!e) return undefined
      return { messageType: e.messageType, clientRequestId: e.clientRequestId }
    },
    [entities],
  )

  const { shouldAutoScroll, handleScroll, scrollToBottom } = useMessageScrollAnchoring({
    scrollContainerRef,
    hydrated,
    roomId,
    renderedAnchorIds: orderedIds,
    getEntityForAnchor,
    contentVersion: version,
  })

  const groups = useMemo(
    () => groupMessagesByUserTurn(orderedIds, entities),
    [orderedIds, entities],
  )

  // Auto-hide scrollbar when not scrolling
  useAutoHideScroll(scrollContainerRef)

  // ── Expand / collapse state (pure UI, not in message store — Gap 4) ──
  const [collapseSignal] = useState(0)
  const [autoCollapseVersion, setAutoCollapseVersion] = useState(0)
  const [userExpandedIds, setUserExpandedIds] = useState<Set<string>>(new Set())
  const prevLatestAgentIdRef = useRef<string | null>(null)

  // Compute which IDs are agent bubbles with renderable content (for expand/collapse pill)
  const allAgentIds = useMessageStore(useShallow(s =>
    s.orderedIds.filter(id => {
      const e = s.entities[id]
      if (!e || e.messageType !== 'agent') return false
      const phase = derivePhase(e)
      return phase === 'complete' || phase === 'streaming'
    })
  ))

  const allExpanded = useMemo(
    () => allAgentIds.length > 0 && allAgentIds.every(id => userExpandedIds.has(id)),
    [allAgentIds, userExpandedIds]
  )

  // Find last agent message ID (for auto-expand)
  const lastAgentMessageId = useMessageStore(useShallow(s => {
    for (let i = s.orderedIds.length - 1; i >= 0; i--) {
      const e = s.entities[s.orderedIds[i]]
      if (!e || e.messageType !== 'agent') continue
      const phase = derivePhase(e)
      if (phase === 'complete' || phase === 'streaming') return s.orderedIds[i]
    }
    return null
  }))

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
    setUserExpandedIds(new Set())
  }, [])

  const expandAll = useCallback(() => {
    setUserExpandedIds(new Set(allAgentIds))
  }, [allAgentIds])

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
        <div className="py-4 min-h-full max-w-4xl mx-auto relative">
          {orderedIds.length === 0 ? (
            <EmptyState />
          ) : (
            <>
              {allAgentIds.length > 0 && (
                <div className="sticky top-2 z-20 flex justify-end pointer-events-none mb-0" style={{ height: 0 }}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={allExpanded ? collapseAll : expandAll}
                        className="h-8 w-8 p-0 pointer-events-auto rounded-full bg-muted/60 backdrop-blur-sm shadow-sm hover:bg-muted hover:shadow-md transition-all mr-2"
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
                      {allExpanded ? 'Collapse all messages' : 'Expand all messages'}
                    </TooltipContent>
                  </Tooltip>
                </div>
              )}

              {/* Messages Display - Timeline view with sticky user message headers */}
              <div className="space-y-4">
                {groups.map((group, groupIdx) => (
                  <div key={group.userMsgId ?? 'system-prefix'} className="space-y-3">
                    {group.userMsgId && (
                      <div
                        className="sticky top-0 z-10 bg-background pb-1 shadow-[0_4px_6px_-1px_rgba(0,0,0,0.05)]"
                        data-message-id={group.userMsgId}
                      >
                        <MemoizedMessage
                          id={group.userMsgId}
                          isLatestAgent={false}
                          collapseSignal={collapseSignal}
                          autoCollapseVersion={autoCollapseVersion}
                          isUserExpanded={userExpandedIds.has(group.userMsgId)}
                          onUserToggle={handleUserToggle}
                          onQuote={onQuote}
                        />
                      </div>
                    )}
                    {group.childMsgIds.map(id => (
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
                    {groupIdx === groups.length - 1 && (
                      <>
                        <div data-content-end className="!mt-0" style={{ height: 0 }} />
                        <ScrollRangeSpacer scrollContainerRef={scrollContainerRef} />
                      </>
                    )}
                  </div>
                ))}
              </div>
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
