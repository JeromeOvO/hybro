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
}

function isProcessingStatusRunning(turn: TurnViewModel): boolean {
  return turn.status === 'active' && turn.phase !== 'completed' && turn.finalAnswer.kind !== 'hitl'
}

function CollectingBlock({
  phase,
  processingStatusLogs,
  isRunning,
}: {
  phase?: TurnViewModel['phase']
  processingStatusLogs?: TurnViewModel['processingStatusLogs']
  isRunning: boolean
}) {
  const theme = getAgentTheme('supervisor_synthesis', 'HYBRO AI')
  const synthesizing = phase === 'synthesizing'
  const display = {
    label: synthesizing ? 'Synthesizing' : 'Working',
    tone: 'accent' as const,
    isAnimated: true,
    ariaLabel: `HYBRO AI — ${synthesizing ? 'Synthesizing' : 'Working'}`,
  }

  return (
    <>
      <AgentCard
        agentId="supervisor_synthesis"
        agentName="HYBRO AI"
        taskDescription=""
        theme={theme}
        display={display}
        interactive={false}
      />
      <ProcessingStatusLog entries={processingStatusLogs ?? []} isRunning={isRunning} />
    </>
  )
}

function ResultHeader({ result, isStreaming }: { result: AgentResultViewModel; isStreaming: boolean }) {
  const theme = getAgentTheme(result.agentId, result.agentName)
  const display = mapResultDisplayProps(result, isStreaming)
  return (
    <AgentCard
      agentId={result.agentId ?? result.messageId}
      agentName={result.agentName}
      taskDescription={result.taskStatusMessage ?? ''}
      theme={theme}
      display={display}
      agentSource={result.agentSource}
      interactive={false}
    />
  )
}

function FailedBlock({ intro, turnId }: { intro: string; turnId?: string }) {
  const theme = getAgentTheme('supervisor_synthesis', 'HYBRO AI')
  const display = {
    label: 'Failed',
    tone: 'danger' as const,
    isAnimated: false,
    ariaLabel: 'HYBRO AI — Failed',
  }

  return (
    <>
      <AgentCard
        agentId="supervisor_synthesis"
        agentName="HYBRO AI"
        taskDescription=""
        theme={theme}
        display={display}
        interactive={false}
      />
      <div className="conversation-content-body" data-quote-message-id={turnId} data-quote-agent-name="HYBRO AI" data-quote-source-kind="user_turn">
        <MarkdownContent className="conversation-markdown-body text-sm" content={intro} />
      </div>
    </>
  )
}

function CanceledBlock({ intro, turnId }: { intro: string; turnId?: string }) {
  const theme = getAgentTheme('supervisor_synthesis', 'HYBRO AI')
  const display = {
    label: 'Canceled',
    tone: 'danger' as const,
    isAnimated: false,
    ariaLabel: 'HYBRO AI — Canceled',
  }

  return (
    <>
      <AgentCard
        agentId="supervisor_synthesis"
        agentName="HYBRO AI"
        taskDescription=""
        theme={theme}
        display={display}
        interactive={false}
      />
      <div className="conversation-content-body" data-quote-message-id={turnId} data-quote-agent-name="HYBRO AI" data-quote-source-kind="user_turn">
        <MarkdownContent className="conversation-markdown-body text-sm" content={intro} />
      </div>
    </>
  )
}

function DeterministicDoneBlock({
  intro,
  summaryResult,
  turnId,
}: {
  intro: string
  summaryResult?: AgentResultViewModel
  turnId?: string
}) {
  const theme = getAgentTheme('supervisor_synthesis', 'HYBRO AI')
  const display = {
    label: 'Combined agent responses',
    tone: 'muted' as const,
    isAnimated: false,
    ariaLabel: 'HYBRO AI — Combined agent responses',
  }

  if (summaryResult) {
    return (
      <>
        <ResultHeader result={summaryResult} isStreaming={false} />
        <SynthesisContent summaryResult={summaryResult} />
      </>
    )
  }

  return (
    <>
      <AgentCard
        agentId="supervisor_synthesis"
        agentName="HYBRO AI"
        taskDescription=""
        theme={theme}
        display={display}
        interactive={false}
      />
      <div className="conversation-content-body" data-quote-message-id={turnId} data-quote-agent-name="HYBRO AI" data-quote-source-kind="user_turn">
        <MarkdownContent className="conversation-markdown-body text-sm" content={intro} />
      </div>
    </>
  )
}

function HitlPrimary({ turn, isRunning }: { turn: TurnViewModel; isRunning: boolean }) {
  const hitl = turn.finalAnswer.hitl
  if (!hitl || hitl.prompts.length === 0) {
    return <ProcessingStatusLog entries={turn.processingStatusLogs} isRunning={isRunning} />
  }

  return (
    <div className="flex flex-col" style={{ gap: 'var(--conversation-gap-block)' }}>
      {hitl.prompts.map(p => (
        <div
          key={p.messageId}
          className="rounded-xl border px-4 py-3"
          style={{
            borderColor: 'hsl(45 42% 68% / 0.18)',
            backgroundColor: 'hsl(45 42% 68% / 0.08)',
          }}
        >
          <div className="text-xs font-medium mb-2" style={{ color: 'var(--conversation-text-muted)' }}>
            {p.agentName} · Needs Input
          </div>
          <div className="text-sm whitespace-pre-wrap" style={{ color: 'var(--conversation-text-primary)' }}>
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
  summaryResult,
  supervisorStatus,
}: {
  summaryResult: AgentResultViewModel
  supervisorStatus: string | null
}) {
  const stream = useResultStreamDisplay(summaryResult)

  return (
    <>
      <ResultHeader result={summaryResult} isStreaming={stream.isStreaming} />
      {supervisorStatus && !stream.isStreaming && (
        <p className="text-xs mt-1" style={{ color: 'var(--conversation-text-muted)' }} aria-live="polite">
          {supervisorStatus}
        </p>
      )}
      <SynthesisContentFromStream
        stream={stream}
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
}: FinalAnswerSurfaceProps) {
  const supervisorStatus = getSupervisorStatusLine(turn)
  const summaryResult = turn.agentResults.find(r => r.isSummaryAgent)
  const realAgents = getStripSourceResults(turn)
  const { finalAnswer } = turn
  const shouldShowProcessingLog = turn.processingStatusLogs.length > 0
  const processingStatusRunning = isProcessingStatusRunning(turn)
  let processingLogRenderedInBody = false

  let body: ReactNode = null

  switch (finalAnswer.kind) {
    case 'hitl':
      body = <HitlPrimary turn={turn} isRunning={processingStatusRunning} />
      break
    case 'llm_synthesis':
      if (summaryResult) {
        body = <SynthesisBlock summaryResult={summaryResult} supervisorStatus={supervisorStatus} />
      } else {
        processingLogRenderedInBody = true
        body = (
          <CollectingBlock
            phase={turn.phase}
            processingStatusLogs={turn.processingStatusLogs}
            isRunning={processingStatusRunning}
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
          <CollectingBlock
            phase={turn.phase}
            processingStatusLogs={turn.processingStatusLogs}
            isRunning={processingStatusRunning}
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
