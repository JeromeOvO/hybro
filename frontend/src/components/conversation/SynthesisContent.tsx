'use client'

import { useResultStreamDisplay, type ResultStreamDisplay } from '@/hooks/useStreamBuffer'
import { MarkdownContent } from '@/components/markdown-content'
import { ArtifactList } from '@/components/artifact-list'
import type { ArtifactData } from '@/stores/message-store/types'
import type { AgentResultViewModel } from '@/lib/room-timeline/types'

function SynthesisStreamingPlaceholder() {
  return (
    <div
      className="conversation-content-body conversation-card-shimmer relative rounded-xl border px-4 py-6 min-h-16"
      style={{ borderColor: 'var(--conversation-border-subtle)' }}
      aria-busy="true"
      aria-label="Synthesis in progress"
    >
      <div className="text-sm" style={{ color: 'var(--conversation-text-muted)' }}>
        Synthesizing responses…
      </div>
    </div>
  )
}

export interface SynthesisContentBodyProps {
  content: string
  isStreaming: boolean
  artifacts: ArtifactData[] | undefined
  turnArtifacts?: ArtifactData[] | undefined
  messageId?: string
  agentName?: string
}

/** Presentational synthesis body — no store subscription. */
export function SynthesisContentBody({
  content,
  isStreaming,
  artifacts,
  turnArtifacts,
  messageId,
  agentName,
}: SynthesisContentBodyProps) {
  if (!content.trim() && isStreaming) {
    return <SynthesisStreamingPlaceholder />
  }

  const effectiveArtifacts = (artifacts && artifacts.length > 0) ? artifacts : turnArtifacts
  const nonTextArtifacts = effectiveArtifacts?.filter(
    a => !a.parts.every(p => p.kind === 'text'),
  )

  return (
    <div data-quote-message-id={messageId} data-quote-agent-name={agentName} data-quote-source-kind="synthesis">
      <div className={`conversation-content-body ${isStreaming ? 'conversation-streaming-cursor' : ''}`}>
        <MarkdownContent className="conversation-markdown-body" content={content} isStreaming={isStreaming} />
      </div>
      {nonTextArtifacts && nonTextArtifacts.length > 0 && (
        <ArtifactList artifacts={nonTextArtifacts} />
      )}
    </div>
  )
}

interface SynthesisContentProps {
  summaryResult: AgentResultViewModel
  turnArtifacts?: ArtifactData[]
}

/** Subscribes to stream buffer for summaryResult; use SynthesisContentBody when parent already has stream. */
export function SynthesisContent({ summaryResult, turnArtifacts }: SynthesisContentProps) {
  const stream = useResultStreamDisplay(summaryResult)
  return (
    <SynthesisContentBody
      content={stream.content}
      isStreaming={stream.isStreaming}
      artifacts={stream.artifacts}
      turnArtifacts={turnArtifacts}
      messageId={summaryResult.messageId}
      agentName={summaryResult.agentName}
    />
  )
}

/** Render synthesis body from a precomputed stream overlay (no extra subscription). */
export function SynthesisContentFromStream({
  stream,
  turnArtifacts,
  messageId,
  agentName,
}: {
  stream: ResultStreamDisplay
  turnArtifacts?: ArtifactData[]
  messageId?: string
  agentName?: string
}) {
  return (
    <SynthesisContentBody
      content={stream.content}
      isStreaming={stream.isStreaming}
      artifacts={stream.artifacts}
      turnArtifacts={turnArtifacts}
      messageId={messageId}
      agentName={agentName}
    />
  )
}
