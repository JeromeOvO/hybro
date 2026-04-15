'use client'

import React from 'react'
import { getAgentAvatarUri } from '@/lib/agent-avatar'
import { CursorMessageRow } from './cursor-message-row'

interface CursorAgentPlaceholderProps {
  agentId: string
  agentName: string
}

/**
 * Minimal waiting indicator for agents that haven't responded yet.
 * Shows avatar + name + bouncing dots.
 */
export const CursorAgentPlaceholder = React.memo(function CursorAgentPlaceholder({
  agentId,
  agentName,
}: CursorAgentPlaceholderProps) {
  return (
    <CursorMessageRow
      avatarSlot={
        <img
          src={getAgentAvatarUri(agentId)}
          alt=""
          aria-hidden="true"
          className="w-7 h-7 rounded-full shrink-0 opacity-50"
        />
      }
    >
      <div className="flex items-center gap-2 py-1">
        <span className="text-sm font-semibold text-foreground/50">{agentName}</span>
        <span className="flex items-center gap-0.5" role="status" aria-label={`${agentName} is thinking`}>
          {[0, 1, 2].map(i => (
            <span
              key={i}
              className="w-1.5 h-1.5 rounded-full bg-muted-foreground/40 animate-bounce"
              style={{ animationDelay: `${i * 150}ms` }}
            />
          ))}
        </span>
      </div>
    </CursorMessageRow>
  )
})
