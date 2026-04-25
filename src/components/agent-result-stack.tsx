// src/components/agent-result-stack.tsx
'use client'

import React from 'react'
import { AgentResultCard } from './agent-result-card'
import type { AgentResultViewModel, TurnSummaryViewModel } from '@/lib/room-timeline/types'
import type { QuoteData } from './message-bubble'

// ── Sort order ──────────────────────────────────────────────────

function sortPriority(
  result: AgentResultViewModel,
  summarySourceId: string | undefined,
): number {
  const hasVisibleBody = result.content.trim().length > 0 || result.artifacts.length > 0
  // 0: summary source agent (highest priority)
  if (summarySourceId && result.agentId === summarySourceId) return 0
  // 1: completed with content
  if (result.status === 'completed' && hasVisibleBody) return 1
  // 2: working (streaming or thinking)
  if (result.status === 'working') return 2
  // 3: awaiting input
  if (result.status === 'awaiting_input') return 3
  // 4: failed
  if (result.status === 'failed') return 4
  // 5: completed but empty
  if (result.status === 'completed' && !hasVisibleBody) return 5
  return 6
}

// ── Main component ──────────────────────────────────────────────

interface AgentResultStackProps {
  results: AgentResultViewModel[]
  summary?: TurnSummaryViewModel | null
  onQuote?: (data: QuoteData) => void
}

export function AgentResultStack({ results, summary, onQuote }: AgentResultStackProps) {
  if (results.length === 0) return null

  const summarySourceId = summary?.sourceAgentId
  const sorted = [...results].sort(
    (a, b) => sortPriority(a, summarySourceId) - sortPriority(b, summarySourceId),
  )

  return (
    <div className="space-y-3" data-testid="agent-result-stack">
      {sorted.map(result => (
        <AgentResultCard key={result.messageId} result={result} onQuote={onQuote} />
      ))}
    </div>
  )
}
