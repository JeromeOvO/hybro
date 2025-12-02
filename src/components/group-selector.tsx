'use client'

import { useState, useEffect } from 'react'
import { ChevronDown, Globe, Users, Star, X, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import type { AgentGroup } from '@/lib/types/agent-group'
import { BUILTIN_GROUP_ALL_AGENTS, BUILTIN_GROUP_ROOM_TEAM } from '@/lib/types/agent-group'

interface MentionedAgent {
  id: string
  name: string
}

interface GroupSelectorProps {
  selectedGroup: string
  onGroupChange: (groupId: string) => void
  groups: AgentGroup[]
  loadingGroups?: boolean
  roomAgentCount?: number
  mentionedAgents?: MentionedAgent[]
  onClearMentions?: () => void
  onManageGroups?: () => void
  className?: string
  disabled?: boolean
}

export function GroupSelector({
  selectedGroup,
  onGroupChange,
  groups,
  loadingGroups = false,
  roomAgentCount = 0,
  mentionedAgents = [],
  onClearMentions,
  onManageGroups,
  className,
  disabled = false,
}: GroupSelectorProps) {
  const hasMentions = mentionedAgents.length > 0

  // Get the display info for current selection
  const getDisplayInfo = () => {
    if (hasMentions) {
      return {
        icon: <Users className="h-3.5 w-3.5" />,
        label: mentionedAgents.length === 1 
          ? `@${mentionedAgents[0].name}`
          : `${mentionedAgents.length} agents mentioned`,
        description: 'Mentioned agents',
      }
    }

    if (selectedGroup === BUILTIN_GROUP_ALL_AGENTS) {
      return {
        icon: <Globe className="h-3.5 w-3.5" />,
        label: 'All Agents',
        description: 'Find best agents',
      }
    }

    if (selectedGroup === BUILTIN_GROUP_ROOM_TEAM) {
      return {
        icon: <Users className="h-3.5 w-3.5" />,
        label: roomAgentCount > 0 ? `Room Team (${roomAgentCount})` : 'Room Team',
        description: roomAgentCount > 0 ? 'Room agents' : 'No agents in room',
      }
    }

    // Custom group
    const customGroup = groups.find(g => g.group_id === selectedGroup)
    if (customGroup) {
      return {
        icon: <Star className="h-3.5 w-3.5" />,
        label: customGroup.name,
        description: `${customGroup.agents.length} agents`,
      }
    }

    return {
      icon: <Globe className="h-3.5 w-3.5" />,
      label: 'All Agents',
      description: 'Find best agents',
    }
  }

  const displayInfo = getDisplayInfo()

  // Filter groups by type
  const builtinGroups = groups.filter(g => g.type === 'builtin')
  const userGroups = groups.filter(g => g.type === 'user')

  if (loadingGroups) {
    return (
      <div className={cn("flex items-center gap-2 px-3 py-1.5 text-sm text-muted-foreground", className)}>
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        <span>Loading groups...</span>
      </div>
    )
  }

  // If mentions are present, show a special display
  if (hasMentions) {
    return (
      <div className={cn("flex items-center gap-2", className)}>
        <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-primary/10 border border-primary/20 text-sm">
          {displayInfo.icon}
          <span className="font-medium text-primary">{displayInfo.label}</span>
          {onClearMentions && (
            <button
              onClick={onClearMentions}
              className="ml-1 hover:bg-primary/20 rounded-full p-0.5"
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild disabled={disabled}>
        <Button
          variant="ghost"
          size="sm"
          className={cn(
            "h-auto py-1.5 px-3 gap-1.5 font-normal hover:bg-muted/50",
            className
          )}
        >
          <span className="text-muted-foreground">{displayInfo.icon}</span>
          <span className="font-medium">{displayInfo.label}</span>
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent 
        align="start" 
        className="w-64 border border-border/50 shadow-lg z-50 bg-background/95 backdrop-blur-md max-h-40 overflow-y-auto"
      >
        {/* Built-in groups */}
        <DropdownMenuItem
          onClick={() => onGroupChange(BUILTIN_GROUP_ALL_AGENTS)}
          className={cn(
            "flex items-start gap-3 py-2.5",
            selectedGroup === BUILTIN_GROUP_ALL_AGENTS && "bg-accent"
          )}
        >
          <Globe className="h-4 w-4 mt-0.5 text-muted-foreground" />
          <div className="flex-1">
            <div className="font-medium">All Agents</div>
            <div className="text-xs text-muted-foreground">
              Find the best agents for your question
            </div>
          </div>
        </DropdownMenuItem>

        <DropdownMenuItem
          onClick={() => onGroupChange(BUILTIN_GROUP_ROOM_TEAM)}
          className={cn(
            "flex items-start gap-3 py-2.5",
            selectedGroup === BUILTIN_GROUP_ROOM_TEAM && "bg-accent"
          )}
          disabled={roomAgentCount === 0}
        >
          <Users className="h-4 w-4 mt-0.5 text-muted-foreground" />
          <div className="flex-1">
            <div className="font-medium">
              Room Team {roomAgentCount > 0 && `(${roomAgentCount})`}
            </div>
            <div className="text-xs text-muted-foreground">
              {roomAgentCount > 0 
                ? 'Use agents assigned to this room'
                : 'No agents in room yet'
              }
            </div>
          </div>
        </DropdownMenuItem>

        {/* User groups */}
        {userGroups.length > 0 && (
          <>
            <DropdownMenuSeparator />
            <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
              My Groups
            </div>
            {userGroups.map(group => (
              <DropdownMenuItem
                key={group.group_id}
                onClick={() => onGroupChange(group.group_id)}
                className={cn(
                  "flex items-start gap-3 py-2.5",
                  selectedGroup === group.group_id && "bg-accent"
                )}
              >
                <Star className="h-4 w-4 mt-0.5 text-muted-foreground" />
                <div className="flex-1">
                  <div className="font-medium">{group.name}</div>
                  <div className="text-xs text-muted-foreground">
                    {group.agents.length} agent{group.agents.length !== 1 ? 's' : ''}
                  </div>
                </div>
              </DropdownMenuItem>
            ))}
          </>
        )}

        {/* Manage groups */}
        {onManageGroups && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={onManageGroups} className="text-muted-foreground">
              <Star className="h-4 w-4 mr-2" />
              Manage groups...
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

