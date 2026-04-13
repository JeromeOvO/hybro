'use client'

import React from 'react'
import Link from 'next/link'
import { AlertCircle, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useQuery } from '@tanstack/react-query'
import { getAgentColorClasses, getAgentInitials } from '@/lib/agent-colors'
import { MarkdownContent } from '@/components/markdown-content'
import { ArtifactRenderer } from '@/components/artifact-renderer'
import type { ContentSlotView } from '@/stores/turn-event-store/types'
import type { Agent } from '@/lib/types/agent'
import { SYSTEM_AGENTS } from '@/lib/system-agents'

interface AgentContentBlockProps {
  slot: ContentSlotView
}

export const AgentContentBlock = React.memo(function AgentContentBlock({ slot }: AgentContentBlockProps) {
  const { agentId, agentName: rawAgentName, content, artifacts, status, error } = slot
  const isStreaming = status === 'streaming'
  const isFailed = status === 'failed' || status === 'rejected'

  // Resolve agent name from catalog when not provided by turn event (legacy hydration)
  const { data: agents } = useQuery<Agent[]>({ queryKey: ['agents', 'all'], enabled: false })
  const resolvedName = rawAgentName
    ?? (agentId && SYSTEM_AGENTS[agentId]?.name)
    ?? (agentId && agents?.find(a => a.agent_id === agentId)?.agent_card?.name)
    ?? 'Agent'
  const iconUrl = agentId ? agents?.find(a => a.agent_id === agentId)?.agent_card?.iconUrl : undefined
  const isLinkable = !!agentId && !SYSTEM_AGENTS[agentId]

  const colors = getAgentColorClasses(agentId ?? 'default')

  return (
    <div
      className={cn(
        'py-3 rounded-lg',
        isFailed && 'border-l-2 border-destructive/50',
      )}
      data-testid="agent-content-block"
    >
      <div className="flex items-center gap-2 mb-2 px-1">
        {iconUrl ? (
          <img
            src={iconUrl}
            alt={resolvedName}
            className="w-7 h-7 rounded-md shrink-0 object-cover"
          />
        ) : (
          <div className={cn(
            'flex items-center justify-center w-7 h-7 rounded-md shrink-0 text-xs font-medium',
            colors.bg, colors.text, colors.border, 'border',
          )}>
            {getAgentInitials(resolvedName)}
          </div>
        )}
        {isLinkable ? (
          <Link
            href={`/c/agents/${agentId}`}
            className="font-semibold text-base text-foreground hover:underline underline-offset-2"
          >
            {resolvedName}
          </Link>
        ) : (
          <span className="font-semibold text-base text-foreground">
            {resolvedName}
          </span>
        )}
        {isStreaming && (
          <Loader2
            className="h-3.5 w-3.5 text-muted-foreground animate-spin"
            data-testid="streaming-indicator"
          />
        )}
        {isFailed && (
          <AlertCircle className="h-3.5 w-3.5 text-destructive" />
        )}
      </div>
      <div className="pl-10 pr-2">
        {content && (
          <div className="prose prose-sm dark:prose-invert max-w-none">
            <MarkdownContent content={content} />
          </div>
        )}
        {artifacts.length > 0 && (
          <div className="mt-2 space-y-2">
            {artifacts.map((artifact) => (
              <ArtifactRenderer key={artifact.artifactId} artifact={artifact} />
            ))}
          </div>
        )}
        {isFailed && error && (
          <p className="mt-1 text-xs text-destructive">{error}</p>
        )}
      </div>
    </div>
  )
})
