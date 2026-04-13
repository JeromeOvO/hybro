'use client'

import React, { useRef, useState, useCallback, useMemo } from 'react'
import { ArrowDown, MessageCirclePlus, ChevronsDownUp, ChevronsUpDown } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { useAutoHideScroll } from '@/hooks/useAutoHideScroll'
import { useTurnEventStore } from '@/stores/turn-event-store'
import { useTurnScroll } from '@/hooks/turn/useTurnScroll'
import { ExpandCollapseContext } from './expand-collapse-context'
import { OrchestraTurn } from './OrchestraTurn'

function EmptyState() {
  return (
    <div className="h-full flex items-center justify-center px-4">
      <div className="text-center max-w-md space-y-2">
        <MessageCirclePlus className="h-6 w-6 text-muted-foreground/60 mx-auto" aria-hidden />
        <p className="text-sm font-medium text-foreground">No messages yet</p>
        <p className="text-xs text-muted-foreground leading-relaxed">
          Type below to start. Agents will respond in the thread.
        </p>
      </div>
    </div>
  )
}

export function TurnList() {
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const { messagesEndRef, shouldAutoScroll, handleScroll, scrollToBottom } = useTurnScroll(scrollContainerRef)

  useAutoHideScroll(scrollContainerRef)

  const orderedTurnIds = useTurnEventStore(s => s.orderedTurnIds)
  const turnLogs = useTurnEventStore(s => s.turnLogs)

  // Expand / collapse all agent responses
  const [expandSignal, setExpandSignal] = useState(0)
  const [collapseSignal, setCollapseSignal] = useState(0)
  const [allExpanded, setAllExpanded] = useState(true)

  const handleToggleAll = useCallback(() => {
    if (allExpanded) {
      setCollapseSignal(prev => prev + 1)
      setAllExpanded(false)
    } else {
      setExpandSignal(prev => prev + 1)
      setAllExpanded(true)
    }
  }, [allExpanded])

  const signals = useMemo(
    () => ({ expandSignal, collapseSignal }),
    [expandSignal, collapseSignal],
  )

  const hasTurns = orderedTurnIds.length > 0

  return (
    <ExpandCollapseContext.Provider value={signals}>
      <div className="h-full flex relative">
        <div
          ref={scrollContainerRef}
          data-message-scroll-container="true"
          onScroll={handleScroll}
          className="flex-1 h-full w-full overflow-y-auto"
        >
          <div className="py-3 sm:py-5 min-h-full max-w-4xl mx-auto px-3 sm:px-5">
            {!hasTurns ? (
              <EmptyState />
            ) : (
              <>
                <div className="sticky top-0 z-10 flex justify-end mb-1 bg-background/80 backdrop-blur-sm">
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={handleToggleAll}
                    className="h-7 w-7 text-muted-foreground hover:text-foreground"
                    aria-label={allExpanded ? 'Collapse all responses' : 'Expand all responses'}
                  >
                    {allExpanded ? (
                      <ChevronsDownUp className="h-4 w-4" />
                    ) : (
                      <ChevronsUpDown className="h-4 w-4" />
                    )}
                  </Button>
                </div>
                <div className="space-y-0">
                  {orderedTurnIds.map(turnId => {
                    const log = turnLogs.get(turnId)
                    if (!log) return null
                    return <OrchestraTurn key={turnId} turnLog={log} />
                  })}
                </div>
              </>
            )}
            <div ref={messagesEndRef} className="h-4" />
          </div>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={scrollToBottom}
          className={cn(
            "absolute bottom-4 left-1/2 -translate-x-1/2 h-9 w-9 p-0 rounded-full bg-muted/80 backdrop-blur-sm shadow-md hover:bg-muted hover:shadow-lg transition-all duration-200 z-10",
            shouldAutoScroll || !hasTurns
              ? "opacity-0 scale-90 pointer-events-none"
              : "opacity-100 scale-100"
          )}
          aria-label="Scroll to bottom"
          tabIndex={shouldAutoScroll ? -1 : 0}
        >
          <ArrowDown className="h-4 w-4" />
        </Button>
      </div>
    </ExpandCollapseContext.Provider>
  )
}
