'use client'

import React, { useRef, useEffect, useState, useCallback, useMemo } from 'react'
import { 
  Bot, 
  Zap, 
  Layers, 
  List, 
  ChevronsDownUp, 
  ChevronsUpDown,
  Map
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ConversationRound, type ConversationRoundData } from './conversation-round'
import { ConversationNavigator } from './conversation-navigator'
import { MessageBubble } from './message-bubble'

export interface MessageData {
  id: string
  type: 'user' | 'agent'
  content: string
  sender_name: string
  timestamp: string
  user_id?: string
  agent_id?: string
}

// Processing Status Component
function ProcessingStatus({ processing }: { processing: boolean }) {
  if (!processing) return null

  return (
    <div className="flex justify-start w-full mb-4">
      <div className="max-w-[80%] rounded-lg p-3 shadow-sm bg-muted border border-purple-200 dark:border-purple-800">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <div className="flex items-center gap-2">
            <Bot className="h-4 w-4 text-purple-600" />
            <span className="text-purple-600 font-medium">Agents are discussing...</span>
          </div>
        </div>
        <div className="mt-2 flex items-center gap-1">
          <div className="flex gap-1">
            <div className="w-2 h-2 bg-purple-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
            <div className="w-2 h-2 bg-purple-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
            <div className="w-2 h-2 bg-purple-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
          </div>
          <Zap className="h-3 w-3 text-yellow-500 animate-pulse ml-2" />
        </div>
      </div>
    </div>
  )
}

/**
 * Groups a flat array of messages into conversation rounds.
 * A "round" starts with a user message and includes all subsequent
 * agent messages until the next user message.
 */
function groupMessagesIntoRounds(
  messages: MessageData[],
  collapsedRounds: Set<number>
): { rounds: ConversationRoundData[]; orphanedAgentMessages: MessageData[] } {
  const rounds: ConversationRoundData[] = []
  const orphanedAgentMessages: MessageData[] = []
  
  let currentRound: ConversationRoundData | null = null
  let roundNumber = 0

  for (const message of messages) {
    if (message.type === 'user') {
      // Save previous round if exists
      if (currentRound) {
        rounds.push(currentRound)
      }
      
      roundNumber++
      
      // Create new round with this user message
      currentRound = {
        id: `round-${roundNumber}-${message.id}`,
        roundNumber,
        userMessage: message,
        agentResponses: [],
        timestamp: message.timestamp,
        isCollapsed: collapsedRounds.has(roundNumber)
      }
      
    } else if (message.type === 'agent') {
      if (currentRound) {
        currentRound.agentResponses.push(message)
      } else {
        // Agent message before any user message
        orphanedAgentMessages.push(message)
      }
    }
  }
  
  // Don't forget the last round
  if (currentRound) {
    rounds.push(currentRound)
  }
  
  return { rounds, orphanedAgentMessages }
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
    () => messages.filter(m => m.type === 'agent').map(m => m.id),
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
      if (messages[i].type === 'agent') {
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
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-muted-foreground">Loading messages...</div>
      </div>
    )
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
          <div className="h-full flex items-center justify-center">
            <div className="text-center text-muted-foreground">
              <p className="text-lg font-medium">No messages yet</p>
              <p className="text-sm">Start the conversation by sending a message</p>
            </div>
          </div>
        ) : (
            <>
              {/* View Controls - Sticky header */}
              {rounds.length > 0 && (
                <div className="sticky top-0 bg-background/95 backdrop-blur-sm py-2 z-10 mb-4 flex items-center justify-between border-b">
                  <div className="flex gap-1">
                    <Button 
                      variant="default"
                      size="sm"
                      onClick={() => setViewMode(prev => prev === 'rounds' ? 'timeline' : 'rounds')}
                      className="h-8"
                      aria-label="Toggle view mode"
                      title={viewMode === 'rounds' ? 'Show Timeline' : 'Show Rounds'}
                    >
                      {viewMode === 'rounds' ? (
                        <>
                          <Layers className="h-4 w-4 mr-1.5" /> 
                          Rounds
                        </>
                      ) : (
                        <>
                          <List className="h-4 w-4 mr-1.5" /> 
                          Timeline
                        </>
                      )}
                    </Button>
                  </div>
                  
                  {viewMode === 'rounds' && rounds.length > 1 && (
                    <div className="flex items-center gap-1">
                      <Button 
                        variant="ghost" 
                        size="sm" 
                        onClick={collapsedRounds.size === rounds.length ? expandAll : collapseAll}
                        className="h-8 w-8 p-0"
                        title={collapsedRounds.size === rounds.length ? 'Expand all' : 'Collapse all'}
                        aria-label={collapsedRounds.size === rounds.length ? 'Expand all rounds' : 'Collapse all rounds'}
                      >
                        {collapsedRounds.size === rounds.length ? (
                          <ChevronsUpDown className="h-3.5 w-3.5" />
                        ) : (
                          <ChevronsDownUp className="h-3.5 w-3.5" /> 
                        )}
                      </Button>
                      
                      {/* Navigator toggle for many rounds */}
                      {rounds.length > 3 && (
                        <Button
                          ref={mapButtonRef}
                          variant={showNavigator ? 'default' : 'ghost'}
                          size="sm"
                          onClick={() => setShowNavigator(!showNavigator)}
                          className="h-8 text-xs ml-2"
                        >
                          <Map className="h-3.5 w-3.5 mr-1" />
                          Map
                        </Button>
                      )}
                    </div>
                  )}

                  {viewMode === 'timeline' && allAgentIds.length > 0 && (
                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={allTimelineExpanded ? collapseAllTimeline : expandAllTimeline}
                        className="h-8 w-8 p-0"
                        title={allTimelineExpanded ? 'Collapse all' : 'Expand all'}
                        aria-label={allTimelineExpanded ? 'Collapse all timeline messages' : 'Expand all timeline messages'}
                      >
                        {allTimelineExpanded ? (
                          <ChevronsDownUp className="h-3.5 w-3.5" />
                        ) : (
                          <ChevronsUpDown className="h-3.5 w-3.5" />
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
                  {orphanedAgentMessages.map(msg => (
                    <MessageBubble
                      key={msg.id}
                      message={msg}
                      defaultExpanded={msg.id === lastAgentMessageId}
                  collapseSignal={collapseSignal}
                  autoCollapseVersion={autoCollapseVersion}
                  isLatestAgent={msg.id === lastAgentMessageId}
                  isUserExpanded={userExpandedIds.has(msg.id)}
                  onUserToggle={handleUserToggle}
                    />
                  ))}
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
                  {messages.map(msg => (
                    <MessageBubble
                      key={msg.id}
                      message={msg}
                      defaultExpanded={msg.id === lastAgentMessageId}
                      collapseSignal={collapseSignal}
                      autoCollapseVersion={autoCollapseVersion}
                      isLatestAgent={msg.id === lastAgentMessageId}
                      isUserExpanded={userExpandedIds.has(msg.id)}
                      onUserToggle={handleUserToggle}
                    />
            ))}
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
