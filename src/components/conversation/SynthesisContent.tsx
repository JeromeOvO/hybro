'use client'

import { useStreamingStore } from '@/stores/streaming-store'
import { MarkdownContent } from '@/components/markdown-content'
import { ArtifactList } from '@/components/artifact-list'
import type { AgentResultViewModel } from '@/lib/room-timeline/types'

function SynthesisStreamingPlaceholder() {
  return (
    <div
      className="conversation-content-body conversation-card-shimmer relative rounded-xl border px-4 py-6 min-h-[4rem]"
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

interface SynthesisContentProps {
  summaryResult: AgentResultViewModel
}

export function SynthesisContent({ summaryResult }: SynthesisContentProps) {
  const buffer = useStreamingStore(s => s.buffers[summaryResult.messageId])
  const content = buffer?.text ?? summaryResult.content
  const isStreaming = buffer ? !buffer.isComplete : summaryResult.status === 'working'
  const artifacts = buffer?.artifacts ?? summaryResult.artifacts

  if (!content.trim() && isStreaming) {
    return <SynthesisStreamingPlaceholder />
  }

  return (
    <div>
      <div className={`conversation-content-body ${isStreaming ? 'conversation-streaming-cursor' : ''}`}>
        <MarkdownContent className="conversation-markdown-body" content={content} isStreaming={isStreaming} />
      </div>
      {artifacts && artifacts.length > 0 && (
        <ArtifactList artifacts={artifacts} />
      )}
    </div>
  )
}
