'use client'

import { useState } from 'react'
import { ChevronRight, Cpu, GitBranch, RotateCw, Wrench } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { TraceNode } from '@/stores/trace-store'

interface TurnTracePanelProps {
  nodes: TraceNode[]
}

function formatMs(value: number | null | undefined): string | null {
  if (value === null || value === undefined) return null
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}ms`
}

function usageLabel(node: TraceNode): string | null {
  if (!node.usage) return null
  const { input, output } = node.usage
  if (input === null && output === null) return null
  const parts: string[] = []
  if (input !== null) parts.push(`in ${input}`)
  if (output !== null) parts.push(`out ${output}`)
  return parts.join(' · ')
}

function renderNode(node: TraceNode, key: string) {
  const meta: string[] = []
  const duration = formatMs(node.durationMs)
  if (duration) meta.push(duration)
  const usage = usageLabel(node)
  if (usage) meta.push(usage)

  switch (node.kind) {
    case 'decision': {
      const agents = (node.chosenAgents ?? []).join(', ')
      return (
        <div key={key} className="conversation-trace-node" data-kind="decision">
          <span className="conversation-trace-icon"><GitBranch aria-hidden="true" /></span>
          <div className="conversation-trace-content">
            <span className="conversation-trace-title">
              Decision{agents ? ` → ${agents}` : ''}
            </span>
            {node.reason ? (
              <span className="conversation-trace-detail">{node.reason}</span>
            ) : null}
            {node.planSteps && node.planSteps.length > 0 ? (
              <ul className="conversation-trace-plan">
                {node.planSteps.map((step, index) => (
                  <li key={`${key}-step-${index}`}>
                    <strong>{step.agent}</strong>
                    {step.summary ? ` — ${step.summary}` : ''}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </div>
      )
    }
    case 'llm_call': {
      const title = [node.model, node.provider].filter(Boolean).join(' · ')
      const outcome = node.outcome === 'completed' ? '' : node.outcome
      return (
        <div key={key} className="conversation-trace-node" data-kind="llm_call">
          <span className="conversation-trace-icon"><Cpu aria-hidden="true" /></span>
          <div className="conversation-trace-content">
            <span className="conversation-trace-title">
              LLM call{title ? ` · ${title}` : ''}
              {outcome ? ` — ${outcome}` : ''}
            </span>
            {meta.length > 0 ? (
              <span className="conversation-trace-meta">{meta.join(' · ')}</span>
            ) : null}
            {node.finishReason ? (
              <span className="conversation-trace-detail">finish: {node.finishReason}</span>
            ) : null}
          </div>
        </div>
      )
    }
    case 'retry': {
      const delay = formatMs(node.retryDelayMs)
      return (
        <div key={key} className="conversation-trace-node" data-kind="retry">
          <span className="conversation-trace-icon"><RotateCw aria-hidden="true" /></span>
          <div className="conversation-trace-content">
            <span className="conversation-trace-title">
              Retry scheduled (attempt {node.attempt ?? '?'})
            </span>
            {node.errorClass ? (
              <span className="conversation-trace-detail">
                {node.errorClass}{delay ? ` · waiting ${delay}` : ''}
              </span>
            ) : null}
          </div>
        </div>
      )
    }
    case 'tool_call': {
      const accepted = node.status === 'accepted'
      const failed = node.exitCode !== null && node.exitCode !== undefined && node.exitCode !== 0
      return (
        <div
          key={key}
          className="conversation-trace-node"
          data-kind="tool_call"
          data-status={node.status}
        >
          <span className="conversation-trace-icon"><Wrench aria-hidden="true" /></span>
          <div className="conversation-trace-content">
            <span className="conversation-trace-title">
              {node.toolName ?? 'Tool'}
              {accepted ? ' — dispatched' : failed ? ' — failed' : ' — done'}
            </span>
            {meta.length > 0 ? (
              <span className="conversation-trace-meta">{meta.join(' · ')}</span>
            ) : null}
            {node.resultSummary ? (
              <span className="conversation-trace-detail">{node.resultSummary}</span>
            ) : null}
          </div>
        </div>
      )
    }
  }
}

export function TurnTracePanel({ nodes }: TurnTracePanelProps) {
  const [isExpanded, setIsExpanded] = useState(false)
  const nodeCountLabel = `${nodes.length} ${nodes.length === 1 ? 'event' : 'events'}`

  return (
    <section className="conversation-processing-log conversation-trace">
      <button
        type="button"
        className="conversation-processing-log-trigger"
        style={{ justifyContent: 'flex-start' }}
        aria-expanded={isExpanded}
        aria-label={`Turn Trace, ${nodeCountLabel}`}
        onClick={() => setIsExpanded((expanded) => !expanded)}
      >
        <ChevronRight
          className={cn(
            'conversation-processing-log-chevron',
            isExpanded && 'conversation-processing-log-chevron-open',
          )}
          aria-hidden="true"
        />
        <span className="conversation-processing-log-title">Turn Trace</span>
      </button>
      <div
        className="conversation-processing-log-scroll"
        data-state={isExpanded ? 'expanded' : 'compact'}
        style={{
          height: isExpanded
            ? 'var(--conversation-processing-log-expanded-height)'
            : 'var(--conversation-processing-log-compact-height)',
        }}
      >
        {nodes.length === 0 ? (
          <div className="conversation-processing-log-row">
            <span className="conversation-processing-log-message">
              No trace events for this turn.
            </span>
          </div>
        ) : (
          nodes.map((node) => renderNode(node, node.id))
        )}
      </div>
    </section>
  )
}
