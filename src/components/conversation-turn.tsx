// src/components/conversation-turn.tsx
'use client'

import React, { useState, useCallback, useEffect, useMemo } from 'react'
import { cn } from '@/lib/utils'
import { AlertTriangle, ChevronRight, ChevronUp } from 'lucide-react'
import { AgentBadge } from './agent-badge'
import { AgentResultCard } from './agent-result-card'
import { AgentPlaceholderRow } from './agent-placeholder-row'
import { SupervisorHeader } from './supervisor-header'
import type { TurnViewModel } from '@/lib/room-timeline/types'
import { isSystemAgent } from '@/lib/system-agents'
import { TruncatedContent } from './truncated-content'
import { UserAttachmentCard } from './message-bubble'
import type { QuoteData } from '@/lib/types/quote'

import { UserCircle } from 'lucide-react'

// -- User badge --------------------------------------------------------------

function UserBadge() {
  return (
    <div className="flex items-center gap-2 mb-2 px-1">
      <div className="flex items-center justify-center w-7 h-7 rounded-md shrink-0 bg-primary/10 border border-primary/20 text-primary">
        <UserCircle className="h-4 w-4" />
      </div>
      <span className="font-semibold text-base text-foreground">You</span>
    </div>
  )
}

// -- User prompt block -------------------------------------------------------

function UserPromptBlock({
  content,
  attachments,
}: {
  content: string
  attachments: TurnViewModel['userAttachments']
}) {
  if (!content && (!attachments || attachments.length === 0)) return null

  /* Render as an equal participant in the chat room */
  return (
    <div className="py-3" data-testid="user-prompt-wrapper">
      <UserBadge />
      <div className="pl-10 pr-2">
        {content && (
          <TruncatedContent
            content={content}
            maxLines={8}
            isMarkdown={false}
            markdownClassName="text-[15px] font-normal leading-relaxed text-foreground whitespace-pre-wrap break-words"
          />
        )}
        {attachments && attachments.length > 0 && (
          <div className="flex flex-wrap gap-2 pt-2">
            {attachments.map((att) => (
              <UserAttachmentCard key={att.fileId} attachment={att} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// -- Summary block (collapsed state only) ------------------------------------

function SummaryBlock({ summary }: { summary: TurnViewModel['summary'] }) {
  if (!summary) return null

  return (
    <div className="mt-1 space-y-1.5" data-testid="turn-summary">
      <div className="flex items-center gap-2">
        <AgentBadge
          agentId={summary.sourceAgentId}
          agentName={summary.sourceAgentName}
          size="sm"
          hideSource
          showDeletedIndicator={false}
        />
      </div>
      <p className="text-sm font-medium text-foreground/95 leading-snug">
        {summary.title}
      </p>
      <p className="text-sm text-muted-foreground leading-relaxed line-clamp-3">
        {summary.body}
      </p>
    </div>
  )
}

// -- Warning line for failed turns -------------------------------------------

function FailedWarning() {
  return (
    <div className="flex items-center gap-1.5 text-xs text-destructive/90 mt-0.5">
      <AlertTriangle className="h-3 w-3 shrink-0" aria-hidden />
      <span>Some responses failed</span>
    </div>
  )
}

// -- Main component ----------------------------------------------------------

interface ConversationTurnProps {
  turn: TurnViewModel
  index: number
  isActive: boolean
  pendingAgents?: { agentId: string; agentName: string }[]
  onQuote?: (data: QuoteData) => void
}

function ConversationTurn({ turn, index, isActive, pendingAgents, onQuote }: ConversationTurnProps) {
  const [isExpanded, setIsExpanded] = useState(isActive)

  // Auto-collapse when turn stops being active (new user message arrived)
  useEffect(() => {
    if (!isActive) {
      setIsExpanded(false)
    }
  }, [isActive])

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

  // Supervisor header data
  const isCompleted = turn.status === 'completed' || turn.status === 'partial' || turn.status === 'failed'

  // Filter ALL system agents (supervisor_hitl, supervisor_synthesis, summary, etc.)
  // from expanded view. These are internal orchestration entities, never shown to users.
  const visibleResults = useMemo(() =>
    turn.agentResults.filter(r => !isSystemAgent(r.agentId)),
    [turn.agentResults],
  )

  const visibleCount = visibleResults.length
  const expandLabel =
    visibleCount === 0
      ? 'Show details'
      : `Show ${visibleCount} response${visibleCount === 1 ? '' : 's'}`

  return (
    <article
      className={cn('space-y-3', index > 0 && 'pt-7 mt-1 border-t border-border/35')}
      aria-label={`Turn ${index + 1}: ${promptPreview}`}
    >
      {/* User prompt — whole row clickable when collapsed to mirror Cursor “open thread” affordance */}
      <div
        className={cn(
          'rounded-sm transition-colors',
          !isActive && !showExpanded && 'cursor-pointer hover:bg-muted/40 -mx-1 px-1 py-0.5 focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-1',
          isActive || showExpanded ? 'cursor-default' : undefined,
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

      {/* Collapsed: compact summary / warning / single muted expand control */}
      {!showExpanded && (
        <div className="space-y-2 pl-0.5">
          <SummaryBlock summary={turn.summary} />
          {(turn.status === 'failed' || turn.status === 'partial') && (
            <FailedWarning />
          )}
          {turn.agentResults.length > 0 && !turn.summary && (
            <button
              type="button"
              data-testid="turn-expand-button"
              onClick={handleToggle}
              className="group inline-flex items-center gap-1 rounded-md py-1 text-xs text-muted-foreground hover:text-foreground transition-colors focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2"
              aria-label={expandLabel}
            >
              <ChevronRight className="h-3.5 w-3.5 opacity-70 group-hover:opacity-100" aria-hidden />
              <span>{expandLabel}</span>
            </button>
          )}
        </div>
      )}

      {/* Expanded: orchestration + results + optional hide */}
      {showExpanded && (
        <div className="space-y-1 pl-0.5">
          {turn.isSupervisorTurn && (
            <SupervisorHeader
              isCompleted={isCompleted}
              stepNumber={turn.supervisorStage?.stepNumber}
              totalSteps={turn.supervisorStage?.totalSteps}
              details={turn.supervisorStage?.details}
              agentCount={turn.agentResults.length}
              totalDurationMs={turn.agentResults.reduce((sum, r) => sum + (r.durationMs ?? 0), 0) || undefined}
            />
          )}

          {(turn.status === 'failed' || turn.status === 'partial') && (
            <FailedWarning />
          )}

          {visibleResults.map(result => (
            <AgentResultCard key={result.messageId} result={result} onQuote={onQuote} />
          ))}

          {isActive && (turn.status === 'active' || turn.status === 'awaiting_input') && pendingAgents && pendingAgents.length > 0 && (
            <div>
              {pendingAgents.map((agent) => (
                <AgentPlaceholderRow
                  key={agent.agentId}
                  agentId={agent.agentId}
                  agentName={agent.agentName}
                />
              ))}
            </div>
          )}

          {!isActive && (
            <button
              type="button"
              data-testid="turn-collapse-button"
              onClick={handleToggle}
              className="group mt-1 inline-flex items-center gap-1 rounded-md py-1 text-xs text-muted-foreground hover:text-foreground transition-colors focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2"
              aria-label="Hide responses"
            >
              <ChevronUp className="h-3.5 w-3.5 opacity-70 group-hover:opacity-100" aria-hidden />
              <span>Hide</span>
            </button>
          )}
        </div>
      )}
    </article>
  )
}

export const MemoizedTurn = React.memo(ConversationTurn)
