'use client'

import { useEffect, useRef } from 'react'
import {
  Bot,
  ChevronRight,
  LoaderCircle,
  RotateCw,
  GitBranch,
} from 'lucide-react'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Marker, MarkerContent, MarkerIcon } from '@/components/ui/marker'
import { cn } from '@/lib/utils'
import type { TurnActivityItem, TurnProjection } from '@/lib/turn-lifecycle/types'
import { useTurnPresentationStore } from '@/stores/turn-presentation-store'
import { useTurnDurationLabel } from './turn-trace-time'

function traceState(turn: TurnProjection): 'Running' | 'Waiting for input' | 'Finished' {
  if (turn.state === 'active') return 'Running'
  if (turn.state === 'awaiting_input') return 'Waiting for input'
  return 'Finished'
}

function RetryMarker() {
  return (
    <Marker data-kind="retry" className="canonical-trace-marker conversation-trace-action-neutral">
      <MarkerIcon><RotateCw /></MarkerIcon>
      <MarkerContent>Retried the model call</MarkerContent>
    </Marker>
  )
}

function DecisionMarker({
  item,
}: {
  item: Extract<TurnActivityItem, { kind: 'decision' }>
}) {
  const copy = {
    interaction_received: {
      label: 'Agent requested input',
      detail: item.questionSummary,
    },
    answered_from_context: {
      label: `Answered ${item.agentLabel ?? 'the agent'} from available information`,
      detail: item.questionSummary ?? item.sourceSummary,
    },
    forwarded_to_user: {
      label: `Forwarding ${item.agentLabel ?? 'the agent'}'s questions`,
      detail: item.questionSummary ?? item.sourceSummary,
    },
    no_progress: {
      label: 'Stopped: the agent made no progress',
      detail: item.reason,
    },
    degraded_to_user: {
      label: 'Handed the question to you',
      detail: item.reason ?? item.questionSummary,
    },
  }[item.decision]
  return (
    <Marker data-kind="decision" className="canonical-trace-marker conversation-trace-action-neutral">
      <MarkerIcon><GitBranch /></MarkerIcon>
      <MarkerContent className="flex-1">
        <span className="conversation-trace-action-label">{copy.label}</span>
        {copy.detail ? (
          <span className="conversation-trace-action-status">{copy.detail}</span>
        ) : null}
      </MarkerContent>
    </Marker>
  )
}

function ToolMarker({
  item,
}: {
  item: Extract<TurnActivityItem, { kind: 'tool' }>
}) {
  const running = item.status === 'running'
  const waiting = item.status === 'suspended'
  const failed = item.status === 'failed'
  const canceled = item.status === 'canceled'
  const completed = item.status === 'completed'
  const agent = item.executionKind === 'agent'
  const asking = item.label === 'request_user_input'
  const action = asking
    ? 'Asking you'
    : agent
      ? `Called ${item.targetName ?? item.label}`
      : `Used ${item.label}`
  const state = waiting
    ? 'Waiting for input'
    : running
      ? 'Running'
      : failed
        ? 'Failed'
        : canceled
          ? 'Canceled'
          : 'Completed'
  return (
    <Marker
      data-kind={agent ? 'agent-call' : 'tool'}
      data-call-id={item.toolCallId}
      data-status={waiting ? 'awaiting_input' : item.status}
      className={cn(
        'canonical-trace-marker items-start',
        failed && 'conversation-trace-action-danger',
        waiting && 'conversation-trace-action-warning',
        completed && 'conversation-trace-action-success',
      )}
    >
      <MarkerIcon><Bot /></MarkerIcon>
      <MarkerContent className="flex-1">
        <span className="conversation-trace-action-label">{action}</span>
        <span className="conversation-trace-action-status">{state}</span>
      </MarkerContent>
    </Marker>
  )
}

