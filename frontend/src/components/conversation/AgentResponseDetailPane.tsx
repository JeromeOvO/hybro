'use client'

import { useRef, useState } from 'react'
import Link from 'next/link'
import { X, ChevronDown, Quote } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { MarkdownContent } from '@/components/markdown-content'
import { ArtifactList } from '@/components/artifact-list'
import { filterDuplicateTextArtifacts } from '@/lib/artifacts/filter-display-artifacts'
import { AgentSourceBadge } from '@/components/agent-source-badge'
import { getAgentAvatarUri } from '@/lib/agent-avatar'
import { cn } from '@/lib/utils'
import type { AgentDisplayProps, AgentResponseDetail } from '@/lib/selectors/conversation-types'
import type { Agent } from '@/lib/types/agent'
import { useDetailPaneScroll } from '@/hooks/useDetailPaneScroll'

interface AgentResponseDetailPaneProps {
  detail: AgentResponseDetail
  onClose: () => void
}

function useAgentFromCatalog(agentId: string): Agent | undefined {
  const qc = useQueryClient()
  const agents = qc.getQueryData<Agent[]>(['agents', 'all'])
  return agents?.find(a => a.agent_id === agentId)
}

function QuotedUserContext({ detail }: { detail: AgentResponseDetail }) {
  const quotedText = detail.requestMessage?.quotedText?.trim()
  if (!quotedText) return null

  const senderName = detail.requestMessage?.quotedSenderName?.trim()

  return (
    <div
      className="mx-3 mb-2 flex items-start gap-2 rounded-lg border border-border/60 bg-background/60 px-3 py-2 text-sm"
      data-testid="agent-detail-quoted-context"
    >
      <div className="w-0.5 shrink-0 self-stretch rounded-full bg-primary" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 mb-1">
          <Quote className="h-3 w-3 text-primary shrink-0" />
          <span className="text-xs font-semibold text-primary">
            {senderName ? `Quoted from ${senderName}` : 'Quoted context'}
          </span>
        </div>
        <p className="text-xs text-muted-foreground whitespace-pre-wrap break-words">
          {quotedText}
        </p>
      </div>
    </div>
  )
}

function EmptyResponse({ detail }: { detail: AgentResponseDetail }) {
  const message = detail.taskError || detail.taskStatusMessage || 'No response content yet.'
  return (
    <div className="text-sm" style={{ color: 'var(--conversation-text-muted)' }}>
      {message}
    </div>
  )
}

function AgentResponseDetailHeader({
  detail,
  onClose,
}: AgentResponseDetailPaneProps) {
  const catalogAgent = useAgentFromCatalog(detail.agentId)
  const iconUrl = catalogAgent?.agent_card?.iconUrl || undefined
  const isHubOnline = catalogAgent?.is_hub_online
  const [taskExpanded, setTaskExpanded] = useState(false)

  const toneColors: Record<AgentDisplayProps['tone'], string> = {
    accent: 'hsl(var(--color-primary))',
    muted: 'var(--conversation-agent-green)',
    danger: 'var(--conversation-danger)',
    warning: 'var(--conversation-agent-yellow)',
  }

  return (
    <div
      className="conversation-detail-agent-header"
      data-testid="agent-response-detail-header"
      style={{ backgroundColor: detail.theme.cardBg }}
    >
      <div
        className={cn(
          "conversation-detail-agent-avatar",
          detail.isStreaming && "conversation-avatar-working",
        )}
      >
        <div className="conversation-detail-agent-avatar-inner relative" style={{ backgroundColor: detail.theme.avatarLightBg }}>
          {iconUrl ? (
            <img
              src={iconUrl}
              alt=""
              className="absolute inset-0 w-full h-full object-cover"
              onError={(e) => {
                (e.currentTarget as HTMLImageElement).style.display = 'none'
              }}
            />
          ) : null}
          <img
            src={getAgentAvatarUri(detail.agentId)}
            alt=""
            className="w-full h-full"
            style={{ display: iconUrl ? 'none' : 'block' }}
          />
        </div>
      </div>

      <div className="conversation-detail-agent-main">
        <div className="conversation-detail-agent-name">
          <Link
            href={`/c/agents/${encodeURIComponent(detail.agentId)}`}
            className="hover:underline focus-visible:outline-none truncate"
          >
            {detail.agentName}
          </Link>
          {detail.agentSource != null && (
            <AgentSourceBadge
              source={detail.agentSource}
              isHubOnline={isHubOnline}
              className="h-3.5 w-3.5 shrink-0"
            />
          )}
          <span
            className="conversation-detail-status-pill ml-auto"
            role="status"
            aria-label={detail.display.ariaLabel}
            style={{ color: toneColors[detail.display.tone] }}
          >
            {detail.display.label}
          </span>
          <button
            type="button"
            aria-label="Close agent response"
            className="conversation-detail-close-button shrink-0"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        {detail.taskDescription && (
          <button
            type="button"
            aria-label={taskExpanded ? 'Collapse task' : 'Expand task'}
            aria-expanded={taskExpanded}
            className="conversation-detail-agent-task-toggle"
            onClick={() => setTaskExpanded(v => !v)}
          >
            <ChevronDown
              className={cn(
                'conversation-detail-agent-branch-icon h-3.5 w-3.5 shrink-0 transition-transform',
                !taskExpanded && '-rotate-90',
              )}
              style={{ transitionDuration: 'var(--conversation-chevron-duration)' }}
            />
            <div className="conversation-detail-agent-task-collapsible">
              <span className={cn(
                "conversation-detail-agent-task-text",
                !taskExpanded && "conversation-detail-agent-task-text-collapsed",
              )}>
                {detail.taskDescription}
              </span>
            </div>
          </button>
        )}
      </div>
    </div>
  )
}

export function AgentResponseDetailPane({ detail, onClose }: AgentResponseDetailPaneProps) {
  const hasContent = detail.content.trim().length > 0
  const displayArtifacts = filterDuplicateTextArtifacts(detail.artifacts, detail.content)
  const bodyRef = useRef<HTMLDivElement>(null)

  useDetailPaneScroll(
    bodyRef,
    detail.messageId,
    detail.isStreaming,
    detail.content.length + (detail.artifacts?.length ?? 0),
  )

  return (
    <aside className="conversation-detail-pane" data-testid="agent-response-detail-pane" aria-label="Agent response detail">
      <div className="conversation-detail-sticky" data-testid="agent-response-detail-sticky">
        <AgentResponseDetailHeader detail={detail} onClose={onClose} />
        <QuotedUserContext detail={detail} />
      </div>

      <div ref={bodyRef} className="conversation-detail-body">
        <div className="conversation-detail-frame">
          <section className="conversation-detail-response" aria-label="Agent response" data-quote-message-id={detail.messageId} data-quote-agent-name={detail.agentName} data-quote-source-kind="agent">
            {hasContent ? (
              <div className={`conversation-content-body ${detail.isStreaming ? 'conversation-streaming-cursor' : ''}`}>
                <MarkdownContent className="conversation-markdown-body" content={detail.content} isStreaming={detail.isStreaming} />
              </div>
            ) : (
              <EmptyResponse detail={detail} />
            )}
            {displayArtifacts && displayArtifacts.length > 0 && (
              <ArtifactList artifacts={displayArtifacts} />
            )}
          </section>
        </div>
      </div>
    </aside>
  )
}
