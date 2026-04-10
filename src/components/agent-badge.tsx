'use client'

import { cn } from '@/lib/utils'
import { getAgentColorClasses } from '@/lib/agent-colors'
import { AgentSourceBadge } from './agent-source-badge'
import { TooltipProvider } from '@/components/ui/tooltip'

interface AgentBadgeProps {
  agentId?: string
  agentName: string
  agentSource?: 'hub' | 'cloud'
  size?: 'sm' | 'md'
  /** When true, never render the source badge icon. Used by summary blocks. */
  hideSource?: boolean
  /** Whether to show "(deleted)" when agentId is missing. Defaults to !agentId.
   *  Set to false for entities like summaries where missing agentId doesn't mean deletion. */
  showDeletedIndicator?: boolean
}

const SIZE_CLASSES = {
  sm: { dot: 'h-1.5 w-1.5', text: 'text-sm', gap: 'gap-1.5', icon: 'h-3 w-3' },
  md: { dot: 'h-2 w-2', text: 'text-base', gap: 'gap-2', icon: 'h-3.5 w-3.5' },
} as const

export function AgentBadge({
  agentId,
  agentName,
  agentSource,
  size = 'sm',
  hideSource = false,
  showDeletedIndicator,
}: AgentBadgeProps) {
  const colors = agentId ? getAgentColorClasses(agentId) : null
  const isDeleted = showDeletedIndicator ?? !agentId
  const displayName = isDeleted
    ? `${agentName || 'Unknown Agent'} (deleted)`
    : agentName
  const s = SIZE_CLASSES[size]

  // Source badge:
  // - hideSource → no badge (summaries)
  // - deleted → no badge (could have been hub or cloud)
  // - explicit agentSource → that icon
  // - undefined on non-deleted → cloud fallback
  const effectiveSource = hideSource
    ? undefined
    : isDeleted
      ? undefined
      : agentSource ?? 'cloud'

  return (
    <span className={cn('inline-flex items-center', s.gap, isDeleted && 'opacity-50')}>
      <span
        className={cn('rounded-full shrink-0', s.dot, colors ? colors.accent : 'bg-muted-foreground')}
        aria-hidden="true"
      />
      <span className={cn('font-medium truncate', s.text, colors ? colors.text : 'text-muted-foreground')}>
        {displayName}
      </span>
      {effectiveSource && (
        <TooltipProvider delayDuration={200}>
          <AgentSourceBadge source={effectiveSource} className={s.icon} />
        </TooltipProvider>
      )}
    </span>
  )
}
