'use client'

import { useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getAgentColorClasses, getAgentInitials } from '@/lib/agent-colors'
import { formatTimestamp } from '@/lib/time'
import { MarkdownContent, LinkifiedContent } from './markdown-content'
import type { MessageData } from './room-messages'
import { MESSAGE_TYPE } from '@/lib/types'

interface MessageBubbleProps {
  message: MessageData
  compact?: boolean
  defaultExpanded?: boolean
  collapseSignal?: number
  autoCollapseVersion?: number
  isLatestAgent?: boolean
  isUserExpanded?: boolean
  onUserToggle?: (id: string, expanded: boolean) => void
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
}: MessageBubbleProps) {
  const [isExpanded, setIsExpanded] = useState(
    defaultExpanded || isUserExpanded || (!compact && message.content.length < 500)
  )
  const prevCollapseSignal = useRef(collapseSignal)
  const prevAutoCollapseVersion = useRef(autoCollapseVersion)
  const toggleButtonRef = useRef<HTMLButtonElement>(null)

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
        <div className={cn(
          "text-sm leading-relaxed",
          contentColorClass,
          !isExpanded && isLongMessage && "line-clamp-4"
        )}>
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
    />
  )
}

