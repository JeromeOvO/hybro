'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getAgentColorClasses, getAgentInitials } from '@/lib/agent-colors'
import { formatTimestamp } from '@/lib/time'
import { MarkdownContent, LinkifiedContent } from './markdown-content'
import type { MessageEntity } from '@/stores/message-store'

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
}

/**
 * User message bubble - internal implementation using BubbleMessage shape.
 */
function UserMessageBubbleInner({ message }: { message: BubbleMessage }) {
  const displayContent = message.content || "No message content"

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
        <div className="text-sm leading-relaxed whitespace-pre-wrap">
          <LinkifiedContent content={displayContent} />
        </div>
      </div>
    </div>
  )
}

/**
 * Agent message bubble - internal implementation using BubbleMessage shape.
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
}: EntityBubbleProps) {
  const [isExpanded, setIsExpanded] = useState(
    defaultExpanded || isUserExpanded || (!compact && message.content.length < 500)
  )
  const prevCollapseSignal = useRef(collapseSignal)
  const prevAutoCollapseVersion = useRef(autoCollapseVersion)
  const toggleButtonRef = useRef<HTMLButtonElement>(null)

  // --- Quote selection state ---
  const contentRef = useRef<HTMLDivElement>(null)
  const quoteBtnRef = useRef<HTMLButtonElement | null>(null)
  const selectedTextRef = useRef<string>('')

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
  const displayContent = message.content || "No message content"
  const isLongMessage = displayContent.length > 500
  const colors = getAgentColorClasses(message.agent_id || 'unknown')
  const textColorClass = colors.text
  const contentColorClass = colors.content

  return (
    <div className="flex w-full">
      {/* Message Content */}
      <div
        className={cn(
          "flex-1 min-w-0 overflow-hidden rounded-xl p-4 shadow-sm border message-bubble",
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
        </div>

        {/* Content - Collapsible for long messages */}
        <div
          ref={contentRef}
          className={cn(
            "text-sm leading-relaxed select-text",
            contentColorClass,
            !isExpanded && isLongMessage && "line-clamp-4"
          )}
          onMouseUp={handleMouseUp}
        >
          <MarkdownContent content={displayContent} />
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
  return <UserMessageBubbleInner message={bubble} />
}

/**
 * Agent bubble that renders a MessageEntity.
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
  const bubble = entityToBubble(entity)
  return (
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
    />
  )
}

