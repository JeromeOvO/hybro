import { cn } from '@/lib/utils'
import { getAgentColorClasses } from '@/lib/agent-colors'

interface AgentBadgeProps {
  agentId?: string
  agentName: string
  agentSource?: 'hub' | 'cloud'
  size?: 'sm' | 'md'
}

const SIZE_CLASSES = {
  sm: { dot: 'h-1.5 w-1.5', text: 'text-xs', gap: 'gap-1.5' },
  md: { dot: 'h-2 w-2', text: 'text-sm', gap: 'gap-2' },
} as const

export function AgentBadge({
  agentId,
  agentName,
  agentSource,
  size = 'sm',
}: AgentBadgeProps) {
  const colors = agentId
    ? getAgentColorClasses(agentId)
    : null

  const s = SIZE_CLASSES[size]

  return (
    <span className={cn('inline-flex items-center', s.gap)}>
      <span
        className={cn(
          'rounded-full shrink-0',
          s.dot,
          colors ? colors.accent : 'bg-muted-foreground',
        )}
        aria-hidden="true"
      />
      <span
        className={cn(
          'font-medium truncate',
          s.text,
          colors ? colors.text : 'text-muted-foreground',
        )}
      >
        {agentName}
      </span>
      {agentSource && (
        <span className="text-[10px] leading-none text-muted-foreground uppercase tracking-wider">
          {agentSource}
        </span>
      )}
    </span>
  )
}
