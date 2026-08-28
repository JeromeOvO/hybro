'use client'

import type { Ref, ReactNode } from 'react'
import type { AgentResultViewModel, TurnViewModel } from '@/lib/room-timeline/types'
import { getSupervisorStatusLine, getStripSourceResults } from '@/lib/room-timeline/turn-live-shell'
import { useResultStreamDisplay } from '@/hooks/useStreamBuffer'
import { MarkdownContent } from '@/components/markdown-content'
import { AgentResultContent } from './AgentResultContent'
import { ProcessingStatusLog } from './ProcessingStatusLog'
import { SynthesisContent, SynthesisContentFromStream } from './SynthesisContent'

interface FinalAnswerSurfaceProps {
  turn: TurnViewModel
  surfaceRef?: Ref<HTMLDivElement>
  selectedAgentMessageId?: string
  onOpenDetail?: (messageId: string) => void
  renderProcessingLog?: boolean
}

/**
 * Avatar / work-log running state follows turn lifecycle, not finalAnswer kind.
 * Do not stop on `deterministic_done` / `single` content alone — those can
 * appear while the turn is still active (e.g. before synthesis or terminal
 * stamp). The old `kind !== 'hitl'` exclusion stopped the spinner during Needs
 * Input; keep HITL running without that exclusion.
 */
function isProcessingStatusRunning(turn: TurnViewModel): boolean {
  if (turn.finalAnswer.kind === 'hitl') return true
  return turn.status === 'active' && turn.phase !== 'completed'
}

function ProcessingBlock({
  entries,
  isRunning,
  show,
}: {
  entries?: TurnViewModel['processingStatusLogs']
  isRunning: boolean
  show: boolean
}) {
  return show ? <ProcessingStatusLog entries={entries ?? []} isRunning={isRunning} /> : null
}

function TerminalContent({ intro, turnId }: { intro: string; turnId?: string }) {
  return (
    <div
      className="conversation-content-body"
      data-quote-message-id={turnId}
      data-quote-agent-name="HYBRO AI"
      data-quote-source-kind="user_turn"
    >
      <MarkdownContent className="conversation-markdown-body" content={intro} />
    </div>
  )
}

function DeterministicDoneBlock({
  intro,
  summaryResult,
  turnArtifacts,
  turnId,
}: {
  intro: string
  summaryResult?: AgentResultViewModel
  turnArtifacts?: TurnViewModel['agentResults'][number]['artifacts']
  turnId?: string
}) {
  if (summaryResult) {
    return <SynthesisContent summaryResult={summaryResult} turnArtifacts={turnArtifacts} />
  }
  return <TerminalContent intro={intro} turnId={turnId} />
}

function SynthesisBlock({
  turn,
  summaryResult,
  supervisorStatus,
  processingStatusLogs,
  processingStatusRunning,
  showProcessingLog,
}: {
  turn: TurnViewModel
  summaryResult: AgentResultViewModel
  supervisorStatus: string | null
  processingStatusLogs?: TurnViewModel['processingStatusLogs']
  processingStatusRunning: boolean
  showProcessingLog: boolean
}) {
  const stream = useResultStreamDisplay(summaryResult)
  const logs = processingStatusLogs ?? []
  const turnArtifacts = turn.agentResults.flatMap((result) => result.artifacts ?? [])

  return (
    <>
      {showProcessingLog && logs.length > 0 ? (
        <ProcessingStatusLog entries={logs} isRunning={processingStatusRunning} />
      ) : null}
      {supervisorStatus && !stream.isStreaming ? (
        <p className="mt-1 text-xs" style={{ color: 'var(--conversation-text-muted)' }} aria-live="polite">
          {supervisorStatus}
        </p>
      ) : null}
      <SynthesisContentFromStream
        stream={stream}
        turnArtifacts={turnArtifacts}
        messageId={summaryResult.messageId}
        agentName={summaryResult.agentName}
      />
    </>
  )
}

export function FinalAnswerSurface({
  turn,
  surfaceRef,
  selectedAgentMessageId,
  onOpenDetail,
  renderProcessingLog = true,
}: FinalAnswerSurfaceProps) {
  const supervisorStatus = getSupervisorStatusLine(turn)
  const summaryResult = turn.agentResults.find((result) => result.isSummaryAgent)
  const realAgents = getStripSourceResults(turn)
  const { finalAnswer } = turn
  const shouldShowProcessingLog = renderProcessingLog && turn.processingStatusLogs.length > 0
  const processingStatusRunning = isProcessingStatusRunning(turn)
  let processingLogRenderedInBody = false
  let body: ReactNode = null

  switch (finalAnswer.kind) {
    case 'hitl':
      // The composer questionnaire is the sole HITL interaction surface.
      // Keep lifecycle feedback here only when this component owns the log.
      processingLogRenderedInBody = true
      body = (
        <ProcessingBlock
          entries={turn.processingStatusLogs}
          isRunning={processingStatusRunning}
          show={renderProcessingLog}
        />
      )
      break
    case 'llm_synthesis':
      processingLogRenderedInBody = true
      body = summaryResult ? (
        <SynthesisBlock
          turn={turn}
          summaryResult={summaryResult}
          supervisorStatus={supervisorStatus}
          processingStatusLogs={turn.processingStatusLogs}
          processingStatusRunning={processingStatusRunning}
          showProcessingLog={renderProcessingLog}
        />
      ) : (
        <ProcessingBlock
          entries={turn.processingStatusLogs}
          isRunning={processingStatusRunning}
          show={renderProcessingLog}
        />
      )
      break
    case 'canceled':
      body = <TerminalContent intro={finalAnswer.canceledIntro ?? ''} turnId={turn.id} />
      break
    case 'failed':
      body = <TerminalContent intro={finalAnswer.failedIntro ?? ''} turnId={turn.id} />
      break
    case 'deterministic_done':
      body = (
        <DeterministicDoneBlock
          intro={finalAnswer.deterministicIntro ?? ''}
          summaryResult={summaryResult?.summaryOrigin === 'deterministic' ? summaryResult : undefined}
          turnArtifacts={turn.agentResults.flatMap((result) => result.artifacts ?? [])}
          turnId={turn.id}
        />
      )
      break
    case 'single': {
      const agent = realAgents[0]
      if (agent) {
        body = (
          <AgentResultContent
            result={agent}
            selected={agent.messageId === selectedAgentMessageId}
            onOpenDetail={onOpenDetail}
          />
        )
      } else {
        processingLogRenderedInBody = true
        body = (
          <ProcessingBlock
            entries={turn.processingStatusLogs}
            isRunning={processingStatusRunning}
            show={renderProcessingLog}
          />
        )
      }
      break
    }
    case 'pending':
    default:
      processingLogRenderedInBody = true
      body = (
        <ProcessingBlock
          entries={turn.processingStatusLogs}
          isRunning={processingStatusRunning}
          show={renderProcessingLog}
        />
      )
      break
  }

  return (
    <div
      ref={surfaceRef}
      className="turn-primary-surface final-answer-surface flex flex-col"
      style={{ gap: 'var(--conversation-gap-block)', overflowAnchor: 'auto' }}
      data-turn-primary-surface
      data-final-answer-kind={finalAnswer.kind}
      data-primary-stream-id={turn.primaryStreamMessageId ?? ''}
    >
      {shouldShowProcessingLog && !processingLogRenderedInBody ? (
        <ProcessingStatusLog entries={turn.processingStatusLogs} isRunning={processingStatusRunning} />
      ) : null}
      {body}
    </div>
  )
}
