'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity,
  ChevronRight,
  Cpu,
  GitBranch,
  RotateCw,
  Wrench,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ProcessingStatusLogEntry } from '@/stores/message-store/types'
import type { TraceNode } from '@/stores/trace-store'

interface TurnTracePanelProps {
  nodes: TraceNode[]
  statusEntries: ProcessingStatusLogEntry[]
  isRunning?: boolean
}

type TraceTimelineItem =
  | { kind: 'status'; key: string; timestamp: number; entry: ProcessingStatusLogEntry }
  | { kind: 'trace'; key: string; timestamp: number; node: TraceNode }

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

function renderStatus(entry: ProcessingStatusLogEntry, key: string, active: boolean) {
  return (
    <div
      key={key}
      className={cn(
        'conversation-trace-node',
        active && 'conversation-processing-log-row-active',
      )}
      data-kind="status"
    >
      <span className="conversation-trace-icon"><Activity aria-hidden="true" /></span>
      <div className="conversation-trace-content">
        <span
          className={cn(
            'conversation-trace-title',
            active && 'conversation-processing-log-message-active',
          )}
        >
          {entry.message}
        </span>
      </div>
    </div>
  )
}

export function TurnTracePanel({
  nodes,
  statusEntries,
  isRunning = false,
}: TurnTracePanelProps) {
  const [isExpanded, setIsExpanded] = useState(false)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const items = useMemo<TraceTimelineItem[]>(() => {
    const timeline: TraceTimelineItem[] = []
    for (let index = 0; index < statusEntries.length; index++) {
      const entry = statusEntries[index]
      const parsed = Date.parse(entry.timestamp)
      timeline.push({
        kind: 'status',
        key: `status:${entry.id}`,
        timestamp: Number.isFinite(parsed) ? parsed : index,
        entry,
      })
    }
    for (const node of nodes) {
      timeline.push({
        kind: 'trace',
        key: `trace:${node.id}`,
        timestamp: node.receivedAt,
        node,
      })
    }
    timeline.sort((a, b) => a.timestamp - b.timestamp || a.key.localeCompare(b.key))
    return timeline
  }, [nodes, statusEntries])

  useEffect(() => {
    const scroll = scrollRef.current
    if (!scroll) return
    scroll.scrollTop = scroll.scrollHeight
  }, [items.length, isExpanded])

  if (items.length === 0) return null

  const eventCountLabel = `${items.length} ${items.length === 1 ? 'event' : 'events'}`
  const activeStatusKey = isRunning
    ? [...items].reverse().find((item) => item.kind === 'status')?.key
    : undefined

  return (
    <section
      className={cn(
        'conversation-processing-log conversation-trace',
        isRunning && 'conversation-processing-log-running',
      )}
    >
      <button
        type="button"
        className="conversation-processing-log-trigger"
        style={{ justifyContent: 'flex-start' }}
        aria-expanded={isExpanded}
        aria-label={`Turn Trace, ${eventCountLabel}`}
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
        ref={scrollRef}
        className="conversation-processing-log-scroll"
        role="log"
        aria-live={isRunning ? 'polite' : 'off'}
        data-state={isExpanded ? 'expanded' : 'compact'}
        style={{
          height: isExpanded
            ? 'var(--conversation-processing-log-expanded-height)'
            : 'var(--conversation-processing-log-compact-height)',
        }}
      >
        {items.map((item) =>
          item.kind === 'status'
            ? renderStatus(item.entry, item.key, item.key === activeStatusKey)
            : renderNode(item.node, item.key),
        )}
      </div>
    </section>
  )
}
