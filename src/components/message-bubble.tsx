'use client'

import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, ImageIcon, Volume2, Film, AlertCircle, Loader2, Clock, MessageCircleQuestion, XCircle, CheckCircle, House, Cloud } from 'lucide-react'
import { cn, tryParseJson } from '@/lib/utils'
import { getAgentColorClasses, getAgentInitials } from '@/lib/agent-colors'
import { getAgentAvatarUri } from '@/lib/agent-avatar'
import { formatTimestamp, elapsedSeconds, formatElapsedTime } from '@/lib/time'
import { isPresignedUrlExpired } from '@/lib/presigned-url'
import { MarkdownContent, LinkifiedContent, JsonBlockExpandedContext } from './markdown-content'
import { CollapsibleJsonBlock } from './part-renderer'
import type { MessageEntity } from '@/stores/message-store'
import type { AttachmentData } from '@/lib/types/attachments'
import { ArtifactList } from './artifact-list'
import { ImageLightbox } from './image-lightbox'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { TASK_STATE, isFailureState, isInteractiveState, isTerminalState } from '@/lib/types/sse'
import type { LucideIcon } from 'lucide-react'
import type { Agent } from '@/lib/types/agent'

// ---------------------------------------------------------------------------
// Phase derivation — single source of truth for agent bubble presentation
// ---------------------------------------------------------------------------

export type AgentPhase =
  | 'waiting'
  | 'streaming'
  | 'interactive'
  | 'failed'
  | 'complete'
  | 'complete-empty'

/**
 * Pure O(1) function: derives the visual phase from entity fields at render time.
 * Must never perform store lookups or iterate beyond the single `.some()` call.
 *
 * Tier 1: taskStatus is authoritative when present.
 * Tier 2: no taskStatus — infer from content/streaming signals.
 */
export function derivePhase(entity: MessageEntity): AgentPhase {
  const hasContent = !!entity.content?.trim()
  const hasArtifacts = (entity.artifacts?.length ?? 0) > 0
  const isStreaming = entity.artifacts?.some(a => a.isStreaming) ?? false
  const hasVisibleBody = hasContent || hasArtifacts

  // ── Tier 1: taskStatus is authoritative when present ──

  if (entity.taskStatus && isFailureState(entity.taskStatus)) return 'failed'

  if (entity.taskStatus === TASK_STATE.COMPLETED) {
    return hasVisibleBody ? 'complete' : 'complete-empty'
  }

  if (entity.hitlResolved && entity.hitlUserAnswer) return 'interactive'

  if (entity.taskStatus && isInteractiveState(entity.taskStatus)) return 'interactive'

  if (entity.taskStatus && !isTerminalState(entity.taskStatus)) {
    return hasVisibleBody ? 'streaming' : 'waiting'
  }

  // ── Tier 2: no taskStatus — infer from content signals ──

  if (isStreaming) return 'streaming'
  if (hasVisibleBody) return 'complete'
  return 'waiting'
}

// ---------------------------------------------------------------------------
// Phase-to-style mapping
// ---------------------------------------------------------------------------

type PhaseStyleEntry = {
  border: string
  bg: string
  text: string
  icon: LucideIcon
  badge: string | ((entity: MessageEntity) => string)
}

const PHASE_STYLES: Partial<Record<AgentPhase, PhaseStyleEntry>> = {
  interactive: {
    border: 'border-amber-200 dark:border-amber-500/20',
    bg: 'bg-amber-50 dark:bg-amber-500/12',
    text: 'text-amber-700 dark:text-amber-400',
    icon: MessageCircleQuestion,
    badge: (entity) => {
      if (entity.hitlResolved) return 'Answered'
      if (entity.taskStatus === 'auth-required') return 'Auth needed'
      return 'Input needed'
    },
  },
  failed: {
    border: 'border-red-200 dark:border-red-500/20',
    bg: 'bg-red-50 dark:bg-red-500/12',
    text: 'text-red-600 dark:text-red-400',
    icon: XCircle,
    badge: (entity) => {
      if (entity.taskStatus === 'rejected') return 'Rejected'
      if (entity.taskStatus === 'canceled') return 'Canceled'
      return 'Failed'
    },
  },
  'complete-empty': {
    border: 'border-emerald-200 dark:border-emerald-500/20',
    bg: 'bg-emerald-50 dark:bg-emerald-500/12',
    text: 'text-emerald-600 dark:text-emerald-400',
    icon: CheckCircle,
    badge: 'Completed',
  },
}

function getPhaseStyles(phase: AgentPhase, entity: MessageEntity) {
  const entry = PHASE_STYLES[phase]
  if (!entry) return null
  const badge = typeof entry.badge === 'function' ? entry.badge(entity) : entry.badge
  return { ...entry, badge }
}

