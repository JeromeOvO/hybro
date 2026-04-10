// src/components/agent-result-card.tsx
'use client'

import React from 'react'
import { cn } from '@/lib/utils'
import { AgentBadge } from './agent-badge'
import { TruncatedContent } from './truncated-content'
import { AlertTriangle } from 'lucide-react'
import type { AgentResultViewModel } from '@/lib/room-timeline/types'

// ── Status indicator ────────────────────────────────────────────

function StatusIndicator({ status }: { status: AgentResultViewModel['status'] }) {
  switch (status) {
    case 'completed':
      return null
    case 'failed':
      return (
        <span className="inline-flex items-center gap-1 text-xs text-destructive">
          <AlertTriangle className="h-3 w-3" />
          <span>Failed</span>
        </span>
      )
    case 'awaiting_input':
      return (
        <span className="text-xs text-muted-foreground">
          Awaiting input...
        </span>
      )
  }
}

// ── Artifact list ───────────────────────────────────────────────

function ArtifactList({ artifacts }: { artifacts: AgentResultViewModel['artifacts'] }) {
  if (!artifacts || artifacts.length === 0) return null

  return (
    <div className="mt-2 space-y-1">
      {artifacts.map(artifact => (
        <div
          key={artifact.artifactId}
          className="text-xs text-muted-foreground flex items-center gap-1.5"
        >
          <span className="h-1 w-1 rounded-full bg-muted-foreground/50 shrink-0" />
          <span className="truncate">{artifact.name || 'Artifact'}</span>
        </div>
      ))}
    </div>
  )
}

// ── HITL history ────────────────────────────────────────────────

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
}

export function AgentResultCard({ result }: AgentResultCardProps) {
  const isStreaming = result.status === 'awaiting_input' && result.content.length > 0
  const isEmpty = result.content.trim().length === 0 && result.status === 'completed'
  const isFailed = result.status === 'failed'

  return (
    <div
      className="py-2"
      aria-busy={isStreaming ? 'true' : undefined}
      data-testid={`agent-result-${result.messageId}`}
    >
      {/* Header: badge + status */}
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <AgentBadge
          agentId={result.agentId}
          agentName={result.agentName}
          agentSource={result.agentSource}
          size="sm"
        />
        <StatusIndicator status={result.status} />
      </div>

      {/* Content */}
      {isEmpty ? (
        <p className="text-xs text-muted-foreground italic">
          No response content
        </p>
      ) : isFailed ? (
        <div className="space-y-1">
          <p className="text-xs text-destructive">{result.content || 'An error occurred'}</p>
        </div>
      ) : (
        <div className={cn(isStreaming && 'shimmer-text')}>
          <TruncatedContent
            content={result.content}
            maxLines={6}
            className="text-sm text-foreground"
          />
        </div>
      )}

      {/* Artifacts */}
      <ArtifactList artifacts={result.artifacts} />

      {/* HITL history */}
      <HitlHistoryList history={result.hitlHistory ?? []} />
    </div>
  )
}
