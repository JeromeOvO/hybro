'use client'

import React, { useRef, useEffect, useState, useCallback, useMemo } from 'react'
import { 
  MessageSquareText,
  ChevronsDownUp, 
  ChevronsUpDown,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { MessageBubble } from './message-bubble'
import { TaskStatusMessage } from './task-status-message'
import type { TaskState } from '@/lib/types/sse'

export interface MessageData {
  id: string
  type: 'user' | 'agent' | 'task'
  content: string
  sender_name: string
  timestamp: string
  user_id?: string
  agent_id?: string
  // Task-specific fields (for type: 'task')
  task_internal_id?: string
  task_status?: string
  task_error?: string | null
  task_status_message?: string | null
  task_requires_input?: boolean
  task_requires_auth?: boolean
  task_content?: string // The task description being processed
  // Workflow step tracking from backend
  step_number?: number
  total_steps?: number
}

// Empty state component
function EmptyState() {
  return (
    <div className="h-full flex items-center justify-center">
      <div className="text-center space-y-4 max-w-sm px-4">
        <div className="w-16 h-16 rounded-full bg-gradient-to-br from-primary/20 to-accent/20 dark:from-primary/10 dark:to-accent/10 flex items-center justify-center mx-auto">
          <MessageSquareText className="h-8 w-8 text-primary/60" />
        </div>
        <div className="space-y-2">
          <p className="text-lg font-medium text-foreground">Start the conversation</p>
          <p className="text-sm text-muted-foreground">
            Send a message and our AI agents will collaborate to help you.
          </p>
        </div>
      </div>
    </div>
  )
}

// Loading state component
function LoadingState() {
  return (
    <div className="h-full flex items-center justify-center">
      <div className="text-center space-y-4">
        <div className="flex justify-center gap-1.5">
          <div className="w-3 h-3 bg-primary/60 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
          <div className="w-3 h-3 bg-primary/60 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
          <div className="w-3 h-3 bg-primary/60 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
        </div>
        <p className="text-sm text-muted-foreground">Loading messages...</p>
      </div>
    </div>
  )
}

function shouldRenderTaskAsAgent(message: MessageData): boolean {
  return message.type === 'task' && message.task_status === 'completed' && !!message.content
}

function toAgentMessage(message: MessageData): MessageData {
  return message.type === 'agent' ? message : { ...message, type: 'agent' }
}

interface RoomMessagesProps {
  messages: MessageData[]
  loading?: boolean
}

export function RoomMessages({ messages, loading }: RoomMessagesProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true)
  const previousMessageCountRef = useRef(messages.length)
  const [collapseSignal, setCollapseSignal] = useState(0)
  const [autoCollapseVersion, setAutoCollapseVersion] = useState(0)
  const [userExpandedIds, setUserExpandedIds] = useState<Set<string>>(new Set())
  const prevLatestAgentIdRef = useRef<string | null>(null)

  const allAgentIds = useMemo(
    () => messages.filter(m => m.type === 'agent' || shouldRenderTaskAsAgent(m)).map(m => m.id),
    [messages]
  )

  const allExpanded = useMemo(
    () => allAgentIds.length > 0 && allAgentIds.every(id => userExpandedIds.has(id)),
    [allAgentIds, userExpandedIds]
  )

  const lastAgentMessageId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].type === 'agent' || shouldRenderTaskAsAgent(messages[i])) {
        return messages[i].id
      }
    }
    return null
  }, [messages])

  // Track newest agent message to auto-collapse prior non-user-expanded responses
  useEffect(() => {
    if (lastAgentMessageId && lastAgentMessageId !== prevLatestAgentIdRef.current) {
      setAutoCollapseVersion((v) => v + 1)
    }
    prevLatestAgentIdRef.current = lastAgentMessageId
  }, [lastAgentMessageId])

  const handleUserToggle = useCallback((id: string, expanded: boolean) => {
    setUserExpandedIds((prev) => {
      const next = new Set(prev)
      if (expanded) next.add(id)
      else next.delete(id)
      return next
    })
  }, [])

  // Bulk collapse/expand for agent message bubbles
  const collapseAll = useCallback(() => {
    setCollapseSignal((v) => v + 1)
    setUserExpandedIds(new Set())
  }, [])

  const expandAll = useCallback(() => {
    setUserExpandedIds(new Set(allAgentIds))
  }, [allAgentIds])

  // Track if user is near bottom of scroll
  const checkIfNearBottom = useCallback(() => {
    const container = scrollContainerRef.current
    if (!container) return false
    
    const threshold = 100
    const isNearBottom = 
      container.scrollHeight - container.scrollTop - container.clientHeight < threshold
    return isNearBottom
  }, [])

  // Handle scroll to detect if user manually scrolls
  const handleScroll = useCallback((event: React.UIEvent<HTMLDivElement>) => {
    // Ignore scroll events we triggered ourselves (e.g., anchoring show less)
    if (event.currentTarget.dataset.programmaticScroll === 'true') {
      event.currentTarget.dataset.programmaticScroll = 'false'
      return
    }

    const isNearBottom = checkIfNearBottom()
    setShouldAutoScroll(isNearBottom)
  }, [checkIfNearBottom])

  // Auto scroll when new messages arrive
  useEffect(() => {
    const messageCountIncreased = messages.length > previousMessageCountRef.current
    const lastMessageIsUser = messages.length > 0 && messages[messages.length - 1].type === 'user'
    
    if (messageCountIncreased && (lastMessageIsUser || shouldAutoScroll)) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'auto' })
    }
    
    previousMessageCountRef.current = messages.length
  }, [messages, shouldAutoScroll])

  if (loading) {
    return <LoadingState />
  }

  return (
    <div className="h-full flex relative">
      {/* Main Content */}
      <div 
        ref={scrollContainerRef}
        data-message-scroll-container="true"
        onScroll={handleScroll}
        className="flex-1 h-full w-full overflow-y-auto"
      >
        <div className="py-4 min-h-full px-4 sm:px-6 max-w-4xl mx-auto">
          {messages.length === 0 ? (
            <EmptyState />
          ) : (
            <>
              {/* Controls - Sticky header */}
              {allAgentIds.length > 0 && (
                <div className="sticky top-0 bg-background/95 backdrop-blur-md py-3 z-10 mb-4 flex items-center justify-end border-b border-border/50">
                  <div className="flex items-center gap-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={allExpanded ? collapseAll : expandAll}
                      className="h-9 w-9 p-0 hover:bg-muted"
                      title={allExpanded ? 'Collapse all messages' : 'Expand all messages'}
                      aria-label={allExpanded ? 'Collapse all messages' : 'Expand all messages'}
                    >
                      {allExpanded ? (
                        <ChevronsDownUp className="h-4 w-4" />
                      ) : (
                        <ChevronsUpDown className="h-4 w-4" />
                      )}
                    </Button>
                  </div>
                </div>
              )}

              {/* Messages Display - Timeline view */}
              <div className="space-y-4">
                {messages.map(msg => {
                  // Render task messages with TaskStatusMessage component
                  if (msg.type === 'task' && !shouldRenderTaskAsAgent(msg)) {
                    return (
                      <TaskStatusMessage
                        key={msg.id}
                        internalId={msg.task_internal_id || msg.id}
                        agentName={msg.sender_name}
                        initialStatus={(msg.task_status || 'working') as TaskState}
                        content={msg.content || null}
                        error={msg.task_error}
                        statusMessage={msg.task_status_message}
                        stepNumber={msg.step_number}
                        totalSteps={msg.total_steps}
                        taskContent={msg.task_content}
                      />
                    )
                  }
                  // Render regular messages
                  return (
                    <MessageBubble
                      key={msg.id}
                      message={msg.type === 'user' ? msg : toAgentMessage(msg)}
                      defaultExpanded={msg.id === lastAgentMessageId}
                      collapseSignal={collapseSignal}
                      autoCollapseVersion={autoCollapseVersion}
                      isLatestAgent={msg.id === lastAgentMessageId}
                      isUserExpanded={userExpandedIds.has(msg.id)}
                      onUserToggle={handleUserToggle}
                    />
                  )
                })}
              </div>
            
              <div ref={messagesEndRef} className="h-4" />
            </>
          )}
        </div>
      </div>
    </div>
  )
}