/** Lightweight UI type for passing quote data between components. */
export interface QuoteData {
  messageId: string
  content: string
  senderName: string
}

/**
 * Unified message shape consumed by bubble components.
 * Both old MessageData and new MessageEntity can be adapted to this.
 */
interface BubbleMessage {
  id: string
  content: string
  sender_name: string
  timestamp: string
  agent_id?: string
}

/** Adapt a MessageEntity to the BubbleMessage shape used by bubble components. */
function entityToBubble(entity: MessageEntity): BubbleMessage {
  return {
    id: entity.id,
    content: entity.content,
    sender_name: entity.senderName,
    timestamp: entity.timestamp,
    agent_id: entity.agentId,
  }
}

function AttachmentExpiredBanner({ icon: Icon }: { icon: React.ComponentType<{ className?: string }> }) {
  return (
    <div className="inline-flex items-center gap-2 rounded-md border border-dashed border-border bg-muted/50 px-2.5 py-1.5 text-xs text-muted-foreground">
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <AlertCircle className="h-3 w-3 shrink-0" />
      <span>Resource expired</span>
    </div>
  )
}

function GenericAttachmentLink({ url, fileName, sizeLabel }: { url: string; fileName: string; sizeLabel: string }) {
  if (isPresignedUrlExpired(url)) {
    return <AttachmentExpiredBanner icon={AlertCircle} />
  }

  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs hover:bg-muted transition-colors"
    >
      <span className="truncate max-w-[120px]">{fileName}</span>
      <span className="text-muted-foreground">{sizeLabel}</span>
    </a>
  )
}

function UserAttachmentCard({ attachment }: { attachment: AttachmentData }) {
  const [loadError, setLoadError] = useState(false)
  const isImg = attachment.mimeType.startsWith('image/')
  const isAudio = attachment.mimeType.startsWith('audio/')
  const isVideo = attachment.mimeType.startsWith('video/')
  const sizeLabel = attachment.sizeBytes < 1024 * 1024
    ? `${(attachment.sizeBytes / 1024).toFixed(0)} KB`
    : `${(attachment.sizeBytes / (1024 * 1024)).toFixed(1)} MB`

  if (isImg && attachment.fileUrl) {
    if (loadError) return <AttachmentExpiredBanner icon={ImageIcon} />
    return (
      <div className="max-w-[200px]">
        <ImageLightbox
          src={attachment.fileUrl}
          alt={attachment.fileName}
          className="max-w-[200px]"
          onError={() => setLoadError(true)}
        />
      </div>
    )
  }

  if (isAudio && attachment.fileUrl) {
    if (loadError) return <AttachmentExpiredBanner icon={Volume2} />
    return (
      <div className="my-1">
        <audio controls preload="metadata" className="max-w-full" onError={() => setLoadError(true)}>
          <source src={attachment.fileUrl} type={attachment.mimeType} />
        </audio>
        <span className="mt-1 block text-xs text-muted-foreground">
          {attachment.fileName} · {sizeLabel}
        </span>
      </div>
    )
  }

  if (isVideo && attachment.fileUrl) {
    if (loadError) return <AttachmentExpiredBanner icon={Film} />
    return (
      <div className="my-1">
        <video controls preload="metadata" className="max-w-full max-h-60 rounded-md border border-border" onError={() => setLoadError(true)}>
          <source src={attachment.fileUrl} type={attachment.mimeType} />
        </video>
        <span className="mt-1 block text-xs text-muted-foreground">
          {attachment.fileName} · {sizeLabel}
        </span>
      </div>
    )
  }

  if (attachment.fileUrl) {
    return (
      <GenericAttachmentLink
        url={attachment.fileUrl}
        fileName={attachment.fileName}
        sizeLabel={sizeLabel}
      />
    )
  }

  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs text-muted-foreground">
      <span className="truncate max-w-[120px]">{attachment.fileName}</span>
      <span>{sizeLabel}</span>
    </span>
  )
}

interface AgentBubbleProps {
  entity: MessageEntity
  compact?: boolean
  defaultExpanded?: boolean
  collapseSignal?: number
  autoCollapseVersion?: number
  isLatestAgent?: boolean
  isUserExpanded?: boolean
  onUserToggle?: (id: string, expanded: boolean) => void
  onQuote?: (data: QuoteData) => void
}

/**
 * User message bubble - internal implementation using BubbleMessage shape.
 */
