'use client'

import { AgentBadge } from './agent-badge'

interface AgentPlaceholderRowProps {
  agentId: string
  agentName: string
}

export function AgentPlaceholderRow({ agentId, agentName }: AgentPlaceholderRowProps) {
  return (
    <div className="py-3 [&+&]:pt-6" data-testid={`placeholder-${agentId}`}>
      <div className="flex items-center gap-2 mb-2 px-1">
        <AgentBadge
          agentId={agentId}
          agentName={agentName}
          size="md"
        />
        <span className="shimmer-text text-[13px] text-muted-foreground font-medium ml-auto">Thinking</span>
      </div>
    </div>
  )
}
