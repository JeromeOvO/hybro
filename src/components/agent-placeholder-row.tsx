'use client'

import { getAgentAvatarUri } from '@/lib/agent-avatar'

interface AgentPlaceholderRowProps {
  agentId: string
  agentName: string
}

export function AgentPlaceholderRow({ agentId, agentName }: AgentPlaceholderRowProps) {
  const avatarUri = getAgentAvatarUri(agentId)

  return (
    <div className="flex gap-3 py-3 border-b border-border last:border-b-0">
      <img
        src={avatarUri}
        alt=""
        aria-hidden="true"
        className="w-7 h-7 rounded-md shrink-0 mt-0.5"
      />
      <div className="flex items-center gap-2">
        <span className="text-base font-semibold text-foreground">{agentName}</span>
        <span className="shimmer-text text-sm text-muted-foreground">Thinking</span>
      </div>
    </div>
  )
}
