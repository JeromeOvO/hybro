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
  const previewText = responses[0]?.content.slice(0, 120) || ''

  return (
    <div className="ml-4 mt-2 p-4 rounded-xl bg-muted/40 dark:bg-muted/20 border border-dashed border-border/60 hover:bg-muted/60 dark:hover:bg-muted/30 transition-colors cursor-pointer">
      <div className="flex items-center gap-3 mb-2">
        <div className="flex -space-x-2">
          {uniqueAgents.slice(0, 5).map((r, i) => (
            <AgentAvatar
              key={r.agent_id || i}
              agentName={r.sender_name}
              agentId={r.agent_id || 'unknown'}
              size="sm"
            />
          ))}
          {uniqueAgents.length > 5 && (
            <div className="w-6 h-6 rounded-full bg-muted flex items-center justify-center text-[10px] font-medium border-2 border-background">
              +{uniqueAgents.length - 5}
            </div>
          )}
        </div>
        <span className="text-xs font-medium text-muted-foreground">
          {responses.length} response{responses.length !== 1 ? 's' : ''} from {uniqueAgents.length} agent{uniqueAgents.length !== 1 ? 's' : ''}
        </span>
      </div>
      {previewText && (
        <p className="text-xs text-muted-foreground/80 line-clamp-2 italic pl-1">
          &ldquo;{previewText}{previewText.length >= 120 ? '...' : ''}&rdquo;
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
  const formattedTime = new Date(round.timestamp).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
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
      <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-gradient-to-b from-primary/50 via-primary/30 to-primary/10 rounded-full" />

      <div className="pl-4">
        {/* Round Header - Clickable */}
        <button
          onClick={onToggle}
          className="flex items-center gap-3 w-full text-left mb-3 group"
        >
          <div className={cn(
            "flex items-center gap-2 px-3 py-2 rounded-xl transition-all duration-200",
            "bg-muted/40 hover:bg-muted/70 dark:bg-muted/20 dark:hover:bg-muted/40",
            "group-hover:shadow-sm border border-transparent hover:border-border/50"
          )}>
            {round.isCollapsed ? (
              <ChevronRight className="h-4 w-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
            ) : (
              <ChevronDown className="h-4 w-4 text-muted-foreground" />
            )}
            <span className="text-sm font-semibold">Round {round.roundNumber}</span>
            {round.agentResponses.length > 0 && (
              <span className="text-xs text-muted-foreground flex items-center gap-1.5 bg-background/50 px-2 py-0.5 rounded-full">
                <MessageSquare className="h-3 w-3" />
                {round.agentResponses.length}
              </span>
            )}
          </div>

          {/* Agent avatars preview in header */}
          {round.agentResponses.length > 0 && (
            <div className="flex -space-x-1.5 opacity-70 group-hover:opacity-100 transition-opacity">
              {groupedByAgent.slice(0, 4).map((group) => (
                <AgentAvatar
                  key={group.agentId}
                  agentName={group.agentName}
                  agentId={group.agentId}
                  size="sm"
                />
              ))}
              {uniqueAgentCount > 4 && (
                <div className="w-6 h-6 rounded-full bg-muted flex items-center justify-center text-[10px] font-semibold border-2 border-background">
                  +{uniqueAgentCount - 4}
                </div>
              )}
            </div>
          )}

          <span className="text-xs text-muted-foreground ml-auto font-medium">
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
              <div className="flex gap-1 mb-3">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setViewByAgent(prev => !prev)
                  }}
                  className={cn(
                    "px-3 py-1.5 text-xs font-medium rounded-lg transition-all duration-200",
                    "bg-primary/10 text-primary hover:bg-primary/20 dark:bg-primary/20 dark:hover:bg-primary/30",
                    "border border-primary/20 hover:border-primary/40"
                  )}
                  title={viewByAgent ? "Show timeline" : "Group by agent"}
                  aria-label={viewByAgent ? "Show timeline" : "Group by agent"}
                >
                  {viewByAgent ? "↓ Show Timeline" : "◫ Group by Agent"}
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

