'use client'

import React, { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getAgentColorClasses, getAgentInitials } from '@/lib/agent-colors'
import type { MessageData } from './room-messages'

interface MessageBubbleProps {
  message: MessageData
  compact?: boolean
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
    
    // Add the mention as a styled span
    parts.push(
      <span
        key={`mention-${mentionIndex++}`}
        className="bg-blue-100 text-blue-800 px-1 rounded font-medium dark:bg-blue-900 dark:text-blue-200"
        title={`Agent ID: ${agentId}`}
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
  // Process mentions before markdown
  const processedContent = content.replace(
    /<@([^|]+)\|([^>]+)>/g,
    '<span class="mention" data-agent-id="$1" title="Agent ID: $1">@$2</span>'
  )

  return (
    <div className="prose prose-sm max-w-none dark:prose-invert leading-relaxed">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          span: ({ className, children, ...props }) => {
            if (className === 'mention') {
              return (
                <span
                  className="bg-blue-100 text-blue-800 px-1 rounded font-medium dark:bg-blue-900 dark:text-blue-200"
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
              <code className="bg-muted px-1 py-0.5 rounded text-sm" {...props}>
                {children}
              </code>
            ) : (
              <pre className="bg-muted p-3 rounded-md overflow-x-auto">
                <code className={className} {...props}>
                  {children}
                </code>
              </pre>
            )
          },
          p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
          ul: ({ children }) => <ul className="mb-2 ml-4">{children}</ul>,
          ol: ({ children }) => <ol className="mb-2 ml-4">{children}</ol>,
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
      <div className="max-w-[80%] rounded-lg p-3 shadow-sm bg-primary text-primary-foreground">
        <div className="text-xs opacity-70 mb-1">
          {message.sender_name} • {new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
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
export function AgentMessageBubble({ message, compact = false }: MessageBubbleProps) {
  const [isExpanded, setIsExpanded] = useState(!compact && message.content.length < 500)
  const displayContent = message.content || "No message content"
  const isLongMessage = displayContent.length > 500
  const colors = getAgentColorClasses(message.agent_id || 'unknown')
  const nameColor = colors.border.replace(/border-/g, 'text-')

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
          "flex-1 max-w-[calc(100%-3rem)] rounded-lg p-3 shadow-sm border",
          colors.border
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-1">
          <span className={cn("text-xs font-medium", nameColor)}>
            {message.sender_name}
          </span>
          <span className="text-xs text-muted-foreground">
            {new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </span>
        </div>
        
        {/* Content - Collapsible for long messages */}
        <div className={cn(
          "text-sm leading-relaxed",
          !isExpanded && isLongMessage && "line-clamp-4"
        )}>
          <MarkdownContent content={displayContent} />
        </div>
        
        {/* Expand/Collapse button */}
        {isLongMessage && (
          <button 
            onClick={() => setIsExpanded(!isExpanded)}
            className={cn(
              "flex items-center gap-1 text-xs mt-2 hover:underline",
              colors.border
            )}
          >
            {isExpanded ? (
              <>
                <ChevronUp className="h-3 w-3" />
                Show less
              </>
            ) : (
              <>
                <ChevronDown className="h-3 w-3" />
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
export function MessageBubble({ message, compact = false }: MessageBubbleProps) {
  if (message.type === 'user') {
    return <UserMessageBubble message={message} />
  }
  return <AgentMessageBubble message={message} compact={compact} />
}

