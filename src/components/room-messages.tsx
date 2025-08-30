'use client'

import React, { useRef, useEffect } from 'react'
import { ScrollArea } from '@/components/ui/scroll-area'
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

// Message Component with enhanced @mention parsing
function MessageBubble({ message }: { message: MessageData }) {
  const renderContent = (content: string) => {
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
        <div className="text-sm leading-relaxed">
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
      <div className="flex items-center justify-center h-full">
        <div className="text-muted-foreground">Loading messages...</div>
      </div>
    )
  }

  return (
    <ScrollArea className="flex-1 p-4">
      <div className="space-y-4">
        {messages.length === 0 ? (
          <div className="flex items-center justify-center h-64">
            <div className="text-center text-muted-foreground">
              <p className="text-lg font-medium">No messages yet</p>
              <p className="text-sm">Start the conversation by sending a message</p>
            </div>
          </div>
        ) : (
          messages.map((message) => (
            <MessageBubble key={message.id} message={message} />
          ))
        )}
        <div ref={messagesEndRef} />
      </div>
    </ScrollArea>
  )
}
