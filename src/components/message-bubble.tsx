'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import { ChevronDown, ChevronUp, Quote } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getAgentColorClasses, getAgentInitials } from '@/lib/agent-colors'
import { formatTimestamp } from '@/lib/time'
import { MarkdownContent, LinkifiedContent } from './markdown-content'
import type { MessageData } from './room-messages'
import { MESSAGE_TYPE } from '@/lib/types'

/** Lightweight UI type for passing quote data between components. */
export interface QuoteData {
  messageId: string
  content: string
  senderName: string
}

interface MessageBubbleProps {
  message: MessageData
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
 * User message bubble - aligned to the right
 */
export function UserMessageBubble({ message }: MessageBubbleProps) {
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
        <div className="text-sm leading-relaxed">
          <LinkifiedContent content={displayContent} />
        </div>
      </div>
    </div>
  )
}

/**
 * Agent message bubble - with optional expand/collapse for long messages
 */
export function AgentMessageBubble({
  message,
  compact = false,
  defaultExpanded = false,
  collapseSignal = 0,
  autoCollapseVersion = 0,
  isLatestAgent = false,
  isUserExpanded = false,
  onUserToggle,
  onQuote,
}: MessageBubbleProps) {
  const [isExpanded, setIsExpanded] = useState(
    defaultExpanded || isUserExpanded || (!compact && message.content.length < 500)
  )
  const prevCollapseSignal = useRef(collapseSignal)
  const prevAutoCollapseVersion = useRef(autoCollapseVersion)
  const toggleButtonRef = useRef<HTMLButtonElement>(null)

  // --- Quote selection state ---
  const contentRef = useRef<HTMLDivElement>(null)
  const [quoteBtn, setQuoteBtn] = useState<{ top: number; left: number; text: string } | null>(null)

  const handleMouseUp = useCallback((e: React.MouseEvent) => {
    if (!onQuote) return
    // Don't dismiss when clicking the Quote button itself (let onClick fire first)
    if ((e.target as HTMLElement).closest('[data-quote-btn]')) return

    const selection = window.getSelection()
    if (!selection || selection.isCollapsed || !contentRef.current) {
      setQuoteBtn(null)
      return
    }
    const text = selection.toString().trim()
    if (!text) { setQuoteBtn(null); return }

    // Make sure the selection is inside this bubble
    const range = selection.getRangeAt(0)
    if (!contentRef.current.contains(range.commonAncestorContainer)) {
      setQuoteBtn(null)
      return
    }

    const rect = range.getBoundingClientRect()
    const containerRect = contentRef.current.getBoundingClientRect()
    setQuoteBtn({
      top: rect.top - containerRect.top - 32,
      left: rect.left - containerRect.left + rect.width / 2,
      text,
    })
  }, [onQuote])

  // Dismiss quote button when clicking elsewhere
  useEffect(() => {
    const handleDown = (e: MouseEvent) => {
      if (!quoteBtn) return
      const target = e.target as HTMLElement
      if (target.closest('[data-quote-btn]')) return
      setQuoteBtn(null)
    }
    document.addEventListener('mousedown', handleDown)
    return () => document.removeEventListener('mousedown', handleDown)
  }, [quoteBtn])

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
            "relative text-sm leading-relaxed",
            contentColorClass,
            !isExpanded && isLongMessage && "line-clamp-4"
          )}
          onMouseUp={handleMouseUp}
        >
          <MarkdownContent content={displayContent} />

          {/* Floating Quote button */}
          {quoteBtn && (
            <button
              data-quote-btn
              type="button"
              onClick={() => {
                onQuote?.({
                  messageId: message.id,
                  content: quoteBtn.text,
                  senderName: message.sender_name,
                })
                setQuoteBtn(null)
                window.getSelection()?.removeAllRanges()
              }}
              className="absolute z-20 flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-md shadow-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors whitespace-nowrap"
              style={{
                top: quoteBtn.top,
                left: quoteBtn.left,
                transform: 'translateX(-50%)',
              }}
            >
              <Quote className="h-3 w-3" />
              Quote
            </button>
          )}
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

/**
 * Generic message bubble that delegates to the appropriate type
 */
export function MessageBubble({
  message,
  compact = false,
  defaultExpanded = false,
  collapseSignal = 0,
  autoCollapseVersion = 0,
  isLatestAgent = false,
  isUserExpanded = false,
  onUserToggle,
  onQuote,
}: MessageBubbleProps) {
  if (message.type === MESSAGE_TYPE.USER) {
    return <UserMessageBubble message={message} />
  }
  return (
    <AgentMessageBubble
      message={message}
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

