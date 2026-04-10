// src/components/turn-event-timeline.tsx
'use client'

import React, { useState } from 'react'
import { cn } from '@/lib/utils'
import { ChevronRight } from 'lucide-react'
import { getAgentColorClasses } from '@/lib/agent-colors'
import {
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
} from '@/components/ui/collapsible'
import type { TimelineEventViewModel } from '@/lib/room-timeline/types'

// ── Helpers ─────────────────────────────────────────────────────

function formatTimestamp(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    })
  } catch {
    return ''
  }
}

// ── Inline artifact preview ─────────────────────────────────────

function ArtifactPreview({ event }: { event: TimelineEventViewModel }) {
  if (event.kind !== 'artifact_emitted' || !event.artifactPayload) return null

  const artifact = event.artifactPayload
  const firstFilePart = artifact.parts.find(p => p.kind === 'file' && p.file)
  const isImage = firstFilePart?.file?.mime_type?.startsWith('image/')

  return (
    <div className="ml-[72px] mt-0.5 mb-1">
      {isImage && firstFilePart?.file?.uri ? (
        <div className="w-16 h-16 rounded bg-muted/50 overflow-hidden">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={firstFilePart.file.uri}
            alt={artifact.name || 'Artifact preview'}
            className="w-full h-full object-cover"
          />
        </div>
      ) : (
        <div className="inline-flex items-center gap-1.5 text-xs text-muted-foreground bg-muted/30 px-2 py-1 rounded">
          <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/50" />
          <span className="truncate max-w-[200px]">{artifact.name || 'Artifact'}</span>
        </div>
      )}
    </div>
  )
}

// ── Event row ───────────────────────────────────────────────────

interface EventRowProps {
  event: TimelineEventViewModel
  isNew?: boolean
}

function EventRow({ event, isNew }: EventRowProps) {
  const colors = event.agentId ? getAgentColorClasses(event.agentId) : null
  const dotColor = colors ? colors.accent : 'bg-muted-foreground'

  return (
    <div
      className={cn(
        'flex items-center gap-3 min-h-[20px] md:min-h-[24px]',
        'min-h-[44px] md:min-h-[20px]',
        isNew && 'animate-event-slide-in',
      )}
    >
      {/* Dot */}
      <div className="relative flex items-center justify-center w-3 shrink-0">
        <span
          className={cn(
            'h-2 w-2 rounded-full',
            dotColor,
            isNew && 'animate-dot-pulse',
            event.isLive && 'animate-breathing-glow',
          )}
          data-testid={event.isLive ? 'live-dot' : 'event-dot'}
          aria-hidden="true"
        />
      </div>

      {/* Timestamp */}
      <span className="text-[11px] font-mono text-muted-foreground tabular-nums shrink-0 w-[60px]">
        {formatTimestamp(event.timestamp)}
      </span>

      {/* Label */}
      <span className="text-xs text-muted-foreground truncate">
        {event.label}
      </span>
    </div>
  )
}

// ── Main component ──────────────────────────────────────────────

interface TurnEventTimelineProps {
  events: TimelineEventViewModel[]
}

export function TurnEventTimeline({ events }: TurnEventTimelineProps) {
  const [showHidden, setShowHidden] = useState(false)

  if (events.length === 0) return null

  const visibleEvents = events.filter(e => !e.isHiddenInCompact)
  const hiddenEvents = events.filter(e => e.isHiddenInCompact)
  const displayEvents = showHidden ? events : visibleEvents

  const hiddenCount = hiddenEvents.length

  return (
    <div role="log" aria-live="polite" aria-label="Agent activity log">
      {/* Vertical rail line + events */}
      <div className="relative pl-1.5">
        {/* Vertical connector line */}
        <div
          className="absolute left-[7px] top-2 bottom-2 w-px bg-border/60"
          aria-hidden="true"
        />

        {/* Event rows */}
        <div className="space-y-0">
          {displayEvents.map(event => (
            <React.Fragment key={event.id}>
              <EventRow event={event} isNew={event.isLive} />
              <ArtifactPreview event={event} />
            </React.Fragment>
          ))}
        </div>
      </div>

      {/* Show process toggle */}
      {hiddenCount > 0 && (
        <Collapsible open={showHidden} onOpenChange={setShowHidden}>
          <CollapsibleTrigger asChild>
            <button
              type="button"
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors mt-1 ml-1.5"
              data-testid="show-process-toggle"
            >
              <ChevronRight
                className={cn(
                  'h-3 w-3 transition-transform duration-150',
                  showHidden && 'rotate-90',
                )}
              />
              <span>
                {showHidden
                  ? 'Hide process'
                  : `Show process (${hiddenCount} events)`}
              </span>
            </button>
          </CollapsibleTrigger>
        </Collapsible>
      )}

      {/* Mobile summary (collapsed by default on < 768px) */}
      <div className="md:hidden">
        {!showHidden && events.length > 0 && hiddenCount === 0 && (
          <p className="text-xs text-muted-foreground ml-6 mt-1">
            {events.length} event{events.length !== 1 ? 's' : ''}
          </p>
        )}
      </div>
    </div>
  )
}
