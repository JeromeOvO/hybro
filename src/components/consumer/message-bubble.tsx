'use client'

import React, { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getAgentColorClasses, getAgentInitials } from '@/lib/agent-colors'
import { formatTimestamp } from '@/lib/time'
import type { MessageData } from './room-messages'

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
 * Parse and render @mentions in content
 */
function renderWithMentions(content: string): (string | React.JSX.Element)[] {
  const parts: (string | React.JSX.Element)[] = []
  let lastIndex = 0

  // Regex to match <@agent_id|agent_name> format
  const mentionRegex = /<@([^|]+)\|([^>]+)>/g
  let match
  let mentionIndex = 0

  while ((match = mentionRegex.exec(content)) !== null) {
    // Add text before the mention
    if (match.index > lastIndex) {
      parts.push(content.slice(lastIndex, match.index))
    }

    // Extract agent_id and agent_name
    const agentId = match[1]
    const agentName = match[2]

    // Add the mention as a styled span using room-mention class
    parts.push(
      <span
        key={`mention-${mentionIndex++}`}
        className="room-mention mx-1"
        data-id={agentId}
        data-name={agentName}
        title={`Agent: ${agentName}`}
      >
        @{agentName}
      </span>
    )

    lastIndex = match.index + match[0].length
  }

  // Add remaining text
  if (lastIndex < content.length) {
    parts.push(content.slice(lastIndex))
  }

  return parts.length > 0 ? parts : [content]
}

/**
 * Render content with full markdown support
 */
function MarkdownContent({ content }: { content: string }) {
  // Process mentions before markdown - use room-mention class with spacing
  const processedContent = content.replace(
    /<@([^|]+)\|([^>]+)>/g,
    '<span class="room-mention" data-id="$1" data-name="$2" title="Agent: $2">@$2</span>'
  )

  return (
    <div className="prose prose-sm max-w-none leading-relaxed prose-p:text-inherit prose-headings:text-inherit prose-li:text-inherit prose-strong:text-inherit prose-em:text-inherit [&_.room-mention]:mx-1">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          span: ({ className, children, ...props }) => {
            if (className === 'room-mention') {
              return (
                <span
                  className="room-mention mx-1"
                  {...props}
                >
                  {children}
                </span>
              )
            }
            return <span className={className} {...props}>{children}</span>
          },
          code: ({ className, children, ...props }) => {
            const match = /language-(\w+)/.exec(className || '')
            const isInline = !match
            return isInline ? (
              <code className="bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-200 px-1.5 py-0.5 rounded text-sm font-mono" {...props}>
                {children}
              </code>
            ) : (
              <pre className="bg-slate-100 dark:bg-slate-900 text-slate-700 dark:text-slate-200 p-3 rounded-md overflow-x-auto border border-slate-200 dark:border-slate-700">
                <code className={className} {...props}>
                  {children}
                </code>
              </pre>
            )
          },
          p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
          ul: ({ children }) => <ul className="mb-2 ml-4 list-disc">{children}</ul>,
          ol: ({ children }) => <ol className="mb-2 ml-4 list-decimal">{children}</ol>,
          li: ({ children }) => <li className="mb-1">{children}</li>,
          h1: ({ children }) => <h1 className="text-lg font-bold mb-2">{children}</h1>,
          h2: ({ children }) => <h2 className="text-base font-bold mb-2">{children}</h2>,
          h3: ({ children }) => <h3 className="text-sm font-bold mb-1">{children}</h3>,
          h4: ({ children }) => <h4 className="text-sm font-semibold mb-1">{children}</h4>,
          h5: ({ children }) => <h5 className="text-xs font-semibold mb-1">{children}</h5>,
          h6: ({ children }) => <h6 className="text-xs font-medium mb-1">{children}</h6>,
        }}
      >
        {processedContent}
      </ReactMarkdown>
    </div>
  )
}

/**
 * Agent Avatar component
 */
function AgentAvatar({ agentName, agentId }: { agentName: string; agentId: string }) {
  const colors = getAgentColorClasses(agentId)
  const initials = getAgentInitials(agentName)

  return (
    <div
      className={cn(
        "w-8 h-8 rounded-full flex items-center justify-center font-semibold border-2 shrink-0",
        colors.bg,
        colors.border,
        colors.text
      )}
      title={agentName}
    >
      <span className="text-xs">{initials}</span>
    </div>
  )
}

/**
 * User message bubble - aligned to the right
 */
export function UserMessageBubble({ message }: MessageBubbleProps) {
  const displayContent = message.content || "No message content"

  return (
    <div className="flex justify-end w-full">
      <div className="max-w-[80%] rounded-xl p-4 shadow-sm bg-primary text-primary-foreground message-bubble">
        <div className="flex items-center justify-between gap-4 mb-2">
          <span className="text-xs font-medium opacity-90">
            {message.sender_name}
          </span>
          <span className="text-xs opacity-70">
            {formatTimestamp(message.timestamp)}
          </span>
        </div>
        <div className="text-sm leading-relaxed">
          {renderWithMentions(displayContent)}
        </div>
      </div>
    </div>
  )
}

/**
 * Agent message bubble - with avatar and optional expand/collapse for long messages
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
    <div className="flex gap-3 w-full">
      {/* Agent Avatar */}
      <AgentAvatar
        agentName={message.sender_name}
        agentId={message.agent_id || 'unknown'}
      />

      {/* Message Content */}
      <div
        className={cn(
          "flex-1 max-w-[calc(100%-3rem)] rounded-xl p-4 shadow-sm border message-bubble",
          colors.border,
          colors.bg
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <span className={cn("text-xs font-semibold", textColorClass)}>
            {message.sender_name}
          </span>
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
  if (message.type === 'user') {
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

