'use client'

import React from 'react'
import { AlertCircle, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getAgentColorClasses, getAgentInitials } from '@/lib/agent-colors'
import { MarkdownContent } from '@/components/markdown-content'
import { ArtifactRenderer } from '@/components/artifact-renderer'
import type { ContentSlotView } from '@/stores/turn-event-store/types'

interface AgentContentBlockProps {
  slot: ContentSlotView
}

export const AgentContentBlock = React.memo(function AgentContentBlock({ slot }: AgentContentBlockProps) {
  const { agentId, agentName, content, artifacts, status, error } = slot
  const isStreaming = status === 'streaming'
  const isFailed = status === 'failed' || status === 'rejected'
  const colors = getAgentColorClasses(agentId)

  return (
    <div
      className={cn(
        'py-3 rounded-lg',
        isFailed && 'border-l-2 border-destructive/50',
      )}
      data-testid="agent-content-block"
    >
      <div className="flex items-center gap-2 mb-2 px-1">
        <div className={cn(
          'flex items-center justify-center w-7 h-7 rounded-md shrink-0 text-xs font-medium',
          colors.bg, colors.text, colors.border, 'border',
        )}>
          {getAgentInitials(agentName ?? 'Agent')}
        </div>
        <span className="font-semibold text-base text-foreground">
          {agentName ?? 'Agent'}
        </span>
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
