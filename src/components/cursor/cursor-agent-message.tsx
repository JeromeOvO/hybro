'use client'

import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  ChevronDown,
  ChevronUp,
  CheckCircle,
  Clock,
  Cloud,
  House,
  Loader2,
  MessageCircleQuestion,
  XCircle,
} from 'lucide-react'
import { cn, tryParseJson } from '@/lib/utils'
import { getAgentAvatarUri } from '@/lib/agent-avatar'
import { getAgentInitials } from '@/lib/agent-colors'
import { formatTimestamp, elapsedSeconds, formatElapsedTime } from '@/lib/time'
import {
  MarkdownContent,
  LinkifiedContent,
  JsonBlockExpandedContext,
} from '@/components/markdown-content'
import { CollapsibleJsonBlock } from '@/components/part-renderer'
import { ArtifactList } from '@/components/artifact-list'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { derivePhase, type AgentPhase, type QuoteData } from '@/components/message-bubble'
import { TASK_STATE, isFailureState, isInteractiveState, isTerminalState } from '@/lib/types/sse'
import type { MessageEntity } from '@/stores/message-store'
import type { ArtifactData, ArtifactPart } from '@/stores/message-store/types'
import type { AgentResultViewModel } from '@/lib/room-timeline/types'
import type { Agent } from '@/lib/types/agent'
import { CursorMessageRow } from './cursor-message-row'
import { CursorHoverActions, CursorMobileActions } from './cursor-hover-actions'

// ---------------------------------------------------------------------------
// Typewriter hook — inlined from message-bubble.tsx (not exported there)
// ---------------------------------------------------------------------------

const TYPEWRITER_CHARS_PER_TICK = 3
const TYPEWRITER_INTERVAL_MS = 12

