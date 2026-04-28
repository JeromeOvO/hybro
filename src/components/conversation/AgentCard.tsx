import type { AgentDisplayProps, AgentTheme } from '@/lib/selectors/conversation-types'

interface AgentCardProps {
  agentName: string
  agentId: string
  taskDescription: string
  theme: AgentTheme
  display: AgentDisplayProps
}

function AgentAvatar({ name, theme }: { name: string; theme: AgentTheme }) {
  const initials = name.slice(0, 2).toUpperCase()
  return (
    <div
      className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-medium shrink-0"
      style={{ backgroundColor: `color-mix(in srgb, ${theme.accent} 15%, transparent)`, color: theme.accent }}
    >
      {initials}
    </div>
  )
}

export function AgentCard({ agentName, agentId, taskDescription, theme, display }: AgentCardProps) {
  const toneColors: Record<AgentDisplayProps['tone'], string> = {
    accent: theme.accent,
    muted: 'var(--conversation-text-dim)',
    danger: 'var(--conversation-danger)',
    warning: 'var(--conversation-agent-yellow)',
  }

  return (
    <div
      className={`relative rounded-lg border px-3 py-2.5 overflow-hidden ${display.isAnimated ? 'conversation-card-shimmer' : ''}`}
      style={{
        backgroundColor: 'var(--conversation-surface)',
        borderColor: display.tone === 'danger' ? 'var(--conversation-danger-border)' : 'var(--conversation-border)',
      }}
    >
      <div className="flex items-center gap-2.5">
        <AgentAvatar name={agentName} theme={theme} />
        <span className="text-sm font-medium" style={{ color: 'var(--conversation-text-primary)' }}>
          {agentName}
        </span>
        <span
          className="ml-auto text-xs"
          role="status"
          aria-label={display.ariaLabel}
          style={{ color: toneColors[display.tone] }}
        >
          {display.label}
        </span>
      </div>
      {taskDescription && (
        <div className="flex items-center gap-1.5 mt-1.5 pl-[42px]">
          <span className="text-xs" style={{ color: 'var(--conversation-text-dim)' }}>└</span>
          <span className="text-xs truncate" style={{ color: 'var(--conversation-text-muted)' }}>
            {taskDescription}
          </span>
        </div>
      )}
      {display.tone === 'warning' && display.label === 'Needs Input' && (
        <div className="mt-1.5 pl-[42px]">
          <span className="text-xs" style={{ color: 'var(--conversation-text-dim)' }}>
            Agent is waiting for your response in the input panel below.
          </span>
        </div>
      )}
    </div>
  )
}
