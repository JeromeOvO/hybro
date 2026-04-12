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

// ── Status indicator ──────────────────────────────────────────

function StatusText({ result }: { result: AgentResultViewModel }) {
  const { status, content } = result

  switch (status) {
    case 'working':
      return (
        <span className="shimmer-text text-xs">
          {content.length > 0 ? 'Generating' : 'Thinking'}
        </span>
      )
    case 'awaiting_input':
      return (
        <span className="shimmer-text-yellow text-xs">
          Needs input
        </span>
      )
    case 'failed':
      return (
        <span className="inline-flex items-center gap-1 text-xs text-destructive/80">
          <AlertTriangle className="h-3 w-3" aria-hidden />
          Failed
        </span>
      )
    case 'completed':
      return null
  }
}

// ── HITL history (legacy compat) ──────────────────────────────

function HitlHistoryList({ history }: { history: { prompt: string; answer: string }[] }) {
  if (!history || history.length === 0) return null

  return (
    <div className="mt-3 space-y-2">
      {history.map((entry, i) => (
        <div key={i} className="border-l-2 border-border/40 pl-3 py-0.5">
          <p className="text-xs text-muted-foreground line-clamp-2">{entry.prompt}</p>
          <p className="text-xs text-foreground/90 font-medium">{entry.answer}</p>
        </div>
      ))}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────

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
      className="py-3 [&+&]:pt-6"
      aria-busy={isStreaming || (isWorking && result.content.length === 0) ? 'true' : undefined}
      data-testid={`agent-result-${result.messageId}`}
    >
      {/* Header: badge + status + inline chips */}
      <div className="flex items-center justify-between gap-2 mb-2 px-1">
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

      {/* Pending HITL */}
      {isAwaitingInput && result.hitlPending && (
        <div className="pl-10">
          <HitlQuestionCard prompt={result.hitlPending.prompt} />
        </div>
      )}

      {/* Resolved HITL */}
      {result.hitlResolved && (
        <div className="pl-10">
          <HitlCompactCard prompt={result.hitlResolved.prompt} answer={result.hitlResolved.answer} />
        </div>
      )}

      {/* Content */}
      <div className="pl-10 pr-2">
        {isEmpty ? (
          <p className="text-[13px] text-muted-foreground/60 italic mt-1">
            No response content
          </p>
        ) : isFailed ? (
          <p className="text-[14px] text-destructive/80 mt-1">{result.content || 'An error occurred'}</p>
        ) : result.content.length > 0 ? (
          <div className={cn('mt-0.5', isStreaming && 'shimmer-text')}>
            <TruncatedContent
              content={result.content}
              maxLines={8}
              className="text-foreground"
              markdownClassName="text-[15px] leading-[1.6]"
            />
          </div>
        ) : null}
      </div>

      {/* Artifacts */}
      {result.artifacts && result.artifacts.length > 0 && (
        <div className="pl-10 mt-3 pr-2">
          <ArtifactList artifacts={result.artifacts} />
        </div>
      )}

      {/* HITL history (legacy — only when no V2 hitl fields) */}
      {!result.hitlResolved && !result.hitlPending && (
        <div className="pl-10">
          <HitlHistoryList history={result.hitlHistory ?? []} />
        </div>
      )}
    </div>
  )
}
