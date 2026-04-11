'use client'

import { cn } from '@/lib/utils'
import { getAgentColorClasses } from '@/lib/agent-colors'
import { AgentSourceBadge } from './agent-source-badge'
import { TooltipProvider } from '@/components/ui/tooltip'
import { getAgentAvatarUri } from '@/lib/agent-avatar'
import { isSummarySystemAgent } from '@/lib/system-agents'

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
  sm: { avatar: 'w-5 h-5', text: 'text-sm', gap: 'gap-1.5', icon: 'h-3 w-3' },
  md: { avatar: 'w-7 h-7', text: 'text-base', gap: 'gap-2', icon: 'h-3.5 w-3.5' },
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
  const isSummary = isSummarySystemAgent(agentId)
  const s = SIZE_CLASSES[size]

  // Summary agents get special display name + brand treatment
  const displayName = isSummary
    ? 'Summary from HYBRO AI'
    : isDeleted
      ? `${agentName || 'Unknown Agent'} (deleted)`
      : agentName

  // Source badge: suppress for summary-family agents (no real agentSource,
  // fallback cloud icon would be misleading)
  const effectiveSource = (hideSource || isSummary)
    ? undefined
    : isDeleted
      ? undefined
      : agentSource ?? 'cloud'

  // Avatar: summary → HYBRO favicon, regular → dicebear, deleted → none
  const avatarSrc = isSummary
    ? '/favicon.svg'
    : agentId
      ? getAgentAvatarUri(agentId)
      : undefined

  return (
    <span className={cn('inline-flex items-center', s.gap, isDeleted && 'opacity-50')}>
      {avatarSrc ? (
        <img
          src={avatarSrc}
          alt=""
          aria-hidden="true"
          className={cn('rounded-md shrink-0', s.avatar, isSummary && 'border border-border bg-background p-0.5')}
        />
      ) : (
        <span
          className={cn('rounded-full shrink-0 h-2 w-2', colors ? colors.accent : 'bg-muted-foreground')}
          aria-hidden="true"
        />
      )}
      <span
        className={cn(
          'font-semibold truncate',
          s.text,
          isSummary
            ? 'text-brand-gradient'
            : colors ? colors.text : 'text-muted-foreground',
        )}
      >
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