export function CanonicalTurnTrace({ turn }: { turn: TurnProjection }) {
  const sectionRef = useRef<HTMLElement | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const collapsePendingRef = useRef(false)
  const presentation = useTurnPresentationStore((state) => state.turns[turn.id])
  const setExpanded = useTurnPresentationStore((state) => state.setExpanded)
  const consumeAutoCollapse = useTurnPresentationStore((state) => state.consumeAutoCollapse)
  const setPinnedToBottom = useTurnPresentationStore((state) => state.setPinnedToBottom)
  const terminal = turn.state === 'completed' || turn.state === 'failed' || turn.state === 'canceled'
  const expanded = presentation?.expanded ?? !terminal
  const pinnedToBottom = presentation?.pinnedToBottom ?? true
  const ordered = turn.activity
    .filter((item): item is Exclude<TurnActivityItem, { kind: 'assistant' }> => item.kind !== 'assistant')
    .sort((left, right) => left.order - right.order || left.id.localeCompare(right.id))
  const live = turn.state === 'active'
  const waiting = turn.state === 'awaiting_input'
  const label = traceState(turn)
  const duration = useTurnDurationLabel({
    startedAt: turn.startedAt,
    durationMs: turn.durationMs,
    live: live || waiting,
  })
  const showPreparing = live && ordered.length === 0
  const liveCursor = `${ordered.length}:${turn.currentAssistant?.text.length ?? 0}`

  useEffect(() => {
    if (!terminal || presentation?.autoCollapseConsumed) return
    const focusInside = sectionRef.current?.contains(document.activeElement) ?? false
    if (focusInside) {
      collapsePendingRef.current = true
      return
    }
    consumeAutoCollapse(turn.id, presentation?.manualAction !== 'collapsed')
  }, [terminal, presentation?.autoCollapseConsumed, presentation?.manualAction, consumeAutoCollapse, turn.id])

  useEffect(() => {
    const scroll = scrollRef.current
    if (!expanded || !scroll || !pinnedToBottom) return
    scroll.scrollTop = scroll.scrollHeight
  }, [expanded, liveCursor, pinnedToBottom])

  const finishDeferredCollapse = () => {
    if (!collapsePendingRef.current) return
    collapsePendingRef.current = false
    consumeAutoCollapse(turn.id, presentation?.manualAction !== 'collapsed')
  }

  return (
    <Collapsible
      open={expanded}
      onOpenChange={(open) => setExpanded(turn.id, open, true)}
      asChild
    >
      <section
        ref={sectionRef}
        className="canonical-turn-trace"
        data-canonical-turn-trace
        data-status={label.toLowerCase().replaceAll(' ', '-')}
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) finishDeferredCollapse()
        }}
      >
        <Marker asChild className="conversation-trace-header">
          <CollapsibleTrigger aria-label={`${label}, ${duration}`}>
            <MarkerContent className="conversation-trace-header-content">
              <span className="conversation-trace-run-state" data-status={label.toLowerCase().replaceAll(' ', '-')}>
                {label}
              </span>
              <span className="conversation-trace-duration">{duration}</span>
            </MarkerContent>
            <ChevronRight className={cn('conversation-trace-chevron', expanded && 'rotate-90')} />
          </CollapsibleTrigger>
        </Marker>
        <CollapsibleContent>
          <div
            ref={scrollRef}
            className="conversation-trace-log canonical-turn-trace-log"
            role={live ? 'log' : undefined}
            aria-live={live ? 'polite' : undefined}
            onScroll={(event) => {
              const target = event.currentTarget
              const pinned = target.scrollHeight - target.scrollTop - target.clientHeight <= 16
              setPinnedToBottom(turn.id, pinned)
            }}
          >
            {showPreparing ? (
              <Marker data-kind="preparing" className="canonical-trace-marker">
                <MarkerIcon><LoaderCircle className="animate-spin motion-reduce:animate-none" /></MarkerIcon>
                <MarkerContent>
                  <span className="conversation-trace-action-label">Preparing a response</span>
                  <span className="conversation-trace-action-status">Running</span>
                </MarkerContent>
              </Marker>
            ) : null}
            {ordered.map((item) => {
              if (item.kind === 'retry') return <RetryMarker key={item.id} />
              if (item.kind === 'decision') return <DecisionMarker key={item.id} item={item} />
              return <ToolMarker key={item.id} item={item} />
            })}
          </div>
        </CollapsibleContent>
      </section>
    </Collapsible>
  )
}
