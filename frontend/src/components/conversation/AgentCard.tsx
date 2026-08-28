'use client'

import Link from 'next/link'
import { useQueryClient } from '@tanstack/react-query'
import type { AgentDisplayProps, AgentTheme } from '@/lib/selectors/conversation-types'
import { getAgentAvatarUri } from '@/lib/agent-avatar'
import { AgentSourceBadge } from '@/components/agent-source-badge'
import { cn } from '@/lib/utils'
import type { ReactNode } from 'react'
import type { Agent } from '@/lib/types/agent'

interface AgentCardProps {
  messageId?: string
  agentName: string
  agentId?: string
  taskDescription: string
  theme: AgentTheme
  display: AgentDisplayProps
  selected?: boolean
  interactive?: boolean
  onOpen?: (messageId: string) => void
  rightAction?: ReactNode
  agentSource?: 'cloud' | 'local' | 'hub'
  /** Single-line strip row: tighter padding, no task row, no card shimmer. */
  compact?: boolean
  /** Appended to status label in compact mode (e.g. artifact count). */
  statusSuffix?: string
  lifecycleStatus?: 'running' | 'awaiting_input' | 'completed' | 'failed' | 'canceled'
}

function useAgentFromCatalog(agentId: string | undefined): Agent | undefined {
  const qc = useQueryClient()
  const agents = qc.getQueryData<Agent[]>(['agents', 'all'])
  return agentId ? agents?.find(a => a.agent_id === agentId) : undefined
}

function AgentAvatar({
  agentId,
  theme,
  isAnimated,
  compact,
}: {
  agentId: string
  theme: AgentTheme
  isAnimated?: boolean
  compact?: boolean
}) {
  const catalogAgent = useAgentFromCatalog(agentId)
  const iconUrl = catalogAgent?.agent_card?.iconUrl || undefined

  return (
    <div className={cn('shrink-0 relative', compact ? 'w-6 h-6' : 'w-8 h-8', isAnimated && 'conversation-avatar-working')}>
      <div
        className="w-full h-full overflow-hidden"
        style={{ backgroundColor: theme.avatarLightBg, borderRadius: 'var(--chat-input-radius)' }}
      >
        {iconUrl ? (
          <img
            src={iconUrl}
            alt=""
            className="w-full h-full object-cover"
            onError={(e) => {
              (e.currentTarget as HTMLImageElement).style.display = 'none'
              const fallback = (e.currentTarget as HTMLImageElement).nextElementSibling as HTMLImageElement | null
              if (fallback) fallback.style.display = 'block'
            }}
          />
        ) : null}
        <img
          src={getAgentAvatarUri(agentId)}
          alt=""
          className="w-full h-full"
          style={{ display: iconUrl ? 'none' : 'block' }}
        />
      </div>
    </div>
  )
}

export function AgentCard({
  messageId,
  agentName,
  agentId,
  taskDescription,
  theme,
  display,
  selected = false,
  interactive = true,
  onOpen,
  rightAction,
  agentSource,
  compact = false,
  statusSuffix,
  lifecycleStatus,
}: AgentCardProps) {
  const catalogAgent = useAgentFromCatalog(agentId)
  const isHubOnline = catalogAgent?.is_hub_online

  const toneColors: Record<AgentDisplayProps['tone'], string> = {
    accent: 'hsl(var(--color-primary))',
    muted: 'var(--conversation-agent-green)',
    danger: 'var(--conversation-danger)',
    warning: 'var(--conversation-agent-yellow)',
  }

  const canOpen = interactive && !!messageId && !!onOpen
  const className = cn(
    'conversation-agent-card relative border overflow-hidden',
    compact && 'conversation-agent-card-compact shrink-0',
    canOpen && 'w-full text-left cursor-pointer transition-colors hover:border-cyan-300/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/35',
    !compact && display.isAnimated && 'conversation-card-shimmer',
  )
  const style = {
    backgroundColor: selected
      ? 'hsl(var(--color-primary) / 0.12)'
      : theme.cardBg,
    borderColor: selected
      ? 'hsl(var(--color-primary) / 0.55)'
      : display.tone === 'danger'
        ? 'var(--conversation-danger-border)'
        : display.tone === 'warning'
          ? '#854d0e'
          : theme.cardBg,
    boxShadow: selected
      ? '0 0 0 1px hsl(var(--color-primary) / 0.28)'
      : 'none',
  }
  const content = (
    <>
      <div className={cn('flex items-center gap-2.5', compact && 'gap-2')} style={{ position: 'relative', zIndex: 1 }}>
        <AgentAvatar agentId={agentId ?? agentName} theme={theme} isAnimated={display.isAnimated} compact={compact} />
        {agentId && !canOpen ? (
          <Link
            href={`/agents/${encodeURIComponent(agentId)}`}
            className="text-[13px] font-medium hover:underline focus-visible:outline-none"
            style={{ color: 'var(--conversation-text-primary)' }}
          >
            {agentName}
          </Link>
        ) : (
          <span
            className="text-[13px] font-medium"
            style={{ color: 'var(--conversation-text-primary)' }}
          >
            {agentName}
          </span>
        )}
        {agentSource != null && (
          <AgentSourceBadge
            source={agentSource}
            isHubOnline={isHubOnline}
            className="h-3.5 w-3.5"
          />
        )}
        <span
          className="conversation-agent-status ml-auto font-medium"
          role="status"
          aria-label={display.ariaLabel}
          style={{ color: toneColors[display.tone], position: 'relative', zIndex: 1 }}
        >
          {display.label}
          {compact && statusSuffix ? ` · ${statusSuffix}` : null}
        </span>
        {rightAction && (
          <span className="conversation-agent-card-action">
            {rightAction}
          </span>
        )}
      </div>
      {!compact && taskDescription && (
        <div className="conversation-agent-task-row flex items-start gap-1.5 pl-[42px]" style={{ position: 'relative', zIndex: 1 }}>
          <span className="text-sm leading-none mt-px shrink-0" style={{ color: 'var(--conversation-text-dim)' }}>&#x2514;</span>
          <span className="conversation-agent-task-text text-[13px] font-medium truncate" style={{ color: 'var(--conversation-text-primary)' }}>
            {taskDescription}
          </span>
        </div>
      )}
    </>
  )

  if (canOpen) {
    return (
      <button
        type="button"
        aria-label={`${selected ? 'Close' : 'Open'} ${agentName} response`}
        data-call-id={messageId?.split(':').at(-1)}
        data-status={lifecycleStatus}
        data-selected={selected ? 'true' : undefined}
        className={className}
        style={style}
        onClick={() => onOpen(messageId)}
      >
        {content}
      </button>
    )
  }

  return (
    <div
      data-call-id={messageId?.split(':').at(-1)}
      data-status={lifecycleStatus}
      data-selected={selected ? 'true' : undefined}
      className={className}
      style={style}
    >
      {content}
    </div>
  )
}
