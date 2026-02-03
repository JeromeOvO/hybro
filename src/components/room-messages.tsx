'use client'

import React, { useRef, useEffect, useState, useCallback, useMemo } from 'react'
import { 
  Sparkles,
  MessageSquareText,
  Layers, 
  List, 
  ChevronsDownUp, 
  ChevronsUpDown,
  Map as MapIcon
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ConversationRound, type ConversationRoundData } from './conversation-round'
import { ConversationNavigator } from './conversation-navigator'
import { MessageBubble } from './message-bubble'
import { TaskStatusMessage } from './task-status-message'
import { cn } from '@/lib/utils'
import type { TaskState } from '@/lib/types/sse'

export interface MessageData {
  id: string
  type: 'user' | 'agent' | 'task'
  content: string
  sender_name: string
  timestamp: string
  user_id?: string
  agent_id?: string
  related_message_id?: string | null
  // Task-specific fields (for type: 'task')
  task_internal_id?: string
  task_status?: string
  task_error?: string | null
  task_status_message?: string | null
  task_requires_input?: boolean
  task_requires_auth?: boolean
}

// Processing Status Component - Styled to match agent message bubbles
function ProcessingStatus({ processing }: { processing: boolean }) {
  if (!processing) return null

  return (
    <div className="flex gap-3 w-full animate-in fade-in slide-in-from-bottom-2 duration-300">
      {/* Avatar matching agent bubble style */}
      <div className="w-8 h-8 rounded-full flex items-center justify-center font-semibold border-2 shrink-0 bg-gradient-to-br from-violet-100 to-fuchsia-100 dark:from-violet-900 dark:to-fuchsia-900 border-violet-300 dark:border-violet-700">
        <Sparkles className="h-4 w-4 text-violet-600 dark:text-violet-300 animate-pulse" />
      </div>

      {/* Message content matching agent bubble style */}
      <div className="flex-1 max-w-[calc(100%-3rem)] rounded-lg p-4 shadow-sm border border-violet-200 dark:border-violet-800 bg-gradient-to-br from-violet-50/50 to-fuchsia-50/30 dark:from-violet-950/50 dark:to-fuchsia-950/30 message-bubble">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-medium text-violet-700 dark:text-violet-300">
            AI Agents
          </span>
          <span className="text-xs text-muted-foreground">
            Processing...
          </span>
        </div>

        {/* Animated content */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-sm text-violet-600 dark:text-violet-300 font-medium">
              Analyzing your request
            </span>
          </div>
          
          {/* Animated typing indicator */}
          <div className="flex items-center gap-3">
            <div className="flex gap-1.5">
              <div className="w-2 h-2 bg-violet-400 dark:bg-violet-500 rounded-full animate-bounce" style={{ animationDelay: '0ms', animationDuration: '0.6s' }} />
              <div className="w-2 h-2 bg-fuchsia-400 dark:bg-fuchsia-500 rounded-full animate-bounce" style={{ animationDelay: '150ms', animationDuration: '0.6s' }} />
              <div className="w-2 h-2 bg-violet-400 dark:bg-violet-500 rounded-full animate-bounce" style={{ animationDelay: '300ms', animationDuration: '0.6s' }} />
            </div>
            <span className="text-xs text-muted-foreground">
              Finding the best agents for your task
            </span>
          </div>
        </div>
      </div>
    </div>
  )
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

/**
 * Groups a flat array of messages into conversation rounds.
 * A "round" starts with a user message and includes all subsequent
 * agent/task messages until the next user message.
 */
function groupMessagesIntoRounds(
  messages: MessageData[],
  collapsedRounds: Set<number>
): { rounds: ConversationRoundData[]; orphanedAgentMessages: MessageData[] } {
  const rounds: ConversationRoundData[] = []
  const orphanedAgentMessages: MessageData[] = []
  const userRoundMap = new Map<string, ConversationRoundData>()
  const messageById = new Map<string, MessageData>()
  let roundNumber = 0

  for (const message of messages) {
    messageById.set(message.id, message)
  }

  for (const message of messages) {
    if (message.type !== 'user') continue
    roundNumber++
    const round: ConversationRoundData = {
      id: `round-${roundNumber}-${message.id}`,
      roundNumber,
      userMessage: message,
      agentResponses: [],
      timestamp: message.timestamp,
      isCollapsed: collapsedRounds.has(roundNumber)
    }
    rounds.push(round)
    userRoundMap.set(message.id, round)
  }

  const resolveUserParentId = (message: MessageData): string | null => {
    let parentId = message.related_message_id ?? null
    const visited = new Set<string>()

    while (parentId) {
      if (visited.has(parentId)) {
        return null
      }
      visited.add(parentId)

      const parentMessage = messageById.get(parentId)
      if (!parentMessage) return null
      if (parentMessage.type === 'user') return parentMessage.id

      parentId = parentMessage.related_message_id ?? null
    }

    return null
  }

  let lastRound: ConversationRoundData | null = null
  for (const message of messages) {
    if (message.type === 'user') {
      lastRound = userRoundMap.get(message.id) ?? null
      continue
    }

    if (message.type !== 'agent' && message.type !== 'task') {
      continue
    }

    // Prefer grouping by explicit parent user message if provided
    const parentUserId = resolveUserParentId(message)
    const targetRound = parentUserId
      ? userRoundMap.get(parentUserId)
      : lastRound

    if (targetRound) {
      targetRound.agentResponses.push(message)
    } else {
      // Agent/task message before any user message
      orphanedAgentMessages.push(message)
    }
  }

  return { rounds, orphanedAgentMessages }
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
  processing?: boolean
}

type ViewMode = 'rounds' | 'timeline'

export function RoomMessages({ messages, loading, processing = false }: RoomMessagesProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true)
  const previousMessageCountRef = useRef(messages.length)
  const [collapseSignal, setCollapseSignal] = useState(0)
  const [autoCollapseVersion, setAutoCollapseVersion] = useState(0)
  const [userExpandedIds, setUserExpandedIds] = useState<Set<string>>(new Set())
  const [skipAutoCollapseUntilNewRound, setSkipAutoCollapseUntilNewRound] = useState(false)
  const mapButtonRef = useRef<HTMLButtonElement>(null)
  const navigatorRef = useRef<HTMLDivElement>(null)
  const allAgentIds = useMemo(
    () => messages.filter(m => m.type === 'agent' || shouldRenderTaskAsAgent(m)).map(m => m.id),
    [messages]
  )
  const allTimelineExpanded = useMemo(
    () => allAgentIds.length > 0 && allAgentIds.every(id => userExpandedIds.has(id)),
    [allAgentIds, userExpandedIds]
  )
  const prevLatestAgentIdRef = useRef<string | null>(null)
  const prevLatestUserIdRef = useRef<string | null>(null)
  const lastUserMessageId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].type === 'user') {
        return messages[i].id
      }
    }
    return null
  }, [messages])
  const lastAgentMessageId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].type === 'agent' || shouldRenderTaskAsAgent(messages[i])) {
        return messages[i].id
      }
    }
    return null
  }, [messages])
  
  // View mode state
  const [viewMode, setViewMode] = useState<ViewMode>('rounds')
  
  // Collapsed rounds tracking
  const [collapsedRounds, setCollapsedRounds] = useState<Set<number>>(new Set())
  
  // Navigator visibility
  const [showNavigator, setShowNavigator] = useState(false)
  const [currentRound, setCurrentRound] = useState(0)
  const prevRoundCountRef = useRef(0)

  // Group messages into rounds
  const { rounds, orphanedAgentMessages } = useMemo(() => {
    return groupMessagesIntoRounds(messages, collapsedRounds)
  }, [messages, collapsedRounds])

  // Update collapsed state when rounds change
  useEffect(() => {
    setCollapsedRounds(prev => {
      const updated = new Set<number>()
      prev.forEach(roundNum => {
        if (roundNum <= rounds.length) {
          updated.add(roundNum)
        }
      })
      return updated
    })
  }, [rounds.length])

  // Keep newly added latest round expanded by default (but allow user collapses afterward)
  useEffect(() => {
    if (rounds.length > prevRoundCountRef.current && rounds.length > 0) {
      const latestRoundNumber = rounds[rounds.length - 1].roundNumber
      setCollapsedRounds((prev) => {
        if (!prev.has(latestRoundNumber)) return prev
        const next = new Set(prev)
        next.delete(latestRoundNumber)
        return next
      })
    }
    prevRoundCountRef.current = rounds.length
  }, [rounds.length, rounds])

  // Track newest agent message to auto-collapse prior non-user-expanded responses
  useEffect(() => {
    if (lastAgentMessageId && lastAgentMessageId !== prevLatestAgentIdRef.current) {
      setAutoCollapseVersion((v) => v + 1)
    }
    prevLatestAgentIdRef.current = lastAgentMessageId
  }, [lastAgentMessageId])

  // When a new user message starts a fresh round, re-enable auto-collapse if it was skipped
  useEffect(() => {
    if (
      skipAutoCollapseUntilNewRound &&
      lastUserMessageId &&
      lastUserMessageId !== prevLatestUserIdRef.current
    ) {
      setSkipAutoCollapseUntilNewRound(false)
    }
    prevLatestUserIdRef.current = lastUserMessageId
  }, [lastUserMessageId, skipAutoCollapseUntilNewRound])

  // Close navigator when clicking outside of it or the toggle button
  useEffect(() => {
    if (!showNavigator) return

    const handleOutside = (event: MouseEvent | TouchEvent) => {
      const navEl = navigatorRef.current
      const btnEl = mapButtonRef.current
      const target = event.target as Node

      if (navEl?.contains(target)) return
      if (btnEl?.contains(target)) return

      setShowNavigator(false)
    }

    document.addEventListener('mousedown', handleOutside)
    document.addEventListener('touchstart', handleOutside)

    return () => {
      document.removeEventListener('mousedown', handleOutside)
      document.removeEventListener('touchstart', handleOutside)
    }
  }, [showNavigator])

  const handleUserToggle = useCallback((id: string, expanded: boolean) => {
    setUserExpandedIds((prev) => {
      const next = new Set(prev)
      if (expanded) next.add(id)
      else next.delete(id)
      return next
    })
  }, [])

  // Toggle individual round
  const toggleRound = useCallback((roundNumber: number) => {
    setCollapsedRounds(prev => {
      const next = new Set(prev)
      if (next.has(roundNumber)) {
        next.delete(roundNumber)
      } else {
        next.add(roundNumber)
        setCollapseSignal((v) => v + 1)
      }
      return next
    })
  }, [])

  // Bulk collapse/expand
  const collapseAll = useCallback(() => {
    setCollapsedRounds(new Set(rounds.map(r => r.roundNumber)))
    setCollapseSignal((v) => v + 1)
    setUserExpandedIds(new Set())
  }, [rounds])

  const expandAll = useCallback(() => {
    setCollapsedRounds(new Set())
    setSkipAutoCollapseUntilNewRound(true)
  }, [])

  // Timeline bulk collapse/expand (for agent message bubbles)
  const collapseAllTimeline = useCallback(() => {
    setCollapseSignal((v) => v + 1)
    setUserExpandedIds(new Set())
  }, [])

  const expandAllTimeline = useCallback(() => {
    setUserExpandedIds(new Set(allAgentIds))
  }, [allAgentIds])

  // Auto-collapse old rounds when many rounds exist
  const collapseOldRounds = useCallback((keepLastN: number = 2) => {
    const toCollapse = rounds
      .slice(0, -keepLastN)
      .map(r => r.roundNumber)
    setCollapsedRounds(new Set(toCollapse))
  }, [rounds])

  // Navigate to specific round
  const navigateToRound = useCallback((roundIndex: number) => {
    const round = rounds[roundIndex]
    if (round) {
      setCurrentRound(roundIndex)
      // Expand the target round if collapsed
      if (collapsedRounds.has(round.roundNumber)) {
        setCollapsedRounds(prev => {
          const next = new Set(prev)
          next.delete(round.roundNumber)
          return next
        })
      }
      // Scroll to round
      const element = document.getElementById(`round-${round.roundNumber}`)
      if (element) {
        element.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }
    }
  }, [rounds, collapsedRounds])

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
    
    // Update current round based on scroll position
    if (viewMode === 'rounds' && rounds.length > 0) {
      const container = scrollContainerRef.current
      if (!container) return
      
      const scrollTop = container.scrollTop
      for (let i = rounds.length - 1; i >= 0; i--) {
        const element = document.getElementById(`round-${rounds[i].roundNumber}`)
        if (element && element.offsetTop <= scrollTop + 100) {
          setCurrentRound(i)
          break
        }
      }
    }
  }, [checkIfNearBottom, viewMode, rounds])

  // Auto scroll when new messages arrive
  useEffect(() => {
    const messageCountIncreased = messages.length > previousMessageCountRef.current
    const lastMessageIsUser = messages.length > 0 && messages[messages.length - 1].type === 'user'
    
    if (messageCountIncreased && (lastMessageIsUser || shouldAutoScroll)) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'auto' })
    }
    
    previousMessageCountRef.current = messages.length
  }, [messages, shouldAutoScroll])

  // Auto scroll when processing state changes
  useEffect(() => {
    if (shouldAutoScroll) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'auto' })
    }
  }, [processing, shouldAutoScroll])

  // Auto-collapse old rounds when conversation gets long
  useEffect(() => {
    if (skipAutoCollapseUntilNewRound) {
      return
    }

    if (rounds.length > 5 && collapsedRounds.size === 0) {
      // Suggest collapsing by auto-collapsing rounds older than the last 2
      collapseOldRounds(2)
    }
  }, [rounds.length, collapsedRounds.size, collapseOldRounds, skipAutoCollapseUntilNewRound])

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
          {messages.length === 0 && !processing ? (
            <EmptyState />
          ) : (
            <>
              {/* View Controls - Sticky header */}
              {rounds.length > 0 && (
                <div className="sticky top-0 bg-background/95 backdrop-blur-md py-3 z-10 mb-4 flex items-center justify-between border-b border-border/50">
                  <div className="flex gap-2">
                    <Button 
                      variant="outline"
                      size="sm"
                      onClick={() => setViewMode(prev => prev === 'rounds' ? 'timeline' : 'rounds')}
                      className={cn(
                        "h-9 px-4 font-medium transition-all",
                        "hover:bg-primary/10 hover:text-primary hover:border-primary/50"
                      )}
                      aria-label="Toggle view mode"
                      title={viewMode === 'rounds' ? 'Switch to Timeline view' : 'Switch to Rounds view'}
                    >
                      {viewMode === 'rounds' ? (
                        <>
                          <Layers className="h-4 w-4 mr-2" /> 
                          Rounds
                        </>
                      ) : (
                        <>
                          <List className="h-4 w-4 mr-2" /> 
                          Timeline
                        </>
                      )}
                    </Button>
                  </div>
                  
                  {viewMode === 'rounds' && rounds.length > 1 && (
                    <div className="flex items-center gap-2">
                      <Button 
                        variant="ghost" 
                        size="sm" 
                        onClick={collapsedRounds.size === rounds.length ? expandAll : collapseAll}
                        className="h-9 w-9 p-0 hover:bg-muted"
                        title={collapsedRounds.size === rounds.length ? 'Expand all rounds' : 'Collapse all rounds'}
                        aria-label={collapsedRounds.size === rounds.length ? 'Expand all rounds' : 'Collapse all rounds'}
                      >
                        {collapsedRounds.size === rounds.length ? (
                          <ChevronsUpDown className="h-4 w-4" />
                        ) : (
                          <ChevronsDownUp className="h-4 w-4" /> 
                        )}
                      </Button>
                      
                      {/* Navigator toggle for many rounds */}
                      {rounds.length > 3 && (
                        <Button
                          ref={mapButtonRef}
                          variant={showNavigator ? 'default' : 'outline'}
                          size="sm"
                          onClick={() => setShowNavigator(!showNavigator)}
                          className={cn(
                            "h-9 text-xs font-medium",
                            showNavigator && "bg-primary text-primary-foreground"
                          )}
                        >
                          <MapIcon className="h-3.5 w-3.5 mr-1.5" />
                          Map
                        </Button>
                      )}
                    </div>
                  )}

                  {viewMode === 'timeline' && allAgentIds.length > 0 && (
                    <div className="flex items-center gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={allTimelineExpanded ? collapseAllTimeline : expandAllTimeline}
                        className="h-9 w-9 p-0 hover:bg-muted"
                        title={allTimelineExpanded ? 'Collapse all messages' : 'Expand all messages'}
                        aria-label={allTimelineExpanded ? 'Collapse all timeline messages' : 'Expand all timeline messages'}
                      >
                        {allTimelineExpanded ? (
                          <ChevronsDownUp className="h-4 w-4" />
                        ) : (
                          <ChevronsUpDown className="h-4 w-4" />
                        )}
                      </Button>
                    </div>
                  )}
                </div>
              )}

              {/* Orphaned agent messages (before first user message) */}
              {orphanedAgentMessages.length > 0 && (
                <div className="mb-6 space-y-3 opacity-70">
                  <div className="text-xs text-muted-foreground mb-2">
                    Earlier messages
                  </div>
                  {orphanedAgentMessages.map(msg => {
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
                        />
                      )
                    }
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
              )}

              {/* Messages Display */}
              {viewMode === 'rounds' ? (
                // Round-based view
                <div className="space-y-6">
                  {rounds.map((round, index) => (
                    <ConversationRound
                      key={round.id}
                      round={{
                        ...round,
                        isCollapsed: collapsedRounds.has(round.roundNumber)
                      }}
                      onToggle={() => toggleRound(round.roundNumber)}
                      isLatest={index === rounds.length - 1}
                  lastAgentMessageId={lastAgentMessageId}
                  collapseSignal={collapseSignal}
                  autoCollapseVersion={autoCollapseVersion}
                  userExpandedIds={userExpandedIds}
                  onUserToggle={handleUserToggle}
                    />
                  ))}
                </div>
              ) : (
                // Timeline view (flat chronological)
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
              )}
            
              {/* Processing Status */}
            <ProcessingStatus processing={processing} />
            
              <div ref={messagesEndRef} className="h-4" />
            </>
          )}
          </div>
      </div>

      {/* Navigation Sidebar - Only visible in rounds mode with many rounds */}
      {viewMode === 'rounds' && showNavigator && rounds.length > 3 && (
        <div ref={navigatorRef} className="absolute right-4 top-16 z-20">
          <ConversationNavigator
            rounds={rounds.map(r => ({
              ...r,
              isCollapsed: collapsedRounds.has(r.roundNumber)
            }))}
            currentRound={currentRound}
            onNavigate={navigateToRound}
            onClose={() => setShowNavigator(false)}
          />
        </div>
      )}
    </div>
  )
}
