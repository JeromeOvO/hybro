'use client'

import React, { useState, useCallback, useEffect, useMemo } from 'react'
import { cn } from '@/lib/utils'
import { ChevronRight, ChevronUp, AlertTriangle, Settings } from 'lucide-react'
import { getAgentAvatarUri } from '@/lib/agent-avatar'
import { isSystemAgent } from '@/lib/system-agents'
import { LinkifiedContent } from '@/components/markdown-content'
import { UserAttachmentCard } from '@/components/message-bubble'
import type { TurnViewModel } from '@/lib/room-timeline/types'
import type { QuoteData } from '@/components/message-bubble'
import { CursorMessageRow } from './cursor-message-row'
import { CursorUserMessage } from './cursor-user-message'
import { CursorAgentMessage } from './cursor-agent-message'
import { CursorAgentPlaceholder } from './cursor-agent-placeholder'
import { useMessage } from '@/hooks/useRoomMessages'

// ── User prompt (turn-based — reads entity from store) ─────────

function TurnUserPrompt({ messageId }: { messageId: string | null }) {
  const entity = useMessage(messageId ?? '')
  if (!entity || !messageId) return null
  return <CursorUserMessage entity={entity} />
}

// ── Supervisor badge (minimal inline indicator) ─────────────────

function SupervisorBadge({
  stepNumber,
  totalSteps,
  details,
}: {
  stepNumber?: number
  totalSteps?: number
  details?: string
}) {
  return (
    <div className="flex items-center gap-1.5 pl-11 py-1 text-xs text-muted-foreground/60">
      <Settings className="h-3 w-3" aria-hidden />
      <span>Supervisor</span>
      {stepNumber != null && totalSteps != null && totalSteps > 0 && (
        <span className="font-medium">Step {stepNumber}/{totalSteps}</span>
      )}
      {details && <span className="truncate max-w-[200px]">· {details}</span>}
    </div>
  )
}

// ── Failed warning ──────────────────────────────────────────────

function FailedWarning() {
  return (
    <div className="flex items-center gap-1.5 text-xs text-destructive/80 pl-11 mt-0.5">
      <AlertTriangle className="h-3 w-3 shrink-0" aria-hidden />
      <span>Some responses failed</span>
    </div>
  )
}

// ── Collapsed turn: single agent preview ────────────────────────

function CollapsedSingleAgent({
  result,
  onClick,
}: {
  result: { agentId?: string; agentName: string; content: string }
  onClick: () => void
}) {
  const preview = result.content.slice(0, 80) + (result.content.length > 80 ? '...' : '')
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center gap-2 mt-2 py-1.5 pl-11 w-full text-left text-sm text-muted-foreground cursor-pointer hover:text-foreground transition-colors rounded-md hover:bg-muted/20"
      data-testid="turn-expand-button"
    >
      {result.agentId && (
        <img
          src={getAgentAvatarUri(result.agentId)}
          alt=""
          className="w-5 h-5 rounded-full shrink-0"
        />
      )}
      <span className="font-medium text-foreground/80 shrink-0">{result.agentName}</span>
      <span className="truncate">{preview}</span>
      <ChevronRight className="h-3.5 w-3.5 shrink-0 opacity-60 ml-auto" aria-hidden />
    </button>
  )
}

// ── Collapsed turn: avatar stack for multiple agents ────────────

function CollapsedAvatarStack({
  results,
  hasFailed,
  onClick,
}: {
  results: { agentId?: string; agentName: string }[]
  hasFailed: boolean
  onClick: () => void
}) {
  const count = results.length
  const shown = results.slice(0, 3)

  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center gap-2.5 mt-2 py-2 pl-11 w-full text-left cursor-pointer hover:bg-muted/20 rounded-md transition-colors"
      data-testid="turn-expand-button"
    >
      {/* Overlapping avatar stack */}
      <div className="flex -space-x-1.5">
        {shown.map((r, i) => (
          <img
            key={r.agentId ?? i}
            src={r.agentId ? getAgentAvatarUri(r.agentId) : undefined}
            alt=""
            className="w-5 h-5 rounded-full ring-2 ring-background shrink-0"
          />
        ))}
      </div>
      <span className="text-sm text-muted-foreground">
        {count} response{count > 1 ? 's' : ''}
        {hasFailed && ' · some failed'}
      </span>
      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground/60 ml-auto" aria-hidden />
    </button>
  )
}

