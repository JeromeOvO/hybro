'use client'

import type { Ref, ReactNode } from 'react'
import type { TurnViewModel, AgentResultViewModel } from '@/lib/room-timeline/types'
import { getSupervisorStatusLine, getStripSourceResults } from '@/lib/room-timeline/turn-live-shell'
import { getAgentTheme } from '@/lib/selectors/conversation-types'
import { mapResultDisplayProps } from '@/lib/room-timeline/map-result-display'
import { useResultStreamDisplay } from '@/hooks/useStreamBuffer'
import { MarkdownContent } from '@/components/markdown-content'
import { AgentCard } from './AgentCard'
import { AgentResultContent } from './AgentResultContent'
import { ProcessingStatusLog } from './ProcessingStatusLog'
import { SynthesisContent, SynthesisContentFromStream } from './SynthesisContent'
import { UserAnswerCard } from './UserAnswerCard'

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

function CollectingBlock({
  phase,
  processingStatusLogs,
  isRunning,
  showProcessingLog,
}: {
  phase?: TurnViewModel['phase']
  processingStatusLogs?: TurnViewModel['processingStatusLogs']
  isRunning: boolean
  showProcessingLog: boolean
}) {
  const theme = getAgentTheme('system:hybro', 'HYBRO AI')
  const synthesizing = phase === 'synthesizing'
  const display = {
    label: synthesizing ? 'Synthesizing' : 'Working',
    tone: 'accent' as const,
    isAnimated: isRunning,
    ariaLabel: `HYBRO AI — ${synthesizing ? 'Synthesizing' : 'Working'}`,
  }

  return (
    <>
      <AgentCard
        agentId="system:hybro"
        agentName="HYBRO AI"
        taskDescription=""
        theme={theme}
        display={display}
        interactive={false}
      />
      {showProcessingLog ? (
        <ProcessingStatusLog entries={processingStatusLogs ?? []} isRunning={isRunning} />
      ) : null}
    </>
  )
}

function ResultHeader({
  result,
  isStreaming,
  displayContent,
}: {
  result: AgentResultViewModel
  isStreaming: boolean
  displayContent?: string
}) {
  const theme = getAgentTheme(result.agentId, result.agentName)
  const display = mapResultDisplayProps(result, isStreaming, displayContent)
  
  // A synthesis result is the final answer, not another delegated task. Live
  // SSE events may have left a task/status label on the same entity; never show
  // that control-plane text above the user-facing answer.
  let taskDescription = result.isSummaryAgent ? '' : (result.taskStatusMessage ?? '')
  if (taskDescription.trim() === result.content.trim()) {
    taskDescription = ''
  }

  return (
    <AgentCard
      agentId={result.agentId ?? result.messageId}
      agentName={result.agentName}
      taskDescription={taskDescription}
      theme={theme}
      display={display}
      agentSource={result.agentSource}
      interactive={false}
    />
  )
}

function FailedBlock({ intro, turnId }: { intro: string; turnId?: string }) {
  const theme = getAgentTheme('system:hybro', 'HYBRO AI')
  const display = {
    label: 'Failed',
    tone: 'danger' as const,
    isAnimated: false,
    ariaLabel: 'HYBRO AI — Failed',
  }

  return (
    <>
      <AgentCard
        agentId="system:hybro"
        agentName="HYBRO AI"
        taskDescription=""
        theme={theme}
        display={display}
        interactive={false}
      />
      <div className="conversation-content-body" data-quote-message-id={turnId} data-quote-agent-name="HYBRO AI" data-quote-source-kind="user_turn">
        <MarkdownContent className="conversation-markdown-body" content={intro} />
      </div>
    </>
  )
}

function CanceledBlock({ intro, turnId }: { intro: string; turnId?: string }) {
  const theme = getAgentTheme('system:hybro', 'HYBRO AI')
  const display = {
    label: 'Canceled',
    tone: 'danger' as const,
    isAnimated: false,
    ariaLabel: 'HYBRO AI — Canceled',
  }

  return (
    <>
      <AgentCard
        agentId="system:hybro"
        agentName="HYBRO AI"
        taskDescription=""
        theme={theme}
        display={display}
        interactive={false}
      />
      <div className="conversation-content-body" data-quote-message-id={turnId} data-quote-agent-name="HYBRO AI" data-quote-source-kind="user_turn">
        <MarkdownContent className="conversation-markdown-body" content={intro} />
      </div>
    </>
  )
}

function DeterministicDoneBlock({
  intro,
  summaryResult,
  turnArtifacts,
  turnId,
  isRunning,
}: {
  intro: string
  summaryResult?: AgentResultViewModel
  turnArtifacts?: TurnViewModel['agentResults'][number]['artifacts']
  turnId?: string
  isRunning: boolean
}) {
  const theme = getAgentTheme('system:hybro', 'HYBRO AI')
  const display = {
    label: 'Combined agent responses',
    tone: 'muted' as const,
    // Keep the avatar spinning while the turn is still active (e.g. single
    // agent already terminal but room processing_status has not completed).
    isAnimated: isRunning,
    ariaLabel: 'HYBRO AI — Combined agent responses',
  }

  if (summaryResult) {
    return (
      <>
        <ResultHeader result={summaryResult} isStreaming={false} />
        <SynthesisContent summaryResult={summaryResult} turnArtifacts={turnArtifacts} />
      </>
    )
  }

  return (
    <>
      <AgentCard
        agentId="system:hybro"
        agentName="HYBRO AI"
        taskDescription=""
        theme={theme}
        display={display}
        interactive={false}
      />
      <div className="conversation-content-body" data-quote-message-id={turnId} data-quote-agent-name="HYBRO AI" data-quote-source-kind="user_turn">
        <MarkdownContent className="conversation-markdown-body" content={intro} />
      </div>
    </>
  )
}

