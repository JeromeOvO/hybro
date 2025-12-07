'use client'

import React, { useState, useMemo } from 'react'
import { ChevronRight, ChevronDown, MessageSquare } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getAgentColorClasses, getAgentInitials } from '@/lib/agent-colors'
import type { MessageData } from './room-messages'
import { AgentMessageBubble, UserMessageBubble } from './message-bubble'

export interface ConversationRoundData {
  id: string
  roundNumber: number
  userMessage: MessageData
  agentResponses: MessageData[]
  timestamp: string
  isCollapsed: boolean
}

interface ConversationRoundProps {
  round: ConversationRoundData
  onToggle: () => void
  isLatest?: boolean
  lastAgentMessageId?: string | null
  collapseSignal?: number
  autoCollapseVersion?: number
  userExpandedIds?: Set<string>
  onUserToggle?: (id: string, expanded: boolean) => void
}

/**
 * Agent Avatar with consistent color based on agent ID
 */
function AgentAvatar({ agentName, agentId, size = 'md' }: { 
  agentName: string
  agentId: string
  size?: 'sm' | 'md'
}) {
  const colors = getAgentColorClasses(agentId)
  const initials = getAgentInitials(agentName)
  
  return (
    <div 
      className={cn(
        "rounded-full flex items-center justify-center font-semibold border-2",
        colors.bg,
        colors.border,
        colors.text,
        size === 'sm' ? "w-6 h-6 text-[10px]" : "w-8 h-8 text-xs"
      )}
      title={agentName}
    >
      {initials}
    </div>
  )
}

/**
 * Collapsed preview showing agent avatars who responded
 */
function CollapsedPreview({ responses }: { responses: MessageData[] }) {
  // Get unique agents
  const uniqueAgents = useMemo(() => {
    const seen = new Set<string>()
    return responses.filter(r => {
      const id = r.agent_id || 'unknown'
      if (seen.has(id)) return false
      seen.add(id)
      return true
    })
  }, [responses])

  // Get preview of first response
  const previewText = responses[0]?.content.slice(0, 100) || ''

  return (
    <div className="ml-4 mt-2 p-3 rounded-lg bg-muted/30 border border-dashed">
      <div className="flex items-center gap-2 mb-2">
        <div className="flex -space-x-2">
          {uniqueAgents.slice(0, 5).map((r, i) => (
            <AgentAvatar 
              key={r.agent_id || i}
              agentName={r.sender_name}
              agentId={r.agent_id || 'unknown'}
              size="sm"
            />
          ))}
        </div>
        <span className="text-xs text-muted-foreground">
          {responses.length} response{responses.length !== 1 ? 's' : ''} from {uniqueAgents.length} agent{uniqueAgents.length !== 1 ? 's' : ''}
        </span>
      </div>
      {previewText && (
        <p className="text-xs text-muted-foreground line-clamp-2 italic">
          &ldquo;{previewText}{previewText.length >= 100 ? '...' : ''}&rdquo;
        </p>
      )}
    </div>
  )
}

/**
 * Group agent responses by agent for better organization
 */
interface AgentResponseGroup {
  agentId: string
  agentName: string
  messages: MessageData[]
}

function groupResponsesByAgent(responses: MessageData[]): AgentResponseGroup[] {
  const agentMap = new Map<string, AgentResponseGroup>()
  
  for (const msg of responses) {
    const agentId = msg.agent_id || 'unknown'
    
    if (!agentMap.has(agentId)) {
      agentMap.set(agentId, {
        agentId,
        agentName: msg.sender_name,
        messages: []
      })
    }
    
    agentMap.get(agentId)!.messages.push(msg)
  }
  
  // Return as array, sorted by first response time
  return Array.from(agentMap.values()).sort((a, b) => 
    new Date(a.messages[0].timestamp).getTime() - 
    new Date(b.messages[0].timestamp).getTime()
  )
}

/**
 * A single conversation round containing user message and all agent responses
 */
