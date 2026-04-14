'use client'

import React, { useState, useRef, useCallback, useEffect, useMemo } from 'react'
import Link from 'next/link'
import { AlertCircle, ChevronDown, ChevronUp } from 'lucide-react'
import { TooltipProvider } from '@/components/ui/tooltip'
import { AgentSourceBadge } from '@/components/agent-source-badge'
import { cn } from '@/lib/utils'
import { useQueryClient } from '@tanstack/react-query'
import { getAgentColorClasses, getAgentInitials } from '@/lib/agent-colors'
import { getAgentAvatarUri } from '@/lib/agent-avatar'
import { MarkdownContent } from '@/components/markdown-content'
import { ArtifactRenderer } from '@/components/artifact-renderer'
import { useExpandCollapseSignals } from './expand-collapse-context'
import type { ContentSlotView, ArtifactData } from '@/stores/turn-event-store/types'
import type { Agent } from '@/lib/types/agent'
import { SYSTEM_AGENTS } from '@/lib/system-agents'

const COLLAPSE_THRESHOLD = 500
const TYPEWRITER_CHARS_PER_TICK = 3
const TYPEWRITER_INTERVAL_MS = 12

function useTypewriter(fullContent: string, slot: ContentSlotView) {
  const [revealedLen, setRevealedLen] = useState(0)
  const [isAnimating, setIsAnimating] = useState(false)
  const prevContentRef = useRef('')
  const hasAnimatedRef = useRef(false)

  const shouldAnimate = useMemo(() => {
    if (!fullContent) return false
    // Only animate live SSE content — skip hydrated (DB/page refresh)
    if (slot.hydrated) return false
    // Skip if artifacts are still streaming
    if (slot.artifacts?.some(a => a.isStreaming)) return false
    return true
  }, [fullContent, slot.hydrated, slot.artifacts])

  useEffect(() => {
    const prev = prevContentRef.current
    prevContentRef.current = fullContent

    if (!shouldAnimate || !fullContent) {
      setRevealedLen(fullContent.length)
      setIsAnimating(false)
      return
    }

    if (hasAnimatedRef.current && fullContent === prev) return

    // Content grew or is brand new — animate from where we were
    const startFrom = fullContent.startsWith(prev) ? prev.length : 0
    if (startFrom >= fullContent.length) {
      setRevealedLen(fullContent.length)
      setIsAnimating(false)
      return
    }

    setRevealedLen(startFrom)
    setIsAnimating(true)
    hasAnimatedRef.current = true
  }, [fullContent, shouldAnimate])

  useEffect(() => {
    if (!isAnimating) return
    if (revealedLen >= fullContent.length) {
      setIsAnimating(false)
      return
    }
    const id = setTimeout(() => {
      setRevealedLen(prev => Math.min(prev + TYPEWRITER_CHARS_PER_TICK, fullContent.length))
    }, TYPEWRITER_INTERVAL_MS)
    return () => clearTimeout(id)
  }, [isAnimating, revealedLen, fullContent])

  return {
    displayContent: isAnimating ? fullContent.slice(0, revealedLen) : fullContent,
    isTypewriting: isAnimating,
  }
}

interface AgentContentBlockProps {
  slot: ContentSlotView
}

