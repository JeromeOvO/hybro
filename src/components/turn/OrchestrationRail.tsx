'use client'

import React, { useState, useEffect, useRef, useMemo } from 'react'
import { Check, X, Pause, Info, ChevronRight } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { cn } from '@/lib/utils'
import { SYSTEM_AGENTS } from '@/lib/system-agents'
import type { RailItemView, RailIcon } from '@/stores/turn-event-store/types'
import type { Agent } from '@/lib/types/agent'

function RailIconComponent({ icon }: { icon: RailIcon }) {
  const size = 'h-3.5 w-3.5'
  switch (icon) {
    case 'spinner':
      return <span className={cn(size, 'inline-block rounded-full bg-foreground/40')} data-testid="rail-spinner" />
    case 'check':
      return <Check className={cn(size, 'text-green-500')} />
    case 'x':
      return <X className={cn(size, 'text-destructive')} />
    case 'pause':
      return <Pause className={cn(size, 'text-amber-500')} />
    case 'info':
      return <Info className={cn(size, 'text-muted-foreground')} />
  }
}

interface OrchestrationRailProps {
  items: RailItemView[]
  isProcessing?: boolean
}

export const OrchestrationRail = React.memo(function OrchestrationRail({ items, isProcessing }: OrchestrationRailProps) {
  // Nothing to show: no items and not processing
  if (items.length === 0 && !isProcessing) return null

  // Resolve agent names from catalog for items that have agentId but generic labels
  const queryClient = useQueryClient()
  const agents = queryClient.getQueryData<Agent[]>(['agents', 'all'])

  const resolvedItems = useMemo(() => {
    if (!agents || agents.length === 0) return items
    return items.map(item => {
      if (!item.agentId) return item
      // Check if label still uses the generic fallback "Agent"
      const colonIdx = item.label.indexOf(':')
      const currentName = colonIdx !== -1 ? item.label.slice(0, colonIdx) : null
      if (currentName && currentName !== 'Agent') return item // already resolved
      // Resolve name
      const resolved =
        SYSTEM_AGENTS[item.agentId]?.name
        ?? agents.find(a => a.agent_id === item.agentId)?.agent_card?.name
      if (!resolved) return item
      const newLabel = colonIdx !== -1
        ? `${resolved}${item.label.slice(colonIdx)}`
        : item.label
      return { ...item, label: newLabel }
    })
  }, [items, agents])

  const hasActiveItems = resolvedItems.some(item => item.isActive) || !!isProcessing
  const [userExpanded, setUserExpanded] = useState<boolean | null>(null)
  const wasActiveRef = useRef(hasActiveItems)

  // Auto-collapse when turn transitions from active to terminal
  useEffect(() => {
    if (wasActiveRef.current && !hasActiveItems) {
      setUserExpanded(false)
    }
    wasActiveRef.current = hasActiveItems
  }, [hasActiveItems])

  // Effective expanded state: user override > auto (expanded when active)
  const isExpanded = userExpanded ?? hasActiveItems

  // Terminal summary line for collapsed state
  const terminalItem = resolvedItems.length > 0 ? resolvedItems[resolvedItems.length - 1] : null

  // When processing but no items yet, show a default processing indicator
  const showProcessingPlaceholder = isProcessing && resolvedItems.length === 0

  return (
    <div className="mt-2 pl-10 pr-2" data-testid="orchestration-rail">
      <div className="border-l-2 border-muted pl-3">
        {showProcessingPlaceholder ? (
          <div className="flex items-center gap-1.5 py-0.5 text-foreground/70 shimmer-rail">
            <RailIconComponent icon="spinner" />
            <span className="text-sm">Processing</span>
          </div>
        ) : isExpanded ? (
          <>
            <div className="space-y-0.5">
              {resolvedItems.map(item => (
                <div
                  key={item.key}
                  className={cn(
                    'flex items-center gap-1.5 py-0.5',
                    item.isActive
                      ? 'text-foreground/80 shimmer-rail'
                      : 'text-foreground/50',
                  )}
                >
                  <RailIconComponent icon={item.icon} />
                  <span className="text-sm">{item.label}</span>
                </div>
              ))}
            </div>
            {!hasActiveItems && (
              <button
                type="button"
                onClick={() => setUserExpanded(false)}
                className="flex items-center gap-1 py-0.5 text-sm text-foreground/30 hover:text-foreground/50 transition-colors"
              >
                <ChevronRight className="h-3 w-3 rotate-90" />
                <span>Collapse</span>
              </button>
            )}
          </>
        ) : (
          <button
            type="button"
            onClick={() => setUserExpanded(true)}
            className="flex items-center gap-1.5 py-0.5 text-muted-foreground hover:text-foreground transition-colors"
          >
            <ChevronRight className="h-3 w-3" />
            {terminalItem && (
              <>
                <RailIconComponent icon={terminalItem.icon} isActive={false} />
                <span className="text-sm">{terminalItem.label}</span>
              </>
            )}
          </button>
        )}
      </div>
    </div>
  )
})
