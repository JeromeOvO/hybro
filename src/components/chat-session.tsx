"use client"

import * as React from "react"
import { Message, type MessageData } from "@/components/message"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"
import type { BaseTask } from "@/lib/types"

interface ChatSessionProps {
  messages: MessageData[]
  isLoading?: boolean
  onClearChat?: () => void
  onRegenerateMessage?: (messageId: string) => void
  onRetryMessage?: (messageId: string) => void
  onLikeMessage?: (messageId: string) => void
  onDislikeMessage?: (messageId: string) => void
  onCopyMessage?: (content: string) => void
  onWorkflowComplete?: (baseTask: BaseTask) => void
  className?: string
  showHeader?: boolean
  title?: string
}

export function ChatSession({
  messages,
  isLoading = false,
  onRegenerateMessage,
  onRetryMessage,
  onLikeMessage,
  onDislikeMessage,
  onCopyMessage,
  onWorkflowComplete,
  className,
}: ChatSessionProps) {
  const scrollAreaRef = React.useRef<HTMLDivElement>(null)
  const messagesEndRef = React.useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom when new messages arrive
  React.useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleCopy = (content: string) => {
    if (onCopyMessage) {
      onCopyMessage(content)
    } else {
      navigator.clipboard.writeText(content)
    }
  }

  return (
    <div className={cn(
      "flex flex-col h-full bg-transparent",
      className
    )}>
      {/* Messages Area */}
      <div className="flex-1 min-h-0 relative">
        <ScrollArea 
          ref={scrollAreaRef}
          className="h-full w-full"
        >
          <div className="flex flex-col min-h-full">
            {messages.length === 0 ? (
              <div className="flex-1 flex items-center justify-center p-8 min-h-[400px]">
                <div className="text-center">
                  <p className="text-sm text-muted-foreground">
                    No messages yet. Start a conversation!
                  </p>
                </div>
              </div>
            ) : (
              <>
                {messages.map((message) => (
                  <Message
                    key={message.id}
                    message={message}
                    onCopy={handleCopy}
                    onLike={onLikeMessage}
                    onDislike={onDislikeMessage}
                    onRegenerate={onRegenerateMessage}
                    onRetry={onRetryMessage}
                    onWorkflowComplete={onWorkflowComplete}
                  />
                ))}
                {isLoading && (
                  <Message
                    message={{
                      id: 'loading',
                      content: '',
                      role: 'agent',
                      timestamp: new Date(),
                      isLoading: true
                    }}
                  />
                )}
                <div ref={messagesEndRef} className="h-4" />
              </>
            )}
          </div>
        </ScrollArea>
      </div>
    </div>
  )
}