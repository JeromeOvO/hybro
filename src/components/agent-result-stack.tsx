// src/components/agent-result-stack.tsx
'use client'

import React from 'react'
import { AgentResultCard } from './agent-result-card'
import type { AgentResultViewModel, TurnSummaryViewModel } from '@/lib/room-timeline/types'

// ── Sort order ──────────────────────────────────────────────────

function sortPriority(
  result: AgentResultViewModel,
  summarySourceId: string | undefined,
): number {
  // 0: summary source agent (highest priority)
  if (summarySourceId && result.agentId === summarySourceId) return 0
  // 1: completed with content
  if (result.status === 'completed' && result.content.trim().length > 0) return 1
  // 2: awaiting input
  if (result.status === 'awaiting_input') return 2
  // 3: failed
  if (result.status === 'failed') return 3
  // 4: completed but empty
  if (result.status === 'completed' && result.content.trim().length === 0) return 4
  return 5
}

// ── Main component ──────────────────────────────────────────────

interface AgentResultStackProps {
  results: AgentResultViewModel[]
  summary?: TurnSummaryViewModel | null
}

export function AgentResultStack({ results, summary }: AgentResultStackProps) {
  if (results.length === 0) return null

  const summarySourceId = summary?.sourceAgentId
  const sorted = [...results].sort(
    (a, b) => sortPriority(a, summarySourceId) - sortPriority(b, summarySourceId),
  )

  return (
    <div className="space-y-3" data-testid="agent-result-stack">
      {sorted.map(result => (
        <AgentResultCard key={result.messageId} result={result} />
      ))}
    </div>
  )
}
