// src/components/agent-result-card.tsx
'use client'

import React from 'react'
import { cn } from '@/lib/utils'
import { AgentBadge } from './agent-badge'
import { TruncatedContent } from './truncated-content'
import { ArtifactList } from './artifact-list'
import { InlineChips } from './inline-chips'
import { HitlCompactCard } from './hitl-compact-card'
import { HitlQuestionCard } from './hitl-question-card'
import { AlertTriangle } from 'lucide-react'
import type { AgentResultViewModel } from '@/lib/room-timeline/types'
import type { QuoteData } from './message-bubble'

// ── Status text ────────────────────────────────────────────────

function StatusText({ result }: { result: AgentResultViewModel }) {
  const { status, content } = result

  switch (status) {
    case 'working':
      return (
        <span className="shimmer-text text-sm text-muted-foreground">
          {content.length > 0 ? 'Generating' : 'Thinking'}
        </span>
      )
    case 'awaiting_input':
      return (
        <span className="shimmer-text-yellow text-sm text-muted-foreground">
          Needs input
        </span>
      )
    case 'failed':
      return (
        <span className="inline-flex items-center gap-1 text-xs text-destructive">
          <AlertTriangle className="h-3 w-3" />
          Failed
        </span>
      )
    case 'completed':
      return null
  }
}

// ── HITL history (legacy compat) ───────────────────────────────

function HitlHistoryList({ history }: { history: { prompt: string; answer: string }[] }) {
  if (!history || history.length === 0) return null

  return (
    <div className="mt-3 space-y-2">
      <p className="text-xs font-medium text-muted-foreground">Human-in-the-loop</p>
      {history.map((entry, i) => (
        <div key={i} className="text-xs space-y-0.5 pl-3 border-l-2 border-border/60">
          <p className="text-muted-foreground">Q: {entry.prompt}</p>
          <p className="text-foreground">A: {entry.answer}</p>
        </div>
      ))}
    </div>
  )
}

// ── Main component ──────────────────────────────────────────────

interface AgentResultCardProps {
  result: AgentResultViewModel
  onQuote?: (data: QuoteData) => void
}

export function AgentResultCard({ result, onQuote }: AgentResultCardProps) {
  const isStreaming = result.status === 'working' && result.content.length > 0
  const isEmpty = result.content.trim().length === 0 && result.status === 'completed'
  const isFailed = result.status === 'failed'
  const isWorking = result.status === 'working'
  const isAwaitingInput = result.status === 'awaiting_input'

  return (
    <div
      className="py-3 border-b border-border last:border-b-0"
      aria-busy={isStreaming || (isWorking && result.content.length === 0) ? 'true' : undefined}
      data-testid={`agent-result-${result.messageId}`}
    >
      {/* Header: badge + status + inline chips */}
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 min-w-0">
          <AgentBadge
            agentId={result.agentId}
            agentName={result.agentName}
            agentSource={result.agentSource}
            size="md"
            showDeletedIndicator={result.status !== 'awaiting_input' && result.status !== 'working' && !result.agentId}
          />
          <InlineChips eventCount={result.eventCount} durationMs={result.durationMs} />
        </div>
        <StatusText result={result} />
      </div>

      {/* Pending HITL question card */}
      {isAwaitingInput && result.hitlPending && (
        <HitlQuestionCard prompt={result.hitlPending.prompt} />
      )}

      {/* Resolved HITL compact card */}
      {result.hitlResolved && (
        <HitlCompactCard prompt={result.hitlResolved.prompt} answer={result.hitlResolved.answer} />
      )}

      {/* Content */}
      {isEmpty ? (
        <p className="text-xs text-muted-foreground italic mt-1">
          No response content
        </p>
      ) : isFailed ? (
        <p className="text-xs text-destructive mt-1">{result.content || 'An error occurred'}</p>
      ) : result.content.length > 0 ? (
        <div className={cn('mt-2', isStreaming && 'shimmer-text')}>
          <TruncatedContent
            content={result.content}
            maxLines={6}
            className="text-foreground"
            markdownClassName="text-base"
          />
        </div>
      ) : null}

      {/* Artifacts */}
      <ArtifactList artifacts={result.artifacts} />

      {/* HITL history (legacy compat — only renders if no V2 hitlResolved/hitlPending) */}
      {!result.hitlResolved && !result.hitlPending && (
        <HitlHistoryList history={result.hitlHistory ?? []} />
      )}
    </div>
  )
}
