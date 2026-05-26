'use client'

import { useEffect, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import type { AgentResultViewModel, TurnViewModel } from '@/lib/room-timeline/types'
import { getAgentTheme } from '@/lib/selectors/conversation-types'
import { mapResultDisplayProps } from '@/lib/room-timeline/map-result-display'
import {
  getActivityStripListMaxHeight,
  getAgentIndexSummary,
  defaultAgentIndexOpen,
  getStripSourceResults,
} from '@/lib/room-timeline/turn-live-shell'
import { useResultStreamDisplay } from '@/hooks/useStreamBuffer'
import { AgentCard } from './AgentCard'
import { AgentResultContent } from './AgentResultContent'
import { cn } from '@/lib/utils'

interface AgentIndexProps {
  turn: TurnViewModel
  sourceResults: AgentResultViewModel[]
  selectedAgentMessageId?: string
  onOpenDetail: (messageId: string) => void
  isLastTurn?: boolean
}

function IndexRow({
  result,
  selected,
  onOpenDetail,
}: {
  result: AgentResultViewModel
  selected: boolean
  onOpenDetail: (messageId: string) => void
}) {
  const { isStreaming, artifacts } = useResultStreamDisplay(result)
  const display = mapResultDisplayProps(result, isStreaming)
  const theme = getAgentTheme(result.agentId, result.agentName)
  const artifactCount = artifacts?.length ?? 0
  const statusSuffix = artifactCount > 0 ? `${artifactCount} file${artifactCount === 1 ? '' : 's'}` : undefined

  return (
    <AgentCard
      messageId={result.messageId}
      agentId={result.agentId ?? result.messageId}
      agentName={result.agentName}
      taskDescription={result.taskStatusMessage ?? ''}
      theme={theme}
      display={display}
      agentSource={result.agentSource}
      selected={selected}
      onOpen={onOpenDetail}
      compact
      statusSuffix={statusSuffix}
    />
  )
}

export function AgentIndex({
  turn,
  sourceResults,
  selectedAgentMessageId,
  onOpenDetail,
  isLastTurn = false,
}: AgentIndexProps) {
  const { finalAnswer } = turn
  const [open, setOpen] = useState(() => defaultAgentIndexOpen(turn, isLastTurn))

  useEffect(() => {
    if (!isLastTurn) setOpen(false)
  }, [isLastTurn])

  if (sourceResults.length === 0) return null
  if (finalAnswer.kind === 'single') return null

  const summary = getAgentIndexSummary(turn, sourceResults, finalAnswer.kind)
  const forceExpand = sourceResults.some(r => r.messageId === selectedAgentMessageId)
  const expanded = open || forceExpand
  const stripResults =
    finalAnswer.kind === 'hitl'
      ? sourceResults.filter(r => r.status === 'completed')
      : sourceResults

  if (stripResults.length === 0) return null

  const showFullBodies = finalAnswer.kind === 'deterministic_done'
  const listMaxHeight = showFullBodies ? 0 : getActivityStripListMaxHeight(stripResults.length)

  return (
    <Collapsible
      open={expanded}
      onOpenChange={setOpen}
      className="turn-activity-strip agent-index"
      style={{ overflowAnchor: 'none' }}
    >
      <CollapsibleTrigger
        className="flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm transition-colors hover:bg-muted/30"
        style={{ borderColor: 'var(--conversation-border-subtle)', color: 'var(--conversation-text-secondary)' }}
        aria-label={`${summary}, expandable`}
      >
        <ChevronRight className={cn('h-4 w-4 shrink-0 transition-transform', expanded && 'rotate-90')} />
        <span>{summary}</span>
      </CollapsibleTrigger>
      <CollapsibleContent className="turn-activity-strip-content data-[state=open]:animate-collapsible-down overflow-hidden data-[state=open]:overflow-visible">
        <div
          className={cn(
            'turn-activity-strip-list mt-2 flex flex-col',
            listMaxHeight > 0 && 'overflow-y-auto overscroll-y-contain',
          )}
          style={{
            gap: 'var(--conversation-gap-block)',
            maxHeight: listMaxHeight > 0 ? `${listMaxHeight}px` : undefined,
          }}
        >
          {stripResults.map(result => (
            showFullBodies ? (
              <AgentResultContent
                key={result.messageId}
                result={result}
                showAttribution={stripResults.length > 1}
                selected={result.messageId === selectedAgentMessageId}
                onOpenDetail={onOpenDetail}
              />
            ) : (
              <IndexRow
                key={result.messageId}
                result={result}
                selected={result.messageId === selectedAgentMessageId}
                onOpenDetail={onOpenDetail}
              />
            )
          ))}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

export function shouldShowAgentIndex(turn: TurnViewModel, onOpenDetail?: (id: string) => void): boolean {
  if (!onOpenDetail) return false
  const sourceResults = getStripSourceResults(turn)
  if (sourceResults.length <= 1 && turn.finalAnswer.kind === 'single') return false
  if (turn.finalAnswer.kind === 'single') return false
  return sourceResults.length > 0
}