export function ConversationRound({
  round,
  onToggle,
  isLatest = false,
  lastAgentMessageId,
  collapseSignal = 0,
  autoCollapseVersion = 0,
  userExpandedIds,
  onUserToggle,
}: ConversationRoundProps) {
  const [viewByAgent, setViewByAgent] = useState(false)
  
  const groupedByAgent = useMemo(
    () => groupResponsesByAgent(round.agentResponses),
    [round.agentResponses]
  )

  const uniqueAgentCount = groupedByAgent.length

  // Format time
  const formattedTime = new Date(round.timestamp).toLocaleTimeString([], { 
    hour: '2-digit', 
    minute: '2-digit' 
  })

  return (
    <div 
      id={`round-${round.roundNumber}`}
      className={cn(
        "relative",
        isLatest && "animate-in fade-in slide-in-from-bottom-4 duration-300"
      )}
    >
      {/* Round indicator line */}
      <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-linear-to-b from-primary/40 to-primary/10 rounded-full" />
      
      <div className="pl-4">
        {/* Round Header - Clickable */}
        <button 
          onClick={onToggle}
          className="flex items-center gap-2 w-full text-left mb-3 group"
        >
          <div className={cn(
            "flex items-center gap-2 px-3 py-1.5 rounded-full transition-colors",
            "bg-muted/50 hover:bg-muted group-hover:shadow-sm"
          )}>
            {round.isCollapsed ? (
              <ChevronRight className="h-4 w-4 text-muted-foreground" />
            ) : (
              <ChevronDown className="h-4 w-4 text-muted-foreground" />
            )}
            <span className="text-sm font-medium">Round {round.roundNumber}</span>
            {round.agentResponses.length > 0 && (
              <span className="text-xs text-muted-foreground flex items-center gap-1">
                <MessageSquare className="h-3 w-3" />
                {round.agentResponses.length}
              </span>
            )}
          </div>
          
          {/* Agent avatars preview in header */}
          {round.agentResponses.length > 0 && (
            <div className="flex -space-x-1 opacity-60 group-hover:opacity-100 transition-opacity">
              {groupedByAgent.slice(0, 4).map((group) => (
                <AgentAvatar 
                  key={group.agentId}
                  agentName={group.agentName}
                  agentId={group.agentId}
                  size="sm"
                />
              ))}
              {uniqueAgentCount > 4 && (
                <div className="w-6 h-6 rounded-full bg-muted flex items-center justify-center text-[10px] font-medium border-2 border-background">
                  +{uniqueAgentCount - 4}
                </div>
              )}
            </div>
          )}
          
          <span className="text-xs text-muted-foreground ml-auto">
            {formattedTime}
          </span>
        </button>

        {/* User Message - Always Visible */}
        <div className="mb-3">
          <UserMessageBubble message={round.userMessage} />
        </div>

        {/* Agent Responses - Collapsible */}
        {!round.isCollapsed && round.agentResponses.length > 0 && (
          <div className="space-y-3">
            {/* View toggle for multiple agents */}
            {uniqueAgentCount > 1 && (
              <div className="flex gap-1 mb-2">
                <button
                  onClick={(e) => { 
                    e.stopPropagation(); 
                    setViewByAgent(prev => !prev) 
                  }}
                  className={cn(
                    "px-2 py-1 text-xs rounded-md transition-colors",
                    "bg-primary text-primary-foreground"
                  )}
                  title={viewByAgent ? "Show timeline" : "Group by agent"}
                  aria-label={viewByAgent ? "Show timeline" : "Group by agent"}
                >
                  {viewByAgent ? "By Agent" : "Timeline"}
                </button>
              </div>
            )}

            {/* Agent Responses */}
            {viewByAgent ? (
              // Grouped by agent view
              <div className="space-y-4">
                {groupedByAgent.map(group => {
                  const colors = getAgentColorClasses(group.agentId)
                  return (
                    <div 
                      key={group.agentId} 
                      className={cn("border-l-2 pl-3 py-1", colors.border)}
                    >
                      <div className="flex items-center gap-2 mb-2">
                        <AgentAvatar 
                          agentName={group.agentName} 
                          agentId={group.agentId} 
                        />
                        <span className={cn("font-medium text-sm", colors.text)}>
                          {group.agentName}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          ({group.messages.length} message{group.messages.length !== 1 ? 's' : ''})
                        </span>
                      </div>
                      <div className="space-y-2">
                        {group.messages.map(msg => (
                          <AgentMessageBubble
                            key={msg.id}
                            message={msg}
                            compact
                            defaultExpanded={msg.id === lastAgentMessageId}
                            collapseSignal={collapseSignal}
                            autoCollapseVersion={autoCollapseVersion}
                            isLatestAgent={msg.id === lastAgentMessageId}
                            isUserExpanded={userExpandedIds?.has(msg.id) ?? false}
                            onUserToggle={onUserToggle}
                          />
                        ))}
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              // Timeline view (chronological)
              <div className="space-y-3">
                {round.agentResponses.map(msg => (
                  <AgentMessageBubble
                    key={msg.id}
                    message={msg}
                    defaultExpanded={msg.id === lastAgentMessageId}
                    collapseSignal={collapseSignal}
                    autoCollapseVersion={autoCollapseVersion}
                    isLatestAgent={msg.id === lastAgentMessageId}
                    isUserExpanded={userExpandedIds?.has(msg.id) ?? false}
                    onUserToggle={onUserToggle}
                  />
                ))}
              </div>
            )}
          </div>
        )}

        {/* Collapsed Preview */}
        {round.isCollapsed && round.agentResponses.length > 0 && (
          <CollapsedPreview responses={round.agentResponses} />
        )}
      </div>
    </div>
  )
}

