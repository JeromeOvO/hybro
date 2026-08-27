'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Bot,
  Check,
  ChevronRight,
  CircleAlert,
  Clock3,
  GitBranch,
  LoaderCircle,
  MessageCircleQuestion,
  RotateCw,
} from 'lucide-react'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Marker, MarkerContent, MarkerIcon } from '@/components/ui/marker'
import { cn } from '@/lib/utils'
import type { ProcessingStatusLogEntry } from '@/stores/message-store/types'
import type { TraceNode } from '@/stores/trace-store'
import { useTurnDurationLabel } from './turn-trace-time'

interface TurnTracePanelProps {
  nodes: TraceNode[]
  statusEntries: ProcessingStatusLogEntry[]
  isRunning?: boolean
  isWaiting?: boolean
  turnTerminal?: boolean
  startedAt?: string
  durationMs?: number
}

function planActions(node: TraceNode): string[] {
  if (node.planSteps?.length) {
    return node.planSteps.map((step) => `Planned work with ${step.agent}`)
  }
  if (node.chosenAgents?.length) {
    return node.chosenAgents.map((agent) => `Planned work with ${agent}`)
  }
  return ['Planned the next step']
}

function DecisionMarkers({ node }: { node: TraceNode }) {
  return (
    <>
      {planActions(node).map((action, index) => (
        <Marker key={`${node.id}:${index}`} data-kind="decision" className="conversation-trace-action">
          <MarkerIcon><GitBranch /></MarkerIcon>
          <MarkerContent>{action}</MarkerContent>
        </Marker>
      ))}
    </>
  )
}

function RetryMarker() {
  return (
    <Marker data-kind="retry" className="conversation-trace-action conversation-trace-action-neutral">
      <MarkerIcon><RotateCw /></MarkerIcon>
      <MarkerContent>Retried the model call</MarkerContent>
    </Marker>
  )
}

function ToolMarker({
  node,
  turnTerminal,
  isWaiting,
}: {
  node: TraceNode
  turnTerminal: boolean
  isWaiting: boolean
}) {
  const asking = node.toolName === 'request_user_input' || node.toolName === 'ask_user'
  const unavailable = node.status === 'accepted' && turnTerminal
  const running = node.status === 'accepted' && !turnTerminal
  const failed = node.exitCode !== null && node.exitCode !== undefined && node.exitCode !== 0
  const completed = !unavailable && !running && !failed && !(asking && isWaiting)
  const state = unavailable
    ? 'Outcome unavailable'
    : asking && isWaiting
      ? 'Waiting for input'
      : running
        ? 'Running'
        : failed
          ? 'Failed'
          : 'Completed'
  const action = asking ? 'Asking you' : `Called ${node.toolName ?? 'tool'}`
  const StateIcon = asking && isWaiting
    ? MessageCircleQuestion
    : unavailable
      ? Clock3
      : running
        ? LoaderCircle
        : failed
          ? CircleAlert
          : Check

  return (
    <Marker
      data-kind="tool_call"
      data-status={state.toLowerCase().replaceAll(' ', '-')}
      className={cn(
        'conversation-trace-action items-start',
        failed && 'conversation-trace-action-danger',
        (asking && isWaiting) && 'conversation-trace-action-warning',
        unavailable && 'conversation-trace-action-neutral',
        completed && 'conversation-trace-action-success',
      )}
    >
      <MarkerIcon><Bot /></MarkerIcon>
      <MarkerContent className="flex-1">
        <span className="conversation-trace-action-label">{action}</span>
        <span className="conversation-trace-action-status">
          <StateIcon className={cn(running && 'animate-spin motion-reduce:animate-none')} />
          {state}
        </span>
      </MarkerContent>
    </Marker>
  )
}

export function TurnTracePanel({
  nodes,
  statusEntries,
  isRunning = false,
  isWaiting = false,
  turnTerminal = false,
  startedAt,
  durationMs,
}: TurnTracePanelProps) {
  const [isExpanded, setIsExpanded] = useState(() => isRunning || isWaiting)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const visibleNodes = useMemo(
    () => nodes
      .filter((node) => node.kind !== 'llm_call')
      .sort((a, b) => a.receivedAt - b.receivedAt || a.id.localeCompare(b.id)),
    [nodes],
  )
  const observedDurationMs = useMemo(() => {
    if (durationMs !== undefined) return durationMs
    if (!startedAt || isRunning || isWaiting) return undefined
    const started = Date.parse(startedAt)
    if (!Number.isFinite(started)) return undefined
    const observed = [
      ...nodes.map((node) => node.receivedAt),
      ...statusEntries.map((entry) => Date.parse(entry.timestamp)),
    ].filter(Number.isFinite)
    if (observed.length === 0) return 0
    return Math.max(0, Math.max(...observed) - started)
  }, [durationMs, isRunning, isWaiting, nodes, startedAt, statusEntries])
  const duration = useTurnDurationLabel({
    startedAt,
    durationMs: observedDurationMs,
    live: isRunning || isWaiting,
  })
  const label = isRunning ? 'Running' : isWaiting ? 'Waiting for input' : 'Finished'
  const showPreparing = visibleNodes.length === 0 && isRunning

  useEffect(() => {
    const scroll = scrollRef.current
    if (!scroll || !isExpanded || !isRunning) return
    scroll.scrollTop = scroll.scrollHeight
  }, [visibleNodes.length, isExpanded, isRunning])

  return (
    <Collapsible
      open={isExpanded}
      onOpenChange={setIsExpanded}
      className="conversation-trace"
      data-status={label.toLowerCase().replaceAll(' ', '-')}
    >
      <Marker asChild className="conversation-trace-header">
        <CollapsibleTrigger aria-label={`${label}, ${duration}`}>
          <MarkerContent className="conversation-trace-header-content">
            <span className="conversation-trace-run-state" data-status={label.toLowerCase().replaceAll(' ', '-')}>
              {label}
            </span>
            <span className="conversation-trace-duration">{duration}</span>
          </MarkerContent>
          <ChevronRight className={cn('conversation-trace-chevron', isExpanded && 'rotate-90')} />
        </CollapsibleTrigger>
      </Marker>
      <CollapsibleContent>
        <div
          ref={scrollRef}
          className="conversation-trace-log"
          role={isRunning ? 'log' : undefined}
          aria-live={isRunning ? 'polite' : undefined}
        >
          {showPreparing ? (
            <Marker data-kind="preparing" className="conversation-trace-action">
              <MarkerIcon><LoaderCircle className="animate-spin motion-reduce:animate-none" /></MarkerIcon>
              <MarkerContent>
                <span className="conversation-trace-action-label">Preparing a response</span>
                <span className="conversation-trace-action-status">Running</span>
              </MarkerContent>
            </Marker>
          ) : null}
          {visibleNodes.map((node) => {
            if (node.kind === 'decision') return <DecisionMarkers key={node.id} node={node} />
            if (node.kind === 'retry') return <RetryMarker key={node.id} />
            return <ToolMarker key={node.id} node={node} turnTerminal={turnTerminal} isWaiting={isWaiting} />
          })}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
