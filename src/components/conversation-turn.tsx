// src/components/conversation-turn.tsx
'use client'

import React, { useState, useCallback } from 'react'
import { cn } from '@/lib/utils'
import { AlertTriangle, ChevronRight, Paperclip } from 'lucide-react'
import { AgentBadge } from './agent-badge'
import { TurnEventTimeline } from './turn-event-timeline'
import { AgentResultStack } from './agent-result-stack'
import type { TurnViewModel } from '@/lib/room-timeline/types'
import type { QuoteData } from './message-bubble'

// -- User prompt block -------------------------------------------------------

function UserPromptBlock({
  content,
  attachments,
}: {
  content: string
  attachments: TurnViewModel['userAttachments']
}) {
  if (!content && (!attachments || attachments.length === 0)) return null

  return (
    <div className="space-y-1">
      {content && (
        <p className="text-sm text-foreground font-medium whitespace-pre-wrap break-words">
          {content}
        </p>
      )}
      {attachments && attachments.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {attachments.map((att, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1 text-xs text-muted-foreground bg-muted/50 px-2 py-0.5 rounded"
            >
              <Paperclip className="h-3 w-3" />
              <span className="truncate max-w-[120px]">
                {att.fileName || `Attachment ${i + 1}`}
              </span>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

// -- Summary block -----------------------------------------------------------

function SummaryBlock({ summary }: { summary: TurnViewModel['summary'] }) {
  if (!summary) return null

  return (
    <div className="mt-2 space-y-1">
      <div className="flex items-center gap-2">
        <AgentBadge
          agentId={summary.sourceAgentId}
          agentName={summary.sourceAgentName}
          size="sm"
        />
      </div>
      <p className="text-base font-semibold text-foreground leading-snug">
        {summary.title}
      </p>
      <p className="text-sm text-muted-foreground line-clamp-3">
        {summary.body}
      </p>
    </div>
  )
}

// -- Warning line for failed turns -------------------------------------------

function FailedWarning() {
  return (
    <div className="flex items-center gap-1.5 text-xs text-destructive mt-1">
      <AlertTriangle className="h-3.5 w-3.5" />
      <span>One or more agents failed in this turn</span>
    </div>
  )
}

// -- Main component ----------------------------------------------------------

interface ConversationTurnProps {
  turn: TurnViewModel
  index: number
  isActive: boolean
  onQuote?: (data: QuoteData) => void
}

function ConversationTurn({ turn, index, isActive, onQuote }: ConversationTurnProps) {
  const [isExpanded, setIsExpanded] = useState(isActive)

  const handleToggle = useCallback(() => {
    if (!isActive) {
      setIsExpanded(prev => !prev)
    }
  }, [isActive])

  // Active turn is always expanded
  const showExpanded = isActive || isExpanded

  const promptPreview = turn.userContent
    ? turn.userContent.slice(0, 50) + (turn.userContent.length > 50 ? '...' : '')
    : 'System turn'

  return (
    <article
      className="space-y-4"
      aria-label={`Turn ${index + 1}: ${promptPreview}`}
    >
      {/* User prompt -- always visible */}
      <div
        className={cn(
          'cursor-default',
          !isActive && !showExpanded && 'cursor-pointer',
        )}
        onClick={!isActive && !showExpanded ? handleToggle : undefined}
        role={!isActive && !showExpanded ? 'button' : undefined}
        tabIndex={!isActive && !showExpanded ? 0 : undefined}
        onKeyDown={
          !isActive && !showExpanded
            ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  handleToggle()
                }
              }
            : undefined
        }
      >
        <UserPromptBlock
          content={turn.userContent}
          attachments={turn.userAttachments}
        />
      </div>

      {/* Collapsed state: summary + failed warning */}
      {!showExpanded && (
        <>
          <SummaryBlock summary={turn.summary} />
          {(turn.status === 'failed' || turn.status === 'partial') && (
            <FailedWarning />
          )}
          {turn.agentResults.length > 0 && !turn.summary && (
            <button
              type="button"
              onClick={handleToggle}
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              <ChevronRight className="h-3 w-3" />
              <span>
                {turn.agentResults.length} agent{turn.agentResults.length !== 1 ? 's' : ''} responded
              </span>
            </button>
          )}
        </>
      )}

      {/* Expanded state: event rail + summary + agent results */}
      {showExpanded && (
        <>
          {/* Event rail */}
          {turn.events.length > 0 && (
            <TurnEventTimeline events={turn.events} />
          )}

          {/* Summary block with extra top margin */}
          {turn.summary && (
            <div className="mt-2">
              <SummaryBlock summary={turn.summary} />
            </div>
          )}

          {/* Failed warning */}
          {(turn.status === 'failed' || turn.status === 'partial') && (
            <FailedWarning />
          )}

          {/* Agent result stack */}
          <AgentResultStack
            results={turn.agentResults}
            summary={turn.summary}
          />

          {/* Collapse button for non-active expanded turns */}
          {!isActive && (
            <button
              type="button"
              onClick={handleToggle}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              Collapse
            </button>
          )}
        </>
      )}
    </article>
  )
}

export const MemoizedTurn = React.memo(ConversationTurn)