export const AgentContentBlock = React.memo(function AgentContentBlock({ slot }: AgentContentBlockProps) {
  const { agentId, agentName: rawAgentName, content, artifacts: rawArtifacts, status, error } = slot

  // Filter out text-only artifacts whose content was already promoted into the
  // main `content` field (artifact_update SSE handler extracts text from
  // text-only artifacts into content, but keeps them in the artifacts array).
  const artifacts = content ? filterPromotedTextArtifacts(rawArtifacts, content) : rawArtifacts
  const isStreaming = status === 'streaming'
  const isFailed = status === 'failed' || status === 'rejected'

  const { displayContent, isTypewriting } = useTypewriter(content, slot)
  const isEffectivelyStreaming = isStreaming || isTypewriting

  const isLongMessage = content.length > COLLAPSE_THRESHOLD
  const [isExpanded, setIsExpanded] = useState(!isLongMessage)
  const toggleRef = useRef<HTMLButtonElement>(null)
  const { expandSignal, collapseSignal } = useExpandCollapseSignals()

  const estimatedLines = isLongMessage ? Math.max(5, Math.ceil(content.length / 80)) : 0

  // Respond to global expand/collapse signals (only affects long messages)
  useEffect(() => {
    if (expandSignal > 0 && isLongMessage) setIsExpanded(true)
  }, [expandSignal, isLongMessage])

  useEffect(() => {
    if (collapseSignal > 0 && isLongMessage) setIsExpanded(false)
  }, [collapseSignal, isLongMessage])

  const handleToggle = useCallback(() => {
    const next = !isExpanded
    const buttonEl = toggleRef.current
    const container = buttonEl?.closest('[data-message-scroll-container="true"]') as HTMLElement | null
    const prevBottom = buttonEl?.getBoundingClientRect().bottom

    setIsExpanded(next)

    // Keep toggle button in view when collapsing
    if (buttonEl && container && !next && typeof prevBottom === 'number') {
      container.dataset.programmaticScroll = 'true'
      requestAnimationFrame(() => {
        const newBottom = buttonEl.getBoundingClientRect().bottom
        const delta = newBottom - prevBottom
        if (delta !== 0) container.scrollTop += delta
        requestAnimationFrame(() => { container.dataset.programmaticScroll = 'false' })
      })
    }
  }, [isExpanded])

  // Resolve agent name from catalog when not provided by turn event (legacy hydration)
  const queryClient = useQueryClient()
  const agents = queryClient.getQueryData<Agent[]>(['agents', 'all'])
  const resolvedName = rawAgentName
    ?? (agentId && SYSTEM_AGENTS[agentId]?.name)
    ?? (agentId && agents?.find(a => a.agent_id === agentId)?.agent_card?.name)
    ?? 'Agent'
  const catalogAgent = agentId ? agents?.find(a => a.agent_id === agentId) : undefined
  const catalogIconUrl = catalogAgent?.agent_card?.iconUrl
  const iconUrl = catalogIconUrl || (agentId ? getAgentAvatarUri(agentId) : undefined)
  const isLinkable = !!agentId && !SYSTEM_AGENTS[agentId]
  const agentSource = catalogAgent?.source
  const isHubOnline = catalogAgent?.is_hub_online

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
        {agentSource && (
          <TooltipProvider delayDuration={200}>
            <AgentSourceBadge source={agentSource} isHubOnline={isHubOnline} className="h-3.5 w-3.5" />
          </TooltipProvider>
        )}
        {isStreaming && (
          <span
            className="text-xs text-muted-foreground animate-pulse"
            data-testid="streaming-indicator"
          >
            Working
          </span>
        )}
        {isFailed && (
          <AlertCircle className="h-3.5 w-3.5 text-destructive" />
        )}
      </div>
      <div className="pl-10 pr-2">
        {content ? (
          <div className="relative">
            <div
              className={cn(
                'prose prose-sm dark:prose-invert max-w-none min-h-0 overflow-hidden transition-opacity duration-200',
                !isExpanded && isLongMessage && 'max-h-[5lh]',
              )}
            >
              <MarkdownContent content={displayContent} isStreaming={isEffectivelyStreaming} />
            </div>
            {!isExpanded && isLongMessage && (
              <div className="absolute inset-x-0 bottom-0 h-8 bg-linear-to-t from-card to-transparent pointer-events-none" />
            )}
          </div>
        ) : isStreaming ? (
          <div className="space-y-2 animate-pulse" data-testid="content-shimmer">
            <div className="h-3 bg-muted rounded w-3/4" />
            <div className="h-3 bg-muted rounded w-1/2" />
            <div className="h-3 bg-muted rounded w-5/6" />
          </div>
        ) : null}
        {isLongMessage && !isEffectivelyStreaming && (
          <button
            ref={toggleRef}
            onClick={handleToggle}
            className="flex items-center gap-1 text-xs mt-2 font-medium transition-colors text-muted-foreground hover:text-foreground"
          >
            {isExpanded ? (
              <>
                <ChevronUp className="h-3.5 w-3.5" />
                Show less
              </>
            ) : (
              <>
                <ChevronDown className="h-3.5 w-3.5" />
                Show more ({estimatedLines} lines)
              </>
            )}
          </button>
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

/** Remove text-only artifacts whose text is already contained in the main content. */
function filterPromotedTextArtifacts(artifacts: ArtifactData[], content: string): ArtifactData[] {
  if (artifacts.length === 0) return artifacts
  return artifacts.filter(a => {
    const isTextOnly = a.parts.length > 0 && a.parts.every(p => p.kind === 'text')
    if (!isTextOnly) return true
    const artifactText = a.parts.map(p => p.text || '').join('')
    return !artifactText || !content.includes(artifactText)
  })
}