function HitlPrimary({
  turn,
  isRunning,
  showProcessingLog,
}: {
  turn: TurnViewModel
  isRunning: boolean
  showProcessingLog: boolean
}) {
  const hitl = turn.finalAnswer.hitl
  if (!hitl || hitl.prompts.length === 0) {
    return showProcessingLog ? (
      <ProcessingStatusLog entries={turn.processingStatusLogs} isRunning={isRunning} />
    ) : null
  }

  return (
    <div className="flex flex-col" style={{ gap: 'var(--conversation-gap-block)' }}>
      {hitl.prompts.map(p => (
        <div
          key={p.messageId}
          className="rounded-xl border px-4 py-3"
          style={{
            borderColor: 'hsl(var(--agent-color-4) / 0.18)',
            backgroundColor: 'hsl(var(--agent-color-4) / 0.08)',
          }}
        >
          <div className="text-xs font-medium mb-2" style={{ color: 'var(--conversation-text-muted)' }}>
            {p.agentName} · Needs Input
          </div>
          <div
            className="conversation-user-message-text whitespace-pre-wrap"
            style={{ color: 'var(--conversation-text-primary)' }}
          >
            {p.prompt}
          </div>
        </div>
      ))}
      {turn.agentResults
        .filter(r => r.hitlResolved)
        .map(r => (
          <UserAnswerCard
            key={r.messageId}
            agentName={r.agentName}
            question={r.hitlResolved!.prompt}
            answer={r.hitlResolved!.answer}
          />
        ))}
    </div>
  )
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
  const shouldRenderProcessingLog = showProcessingLog && logs.length > 0
  const turnArtifacts = turn.agentResults.flatMap(r => r.artifacts ?? [])

  return (
    <>
      <ResultHeader
        result={summaryResult}
        isStreaming={stream.isStreaming}
        displayContent={stream.content}
      />
      {shouldRenderProcessingLog && (
        <ProcessingStatusLog
          entries={logs}
          isRunning={processingStatusRunning}
        />
      )}
      {supervisorStatus && !stream.isStreaming && (
        <p className="text-xs mt-1" style={{ color: 'var(--conversation-text-muted)' }} aria-live="polite">
          {supervisorStatus}
        </p>
      )}
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
  const summaryResult = turn.agentResults.find(r => r.isSummaryAgent)
  const realAgents = getStripSourceResults(turn)
  const { finalAnswer } = turn
  const shouldShowProcessingLog =
    renderProcessingLog && turn.processingStatusLogs.length > 0
  const processingStatusRunning = isProcessingStatusRunning(turn)
  let processingLogRenderedInBody = false

  let body: ReactNode = null

  switch (finalAnswer.kind) {
    case 'hitl':
      body = (
        <HitlPrimary
          turn={turn}
          isRunning={processingStatusRunning}
          showProcessingLog={renderProcessingLog}
        />
      )
      break
    case 'llm_synthesis':
      if (summaryResult) {
        processingLogRenderedInBody = true
        body = (
          <SynthesisBlock
            turn={turn}
            summaryResult={summaryResult}
            supervisorStatus={supervisorStatus}
            processingStatusLogs={turn.processingStatusLogs ?? []}
            processingStatusRunning={processingStatusRunning}
            showProcessingLog={renderProcessingLog}
          />
        )
      } else {
        processingLogRenderedInBody = true
        body = (
          <CollectingBlock
            phase={turn.phase}
            processingStatusLogs={turn.processingStatusLogs ?? []}
            isRunning={processingStatusRunning}
            showProcessingLog={renderProcessingLog}
          />
        )
      }
      break
    case 'canceled':
      body = <CanceledBlock intro={finalAnswer.canceledIntro ?? ''} turnId={turn.id} />
      break
    case 'failed':
      body = <FailedBlock intro={finalAnswer.failedIntro ?? ''} turnId={turn.id} />
      break
    case 'deterministic_done':
      body = (
        <DeterministicDoneBlock
          intro={finalAnswer.deterministicIntro ?? ''}
          summaryResult={
            summaryResult?.summaryOrigin === 'deterministic' ? summaryResult : undefined
          }
          turnArtifacts={turn.agentResults.flatMap(r => r.artifacts ?? [])}
          turnId={turn.id}
          isRunning={processingStatusRunning}
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
          <CollectingBlock
            phase={turn.phase}
            processingStatusLogs={turn.processingStatusLogs}
            isRunning={processingStatusRunning}
            showProcessingLog={renderProcessingLog}
          />
        )
      }
      break
    }
    case 'pending':
    default:
      processingLogRenderedInBody = true
      body = (
        <CollectingBlock
          phase={turn.phase}
          processingStatusLogs={turn.processingStatusLogs}
          isRunning={processingStatusRunning}
          showProcessingLog={renderProcessingLog}
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
      {shouldShowProcessingLog && !processingLogRenderedInBody && (
        <ProcessingStatusLog entries={turn.processingStatusLogs} isRunning={processingStatusRunning} />
      )}
      {body}
    </div>
  )
}
