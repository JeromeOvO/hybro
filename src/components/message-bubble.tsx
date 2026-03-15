'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import { ChevronDown, ChevronUp, ImageIcon, Volume2, Film, AlertCircle, Shield, Cloud, Loader2, Clock } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getAgentColorClasses, getAgentInitials } from '@/lib/agent-colors'
import { formatTimestamp, elapsedSeconds, formatElapsedTime } from '@/lib/time'
import { isPresignedUrlExpired } from '@/lib/presigned-url'
import { MarkdownContent, LinkifiedContent } from './markdown-content'
import { useStreamingContent } from '@/hooks/useStreamingContent'
import type { MessageEntity } from '@/stores/message-store'
import type { AttachmentData } from '@/lib/types/attachments'
import { ArtifactList } from './artifact-list'
import { PENDING_STATES } from '@/lib/types/sse'

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
  agentSource?: 'cloud' | 'hub'
}

/** Adapt a MessageEntity to the BubbleMessage shape used by bubble components. */
function entityToBubble(entity: MessageEntity): BubbleMessage {
  return {
    id: entity.id,
    content: entity.content,
    sender_name: entity.senderName,
    timestamp: entity.timestamp,
    agent_id: entity.agentId,
    agentSource: entity.agentSource,
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
      <a href={attachment.fileUrl} target="_blank" rel="noopener noreferrer" className="block max-w-[200px]">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={attachment.fileUrl}
          alt={attachment.fileName}
          className="rounded-md border border-border max-h-40 object-cover"
          loading="lazy"
          onError={() => setLoadError(true)}
        />
      </a>
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

interface EntityBubbleProps {
  message: BubbleMessage
  compact?: boolean
  defaultExpanded?: boolean
  collapseSignal?: number
  autoCollapseVersion?: number
  isLatestAgent?: boolean
  isUserExpanded?: boolean
  onUserToggle?: (id: string, expanded: boolean) => void
  onQuote?: (data: QuoteData) => void
  isStreaming?: boolean
  showIndicator?: boolean
  waitingInfo?: {
    taskStatus: string
    taskStatusMessage?: string
    taskContent?: string
    taskCreatedAt?: string
  }
}

/**
 * User message bubble - internal implementation using BubbleMessage shape.
 */
function UserMessageBubbleInner({ message }: { message: BubbleMessage }) {
  const displayContent = message.content || "No message content"
  const isLongMessage = displayContent.length > 500
  const [isExpanded, setIsExpanded] = useState(false)
  const toggleButtonRef = useRef<HTMLButtonElement>(null)

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
        <div
          className={cn(
            "text-sm leading-relaxed",
            !isExpanded && isLongMessage ? "line-clamp-4" : "whitespace-pre-wrap"
          )}
        >
          <LinkifiedContent content={displayContent} />
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
                Show more
              </>
            )}
          </button>
        )}
      </div>
    </div>
  )
}

/**
 * Agent message bubble - internal implementation using BubbleMessage shape.
 * Renders static or typewriter-reveal content via Streamdown.
 * When showIndicator=true the content area fades out and bouncing dots fade
 * in, all within the same mounted component so there is no layout flash.
 */
