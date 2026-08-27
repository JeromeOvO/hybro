'use client'

import type { Ref } from 'react'
import type { TurnViewModel } from '@/lib/room-timeline/types'
import { getStripSourceResults } from '@/lib/room-timeline/turn-live-shell'
import { FinalAnswerSurface } from './FinalAnswerSurface'
import { AgentIndex, shouldShowAgentIndex } from './AgentIndex'

interface TurnBodyProps {
  turn: TurnViewModel
  selectedAgentMessageId?: string
  onOpenDetail?: (messageId: string) => void
  primarySurfaceRef?: Ref<HTMLDivElement>
  isLastTurn?: boolean
  renderProcessingLog?: boolean
}

export function TurnBody({
  turn,
  selectedAgentMessageId,
  onOpenDetail,
  primarySurfaceRef,
  isLastTurn = false,
  renderProcessingLog = true,
}: TurnBodyProps) {
  const sourceResults = getStripSourceResults(turn)

  const stripResults =
    turn.finalAnswer.kind === 'hitl'
      ? sourceResults.filter(r => r.status === 'completed')
      : sourceResults

  const showIndex = shouldShowAgentIndex(turn)

  return (
    <div className="turn-body flex flex-col" style={{ gap: 'var(--conversation-gap-block)' }}>
      <FinalAnswerSurface
        turn={turn}
        surfaceRef={primarySurfaceRef}
        selectedAgentMessageId={selectedAgentMessageId}
        onOpenDetail={onOpenDetail}
        renderProcessingLog={renderProcessingLog}
      />

      {showIndex ? (
        <AgentIndex
          turn={turn}
          sourceResults={stripResults.length > 0 ? stripResults : sourceResults}
          selectedAgentMessageId={selectedAgentMessageId}
          onOpenDetail={onOpenDetail}
          isLastTurn={isLastTurn}
        />
      ) : null}
    </div>
  )
}
