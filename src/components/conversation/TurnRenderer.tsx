'use client'

import { useMessageStore } from '@/stores/message-store'
import type { TurnViewModel } from '@/lib/room-timeline/types'
import { UserMessageBlock } from './UserMessageBlock'
import { TurnBody } from './TurnBody'
import type { Ref } from 'react'

interface TurnRendererProps {
  turn: TurnViewModel
  selectedAgentMessageId?: string
  onOpenAgentDetail?: (messageId: string) => void
  primarySurfaceRef?: Ref<HTMLDivElement>
  isLastTurn?: boolean
}

export function TurnRenderer({
  turn,
  selectedAgentMessageId,
  onOpenAgentDetail,
  primarySurfaceRef,
  isLastTurn = false,
}: TurnRendererProps) {
  const userEntity = useMessageStore(s =>
    turn.userMessageId ? s.entities[turn.userMessageId] : undefined,
  )
  const hasAssistantSurface =
    turn.agentResults.length > 0 ||
    turn.processingStatusLogs.length > 0

  return (
    <div className="conversation-turn">
      {turn.userMessageId === null ? (
        <div className="text-xs font-medium mb-2" style={{ color: 'var(--conversation-text-muted)' }}>
          Unattributed responses
        </div>
      ) : userEntity ? (
        <div className="conversation-user-sticky">
          <UserMessageBlock entity={userEntity} />
        </div>
      ) : null}

      {hasAssistantSurface && (
        <div className="conversation-body-frame conversation-turn-content flex flex-col">
          <TurnBody
            turn={turn}
            selectedAgentMessageId={selectedAgentMessageId}
            onOpenDetail={onOpenAgentDetail}
            primarySurfaceRef={primarySurfaceRef}
            isLastTurn={isLastTurn}
          />
        </div>
      )}
    </div>
  )
}