function useTypewriter(fullContent: string, phase: AgentPhase, entity: MessageEntity) {
  const [revealedLen, setRevealedLen] = useState(0)
  const [isAnimating, setIsAnimating] = useState(false)
  const prevContentRef = useRef('')
  const hasAnimatedRef = useRef(false)

  const shouldAnimate = useMemo(() => {
    if (!fullContent) return false
    if (entity.source !== 'sse') return false
    if (entity.messageType !== 'agent') return false
    if (entity.artifacts?.some(a => a.isStreaming)) return false
    if (entity.taskStatus && !isTerminalState(entity.taskStatus)) return false
    return true
  }, [fullContent, entity.source, entity.messageType, entity.artifacts, entity.taskStatus])

  useEffect(() => {
    const prev = prevContentRef.current
    prevContentRef.current = fullContent
    if (!shouldAnimate || !fullContent) {
      setRevealedLen(fullContent.length)
      setIsAnimating(false)
      return
    }
    if (hasAnimatedRef.current && fullContent === prev) return
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

// ---------------------------------------------------------------------------
// Agent avatar
// ---------------------------------------------------------------------------

function AgentAvatar({
  agentId,
  agentName,
  iconUrl,
  className,
}: {
  agentId?: string
  agentName: string
  iconUrl?: string | null
  className?: string
}) {
  const src = iconUrl ?? (agentId ? getAgentAvatarUri(agentId) : undefined)

  if (src) {
    return (
      <img
        src={src}
        alt=""
        aria-hidden="true"
        className={cn('w-7 h-7 rounded-full shrink-0 object-cover', className)}
      />
    )
  }

  return (
    <div
      className={cn(
        'w-7 h-7 rounded-full bg-muted flex items-center justify-center shrink-0',
        className,
      )}
      aria-hidden="true"
    >
      <span className="text-[10px] font-semibold text-muted-foreground">
        {getAgentInitials(agentName)}
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component: CursorAgentMessage
// ---------------------------------------------------------------------------

interface CursorAgentMessageFromEntity {
  entity: MessageEntity
  result?: never
  onQuote?: (data: QuoteData) => void
}

interface CursorAgentMessageFromResult {
  entity?: never
  result: AgentResultViewModel
  onQuote?: (data: QuoteData) => void
}

type CursorAgentMessageProps = CursorAgentMessageFromEntity | CursorAgentMessageFromResult

/**
 * Cursor-style agent message — flat, borderless content block with avatar.
 * Accepts either a `MessageEntity` (direct store rendering) or an `AgentResultViewModel`
 * (turn-based rendering). Handles all 6 phases.
 */
export const CursorAgentMessage = React.memo(function CursorAgentMessage(
  props: CursorAgentMessageProps,
) {
  const { onQuote } = props

  // Normalize inputs into a unified shape
  const isEntityMode = 'entity' in props && !!props.entity
  const entity = isEntityMode ? props.entity! : null
  const result = !isEntityMode ? props.result! : null

  const agentId = entity?.agentId ?? result?.agentId
  const agentName = entity?.senderName ?? result?.agentName ?? 'Agent'
  const agentSource = entity?.agentSource ?? result?.agentSource
  const messageId = entity?.id ?? result?.messageId ?? ''
  const rawContent = entity?.content ?? result?.content ?? ''
  const timestamp = entity?.timestamp ?? ''
  const artifacts: ArtifactData[] = entity?.artifacts ?? result?.artifacts ?? []
  const stepNumber = entity?.stepNumber
  const totalSteps = entity?.totalSteps

  // --- Agent icon URL from React Query cache ---
  const queryClient = useQueryClient()
  const agentIconUrl = agentId
    ? (queryClient.getQueryData<Agent[]>(['agents', 'all'])
        ?.find(a => a.agent_id === agentId)
        ?.agent_card?.iconUrl ?? null)
    : null

  // --- Phase derivation ---
  // For entity mode, use full phase derivation
  // For result mode, map result.status to a simplified phase
  const phase: AgentPhase = entity
    ? derivePhase(entity)
    : result
      ? mapResultStatus(result)
      : 'complete'

  // --- Typewriter (entity mode only; results are always complete/working) ---
  const dummyEntity = useMemo<MessageEntity>(() => ({
    id: messageId,
    roomId: '',
    messageType: 'agent' as const,
    content: rawContent,
    senderName: agentName,
    timestamp: '',
    source: 'db' as const,
    sourceVersion: 0,
    displayType: 'agent-bubble' as const,
    isEphemeral: false,
    createdAt: 0,
    updatedAt: 0,
  }), [messageId, rawContent, agentName])

  const typewriterEntity = entity ?? dummyEntity
  const fullContent = rawContent
  const { displayContent, isTypewriting } = useTypewriter(fullContent, phase, typewriterEntity)
  const isEffectivelyStreaming = phase === 'streaming' || isTypewriting
  const isArtifactStreaming = artifacts.some(a => a.isStreaming)

  // --- Content analysis ---
  const parsedJson = isTypewriting ? null : tryParseJson(displayContent)
  const isLong = parsedJson === null && fullContent.length > 500
  const estimatedLines = isLong ? Math.max(5, Math.ceil(fullContent.length / 80)) : 0

  // --- State ---
  const [expanded, setExpanded] = useState(false)
  const [jsonOpen, setJsonOpen] = useState(false)
  const [selectionActive, setSelectionActive] = useState(false)
  const toggleRef = useRef<HTMLButtonElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const quoteBtnRef = useRef<HTMLButtonElement | null>(null)
  const selectedTextRef = useRef<string>('')
  const wasStreamingRef = useRef(false)

  // --- Elapsed timer ---
  const [elapsed, setElapsed] = useState(() =>
    entity?.taskCreatedAt ? elapsedSeconds(entity.taskCreatedAt) : 0,
  )
  useEffect(() => {
    const needsTimer =
      phase === 'waiting' || (phase === 'interactive' && entity && !entity.hitlResolved)
    if (!needsTimer || !entity?.taskCreatedAt) {
      setElapsed(0)
      return
    }
    setElapsed(elapsedSeconds(entity.taskCreatedAt))
    const id = setInterval(() => {
      setElapsed(elapsedSeconds(entity!.taskCreatedAt!))
    }, 1000)
    return () => clearInterval(id)
  }, [phase, entity?.taskCreatedAt, entity?.hitlResolved, entity])

  // Open JSON collapsible after streaming finishes
  useEffect(() => {
    if (isEffectivelyStreaming) wasStreamingRef.current = true
  }, [isEffectivelyStreaming])
  useEffect(() => {
    if (!isEffectivelyStreaming && wasStreamingRef.current && parsedJson !== null) {
      setJsonOpen(true)
    }
  }, [isEffectivelyStreaming, parsedJson])

  // --- Quote selection (ported from message-bubble.tsx) ---
  const hideQuoteButton = useCallback(() => {
    if (quoteBtnRef.current) {
      quoteBtnRef.current.remove()
      quoteBtnRef.current = null
    }
    selectedTextRef.current = ''
    setSelectionActive(false)
  }, [])

  const showQuoteButton = useCallback(
    (top: number, left: number, text: string) => {
      selectedTextRef.current = text
      if (quoteBtnRef.current) quoteBtnRef.current.remove()

      const btn = document.createElement('button')
      btn.setAttribute('data-quote-btn', 'true')
      btn.setAttribute('type', 'button')
      btn.className =
        'fixed z-[9999] flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-md shadow-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors whitespace-nowrap select-none'
      btn.style.top = `${top}px`
      btn.style.left = `${left}px`
      btn.style.transform = 'translateX(-50%)'
      btn.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 21c3 0 7-1 7-8V5c0-1.25-.756-2.017-2-2H4c-1.25 0-2 .75-2 1.972V11c0 1.25.75 2 2 2 1 0 1 0 1 1v1c0 1-1 2-2 2s-1 .008-1 1.031V21c0 1 0 1 1 1z"/><path d="M15 21c3 0 7-1 7-8V5c0-1.25-.757-2.017-2-2h-4c-1.25 0-2 .75-2 1.972V11c0 1.25.75 2 2 2h.75c0 2.25.25 4-2.75 4v3c0 1 0 1 1 1z"/></svg>Quote`

      btn.onmousedown = (e) => e.preventDefault()
      btn.onclick = () => {
        onQuote?.({ messageId, content: selectedTextRef.current, senderName: agentName })
        hideQuoteButton()
        window.getSelection()?.removeAllRanges()
      }

      document.body.appendChild(btn)
      quoteBtnRef.current = btn
      setSelectionActive(true)
    },
    [messageId, agentName, onQuote, hideQuoteButton],
  )

  useEffect(() => () => hideQuoteButton(), [hideQuoteButton])

  const handleMouseUp = useCallback(() => {
    requestAnimationFrame(() => {
      const selection = window.getSelection()
      if (!selection || selection.isCollapsed || !contentRef.current) {
        hideQuoteButton()
        return
      }
      const text = selection.toString().trim()
      if (!text) { hideQuoteButton(); return }
      const range = selection.getRangeAt(0)
      if (!contentRef.current.contains(range.commonAncestorContainer)) {
        hideQuoteButton()
        return
      }
      if (!onQuote) return
      const rect = range.getBoundingClientRect()
      showQuoteButton(rect.top - 32 + window.scrollY, rect.left + rect.width / 2 + window.scrollX, text)
    })
  }, [onQuote, showQuoteButton, hideQuoteButton])

  // Dismiss quote on outside click
  useEffect(() => {
    const handleDown = (e: MouseEvent) => {
      if (!quoteBtnRef.current) return
      const target = e.target as HTMLElement
      if (target.closest('[data-quote-btn]')) return
      if (contentRef.current?.contains(target)) return
      hideQuoteButton()
    }
    document.addEventListener('mousedown', handleDown)
    return () => document.removeEventListener('mousedown', handleDown)
  }, [hideQuoteButton])

  // --- Scroll-preserving toggle ---
  const handleToggle = useCallback(() => {
    const next = !expanded
    const btn = toggleRef.current
    const container = btn?.closest('[data-message-scroll-container="true"]') as HTMLElement | null
    const prevBottom = btn?.getBoundingClientRect().bottom
    setExpanded(next)
    if (btn && container && !next && typeof prevBottom === 'number') {
      container.dataset.programmaticScroll = 'true'
      requestAnimationFrame(() => {
        const newBottom = btn.getBoundingClientRect().bottom
        const delta = newBottom - prevBottom
        if (delta !== 0) container.scrollTop += delta
        requestAnimationFrame(() => { container.dataset.programmaticScroll = 'false' })
      })
    }
  }, [expanded])

  // --- HITL fields ---
  const hitlPrompt = entity?.hitlPrompt ?? result?.hitlPending?.prompt ?? result?.hitlResolved?.prompt
  const hitlResolved = entity?.hitlResolved ?? !!result?.hitlResolved
  const hitlUserAnswer = entity?.hitlUserAnswer ?? result?.hitlResolved?.answer
  const hasResolvedHitl = hitlResolved && !!hitlUserAnswer

  // --- Non-duplicate artifacts (same logic as EntityAgentBubble) ---
  const visibleArtifacts = useMemo(() => {
    const textContent = (rawContent || '').trim()
    return artifacts.filter(a => {
      const isTextOnly = a.parts.length > 0 && a.parts.every((p: ArtifactPart) => p.kind === 'text')
      return !(isTextOnly && textContent)
    })
  }, [artifacts, rawContent])

  // --- Phase badge ---
  const phaseBadge = useMemo(() => {
    switch (phase) {
      case 'interactive':
        return (
          <span className="flex items-center gap-1 text-xs font-medium text-amber-600 dark:text-amber-400">
            <MessageCircleQuestion className="h-3 w-3" />
            {hitlResolved ? 'Answered' : entity?.taskStatus === 'auth-required' ? 'Auth needed' : 'Input needed'}
          </span>
        )
      case 'failed':
        return (
          <span className="flex items-center gap-1 text-xs font-medium text-red-600 dark:text-red-400">
            <XCircle className="h-3 w-3" />
            {entity?.taskStatus === 'rejected' ? 'Rejected' : entity?.taskStatus === 'canceled' ? 'Canceled' : 'Failed'}
          </span>
        )
      case 'waiting':
        return null // dots shown inline
      case 'streaming':
        return null
      case 'complete-empty':
        return null // shown inline
      case 'complete':
        return null
    }
  }, [phase, hitlResolved, entity?.taskStatus])

  // ═══════════════════════════════════════════════════════════════
  // RENDER
  // ═══════════════════════════════════════════════════════════════

  return (
    <CursorMessageRow
      avatarSlot={<AgentAvatar agentId={agentId} agentName={agentName} iconUrl={agentIconUrl} />}
      messageId={messageId}
      mobileActions={(dismiss) => (
        <CursorMobileActions
          content={displayContent}
          messageId={messageId}
          senderName={agentName}
          timestamp={timestamp ? formatTimestamp(timestamp) : undefined}
          onQuote={onQuote}
          onDismiss={dismiss}
        />
      )}
    >
      {/* Desktop hover toolbar */}
      {(phase === 'complete' || phase === 'streaming' || isTypewriting) && displayContent && (
        <CursorHoverActions
          content={displayContent}
          messageId={messageId}
          senderName={agentName}
          onQuote={onQuote}
          selectionActive={selectionActive}
        />
      )}

      {/* Header: name + source + phase badge + timestamp */}
      <div className="flex items-center gap-2 mb-1.5">
        <a
          href={agentId ? `/c/agents/${agentId}` : undefined}
          target="_blank"
          rel="noopener noreferrer"
          className="text-sm font-semibold text-foreground hover:underline underline-offset-2"
        >
          {agentName}
        </a>
        {agentSource && (
          <TooltipProvider delayDuration={200}>
            <Tooltip>
              <TooltipTrigger asChild>
                {agentSource === 'hub' ? (
                  <House className="h-3 w-3 shrink-0 text-muted-foreground/30" />
                ) : (
                  <Cloud className="h-3 w-3 shrink-0 text-muted-foreground/30" />
                )}
              </TooltipTrigger>
              <TooltipContent side="top" sideOffset={4}>
                {agentSource === 'hub' ? 'Local agent' : 'Cloud agent'}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
        {stepNumber != null && totalSteps != null && totalSteps > 0 && (
          <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
            Step {stepNumber}/{totalSteps}
          </span>
        )}
        {phaseBadge}
        {timestamp && (
          <span className="cursor-timestamp text-xs text-muted-foreground/50 opacity-0 group-hover:opacity-100 transition-opacity ml-auto">
            {formatTimestamp(timestamp)}
          </span>
        )}
      </div>

      {/* ── WAITING phase ── */}
      {phase === 'waiting' && (
        <div className="py-2" role="status" aria-label={`${agentName} is working`} aria-busy="true">
          {entity?.taskStatus ? (
            <div className="space-y-1.5">
              <div className="flex items-start gap-2">
                <Loader2 className="w-4 h-4 animate-spin mt-0.5 shrink-0 text-muted-foreground" />
                <span className="text-sm text-muted-foreground">
                  {entity.taskStatusMessage || entity.taskContent || 'Working on your request...'}
                </span>
              </div>
              {elapsed > 0 && (
                <p className="text-xs text-muted-foreground/50 flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  {formatElapsedTime(elapsed)} elapsed
                </p>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-0.5">
              {[0, 1, 2].map(i => (
                <span
                  key={i}
                  className="w-1.5 h-1.5 rounded-full bg-muted-foreground/50 animate-bounce"
                  style={{ animationDelay: `${i * 150}ms` }}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── INTERACTIVE phase (HITL) — also shows resolved HITL history in other phases ── */}
      {(phase === 'interactive' || hasResolvedHitl) && (() => {
        const promptText = hitlPrompt || rawContent || entity?.taskStatusMessage || 'The agent needs additional information.'
        return (
          <div className="border-l-2 border-amber-400 dark:border-amber-500/50 pl-4 py-1 mt-1">
            <div className="text-sm text-foreground/80">
              <MarkdownContent content={promptText} />
            </div>
            {hitlResolved && hitlUserAnswer && (
              <div className="mt-2 text-sm">
                <span className="text-xs text-muted-foreground">Your answer: </span>
                <span className="text-foreground font-medium">{hitlUserAnswer}</span>
              </div>
            )}
            {!hitlResolved && phase === 'interactive' && (
              <p className="text-xs text-muted-foreground/50 mt-1 flex items-center gap-1">
                <Clock className="w-3 h-3" />
                {formatElapsedTime(elapsed)} elapsed
              </p>
            )}
          </div>
        )
      })()}

      {/* ── FAILED phase ── */}
      {phase === 'failed' && (() => {
        const errorBody = entity?.taskError || rawContent || 'An error occurred'
        const isLongError = errorBody.length > 500
        return (
          <div className="border-l-2 border-red-400 dark:border-red-500/50 pl-4 py-1 mt-1">
            <div className="relative">
              <div
                className={cn(
                  'text-sm text-foreground/70',
                  !expanded && isLongError && 'max-h-[5lh] overflow-hidden',
                )}
              >
                <MarkdownContent content={errorBody} />
              </div>
              {!expanded && isLongError && (
                <div className="absolute inset-x-0 bottom-0 h-8 bg-gradient-to-t from-background to-transparent pointer-events-none" />
              )}
            </div>
            {isLongError && (
              <button
                ref={toggleRef}
                type="button"
                onClick={handleToggle}
                className="flex items-center gap-1 text-xs mt-2 text-muted-foreground hover:text-foreground transition-colors"
              >
                {expanded ? <><ChevronUp className="h-3.5 w-3.5" />Show less</> : <><ChevronDown className="h-3.5 w-3.5" />Show more</>}
              </button>
            )}
          </div>
        )
      })()}

      {/* ── COMPLETE-EMPTY phase ── */}
      {phase === 'complete-empty' && (
        <div className="flex items-center gap-1.5 py-1 mt-1">
          <CheckCircle className="h-3.5 w-3.5 text-muted-foreground/50" />
          <span className="text-xs text-muted-foreground/60">Completed</span>
          {entity?.taskCreatedAt && (
            <span className="text-xs text-muted-foreground/40 flex items-center gap-0.5">
              <Clock className="h-3 w-3" />
              {formatElapsedTime(elapsedSeconds(entity.taskCreatedAt))}
            </span>
          )}
        </div>
      )}

      {/* ── STREAMING / COMPLETE phases ── */}
      {(phase === 'streaming' || phase === 'complete' || isTypewriting) && (
        <div className="mt-1" ref={contentRef} onMouseUp={handleMouseUp}>
          {parsedJson !== null ? (
            <CollapsibleJsonBlock data={parsedJson} open={jsonOpen} onOpenChange={setJsonOpen} />
          ) : (
            <>
              <div className="relative">
                <div
                  className={cn(
                    'min-h-0 overflow-hidden text-[15px] leading-[1.65] select-text',
                    'text-foreground',
                    !expanded && isLong && 'max-h-[5lh]',
                  )}
                >
                  <JsonBlockExpandedContext.Provider value={expanded}>
                    <MarkdownContent content={displayContent} isStreaming={isEffectivelyStreaming} />
                  </JsonBlockExpandedContext.Provider>
                </div>
                {!expanded && isLong && (
                  <div className="absolute inset-x-0 bottom-0 h-8 bg-gradient-to-t from-background to-transparent pointer-events-none" />
                )}
              </div>
              {isLong && (
                <button
                  ref={toggleRef}
                  type="button"
                  onClick={handleToggle}
                  className="flex items-center gap-1 text-xs mt-2 text-muted-foreground hover:text-foreground transition-colors"
                >
                  {expanded ? (
                    <><ChevronUp className="h-3.5 w-3.5" />Show less</>
                  ) : (
                    <><ChevronDown className="h-3.5 w-3.5" />Show more ({estimatedLines} lines)</>
                  )}
                </button>
              )}
            </>
          )}

          {/* Spinner footer for non-terminal streaming */}
          {entity?.taskStatus && !isTerminalState(entity.taskStatus) &&
           !isInteractiveState(entity.taskStatus) && !isArtifactStreaming && (
            <div className="flex items-center gap-1.5 mt-2 text-xs text-muted-foreground">
              <Loader2 className="w-3 h-3 animate-spin" />
              <span>{entity.taskStatusMessage || 'Still working...'}</span>
            </div>
          )}
        </div>
      )}

      {/* Artifacts */}
      {visibleArtifacts.length > 0 && (
        <div className="mt-3">
          <ArtifactList artifacts={visibleArtifacts} />
        </div>
      )}
    </CursorMessageRow>
  )
})

// ---------------------------------------------------------------------------
// Helper: map AgentResultViewModel.status -> AgentPhase
// ---------------------------------------------------------------------------

function mapResultStatus(result: AgentResultViewModel): AgentPhase {
  switch (result.status) {
    case 'working':
      return result.content.length > 0 ? 'streaming' : 'waiting'
    case 'awaiting_input':
      return 'interactive'
    case 'failed':
      return 'failed'
    case 'completed':
      return result.content.trim().length === 0 && result.artifacts.length === 0
        ? 'complete-empty'
        : 'complete'
  }
}
