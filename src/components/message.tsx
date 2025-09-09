"use client"

import * as React from "react"
import { Bot, User, Copy, ThumbsUp, ThumbsDown, RotateCcw } from "lucide-react"
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { MessageData, BaseTask } from "@/lib/types"
import { WorkflowContainer } from "./workflow-container"

export type { MessageData }

interface MessageProps {
  message: MessageData
  onCopy?: (content: string) => void
  onLike?: (messageId: string) => void
  onDislike?: (messageId: string) => void
  onRegenerate?: (messageId: string) => void
  onRetry?: (messageId: string) => void
  onWorkflowComplete?: (baseTask: BaseTask) => void // Add new callback
  className?: string
}

export function Message({
  message,
  onCopy,
  onLike,
  onDislike,
  onRegenerate,
  onRetry,
  onWorkflowComplete,
  className
}: MessageProps) {
  const isUser = message.role === 'user'
  const isAgent = message.role === 'agent'

  const handleCopy = async () => {
    if (onCopy) {
      onCopy(message.content)
    } else {
      await navigator.clipboard.writeText(message.content)
    }
  }

  const getSenderInfo = () => {
    if (message.sender) {
      return message.sender
    }
    
    if (isUser) {
      return { name: 'You', avatar: undefined }
    } else if (isAgent) {
      return { name: 'HYBRO AI', avatar: undefined }
    } else {
      return { name: 'HYBRO AI', avatar: undefined }
    }
  }

  const senderInfo = getSenderInfo()

  // Render workflow message content
  const renderWorkflowContent = () => {
    if (!message.workflowData) {
      console.error('Workflow message missing workflowData:', message)
      return <div className="text-red-500">Error: Workflow data not available</div>
    }
    
    return (
      <div className="w-full">
        <WorkflowContainer
          baseTaskId={message.workflowData.baseTask.task_id}
          metaTasks={message.workflowData.metaTasks}
          onWorkflowComplete={onWorkflowComplete}
        />
      </div>
    )
  }

  // Render text message content
  const renderTextContent = () => {
    if (message.isThinking) {
      return (
        <div className="flex items-center gap-2">
          <div className="flex space-x-1">
            <div className="w-2 h-2 bg-current rounded-full animate-bounce"></div>
            <div className="w-2 h-2 bg-current rounded-full animate-bounce" style={{ animationDelay: '0.1s' }}></div>
            <div className="w-2 h-2 bg-current rounded-full animate-bounce" style={{ animationDelay: '0.2s' }}></div>
          </div>
          <span className="text-xs opacity-70">Thinking...</span>
        </div>
      )
    }

    if (message.isLoading) {
      return (
        <div className="flex items-center gap-2">
          <div className="flex space-x-1">
            <div className="w-2 h-2 bg-current rounded-full animate-bounce"></div>
            <div className="w-2 h-2 bg-current rounded-full animate-bounce" style={{ animationDelay: '0.1s' }}></div>
            <div className="w-2 h-2 bg-current rounded-full animate-bounce" style={{ animationDelay: '0.2s' }}></div>
          </div>
          <span className="text-xs opacity-70">Typing...</span>
        </div>
      )
    }

    if (message.error) {
      return (
        <div>
          <p className="font-medium">Error</p>
          <p className="text-xs mt-1 opacity-80">{message.error}</p>
        </div>
      )
    }

    // Render markdown for assistant messages, plain text for user messages
    if (isUser) {
      return (
        <div className="whitespace-pre-wrap break-words text-[15px] md:text-base leading-relaxed">
          {message.content}
        </div>
      )
    } else {
      return (
        <div className="prose prose-sm md:prose-base max-w-none dark:prose-invert leading-relaxed">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeHighlight]}
            components={{
              // Customize code blocks
              code: ({ className, children, ...props }) => {
                const match = /language-(\w+)/.exec(className || '')
                const isInline = !match
                return isInline ? (
                  <code className="bg-muted px-1 py-0.5 rounded text-sm" {...props}>
                    {children}
                  </code>
                ) : (
                  <pre className="bg-muted p-4 rounded-md overflow-x-auto">
                    <code className={className} {...props}>
                      {children}
                    </code>
                  </pre>
                )
              },
              // Customize paragraphs to reduce spacing
              p: ({ children }) => <p className="mb-3 last:mb-0">{children}</p>,
              // Customize lists
              ul: ({ children }) => <ul className="mb-3 ml-5">{children}</ul>,
              ol: ({ children }) => <ol className="mb-3 ml-5">{children}</ol>,
            }}
          >
            {message.content}
          </ReactMarkdown>
        </div>
      )
    }
  }

  return (
    <div className={cn(
      "group flex gap-5 p-5 md:p-6 hover:bg-muted/30 transition-colors",
      isUser && "flex-row-reverse",
      className
    )}>
      {/* Avatar */}
      <div className="flex-shrink-0">
        <Avatar className="h-8 w-8">
          <AvatarImage src={senderInfo.avatar} alt={senderInfo.name} />
          <AvatarFallback className={cn(
            "text-xs font-medium",
            isUser && "bg-primary text-primary-foreground",
            isAgent && "bg-blue-500 text-white"
          )}>
            {isUser ? <User className="h-4 w-4 icon-info" /> : 
             isAgent ? <Bot className="h-4 w-4 icon-action" /> :
             <Bot className="h-4 w-4 icon-neutral" />}
          </AvatarFallback>
        </Avatar>
      </div>

      {/* Message Content */}
      <div className={cn(
        "flex-1 min-w-0",
        isUser && "flex flex-col items-end"
      )}>
        {/* Sender Name & Timestamp */}
        <div className={cn(
          "flex items-center gap-2 mb-1",
          isUser && "flex-row-reverse"
        )}>
          <span className="text-sm font-medium text-foreground">
            {senderInfo.name}
          </span>
          <span className="text-xs text-muted-foreground">
            {message.timestamp.toLocaleTimeString([], { 
              hour: '2-digit', 
              minute: '2-digit' 
            })}
          </span>
        </div>

        {/* Message Content - Render different content based on message type */}
        {message.messageType === 'workflow' ? (
          // Workflow message - No bubble style needed, render component directly
          <div className="w-full">
            {renderWorkflowContent()}
          </div>
        ) : (
          // Text message - Use bubble style with enhanced styling
          <div className={cn(
            "message-bubble relative max-w-[72ch] rounded-2xl px-5 py-4 text-[15px] md:text-base leading-relaxed",
            isUser 
              ? "bg-primary text-primary-foreground" 
              : "bg-card/80 text-card-foreground shadow-sm",
            message.error && "bg-destructive/10 text-destructive"
          )}>
            {renderTextContent()}
          </div>
        )}

        {/* Action Buttons - Only show for text messages */}
        {message.messageType !== 'workflow' && !isUser && !message.isLoading && !message.isThinking && !message.error && (
          <div className="flex items-center gap-1 mt-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={handleCopy}
              className="h-7 px-2 text-xs hover:bg-muted/50"
            >
              <Copy className="h-3 w-3 mr-1 icon-neutral" />
              Copy
            </Button>
            
            {/* Agent message specific Retry button */}
            {isAgent && onRetry && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onRetry(message.id)}
                className="h-7 px-2 text-xs hover:bg-muted/50"
              >
                <RotateCcw className="h-3 w-3 mr-1 icon-action" />
                Retry
              </Button>
            )}
            
            {onLike && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onLike(message.id)}
                className="h-7 px-2 text-xs hover:bg-muted/50"
              >
                <ThumbsUp className="h-3 w-3 icon-success" />
              </Button>
            )}
            
            {onDislike && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onDislike(message.id)}
                className="h-7 px-2 text-xs hover:bg-muted/50"
              >
                <ThumbsDown className="h-3 w-3 icon-error" />
              </Button>
            )}

            {onRegenerate && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onRegenerate(message.id)}
                className="h-7 px-2 text-xs hover:bg-muted/50"
              >
                Regenerate
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  )
} 