// ── Main turn component ─────────────────────────────────────────

interface CursorTurnProps {
  turn: TurnViewModel
  index: number
  isActive: boolean
  pendingAgents?: { agentId: string; agentName: string }[]
  onQuote?: (data: QuoteData) => void
}

function CursorTurn({ turn, index, isActive, pendingAgents, onQuote }: CursorTurnProps) {
  const [isExpanded, setIsExpanded] = useState(isActive)

  // Auto-collapse when turn stops being active
  useEffect(() => {
    if (!isActive) setIsExpanded(false)
  }, [isActive])

  const handleToggle = useCallback(() => {
    if (!isActive) setIsExpanded(prev => !prev)
  }, [isActive])

  const showExpanded = isActive || isExpanded

  // Filter system agents
  const visibleResults = useMemo(
    () => turn.agentResults.filter(r => !isSystemAgent(r.agentId)),
    [turn.agentResults],
  )

  const hasFailed = turn.status === 'failed' || turn.status === 'partial'

  return (
    <article
      className={cn('space-y-1', index > 0 && 'pt-8 mt-2')}
      aria-label={`Turn ${index + 1}`}
    >
      {/* Subtle separator between turns */}
      {index > 0 && <div className="mb-6 border-t border-border/15" aria-hidden="true" />}

      {/* User message */}
      <TurnUserPrompt messageId={turn.userMessageId} />

      {/* Supervisor badge */}
      {turn.isSupervisorTurn && showExpanded && (
        <SupervisorBadge
          stepNumber={turn.supervisorStage?.stepNumber}
          totalSteps={turn.supervisorStage?.totalSteps}
          details={turn.supervisorStage?.details}
        />
      )}

      {/* Failed warning */}
      {hasFailed && showExpanded && <FailedWarning />}

      {/* Collapsed state */}
      {!showExpanded && visibleResults.length > 0 && (
        <>
          {hasFailed && <FailedWarning />}
          {visibleResults.length === 1 ? (
            <CollapsedSingleAgent result={visibleResults[0]} onClick={handleToggle} />
          ) : (
            <CollapsedAvatarStack
              results={visibleResults}
              hasFailed={hasFailed}
              onClick={handleToggle}
            />
          )}
        </>
      )}

      {/* Expanded: all agent messages */}
      {showExpanded && (
        <div className="mt-4 space-y-5">
          {visibleResults.map(result => (
            <CursorAgentMessage key={result.messageId} result={result} onQuote={onQuote} />
          ))}

          {/* Pending agent placeholders (active turn only) */}
          {isActive &&
            (turn.status === 'active' || turn.status === 'awaiting_input') &&
            pendingAgents &&
            pendingAgents.length > 0 && (
              <div>
                {pendingAgents.map(agent => (
                  <CursorAgentPlaceholder
                    key={agent.agentId}
                    agentId={agent.agentId}
                    agentName={agent.agentName}
                  />
                ))}
              </div>
            )}

          {/* Collapse button (non-active expanded turns) */}
          {!isActive && (
            <button
              type="button"
              onClick={handleToggle}
              className="flex items-center gap-1 pl-11 text-xs text-muted-foreground hover:text-foreground transition-colors"
              data-testid="turn-collapse-button"
              aria-label="Hide responses"
            >
              <ChevronUp className="h-3.5 w-3.5" />
              Hide responses
            </button>
          )}
        </div>
      )}
    </article>
  )
}

export const MemoizedCursorTurn = React.memo(CursorTurn)