function AgentMessageBubbleInner({
  message,
  compact = false,
  defaultExpanded = false,
  collapseSignal = 0,
  autoCollapseVersion = 0,
  isLatestAgent = false,
  isUserExpanded = false,
  onUserToggle,
  onQuote,
  isStreaming = false,
  showIndicator = false,
  waitingInfo,
}: EntityBubbleProps) {
  const [isExpanded, setIsExpanded] = useState(
    defaultExpanded || isUserExpanded || (!compact && message.content.length < 500)
  )
  const prevCollapseSignal = useRef(collapseSignal)
  const prevAutoCollapseVersion = useRef(autoCollapseVersion)
  const toggleButtonRef = useRef<HTMLButtonElement>(null)
  const bubbleRef = useRef<HTMLDivElement>(null)

  // --- Quote selection state ---
  const contentRef = useRef<HTMLDivElement>(null)
  const quoteBtnRef = useRef<HTMLButtonElement | null>(null)
  const selectedTextRef = useRef<string>('')

  // --- Elapsed timer for WAITING phase ---
  const [elapsed, setElapsed] = useState(() =>
    waitingInfo?.taskCreatedAt ? elapsedSeconds(waitingInfo.taskCreatedAt) : 0
  )
  useEffect(() => {
    if (!showIndicator || !waitingInfo?.taskCreatedAt) return
    setElapsed(elapsedSeconds(waitingInfo.taskCreatedAt))
    const id = setInterval(() => {
      setElapsed(elapsedSeconds(waitingInfo.taskCreatedAt))
    }, 1000)
    return () => clearInterval(id)
  }, [showIndicator, waitingInfo?.taskCreatedAt])

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
        messageId: message.id,
        content: selectedTextRef.current,
        senderName: message.sender_name,
      })
      hideQuoteButton()
      window.getSelection()?.removeAllRanges()
    }

    document.body.appendChild(btn)
    quoteBtnRef.current = btn
  }, [message.id, message.sender_name, onQuote, hideQuoteButton])

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
    onUserToggle?.(message.id, false)
  }, [collapseSignal, message.id, onUserToggle])

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
  
  const displayContent = message.content
  const isLongMessage = displayContent.length > 500
  
  const colors = getAgentColorClasses(message.agent_id || 'unknown')
  const textColorClass = colors.text
  const contentColorClass = colors.content

  return (
    <div className="flex w-full">
      {/* Message Content */}
      <div
        ref={bubbleRef}
        className={cn(
          "flex-1 min-w-0 overflow-hidden rounded-xl p-4 shadow-sm border message-bubble agent-message",
          colors.border,
          colors.bg
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <a
            href={`/c/agents/${message.agent_id}`}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 hover:opacity-80 transition-opacity"
          >
            <div
              className={cn(
                "w-6 h-6 rounded-full flex items-center justify-center font-semibold border shrink-0",
                colors.bg,
                colors.border,
                textColorClass
              )}
              title={message.sender_name}
            >
              <span className="text-[10px]">{getAgentInitials(message.sender_name)}</span>
            </div>
            <span className={cn("text-xs font-semibold underline-offset-2 hover:underline", textColorClass)}>
              {message.sender_name}
            </span>
          </a>
          <span className="text-xs text-slate-500 dark:text-slate-400">
            {formatTimestamp(message.timestamp)}
          </span>
          {message.agentSource === 'hub' && (
            <span className="inline-flex items-center gap-0.5 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
              <Shield className="h-2.5 w-2.5" />
              Local
            </span>
          )}
          {message.agentSource === 'cloud' && (
            <span className="inline-flex items-center gap-0.5 rounded-full bg-sky-500/10 px-1.5 py-0.5 text-[10px] font-medium text-sky-600 dark:text-sky-400">
              <Cloud className="h-2.5 w-2.5" />
              Cloud
            </span>
          )}
        </div>
        {/* Typing / waiting indicator — lives in normal flow above the content grid.
            When task metadata is available (WAITING phase), show a richer status
            with elapsed time. Otherwise fall back to simple bouncing dots. */}
        <div
          className={cn(
            "transition-opacity duration-200",
            showIndicator ? "opacity-100" : "opacity-0 pointer-events-none h-0"
          )}
          aria-hidden={!showIndicator}
        >
          {waitingInfo ? (
            <div className="flex items-center gap-2 py-1 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0" />
              <span>{waitingInfo.taskStatusMessage || waitingInfo.taskContent || 'Working on your request…'}</span>
              {elapsed > 0 && (
                <span className="flex items-center gap-0.5 opacity-60">
                  <Clock className="h-3 w-3" />
                  {formatElapsedTime(elapsed)}
                </span>
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

        {/* Content wrapper. During the indicator phase, collapse the content
            area. No transition is used because the 0fr→1fr animation causes a
            brief "one-token-per-line" flash as the container height grows. */}
        <div
          className={cn(
            "grid",
            showIndicator ? "grid-rows-[0fr]" : "grid-rows-[1fr]"
          )}
        >
          <div
            ref={contentRef}
            className={cn(
              "min-h-0 overflow-hidden text-sm leading-relaxed select-text",
              contentColorClass,
              !isExpanded && isLongMessage && "line-clamp-4"
            )}
            onMouseUp={handleMouseUp}
          >
            <MarkdownContent
              content={displayContent}
              isStreaming={isStreaming}
            />
          </div>
        </div>

        {/* Expand/Collapse button */}
        {isLongMessage && (
          <button
            ref={toggleButtonRef}
            onClick={() => {
              const next = !isExpanded
              const buttonEl = toggleButtonRef.current
              const container = buttonEl?.closest('[data-message-scroll-container="true"]') as HTMLElement | null
              const prevBottom = buttonEl?.getBoundingClientRect().bottom

              setIsExpanded(next)
              onUserToggle?.(message.id, next)

              // Keep collapse from jumping; let expand naturally push content downward.
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
                Show more
              </>
            )}
          </button>
        )}
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
 * - Shows typing indicator until the first streaming token arrives.
 * - During streaming: shows content live via Streamdown in streaming mode,
 *   with line-buffering from the streaming buffer ensuring stable chunks.
 * - After streaming ends: typewriter-reveals the final content via Streamdown.
 *
 * The typing indicator and content area live inside a single always-mounted
 * AgentMessageBubbleInner so there is never a React mount/unmount between
 * states, eliminating layout-flash on the transition.
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
  const { isStreaming, streamingText } = useStreamingContent(entity.id)

  // Typewriter reveal state. After streaming ends we animate the final content
  // in by progressively slicing entity.content and feeding it to Streamdown.
  const [revealedChars, setRevealedChars] = useState(0)
  const [isRevealing, setIsRevealing] = useState(false)
  const prevIsStreaming = useRef(isStreaming)

  const streamingPreviewText = streamingText

  // Detect the exact render where streaming just ended so we can keep showing
  // the last preview text until the reveal state becomes active on the next render.
  const isStartingReveal =
    prevIsStreaming.current &&
    !isStreaming &&
    entity.content.length > 0 &&
    !isRevealing

  const lastStreamingPreviewRef = useRef('')
  if (isStreaming && streamingPreviewText) {
    lastStreamingPreviewRef.current = streamingPreviewText
  }

  // Detect the streaming→done transition synchronously during render.
  // Calling setState here schedules the next render with isRevealing=true,
  // but the CURRENT render still executes with isRevealing=false. The
  // displayContent ternary below uses lastLiveTextRef to cover that gap.
  if (prevIsStreaming.current !== isStreaming) {
    prevIsStreaming.current = isStreaming
    if (!isStreaming && entity.content.length > 0 && !isRevealing) {
      const initialRevealChars = lastStreamingPreviewRef.current
        ? Math.min(entity.content.length, Math.max(10, lastStreamingPreviewRef.current.length))
        : Math.min(10, entity.content.length)
      setRevealedChars(initialRevealChars)
      setIsRevealing(true)
    }
  }

  // Advance the reveal at ~60fps (10 chars/frame ≈ 600 chars/sec).
  useEffect(() => {
    if (!isRevealing) return
    const full = entity.content
    let animId: number
    let active = true

    const step = () => {
      if (!active) return
      setRevealedChars(prev => {
        const next = prev + 10
        if (next >= full.length) {
          setIsRevealing(false)
          return full.length
        }
        return next
      })
      animId = requestAnimationFrame(step)
    }

    animId = requestAnimationFrame(step)
    return () => {
      active = false
      cancelAnimationFrame(animId)
    }
  }, [isRevealing, entity.content])

  // Show the typing indicator while no displayable content exists:
  // - Before streaming starts (~16ms gap before the first rAF flush).
  // - Once streaming is active but no complete line has arrived yet.
  // - But NOT when artifacts are present (Ollama streams content as artifacts).
  const hasArtifactContent = (entity.artifacts?.length ?? 0) > 0
  const showIndicator =
    ((!isStreaming && !isRevealing && !entity.content) ||
    (isStreaming && !streamingPreviewText.trim())) &&
    !hasArtifactContent

  // WAITING phase: entity has a pending task status but no content/streaming yet.
  // Pass task metadata to the inner component for a richer waiting indicator.
  const waitingInfo = showIndicator && entity.taskStatus && PENDING_STATES.includes(entity.taskStatus)
    ? {
        taskStatus: entity.taskStatus,
        taskStatusMessage: entity.taskStatusMessage ?? undefined,
        taskContent: entity.taskContent,
        taskCreatedAt: entity.taskCreatedAt,
      }
    : undefined

  // displayContent priority:
  // 1. '' — typing indicator phase
  // 2. entity.content slice — typewriter reveal
  // 3. streamingPreviewText — plain-text live preview during streaming
  // 4. lastStreamingPreviewRef — transition frame: streaming ended, reveal not yet active
  // 5. entity.content — static / fully revealed
  const displayContent = showIndicator
    ? ''
    : isRevealing
      ? entity.content.slice(0, revealedChars)
      : isStreaming
        ? streamingPreviewText
        : isStartingReveal
          ? lastStreamingPreviewRef.current
          : entity.content

  const bubble: BubbleMessage = {
    id: entity.id,
    content: displayContent,
    sender_name: entity.senderName,
    timestamp: entity.timestamp,
    agent_id: entity.agentId,
    agentSource: entity.agentSource,
  }

  return (
    <>
      <AgentMessageBubbleInner
        message={bubble}
        compact={compact}
        defaultExpanded={defaultExpanded}
        collapseSignal={collapseSignal}
        autoCollapseVersion={autoCollapseVersion}
        isLatestAgent={isLatestAgent}
        isUserExpanded={isUserExpanded}
        onUserToggle={onUserToggle}
        onQuote={onQuote}
        isStreaming={isStreaming || isRevealing}
        showIndicator={showIndicator}
        waitingInfo={waitingInfo}
      />
      {entity.artifacts && entity.artifacts.length > 0 && (() => {
        const messageText = (entity.content || '').trim()
        const nonDuplicate = entity.artifacts.filter((a) => {
          const isTextOnly = a.parts.length > 0 && a.parts.every((p) => p.kind === 'text')
          if (!isTextOnly) return true
          const artifactText = a.parts.map((p) => p.text || '').join('').trim()
          return artifactText !== messageText
        })
        return nonDuplicate.length > 0 ? <ArtifactList artifacts={nonDuplicate} /> : null
      })()}
    </>
  )
}

