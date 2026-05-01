import type { AgentDisplayProps, AgentTheme } from '@/lib/selectors/conversation-types'
import { getAgentAvatarUri } from '@/lib/agent-avatar'

interface AgentCardProps {
  agentName: string
  agentId: string
  taskDescription: string
  theme: AgentTheme
  display: AgentDisplayProps
}

function AgentAvatar({ agentId, theme }: { agentId: string; theme: AgentTheme }) {
  return (
    <div
      className="w-8 h-8 rounded-lg overflow-hidden shrink-0"
      style={{ backgroundColor: theme.avatarBg }}
    >
      <img
        src={getAgentAvatarUri(agentId)}
        alt=""
        className="w-full h-full"
      />
    </div>
  )
}

export function AgentCard({ agentName, agentId, taskDescription, theme, display }: AgentCardProps) {
  const toneColors: Record<AgentDisplayProps['tone'], string> = {
    accent: 'rgb(0, 255, 255)',
    muted: 'var(--conversation-agent-green)',
    danger: 'var(--conversation-danger)',
    warning: 'var(--conversation-agent-yellow)',
  }

  return (
    <div
      className={`conversation-agent-card relative border overflow-hidden ${display.isAnimated ? 'conversation-card-shimmer' : ''}`}
      style={{
        backgroundColor: theme.cardBg,
        borderColor: display.tone === 'danger' ? 'var(--conversation-danger-border)' : display.tone === 'warning' ? '#854d0e' : theme.cardBg,
      }}
    >
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
      </div>
      {taskDescription && (
        <div className="conversation-agent-task-row flex items-center gap-1.5 pl-[42px]" style={{ position: 'relative', zIndex: 1 }}>
          <span className="text-sm leading-none" style={{ color: 'var(--conversation-text-dim)' }}>&#x2514;</span>
          <span className="conversation-agent-task-text text-[13px] font-medium truncate" style={{ color: 'var(--conversation-text-primary)' }}>
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
