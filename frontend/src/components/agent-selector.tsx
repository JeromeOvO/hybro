'use client'

import { Plus, Minus, Users } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from '@/components/ui/hover-card'
import type { Agent } from '@/lib/types/agent'
import { getAgentAvatarUri } from '@/lib/agent-avatar'
import { AgentSourceBadge } from './agent-source-badge'

interface AgentSelectorProps {
  selectedAgents: { [agentId: string]: Agent }
  onAgentAdd: (agent: Agent) => void
  onAgentRemove: (agentId: string) => void
  availableAgents?: Agent[]
  loading?: boolean
  error?: string | null
  onRetry?: () => void
  className?: string
}

interface AgentCardHoverProps {
  agent: Agent
  children: React.ReactNode
}

function AgentCardHover({ agent, children }: AgentCardHoverProps) {
  return (
    <HoverCard>
      <HoverCardTrigger asChild>
        {children}
      </HoverCardTrigger>
      <HoverCardContent 
        className="w-80 bg-background/80 backdrop-blur-md border shadow-lg z-[60]"
        side="top"
        sideOffset={8}
        align="center"
        avoidCollisions={true}
        collisionPadding={10}
      >
        <div className="space-y-4">
          {/* Header */}
          <div className="flex items-start gap-4">
            <Avatar className="w-12 h-12">
              <AvatarImage src={agent.agent_card.iconUrl || undefined} />
              <AvatarFallback className="text-lg p-0 overflow-hidden">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={getAgentAvatarUri(agent.agent_id)} alt={agent.agent_card.name} className="h-full w-full" />
              </AvatarFallback>
            </Avatar>
            <div className="space-y-1 flex-1 min-w-0">
              <h4 className="text-sm font-semibold truncate">
                {agent.agent_card.name}
              </h4>
              <p className="text-xs text-muted-foreground">
                Version {agent.agent_card.version}
              </p>
              {agent.agent_card.provider && (
                <p className="text-xs text-muted-foreground">
                  by {agent.agent_card.provider.organization}
                </p>
              )}
            </div>
          </div>

          {/* Description */}
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground leading-relaxed">
              {agent.agent_card.description}
            </p>
          </div>

          {/* Skills */}
          {agent.agent_card.skills && agent.agent_card.skills.length > 0 && (
            <div className="space-y-2">
              <h5 className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                Skills
              </h5>
              <div className="flex flex-wrap gap-1">
                {agent.agent_card.skills.slice(0, 6).map((skill) => (
                  <Badge key={skill.id} variant="outline" className="text-xs h-5">
                    {skill.name}
                  </Badge>
                ))}
                {agent.agent_card.skills.length > 6 && (
                  <Badge variant="outline" className="text-xs h-5">
                    +{agent.agent_card.skills.length - 6} more
                  </Badge>
                )}
              </div>
            </div>
          )}

          {/* Capabilities */}
          <div className="space-y-2">
            <h5 className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
              Capabilities
            </h5>
            <div className="flex flex-wrap gap-1">
              {agent.agent_card.capabilities.streaming && (
                <Badge variant="secondary" className="text-xs h-5">
                  Streaming
                </Badge>
              )}
              {agent.agent_card.capabilities.pushNotifications && (
                <Badge variant="secondary" className="text-xs h-5">
                  Push Notifications
                </Badge>
              )}
              {agent.agent_card.capabilities.stateTransitionHistory && (
                <Badge variant="secondary" className="text-xs h-5">
                  State History
                </Badge>
              )}
              {(!agent.agent_card.capabilities.streaming && 
                !agent.agent_card.capabilities.pushNotifications && 
                !agent.agent_card.capabilities.stateTransitionHistory) && (
                <span className="text-xs text-muted-foreground">
                  Basic capabilities
                </span>
              )}
            </div>
          </div>

          {/* Input/Output Modes */}
          <div className="grid grid-cols-2 gap-3">
            {agent.agent_card.defaultInputModes && agent.agent_card.defaultInputModes.length > 0 && (
              <div className="space-y-1">
                <h6 className="text-xs font-medium text-muted-foreground">Input</h6>
                <div className="flex flex-wrap gap-1">
                  {agent.agent_card.defaultInputModes.slice(0, 2).map((mode, index) => (
                    <Badge key={index} variant="outline" className="text-xs h-4 px-1">
                      {mode}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
            
            {agent.agent_card.defaultOutputModes && agent.agent_card.defaultOutputModes.length > 0 && (
              <div className="space-y-1">
                <h6 className="text-xs font-medium text-muted-foreground">Output</h6>
                <div className="flex flex-wrap gap-1">
                  {agent.agent_card.defaultOutputModes.slice(0, 2).map((mode, index) => (
                    <Badge key={index} variant="outline" className="text-xs h-4 px-1">
                      {mode}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </HoverCardContent>
    </HoverCard>
  )
}

export function AgentSelector({
  selectedAgents,
  onAgentAdd,
  onAgentRemove,
  availableAgents = [],
  loading = false,
  error = null,
  onRetry,
  className
}: AgentSelectorProps) {
  const selectedAgentsList = Object.values(selectedAgents)

  // Remove duplicates from availableAgents
  const uniqueAgents = Array.from(
    new Map(availableAgents.map(agent => [agent.agent_id, agent])).values()
  );
  const unselectedAgents = uniqueAgents
    .filter(agent => !selectedAgents[agent.agent_id])
    .sort((a, b) => {
      const aIsHub = a.source === 'hub' ? 1 : 0
      const bIsHub = b.source === 'hub' ? 1 : 0
      return aIsHub - bIsHub
    });

  return (
    <div className={`space-y-4 ${className}`}>
      {/* Header */}
      <div className="flex items-center gap-2">
        <Users className="w-5 h-5" />
        <Label className="text-base font-semibold">Room Agents</Label>
      </div>

      {/* Error State */}
      {error && (
        <div className="p-3 rounded-lg border border-destructive/20 bg-destructive/10 text-destructive text-sm">
          {error}
          {onRetry && (
            <Button
              size="sm"
              variant="ghost"
              onClick={onRetry}
              className="ml-2 h-auto p-1 text-destructive hover:text-destructive"
            >
              Retry
            </Button>
          )}
        </div>
      )}

      {/* Selected Agents */}
      {selectedAgentsList.length > 0 && (
        <div className="space-y-3">
          <h4 className="text-sm font-medium text-muted-foreground">Selected Agents</h4>
          <div className="flex flex-wrap gap-2 min-h-[44px]">
            {selectedAgentsList.map((agent) => (
              <AgentCardHover key={agent.agent_id} agent={agent}>
                <div
                  className={`flex items-center gap-2 px-3 py-2 rounded-lg border bg-muted/50 hover:bg-muted transition-colors cursor-pointer h-10${
                    agent.source === 'hub' && !agent.is_hub_online ? ' opacity-50' : ''
                  }`}
                >
                  <Avatar className="w-6 h-6 flex-shrink-0">
                    <AvatarImage src={agent.agent_card.iconUrl || undefined} />
                    <AvatarFallback className="text-xs">
                      {agent.agent_card.name.charAt(0).toUpperCase()}
                    </AvatarFallback>
                  </Avatar>
                  <AgentSourceBadge source={agent.source} isHubOnline={agent.is_hub_online} className="h-3 w-3" />
                  <span className="text-sm font-medium truncate max-w-32">
                    {agent.agent_card.name}
                    {agent.source === 'hub' && !agent.is_hub_online && (
                      <span className="text-muted-foreground font-normal"> (offline)</span>
                    )}
                  </span>
                  <Minus 
                    className="w-4 h-4 text-destructive ml-2 flex-shrink-0 cursor-pointer hover:text-destructive/80" 
                    onClick={(e) => {
                      e.stopPropagation()
                      onAgentRemove(agent.agent_id)
                    }}
                  />
                </div>
              </AgentCardHover>
            ))}
          </div>
        </div>
      )}

      {/* Available Agents */}
      <div className="space-y-3">
        <h4 className="text-sm font-medium text-muted-foreground">Available Agents</h4>
        {loading ? (
          <div className="text-center py-8 text-muted-foreground min-h-[120px] flex items-center justify-center">
            <div className="flex flex-col items-center gap-2">
              <div className="animate-pulse">Loading agents...</div>
            </div>
          </div>
        ) : error ? (
          <div className="text-center py-8 text-muted-foreground min-h-[120px] flex items-center justify-center">
            Failed to load. Please try again.
          </div>
        ) : unselectedAgents.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground min-h-[120px] flex items-center justify-center">
            {uniqueAgents.length === 0 ? 'No agents available' : 'All agents selected'}
          </div>
        ) : (
          <div className="flex flex-wrap gap-2 max-h-64 min-h-[120px] overflow-y-auto p-1">
            {unselectedAgents.map((agent) => (
              <AgentCardHover key={agent.agent_id} agent={agent}>
                <div
                  className={`flex items-center gap-2 px-3 py-2 rounded-lg border hover:bg-muted/30 transition-colors cursor-pointer h-10${
                    agent.source === 'hub' && !agent.is_hub_online ? ' opacity-50' : ''
                  }`}
                  onClick={() => onAgentAdd(agent)}
                >
                  <Avatar className="w-6 h-6 flex-shrink-0">
                    <AvatarImage src={agent.agent_card.iconUrl || undefined} />
                    <AvatarFallback className="text-xs">
                      {agent.agent_card.name.charAt(0).toUpperCase()}
                    </AvatarFallback>
                  </Avatar>
                  <AgentSourceBadge source={agent.source} isHubOnline={agent.is_hub_online} className="h-3 w-3" />
                  <span className="text-sm font-medium truncate max-w-32">
                    {agent.agent_card.name}
                    {agent.source === 'hub' && !agent.is_hub_online && (
                      <span className="text-muted-foreground font-normal"> (offline)</span>
                    )}
                  </span>
                  <Plus className="w-4 h-4 text-primary ml-2 flex-shrink-0" />
                </div>
              </AgentCardHover>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
