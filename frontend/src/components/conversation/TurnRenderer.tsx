'use client'

import { memo, type Ref } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { useMessageStore } from '@/stores/message-store'
import { useTraceStore } from '@/stores/trace-store'
import { Separator } from '@/components/ui/separator'
import type { TurnProjection } from '@/lib/pi-turn/types'
import type { TurnViewModel } from '@/lib/room-timeline/types'
import { UserMessageBlock } from './UserMessageBlock'
import { TurnBody } from './TurnBody'
import { TurnTracePanel } from './TurnTracePanel'
import { CanonicalTurnRenderer } from './CanonicalTurnRenderer'

interface TurnRendererProps {
  turn?: TurnViewModel
  canonicalTurn?: TurnProjection
  selectedAgentMessageId?: string
  onOpenAgentDetail?: (messageId: string) => void
  primarySurfaceRef?: Ref<HTMLDivElement>
  isLastTurn?: boolean
}

function LegacyTurnRenderer({
  turn,
  selectedAgentMessageId,
  onOpenAgentDetail,
  primarySurfaceRef,
  isLastTurn = false,
}: TurnRendererProps & { turn: TurnViewModel }) {
  const userEntity = useMessageStore((state) => (
    turn.userMessageId ? state.entities[turn.userMessageId] : undefined
  ))
  const clientRequestId = userEntity?.clientRequestId
  const traceNodes = useTraceStore(useShallow((state) => {
    if (!clientRequestId) return []
    return Object.values(state.nodes)
      .filter((node) => node.clientRequestId === clientRequestId)
      .sort((a, b) => a.receivedAt - b.receivedAt)
  }))
  const hasActivity = turn.processingStatusLogs.length > 0 || traceNodes.length > 0
  const hasAssistantSurface = turn.agentResults.length > 0 || hasActivity
  const isActivityRunning = turn.status === 'active'
    && turn.phase !== 'completed'
    && turn.finalAnswer.kind !== 'hitl'
  const hasFollowingContent = turn.finalAnswer.kind !== 'hitl'
    && (turn.finalAnswer.kind !== 'pending' || turn.agentResults.length > 0)

  return (
    <div className="conversation-turn" data-turn-kind="legacy">
      {turn.userMessageId === null ? (
        <div className="mb-2 text-xs font-medium" style={{ color: 'var(--conversation-text-muted)' }}>
          Unattributed responses
        </div>
      ) : userEntity ? (
        <div className="conversation-user-sticky">
          <UserMessageBlock entity={userEntity} />
        </div>
      ) : null}

      {hasAssistantSurface ? (
        <div className="conversation-body-frame conversation-turn-content flex flex-col">
          {hasActivity ? (
            <TurnTracePanel
              nodes={traceNodes}
              statusEntries={turn.processingStatusLogs}
              isRunning={isActivityRunning}
              isWaiting={turn.status === 'awaiting_input' && turn.finalAnswer.kind === 'hitl'}
              startedAt={turn.timestamp}
              turnTerminal={
                turn.status === 'completed'
                || turn.status === 'failed'
                || turn.status === 'partial'
                || (turn.status === 'active' && turn.phase === 'completed')
              }
            />
          ) : null}
          {hasActivity && hasFollowingContent ? (
            <Separator className="conversation-trace-separator" />
          ) : null}
          <TurnBody
            turn={turn}
            selectedAgentMessageId={selectedAgentMessageId}
            onOpenDetail={onOpenAgentDetail}
            primarySurfaceRef={primarySurfaceRef}
            isLastTurn={isLastTurn}
            renderProcessingLog={false}
          />
        </div>
      ) : null}
    </div>
  )
}

/** Mutually exclusive compatibility boundary: a canonical root always owns its
 * User request; otherwise the incumbent legacy renderer remains unchanged. */
function TurnRendererComponent(props: TurnRendererProps) {
  if (props.canonicalTurn) {
    return (
      <CanonicalTurnRenderer
        turn={props.canonicalTurn}
        selectedAgentMessageId={props.selectedAgentMessageId}
        onOpenAgentDetail={props.onOpenAgentDetail}
        primarySurfaceRef={props.primarySurfaceRef}
        isLastTurn={props.isLastTurn}
      />
    )
  }
  if (!props.turn) return null
  return <LegacyTurnRenderer {...props} turn={props.turn} />
}

export const TurnRenderer = memo(TurnRendererComponent)