function UserMessageBubbleInner({ message }: { message: BubbleMessage }) {
  const displayContent = message.content || "No message content"
  const isLongMessage = displayContent.length > 500
  const [isExpanded, setIsExpanded] = useState(false)
  const toggleButtonRef = useRef<HTMLButtonElement>(null)
  const estimatedLines = isLongMessage ? Math.max(5, Math.ceil(displayContent.length / 80)) : 0

  return (
    <div className="flex justify-end w-full">
        <div className="max-w-[80%] rounded-xl p-4 shadow-sm bg-secondary text-secondary-foreground message-bubble">
        <div className="flex items-center justify-between gap-4 mb-2">
          <span className="text-xs font-medium opacity-90">
            {message.sender_name}
          </span>
          <span className="text-xs opacity-70">
            {formatTimestamp(message.timestamp)}
          </span>
        </div>
        <div className="relative">
          <div
            className={cn(
              "text-sm leading-relaxed transition-opacity duration-200",
              !isExpanded && isLongMessage ? "line-clamp-4" : "whitespace-pre-wrap"
            )}
          >
            <LinkifiedContent content={displayContent} />
          </div>
          {!isExpanded && isLongMessage && (
            <div className="absolute inset-x-0 bottom-0 h-8 bg-linear-to-t from-secondary to-transparent pointer-events-none" />
          )}
        </div>

        {isLongMessage && (
          <button
            ref={toggleButtonRef}
            onClick={() => {
              const next = !isExpanded
              const buttonEl = toggleButtonRef.current
              const container = buttonEl?.closest('[data-message-scroll-container="true"]') as HTMLElement | null
              const prevBottom = buttonEl?.getBoundingClientRect().bottom

              setIsExpanded(next)

              if (buttonEl && container && !next && typeof prevBottom === 'number') {
                container.dataset.programmaticScroll = 'true'
                requestAnimationFrame(() => {
                  const newBottom = buttonEl.getBoundingClientRect().bottom
                  const delta = newBottom - prevBottom
                  if (delta !== 0) {
                    container.scrollTop += delta
                  }
                  requestAnimationFrame(() => {
                    container.dataset.programmaticScroll = 'false'
                  })
                })
              }
            }}
            className="flex items-center gap-1 text-xs mt-3 font-medium transition-colors text-secondary-foreground/70 hover:text-secondary-foreground"
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
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Typewriter hook — progressively reveals content for agent_response messages
// ---------------------------------------------------------------------------

const TYPEWRITER_CHARS_PER_TICK = 3
const TYPEWRITER_INTERVAL_MS = 12

function useTypewriter(fullContent: string, phase: AgentPhase, entity: MessageEntity) {
  const [revealedLen, setRevealedLen] = useState(0)
  const [isAnimating, setIsAnimating] = useState(false)
  const prevContentRef = useRef('')
  const hasAnimatedRef = useRef(false)

  // Detect when new complete content arrives that should be animated
  const shouldAnimate = useMemo(() => {
    if (!fullContent) return false
    // Only animate messages arriving live via SSE — skip DB-hydrated and optimistic
    if (entity.source !== 'sse') return false
    // Only animate agent messages that arrive complete (no task-based streaming)
    if (entity.messageType !== 'agent') return false
    // If already streaming via artifacts, skip typewriter
    if (entity.artifacts?.some(a => a.isStreaming)) return false
    // If task status indicates real streaming, skip
    if (entity.taskStatus && !isTerminalState(entity.taskStatus)) return false
    return true
  }, [fullContent, entity.source, entity.messageType, entity.artifacts, entity.taskStatus])

  useEffect(() => {
    // Content changed — decide whether to animate the new portion
    const prev = prevContentRef.current
    prevContentRef.current = fullContent

    if (!shouldAnimate || !fullContent) {
      setRevealedLen(fullContent.length)
      setIsAnimating(false)
      return
    }

    // If this entity was already fully revealed once, don't re-animate
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

/**
 * Agent message bubble - internal implementation rendering a MessageEntity.
 * Renders all agent message phases (waiting, streaming, interactive, failed,
 * complete, complete-empty) within a single always-mounted component so there
 * is never a React mount/unmount between states.
 */
function AgentMessageBubbleInner({
  entity,
  compact = false,
  defaultExpanded = false,
  collapseSignal = 0,
  autoCollapseVersion = 0,
  isLatestAgent = false,
  isUserExpanded = false,
  onUserToggle,
  onQuote,
}: AgentBubbleProps) {
  const queryClient = useQueryClient()
  const agentIconUrl = entity.agentId
    ? (queryClient.getQueryData<Agent[]>(['agents', 'all'])
        ?.find(a => a.agent_id === entity.agentId)
        ?.agent_card?.iconUrl ?? null)
    : null

  const phase = derivePhase(entity)
  const showIndicator = phase === 'waiting'
  const isArtifactStreaming = entity.artifacts?.some(a => a.isStreaming) ?? false
  const [isExpanded, setIsExpanded] = useState(
    defaultExpanded || isUserExpanded || (!compact && (entity.content || '').length < 500)
  )
  const prevCollapseSignal = useRef(collapseSignal)
  const prevAutoCollapseVersion = useRef(autoCollapseVersion)
  const toggleButtonRef = useRef<HTMLButtonElement>(null)
  const bubbleRef = useRef<HTMLDivElement>(null)
  const wasStreamingContent = useRef(false)
  const [jsonContentOpen, setJsonContentOpen] = useState(defaultExpanded)

  // --- Quote selection state ---
  const contentRef = useRef<HTMLDivElement>(null)
  const quoteBtnRef = useRef<HTMLButtonElement | null>(null)
  const selectedTextRef = useRef<string>('')

  // --- Elapsed timer for WAITING and active INTERACTIVE phases ---
  const [elapsed, setElapsed] = useState(() =>
    entity.taskCreatedAt ? elapsedSeconds(entity.taskCreatedAt) : 0
  )
  useEffect(() => {
    const needsTimer = phase === 'waiting' || (phase === 'interactive' && !entity.hitlResolved)
    if (!needsTimer || !entity.taskCreatedAt) {
      setElapsed(0)
      return
    }
    setElapsed(elapsedSeconds(entity.taskCreatedAt))
    const id = setInterval(() => {
      setElapsed(elapsedSeconds(entity.taskCreatedAt!))
    }, 1000)
    return () => clearInterval(id)
  }, [phase, entity.taskCreatedAt, entity.hitlResolved])

  const hideQuoteButton = useCallback(() => {
    if (quoteBtnRef.current) {
      quoteBtnRef.current.remove()
      quoteBtnRef.current = null
    }
    selectedTextRef.current = ''
  }, [])

  // Create or update quote button using native DOM to avoid React re-render
  const showQuoteButton = useCallback((top: number, left: number, text: string) => {
    selectedTextRef.current = text
    
    // Remove existing button if any
    if (quoteBtnRef.current) {
      quoteBtnRef.current.remove()
    }

    // Create button element
    const btn = document.createElement('button')
    btn.setAttribute('data-quote-btn', 'true')
    btn.setAttribute('type', 'button')
    btn.className = 'fixed z-[9999] flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-md shadow-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors whitespace-nowrap select-none'
    btn.style.top = `${top}px`
    btn.style.left = `${left}px`
    btn.style.transform = 'translateX(-50%)'
    btn.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 21c3 0 7-1 7-8V5c0-1.25-.756-2.017-2-2H4c-1.25 0-2 .75-2 1.972V11c0 1.25.75 2 2 2 1 0 1 0 1 1v1c0 1-1 2-2 2s-1 .008-1 1.031V21c0 1 0 1 1 1z"/><path d="M15 21c3 0 7-1 7-8V5c0-1.25-.757-2.017-2-2h-4c-1.25 0-2 .75-2 1.972V11c0 1.25.75 2 2 2h.75c0 2.25.25 4-2.75 4v3c0 1 0 1 1 1z"/></svg>Quote`
    
    btn.onmousedown = (e) => {
      e.preventDefault() // Prevent selection from being cleared
    }
    
    btn.onclick = () => {
      onQuote?.({
        messageId: entity.id,
        content: selectedTextRef.current,
        senderName: entity.senderName,
      })
      hideQuoteButton()
      window.getSelection()?.removeAllRanges()
    }

    document.body.appendChild(btn)
    quoteBtnRef.current = btn
  }, [entity.id, entity.senderName, onQuote, hideQuoteButton])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      hideQuoteButton()
    }
  }, [hideQuoteButton])

  const handleMouseUp = useCallback((e: React.MouseEvent) => {
    // Don't dismiss when clicking the Quote button itself (let onClick fire first)
    if ((e.target as HTMLElement).closest('[data-quote-btn]')) return

    // Use a small delay to let the browser finalize the selection
    requestAnimationFrame(() => {
      const selection = window.getSelection()
      if (!selection || selection.isCollapsed || !contentRef.current) {
        hideQuoteButton()
        return
      }
      const text = selection.toString().trim()
      if (!text) { hideQuoteButton(); return }

      // Make sure the selection is inside this bubble
      const range = selection.getRangeAt(0)
      if (!contentRef.current.contains(range.commonAncestorContainer)) {
        hideQuoteButton()
        return
      }

      // Only show quote button if onQuote callback is provided
      if (!onQuote) return

      // Use viewport coordinates for positioning
      const rect = range.getBoundingClientRect()
      showQuoteButton(
        rect.top - 32 + window.scrollY,
        rect.left + rect.width / 2 + window.scrollX,
        text
      )
    })
  }, [onQuote, showQuoteButton, hideQuoteButton])

  // Dismiss quote button when clicking elsewhere (but not when selecting text)
  useEffect(() => {
    const handleDown = (e: MouseEvent) => {
      if (!quoteBtnRef.current) return
      const target = e.target as HTMLElement
      // Don't dismiss when clicking the Quote button itself
      if (target.closest('[data-quote-btn]')) return
      // Don't dismiss when clicking inside the content area (user might be selecting text)
      if (contentRef.current?.contains(target)) return
      hideQuoteButton()
    }
    document.addEventListener('mousedown', handleDown)
    return () => document.removeEventListener('mousedown', handleDown)
  }, [hideQuoteButton])

  useEffect(() => {
    setIsExpanded(false)
    onUserToggle?.(entity.id, false)
  }, [collapseSignal, entity.id, onUserToggle])

  // Sync expansion when parent marks message as user-expanded (e.g., timeline expand-all)
  useEffect(() => {
    if (isUserExpanded && !isExpanded) {
      setIsExpanded(true)
    }
  }, [isUserExpanded, isExpanded])

  useEffect(() => {
    if (defaultExpanded && collapseSignal === prevCollapseSignal.current) {
      setIsExpanded(true)
    }
    prevCollapseSignal.current = collapseSignal
  }, [defaultExpanded, collapseSignal])

  // Collapse older agent responses when a new agent message arrives,
  // unless the user explicitly expanded this one.
  useEffect(() => {
    if (
      autoCollapseVersion !== undefined &&
      prevAutoCollapseVersion.current !== undefined &&
      autoCollapseVersion !== prevAutoCollapseVersion.current &&
      !isLatestAgent &&
      !isUserExpanded
    ) {
      setIsExpanded(false)
    }
    prevAutoCollapseVersion.current = autoCollapseVersion
  }, [autoCollapseVersion, isLatestAgent, isUserExpanded])
  
  const fullContent = entity.content || ''
  const { displayContent, isTypewriting } = useTypewriter(fullContent, phase, entity)
  const isEffectivelyStreaming = phase === 'streaming' || isTypewriting

  useEffect(() => {
    if (isEffectivelyStreaming) wasStreamingContent.current = true
  }, [isEffectivelyStreaming])
  // Use tryParseJson unconditionally — it returns null for incomplete/invalid JSON,
  // so it's safe to call during streaming. Removing the phase guard fixes cases where
  // taskStatus never transitions to 'completed' but content is valid JSON.
  const parsedJsonContent = isTypewriting ? null : tryParseJson(displayContent)
  const isLongMessage = parsedJsonContent === null && fullContent.length > 500
  const estimatedLines = isLongMessage ? Math.max(5, Math.ceil(fullContent.length / 80)) : 0

  // Open the JSON collapsible when streaming/typewriting finishes and content is JSON
  useEffect(() => {
    if (!isEffectivelyStreaming && wasStreamingContent.current && parsedJsonContent !== null) {
      setJsonContentOpen(true)
    }
  }, [isEffectivelyStreaming, parsedJsonContent])
  
  const colors = getAgentColorClasses(entity.agentId || 'unknown')
  const textColorClass = colors.text
  const contentColorClass = colors.content

  // Phase-aware styling: use PHASE_STYLES colors when available, else agent colors
  const phaseStyle = getPhaseStyles(phase, entity)
  const bubbleBorder = phaseStyle?.border ?? colors.border
  const bubbleBg = phaseStyle?.bg ?? colors.bg
  const phaseTextColor = phaseStyle?.text ?? textColorClass

  // Whether to split HITL history and completion content into separate bubbles
  const hasResolvedHitl = entity.hitlResolved === true && !!entity.hitlUserAnswer
  const hasCompletionContent = (phase === 'streaming' || phase === 'complete' || isTypewriting) && (!!displayContent || parsedJsonContent !== null)
  const splitBubbles = hasResolvedHitl && hasCompletionContent && phase !== 'interactive'

  const renderHeader = (headerPhaseStyle: typeof phaseStyle) => (
    <div className="flex items-center justify-between mb-2">
      <div className="flex items-center gap-2">
        <a
          href={`/c/agents/${entity.agentId}`}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-2 hover:opacity-80 transition-opacity"
        >
          <div
            className={cn(
              "w-6 h-6 rounded-full flex items-center justify-center font-semibold border shrink-0 overflow-hidden",
              headerPhaseStyle
                ? `${headerPhaseStyle.bg} ${headerPhaseStyle.border}`
                : `${colors.bg} ${colors.border}`,
              phaseTextColor
            )}
            title={entity.senderName}
          >
            {headerPhaseStyle
              ? <headerPhaseStyle.icon className="h-3 w-3" />
              : entity.agentId
                ? <img src={agentIconUrl ?? getAgentAvatarUri(entity.agentId)} alt="" className="h-full w-full object-cover" />
                : <span className="text-[10px]">{getAgentInitials(entity.senderName)}</span>
            }
          </div>
          <span className={cn("text-xs font-semibold underline-offset-2 hover:underline", phaseTextColor)}>
            {entity.senderName}
          </span>
        </a>
        {entity.stepNumber && entity.totalSteps && entity.totalSteps > 0 && (
          <span className={cn("text-[10px] font-medium px-1.5 py-0.5 rounded bg-current/10", phaseTextColor)}>
            Step {entity.stepNumber} / {entity.totalSteps}
          </span>
        )}
        {entity.agentSource && (
          <TooltipProvider delayDuration={200}>
            <Tooltip>
              <TooltipTrigger asChild>
                {entity.agentSource === 'hub'
                  ? <House className={cn("h-3 w-3 shrink-0", phaseTextColor)} />
                  : <Cloud className={cn("h-3 w-3 shrink-0", phaseTextColor)} />}
              </TooltipTrigger>
              <TooltipContent side="top" sideOffset={4}>
                {entity.agentSource === 'hub' ? 'Local agent' : 'Cloud agent'}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
      </div>
      <div className="flex items-center gap-2">
        {headerPhaseStyle && (
          <span className={cn("text-xs font-medium", phaseTextColor)}>
            {headerPhaseStyle.badge}
          </span>
        )}
        <span className="text-xs text-slate-500 dark:text-slate-400">
          {formatTimestamp(entity.timestamp)}
        </span>
      </div>
    </div>
  )

  const renderCompletionContent = () => (
    <>
      {parsedJsonContent !== null ? (
        <CollapsibleJsonBlock data={parsedJsonContent} open={jsonContentOpen} onOpenChange={setJsonContentOpen} />
      ) : (
        <div className="relative">
          <div
            ref={contentRef}
            className={cn(
              "min-h-0 overflow-hidden text-sm leading-relaxed select-text transition-opacity duration-200",
              contentColorClass,
              !isExpanded && isLongMessage && "max-h-[5lh]"
            )}
            onMouseUp={handleMouseUp}
          >
            <JsonBlockExpandedContext.Provider value={isExpanded}>
              <MarkdownContent
                content={displayContent}
                isStreaming={isEffectivelyStreaming}
              />
            </JsonBlockExpandedContext.Provider>
          </div>
          {!isExpanded && isLongMessage && (
            <div className="absolute inset-x-0 bottom-0 h-8 bg-linear-to-t from-card to-transparent pointer-events-none" />
          )}
        </div>
      )}

      {isLongMessage && (
        <button
          ref={toggleButtonRef}
          onClick={() => {
            const next = !isExpanded
            const buttonEl = toggleButtonRef.current
            const container = buttonEl?.closest('[data-message-scroll-container="true"]') as HTMLElement | null
            const prevBottom = buttonEl?.getBoundingClientRect().bottom

            setIsExpanded(next)
            onUserToggle?.(entity.id, next)

            if (buttonEl && container && !next && typeof prevBottom === 'number') {
              container.dataset.programmaticScroll = 'true'
              requestAnimationFrame(() => {
                const newBottom = buttonEl.getBoundingClientRect().bottom
                const delta = newBottom - prevBottom
                if (delta !== 0) {
                  container.scrollTop += delta
                }
                requestAnimationFrame(() => {
                  container.dataset.programmaticScroll = 'false'
                })
              })
            }
          }}
          className={cn(
            "flex items-center gap-1 text-xs mt-3 font-medium transition-colors",
            textColorClass,
            "hover:opacity-80"
          )}
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

      {entity.taskStatus && !isTerminalState(entity.taskStatus) &&
       !isInteractiveState(entity.taskStatus) && !isArtifactStreaming && (
        <div className="flex items-center gap-1.5 mt-2 text-xs text-muted-foreground">
          <Loader2 className="w-3 h-3 animate-spin" />
          <span>{entity.taskStatusMessage || 'Still working…'}</span>
        </div>
      )}
    </>
  )

  // When split, render HITL resolved history as a separate bubble above
  if (splitBubbles) {
    const hitlPromptText = entity.hitlPrompt || entity.taskStatusMessage || 'The agent needs additional information to continue.'
    const interactiveStyle = getPhaseStyles('interactive', entity)
    const hitlBorder = interactiveStyle?.border ?? colors.border
    const hitlBg = interactiveStyle?.bg ?? colors.bg

    return (
      <div className="flex flex-col w-full gap-3">
        {/* HITL bubble */}
        <div className={cn("flex-1 min-w-0 overflow-hidden rounded-xl p-4 shadow-sm border message-bubble agent-message", hitlBorder, hitlBg)}>
          {renderHeader(interactiveStyle)}
          <div className="space-y-2">
            <div className="text-sm text-amber-700 dark:text-amber-300">
              <MarkdownContent content={hitlPromptText} />
            </div>
            <div className="pt-2 border-t border-amber-200 dark:border-amber-500/20">
              <p className="text-xs text-amber-600 dark:text-amber-400 font-medium mb-1">Your answer:</p>
              <p className="text-sm text-amber-700 dark:text-amber-300 bg-amber-100/50 dark:bg-amber-500/8 rounded-md px-3 py-1.5">
                {entity.hitlUserAnswer}
              </p>
            </div>
          </div>
        </div>

        {/* Completion bubble */}
        <div
          ref={bubbleRef}
          className={cn("flex-1 min-w-0 overflow-hidden rounded-xl p-4 shadow-sm border message-bubble agent-message", bubbleBorder, bubbleBg)}
        >
          {renderHeader(phaseStyle)}
          {renderCompletionContent()}
        </div>
      </div>
    )
  }

  return (
    <div className="flex w-full">
      {/* Message Content */}
      <div
        ref={bubbleRef}
        className={cn(
          "flex-1 min-w-0 overflow-hidden rounded-xl p-4 shadow-sm border message-bubble agent-message",
          bubbleBorder,
          bubbleBg
        )}
      >
        {renderHeader(phaseStyle)}

        {/* ── WAITING phase: spinner + status text ── */}
        <div
          className={cn(
            "transition-opacity duration-200",
            showIndicator ? "opacity-100" : "opacity-0 pointer-events-none h-0"
          )}
          aria-hidden={!showIndicator}
        >
          {entity.taskStatus ? (
            <div className="space-y-2">
              <div className="flex items-start gap-2">
                <Loader2 className={cn("w-4 h-4 animate-spin mt-0.5 shrink-0", textColorClass)} />
                <span className={cn("text-sm shimmer-text", textColorClass)}>
                  {entity.taskStatusMessage || entity.taskContent || 'Working on your request…'}
                </span>
              </div>
              {elapsed > 0 && (
                <p className="text-xs text-muted-foreground flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  {formatElapsedTime(elapsed)} elapsed
                </p>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-0.5">
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className={cn("w-1.5 h-1.5 rounded-full animate-bounce", textColorClass)}
                  style={{ animationDelay: `${i * 150}ms` }}
                />
              ))}
            </div>
          )}
        </div>

        {/* ── INTERACTIVE phase: HITL prompt + optional answer ── */}
        {/* Show when actively interactive OR when resolved HITL history should remain visible */}
        {(phase === 'interactive' || (entity.hitlResolved && entity.hitlUserAnswer)) && (() => {
          const promptText = entity.hitlPrompt || entity.content || entity.taskStatusMessage || 'The agent needs additional information to continue.'
          const isResolved = entity.hitlResolved === true
          return (
            <div className="space-y-2">
              <div className="text-sm text-amber-700 dark:text-amber-300">
                <MarkdownContent content={promptText} />
              </div>
              {isResolved && entity.hitlUserAnswer && (
                <div className="pt-2 border-t border-amber-200 dark:border-amber-500/20">
                  <p className="text-xs text-amber-600 dark:text-amber-400 font-medium mb-1">Your answer:</p>
                  <p className="text-sm text-amber-700 dark:text-amber-300 bg-amber-100/50 dark:bg-amber-500/8 rounded-md px-3 py-1.5">
                    {entity.hitlUserAnswer}
                  </p>
                </div>
              )}
              {!isResolved && (
                <p className="text-xs text-amber-600 dark:text-amber-400 flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  {formatElapsedTime(elapsed)} elapsed
                </p>
              )}
            </div>
          )
        })()}

        {/* ── FAILED phase: error message ── */}
        {phase === 'failed' && (() => {
          const failedBadge = phaseStyle?.badge ?? 'Failed'
          const displayBody = entity.taskError || entity.content || `Task ${failedBadge.toLowerCase()}`
          const isLong = displayBody.length > 500
          const failedEstimatedLines = isLong ? Math.max(5, Math.ceil(displayBody.length / 80)) : 0
          return (
            <div>
              <div className="relative">
                <div className={cn("text-sm text-red-700 dark:text-red-300 transition-opacity duration-200", !isExpanded && isLong && "line-clamp-4")}>
                  <MarkdownContent content={displayBody} />
                </div>
                {!isExpanded && isLong && (
                  <div className="absolute inset-x-0 bottom-0 h-8 bg-linear-to-t from-card to-transparent pointer-events-none" />
                )}
              </div>
              {isLong && (
                <button
                  ref={toggleButtonRef}
                  onClick={() => {
                    const next = !isExpanded
                    const buttonEl = toggleButtonRef.current
                    const container = buttonEl?.closest('[data-message-scroll-container="true"]') as HTMLElement | null
                    const prevBottom = buttonEl?.getBoundingClientRect().bottom
                    setIsExpanded(next)
                    onUserToggle?.(entity.id, next)
                    if (buttonEl && container && !next && typeof prevBottom === 'number') {
                      container.dataset.programmaticScroll = 'true'
                      requestAnimationFrame(() => {
                        const newBottom = buttonEl.getBoundingClientRect().bottom
                        const delta = newBottom - prevBottom
                        if (delta !== 0) container.scrollTop += delta
                        requestAnimationFrame(() => { container.dataset.programmaticScroll = 'false' })
                      })
                    }
                  }}
                  className="flex items-center gap-1 text-xs mt-3 font-medium transition-colors text-red-600 dark:text-red-400 hover:opacity-80"
                >
                  {isExpanded ? <><ChevronUp className="h-3.5 w-3.5" />Show less</> : <><ChevronDown className="h-3.5 w-3.5" />Show more ({failedEstimatedLines} lines)</>}
                </button>
              )}
            </div>
          )
        })()}

        {/* ── COMPLETE-EMPTY phase: minimal badge ── */}
        {phase === 'complete-empty' && (
          <div className="flex items-center gap-2 py-1 text-xs text-emerald-600 dark:text-emerald-400">
            <CheckCircle className="h-3.5 w-3.5" />
            <span>Completed</span>
            {entity.taskCreatedAt && (
              <span className="flex items-center gap-0.5 opacity-60">
                <Clock className="h-3 w-3" />
                {formatElapsedTime(elapsedSeconds(entity.taskCreatedAt))}
              </span>
            )}
          </div>
        )}

        {/* ── STREAMING / COMPLETE phases: normal content area ── */}
        {!splitBubbles && (phase === 'streaming' || phase === 'complete' || isTypewriting) && renderCompletionContent()}
      </div>
    </div>
  )
}

// ── Entity-based bubble components (for normalized store) ────────────────

/**
 * User bubble that renders a MessageEntity.
 * Adapts entity fields to the BubbleMessage shape used internally.
 */
export function EntityUserBubble({ entity }: { entity: MessageEntity }) {
  const bubble = entityToBubble(entity)
  return (
    <>
      <UserMessageBubbleInner message={bubble} />
      {entity.attachments && entity.attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 mt-1.5 justify-end">
          {entity.attachments.map(att => (
            <UserAttachmentCard key={att.fileId} attachment={att} />
          ))}
        </div>
      )}
    </>
  )
}

/**
 * Agent bubble that renders a MessageEntity.
 * All phases (waiting, streaming, interactive, failed, complete, complete-empty)
 * are rendered inside a single always-mounted AgentMessageBubbleInner so there
 * is never a React mount/unmount between states.
 */
export function EntityAgentBubble({
  entity,
  compact = false,
  defaultExpanded = false,
  collapseSignal = 0,
  autoCollapseVersion = 0,
  isLatestAgent = false,
  isUserExpanded = false,
  onUserToggle,
  onQuote,
}: {
  entity: MessageEntity
  compact?: boolean
  defaultExpanded?: boolean
  collapseSignal?: number
  autoCollapseVersion?: number
  isLatestAgent?: boolean
  isUserExpanded?: boolean
  onUserToggle?: (id: string, expanded: boolean) => void
  onQuote?: (data: QuoteData) => void
}) {
  return (
    <>
      <AgentMessageBubbleInner
        entity={entity}
        compact={compact}
        defaultExpanded={defaultExpanded}
        collapseSignal={collapseSignal}
        autoCollapseVersion={autoCollapseVersion}
        isLatestAgent={isLatestAgent}
        isUserExpanded={isUserExpanded}
        onUserToggle={onUserToggle}
        onQuote={onQuote}
      />
      {entity.artifacts && entity.artifacts.length > 0 && (() => {
        const messageText = (entity.content || '').trim()
        const nonDuplicate = entity.artifacts.filter((a) => {
          const isTextOnly = a.parts.length > 0 && a.parts.every((p) => p.kind === 'text')
          if (isTextOnly && messageText) return false
          return true
        })
        return nonDuplicate.length > 0 ? <ArtifactList artifacts={nonDuplicate} /> : null
      })()}
    </>
  )
}

