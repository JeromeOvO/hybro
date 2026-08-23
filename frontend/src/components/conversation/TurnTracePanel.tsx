'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity,
  Bot,
  Check,
  ChevronRight,
  CircleAlert,
  Clock3,
  Cpu,
  GitBranch,
  LoaderCircle,
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
  runStatus?: string
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
  if (input !== null) parts.push(`${input} input`)
  if (output !== null) parts.push(`${output} output`)
  return parts.join(' · ')
}

function formatPayload(value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function AssistantPlan({ node }: { node: TraceNode }) {
  const agents = node.chosenAgents ?? []
  return (
    <article className="conversation-trace-block conversation-trace-plan-block" data-kind="decision">
      <header className="conversation-trace-block-header">
        <span className="conversation-trace-block-icon"><GitBranch aria-hidden="true" /></span>
        <div className="conversation-trace-block-heading">
          <strong>Assistant plan</strong>
          {agents.length > 0 ? <span>{agents.join(' · ')}</span> : null}
        </div>
      </header>
      {node.reason ? <p className="conversation-trace-block-copy">{node.reason}</p> : null}
      {node.planSteps && node.planSteps.length > 0 ? (
        <ol className="conversation-trace-step-list">
          {node.planSteps.map((step, index) => (
            <li key={`${node.id}:step:${index}`}>
              <span className="conversation-trace-step-index">{index + 1}</span>
              <span>
                <strong>{step.agent}</strong>
                {step.summary ? <span>{step.summary}</span> : null}
              </span>
            </li>
          ))}
        </ol>
      ) : null}
    </article>
  )
}

function ModelCall({ node }: { node: TraceNode }) {
  const meta = [formatMs(node.durationMs), usageLabel(node)].filter(Boolean)
  const model = [node.model, node.provider].filter(Boolean).join(' · ')
  return (
    <div className="conversation-trace-model-row" data-kind="llm_call">
      <span className="conversation-trace-model-icon"><Cpu aria-hidden="true" /></span>
      <span className="conversation-trace-model-copy">
        <strong>Model response</strong>
        {model ? <span>{model}</span> : null}
      </span>
      {meta.length > 0 ? <span className="conversation-trace-model-meta">{meta.join(' · ')}</span> : null}
      {node.outcome && node.outcome !== 'completed' ? (
        <span className="conversation-trace-state" data-tone="danger">{node.outcome}</span>
      ) : null}
    </div>
  )
}

function RetryRow({ node }: { node: TraceNode }) {
  const delay = formatMs(node.retryDelayMs)
  return (
    <div className="conversation-trace-retry" data-kind="retry">
      <RotateCw aria-hidden="true" />
      <span>
        Retrying model call{node.attempt ? ` · attempt ${node.attempt}` : ''}
        {node.errorClass ? ` · ${node.errorClass}` : ''}
        {delay ? ` · in ${delay}` : ''}
      </span>
    </div>
  )
}

function ToolCall({ node }: { node: TraceNode }) {
  const input = formatPayload(node.argSummary)
  const output = node.resultSummary?.trim() || null
  const running = node.status === 'accepted'
  const failed = node.exitCode !== null && node.exitCode !== undefined && node.exitCode !== 0
  const state = running ? 'Running' : failed ? 'Failed' : 'Completed'
  const StateIcon = running ? LoaderCircle : failed ? CircleAlert : Check
  const meta = [formatMs(node.durationMs)].filter(Boolean)

  return (
    <article className="conversation-trace-block conversation-trace-tool-block" data-kind="tool_call" data-status={state.toLowerCase()}>
      <header className="conversation-trace-block-header">
        <span className="conversation-trace-block-icon"><Wrench aria-hidden="true" /></span>
        <div className="conversation-trace-block-heading">
          <strong>{node.toolName ?? 'Tool call'}</strong>
          {meta.length > 0 ? <span>{meta.join(' · ')}</span> : null}
        </div>
        <span className="conversation-trace-state" data-tone={failed ? 'danger' : running ? 'active' : 'success'}>
          <StateIcon className={cn(running && 'animate-spin')} aria-hidden="true" />
          {state}
        </span>
      </header>
      {input ? (
        <div className="conversation-trace-io">
          <span>Input</span>
          <pre>{input}</pre>
        </div>
      ) : null}
      {output ? (
        <div className="conversation-trace-io">
          <span>{failed ? 'Error' : 'Output'}</span>
          <pre>{output}</pre>
        </div>
      ) : running ? (
        <div className="conversation-trace-tool-waiting">Waiting for tool output…</div>
      ) : null}
    </article>
  )
}

function ProgressDetails({ entries, open }: { entries: ProcessingStatusLogEntry[]; open: boolean }) {
  const deduped = entries.filter((entry, index) => (
    index === 0 || entry.message !== entries[index - 1]?.message
  ))
  if (deduped.length === 0) return null
  return (
    <details className="conversation-trace-progress" open={open}>
      <summary>
        <Activity aria-hidden="true" />
        <span>Progress</span>
        <span>{deduped.length} updates</span>
      </summary>
      <ol>
        {deduped.map((entry) => (
          <li key={entry.id}>
            <span className="conversation-trace-progress-dot" />
            <span>{entry.message}</span>
          </li>
        ))}
      </ol>
    </details>
  )
}

function statusLabel(runStatus: string | undefined, isRunning: boolean): string {
  if (isRunning) return 'Running'
  if (runStatus === 'failed') return 'Failed'
  if (runStatus === 'canceled') return 'Canceled'
  if (runStatus === 'completed') return 'Completed'
  return 'Finished'
}

export function TurnTracePanel({
  nodes,
  statusEntries,
  isRunning = false,
  runStatus,
}: TurnTracePanelProps) {
  const [isExpanded, setIsExpanded] = useState(false)
  const sectionRef = useRef<HTMLElement | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const orderedNodes = useMemo(
    () => [...nodes].sort((a, b) => a.receivedAt - b.receivedAt || a.id.localeCompare(b.id)),
    [nodes],
  )

  useEffect(() => {
    const scroll = scrollRef.current
    if (!scroll) return
    scroll.scrollTop = isExpanded && !isRunning ? 0 : scroll.scrollHeight
  }, [orderedNodes.length, statusEntries.length, isExpanded, isRunning])

  if (orderedNodes.length === 0 && statusEntries.length === 0) return null

  const tools = orderedNodes.filter((node) => node.kind === 'tool_call').length
  const label = statusLabel(runStatus, isRunning)

  return (
    <section ref={sectionRef} className={cn('conversation-processing-log conversation-trace', isRunning && 'conversation-processing-log-running')}>
      <button
        type="button"
        className="conversation-processing-log-trigger conversation-trace-trigger"
        aria-expanded={isExpanded}
        aria-label={`Turn Trace, ${label}`}
        onClick={() => setIsExpanded((expanded) => {
          const next = !expanded
          if (next) {
            requestAnimationFrame(() => sectionRef.current?.scrollIntoView({ block: 'center' }))
          }
          return next
        })}
      >
        <ChevronRight className={cn('conversation-processing-log-chevron', isExpanded && 'conversation-processing-log-chevron-open')} aria-hidden="true" />
        <Bot className="conversation-trace-trigger-icon" aria-hidden="true" />
        <span className="conversation-processing-log-title">Turn Trace</span>
        {tools > 0 ? <span className="conversation-trace-count">{tools} tool {tools === 1 ? 'call' : 'calls'}</span> : null}
        <span className="conversation-trace-run-state" data-status={label.toLowerCase()}>
          {isRunning ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Clock3 aria-hidden="true" />}
          {label}
        </span>
      </button>
      {isExpanded ? (
        <div
          ref={scrollRef}
          className="conversation-processing-log-scroll conversation-trace-scroll"
          role="log"
          aria-live={isRunning ? 'polite' : 'off'}
          data-state="expanded"
          style={{ height: 'var(--conversation-processing-log-expanded-height)' }}
        >
          <div className="conversation-trace-stack">
            {orderedNodes.map((node) => {
              if (node.kind === 'decision') return <AssistantPlan key={node.id} node={node} />
              if (node.kind === 'llm_call') return <ModelCall key={node.id} node={node} />
              if (node.kind === 'retry') return <RetryRow key={node.id} node={node} />
              return <ToolCall key={node.id} node={node} />
            })}
            <ProgressDetails entries={statusEntries} open={orderedNodes.length === 0} />
          </div>
        </div>
      ) : null}
    </section>
  )
}
