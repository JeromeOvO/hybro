'use client'

import React from 'react'
import { cn } from '@/lib/utils'
import { getAgentColorClasses } from '@/lib/agent-colors'
import { Map as MapIcon, X } from 'lucide-react'
import type { ConversationRoundData } from './conversation-round'
import type { MessageData } from './room-messages'

interface ConversationNavigatorProps {
  rounds: ConversationRoundData[]
  currentRound: number
  onNavigate: (roundIndex: number) => void
  onClose?: () => void
}

/**
 * Mini-map navigation sidebar for quick jumping between conversation rounds
 */
export function ConversationNavigator({ 
  rounds, 
  currentRound, 
  onNavigate,
  onClose 
}: ConversationNavigatorProps) {
  if (rounds.length === 0) return null

  return (
    <div className="bg-background/95 backdrop-blur-sm rounded-lg border shadow-lg p-2 w-52 max-h-[70vh] flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-2 pb-2 border-b mb-2">
        <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
          <MapIcon className="h-3.5 w-3.5" />
          <span>Navigation</span>
        </div>
        {onClose && (
          <button 
            onClick={onClose}
            className="p-1 hover:bg-muted rounded-md transition-colors"
          >
            <X className="h-3.5 w-3.5 text-muted-foreground" />
          </button>
        )}
      </div>

      {/* Round List */}
      <div className="flex-1 overflow-y-auto space-y-1 pr-1">
        {rounds.map((round, idx) => {
          // Get unique agents in this round
          const uniqueAgents = Array.from(
            new Map<string | undefined, MessageData>(
              round.agentResponses.map(r => [r.agent_id, r])
            ).values()
          )

          return (
            <button
              key={round.id}
              onClick={() => onNavigate(idx)}
              className={cn(
                "w-full text-left px-2.5 py-2 rounded-md text-sm transition-all",
                currentRound === idx 
                  ? "bg-primary text-primary-foreground shadow-sm" 
                  : "hover:bg-muted"
              )}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="font-medium text-xs">Round {round.roundNumber}</span>
                {round.isCollapsed && (
                  <span className="text-[10px] opacity-60">collapsed</span>
                )}
              </div>
              
              {/* User message preview */}
              <p className={cn(
                "text-xs truncate mb-1.5",
                currentRound === idx 
                  ? "text-primary-foreground/80" 
                  : "text-muted-foreground"
              )}>
                {round.userMessage.content.slice(0, 50)}
                {round.userMessage.content.length > 50 ? '...' : ''}
              </p>
              
              {/* Agent avatars */}
              {uniqueAgents.length > 0 && (
                <div className="flex items-center gap-1">
                  <div className="flex -space-x-1">
                    {uniqueAgents.slice(0, 4).map((r, i) => {
                      const colors = getAgentColorClasses(r.agent_id || 'unknown')
                      return (
                        <div
                          key={r.agent_id || i}
                          className={cn(
                            "w-4 h-4 rounded-full border border-background",
                            colors.accent
                          )}
                          title={r.sender_name}
                        />
                      )
                    })}
                  </div>
                  <span className={cn(
                    "text-[10px]",
                    currentRound === idx 
                      ? "text-primary-foreground/70" 
                      : "text-muted-foreground"
                  )}>
                    {round.agentResponses.length} msg{round.agentResponses.length !== 1 ? 's' : ''}
                  </span>
                </div>
              )}
            </button>
          )
        })}
      </div>

      {/* Summary footer */}
      <div className="pt-2 mt-2 border-t">
        <div className="text-[10px] text-muted-foreground text-center">
          {rounds.length} round{rounds.length !== 1 ? 's' : ''} • {' '}
          {rounds.reduce((sum, r) => sum + r.agentResponses.length, 0)} total responses
        </div>
      </div>
    </div>
  )
}

/**
 * Floating navigation button that toggles the navigator
 */
export function NavigatorToggle({ 
  isOpen, 
  onToggle, 
  roundCount 
}: { 
  isOpen: boolean
  onToggle: () => void 
  roundCount: number
}) {
  return (
    <button
      onClick={onToggle}
      className={cn(
        "flex items-center gap-2 px-3 py-2 rounded-full shadow-lg border transition-all",
        isOpen 
          ? "bg-primary text-primary-foreground" 
          : "bg-background hover:bg-muted"
      )}
    >
      <MapIcon className="h-4 w-4" />
      <span className="text-sm font-medium">{roundCount}</span>
    </button>
  )
}

