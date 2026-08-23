'use client'

import { useMemo } from 'react'
import { useMessageStore } from '@/stores/message-store'
import { useTraceStore } from '@/stores/trace-store'
import type { TurnViewModel } from '@/lib/room-timeline/types'
import { UserMessageBlock } from './UserMessageBlock'
import { TurnBody } from './TurnBody'
import { TurnTracePanel } from './TurnTracePanel'
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
  const traceNodesById = useTraceStore(s => s.nodes)
  const clientRequestId = userEntity?.clientRequestId
  const traceNodes = useMemo(() => {
    if (!clientRequestId) return []
    const nodes = Object.values(traceNodesById).filter(
      (node) => node.clientRequestId === clientRequestId,
    )
    nodes.sort((a, b) => a.receivedAt - b.receivedAt)
    return nodes
  }, [traceNodesById, clientRequestId])
  const hasActivity = turn.processingStatusLogs.length > 0 || traceNodes.length > 0
  const hasAssistantSurface = turn.agentResults.length > 0 || hasActivity
  const isActivityRunning =
    turn.status === 'active' &&
    turn.phase !== 'completed' &&
    turn.finalAnswer.kind !== 'hitl'

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
            renderProcessingLog={false}
          />
          {hasActivity ? (
            <TurnTracePanel
              nodes={traceNodes}
              statusEntries={turn.processingStatusLogs}
              isRunning={isActivityRunning}
            />
          ) : null}
        </div>
      )}
    </div>
  )
}
