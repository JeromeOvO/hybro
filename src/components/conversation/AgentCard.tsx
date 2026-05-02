import type { AgentDisplayProps, AgentTheme } from '@/lib/selectors/conversation-types'
import { getAgentAvatarUri } from '@/lib/agent-avatar'
import { cn } from '@/lib/utils'
import type { ReactNode } from 'react'

interface AgentCardProps {
  messageId?: string
  agentName: string
  agentId: string
  taskDescription: string
  theme: AgentTheme
  display: AgentDisplayProps
  selected?: boolean
  interactive?: boolean
  onOpen?: (messageId: string) => void
  rightAction?: ReactNode
}

function AgentAvatar({ agentId, theme }: { agentId: string; theme: AgentTheme }) {
  return (
    <div
      className="w-8 h-8 overflow-hidden shrink-0"
      style={{ backgroundColor: theme.avatarLightBg, borderRadius: 'var(--chat-input-radius)' }}
    >
      <img
        src={getAgentAvatarUri(agentId)}
        alt=""
        className="w-full h-full"
      />
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
}: AgentCardProps) {
  const toneColors: Record<AgentDisplayProps['tone'], string> = {
    accent: 'rgb(0, 255, 255)',
    muted: 'var(--conversation-agent-green)',
    danger: 'var(--conversation-danger)',
    warning: 'var(--conversation-agent-yellow)',
  }

  const canOpen = interactive && !!messageId && !!onOpen
  const className = cn(
    'conversation-agent-card relative border overflow-hidden',
    canOpen && 'w-full text-left cursor-pointer transition-colors hover:border-cyan-300/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/35',
    display.isAnimated && 'conversation-card-shimmer',
  )
  const style = {
    backgroundColor: theme.cardBg,
    borderColor: selected
      ? 'rgba(34, 211, 238, 0.45)'
      : display.tone === 'danger'
        ? 'var(--conversation-danger-border)'
        : display.tone === 'warning'
          ? '#854d0e'
          : theme.cardBg,
  }
  const content = (
    <>
      <div className="flex items-center gap-2.5" style={{ position: 'relative', zIndex: 1 }}>
        <AgentAvatar agentId={agentId} theme={theme} />
        <span className="text-[13px] font-medium" style={{ color: 'var(--conversation-text-primary)' }}>
          {agentName}
        </span>
        <span
          className="conversation-agent-status ml-auto font-medium"
          role="status"
          aria-label={display.ariaLabel}
          style={{ color: toneColors[display.tone], position: 'relative', zIndex: 1 }}
        >
          {display.label}
        </span>
        {rightAction && (
          <span className="conversation-agent-card-action">
            {rightAction}
          </span>
        )}
      </div>
      {taskDescription && (
        <div className="conversation-agent-task-row flex items-center gap-1.5 pl-[42px]" style={{ position: 'relative', zIndex: 1 }}>
          <span className="text-sm leading-none" style={{ color: 'var(--conversation-text-dim)' }}>&#x2514;</span>
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
        aria-label={`Open ${agentName} response`}
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
      data-selected={selected ? 'true' : undefined}
      className={className}
      style={style}
    >
      {content}
    </div>
  )
}
