'use client'

import React, { useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { cn } from '@/lib/utils'

export interface MessageData {
  id: string
  type: 'user' | 'agent'
  content: string
  sender_name: string
  timestamp: string
  user_id?: string
  agent_id?: string
}

// Message Component with enhanced @mention parsing and markdown support
function MessageBubble({ message }: { message: MessageData }) {
  const isUser = message.type === 'user'
  const isAgent = message.type === 'agent'

  const renderContent = (content: string) => {
    const displayContent = content !== "" ? content : "No message content received"
    
    // For user messages, render as plain text with mention parsing only
    if (isUser) {
      return renderWithMentions(displayContent)
    }
    
    // For agent messages, render with full markdown support
    if (isAgent) {
      return renderWithMarkdown(displayContent)
    }
    
    // Fallback to mention parsing
    return renderWithMentions(displayContent)
  }

  const renderWithMentions = (content: string) => {
    // Parse mentions in the format <@agent_id|agent_name>
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
      
      // Add the mention as a styled span, showing only the agent name
      parts.push(
        <span
          key={`mention-${mentionIndex++}`}
          className="bg-blue-100 text-blue-800 px-1 rounded font-medium dark:bg-blue-900 dark:text-blue-200"
          title={`Agent ID: ${agentId}`} // Show ID on hover
        >
          @{agentName}
        </span>
      )
      
      lastIndex = match.index + match[0].length
    }
    
    // Add remaining text after the last mention
    if (lastIndex < content.length) {
      parts.push(content.slice(lastIndex))
    }
    
    // If no mentions found, return the original content
    if (parts.length === 0) {
      return content
    }
    
    return parts
  }

  const renderWithMarkdown = (content: string) => {
    // First process mentions in the content, then render as markdown
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
            // Custom mention component
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
            // Customize code blocks
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
            // Customize paragraphs to reduce spacing
            p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
            // Customize lists
            ul: ({ children }) => <ul className="mb-2 ml-4">{children}</ul>,
            ol: ({ children }) => <ol className="mb-2 ml-4">{children}</ol>,
            // Customize headers to be smaller in room context
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

  return (
    <div
      className={cn(
        "flex w-full mb-4",
        message.type === 'user' ? "justify-end" : "justify-start"
      )}
    >
      <div
        className={cn(
          "max-w-[80%] rounded-lg p-3 shadow-sm",
          message.type === 'user'
            ? "bg-primary text-primary-foreground"
            : "bg-muted"
        )}
      >
        <div className="text-xs opacity-70 mb-1">
          {message.sender_name} • {new Date(message.timestamp).toLocaleTimeString()}
        </div>
        <div className={cn(
          "leading-relaxed",
          isUser ? "text-sm" : "text-sm" // Keep consistent text size for both
        )}>
          {renderContent(message.content)}
        </div>
      </div>
    </div>
  )
}

interface RoomMessagesProps {
  messages: MessageData[]
  loading?: boolean
}

export function RoomMessages({ messages, loading }: RoomMessagesProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Auto scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-muted-foreground">Loading messages...</div>
      </div>
    )
  }

  return (
    <div className="h-full w-full overflow-y-auto">
      <div className="py-4 min-h-full">
        {messages.length === 0 ? (
          <div className="h-full flex items-center justify-center">
            <div className="text-center text-muted-foreground">
              <p className="text-lg font-medium">No messages yet</p>
              <p className="text-sm">Start the conversation by sending a message</p>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            {messages.map((message) => (
              <MessageBubble key={message.id} message={message} />
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>
    </div>
  )
}
