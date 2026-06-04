"use client"

import { useState, useEffect, useCallback, useMemo } from "react"
import { listAgentGroups } from "@/lib/api/agent-group"
import { getAllActiveAgents } from "@/lib/api/agent"
import type { AgentGroup, TargetModeDispatchInput } from "@/lib/types/agent-group"
import type { Agent } from "@/lib/types/agent"
import {
  BUILTIN_GROUP_ALL_AGENTS,
  BUILTIN_GROUP_ROOM_TEAM,
  resolveSelectedGroupDispatch,
} from "@/lib/types/agent-group"

interface UseGroupManagementOptions {
  userId?: string
  getToken: () => Promise<string | null>
  isLoaded: boolean
  /** Default group when no override is active */
  defaultGroup?: string
  /** Room ID for localStorage persistence (room page only) */
  roomId?: string
  /** Number of room agents to determine default group */
  roomAgentCount?: number
  /** Called when an action requires authentication but user is not signed in */
  onRequireAuth?: () => void
}

interface GroupManagementState {
  // Group state
  groups: AgentGroup[]
  loadingGroups: boolean
  selectedGroup: string
  isOverride: boolean
  resolvedTargetMode: TargetModeDispatchInput
  // Modal state
  groupManagementOpen: boolean
  groupAction: { type: 'create' | 'edit' | 'delete'; group?: AgentGroup } | null
  // Agent state (for modal & mentions)
  availableAgents: Agent[]
  loadingAgents: boolean
  agentsError: string | null
}

interface GroupManagementActions {
  // Group management
  handleGroupsChange: () => Promise<void>
  handleCreateGroup: () => void
  handleEditGroup: (group: AgentGroup) => void
  handleDeleteGroup: (group: AgentGroup) => void
  handleGroupCreated: (group: AgentGroup) => void
  handleGroupChange: (groupId: string) => void
  handleClearOverride: () => void
  setGroupManagementOpen: (open: boolean) => void
  setGroupAction: (action: { type: 'create' | 'edit' | 'delete'; group?: AgentGroup } | null) => void
  // Agent loading
  loadAvailableAgents: () => Promise<void>
  setAvailableAgents: (agents: Agent[]) => void
}

export function useGroupManagement(
  options: UseGroupManagementOptions
): GroupManagementState & GroupManagementActions {
  const { userId, getToken, isLoaded, defaultGroup, roomId, roomAgentCount = 0, onRequireAuth } = options

  // Group state
  const [groups, setGroups] = useState<AgentGroup[]>([])
  const [loadingGroups, setLoadingGroups] = useState(false)
  const [overrideGroup, setOverrideGroup] = useState<string | null>(null)

  // Derived: selectedGroup and isOverride are computed from overrideGroup
  const selectedGroup = useMemo(() => {
    if (overrideGroup !== null) return overrideGroup
    if (roomAgentCount > 0) return BUILTIN_GROUP_ROOM_TEAM
    return defaultGroup || BUILTIN_GROUP_ALL_AGENTS
  }, [overrideGroup, roomAgentCount, defaultGroup])

  const isOverride = overrideGroup !== null

  // Modal state
  const [groupManagementOpen, setGroupManagementOpen] = useState(false)
  const [groupAction, setGroupAction] = useState<{
    type: 'create' | 'edit' | 'delete'
    group?: AgentGroup
  } | null>(null)

  // Agent state
  const [availableAgents, setAvailableAgents] = useState<Agent[]>([])
  const [loadingAgents, setLoadingAgents] = useState(false)
  const [agentsError, setAgentsError] = useState<string | null>(null)

  // Load user's groups
  useEffect(() => {
    const loadGroups = async () => {
      if (!userId) return
      setLoadingGroups(true)
      try {
        const response = await listAgentGroups(userId, getToken)
        if (response.success && response.groups) {
          setGroups(response.groups)
        }
      } catch (error) {
        console.error('Failed to load groups:', error)
      } finally {
        setLoadingGroups(false)
      }
    }

    if (isLoaded && userId) {
      loadGroups()
    }
  }, [isLoaded, userId, getToken])

  // Load available agents
  const loadAvailableAgents = useCallback(async () => {
    if (availableAgents.length > 0) return
    setLoadingAgents(true)
    setAgentsError(null)
    try {
      const response = await getAllActiveAgents(undefined, undefined, getToken)
      if (response.success && response.agents) {
        setAvailableAgents(response.agents)
      } else {
        setAgentsError(response.error || 'Failed to load agents')
      }
    } catch (error) {
      console.error('Failed to load agents:', error)
      setAgentsError(error instanceof Error ? error.message : 'Failed to load agents')
    } finally {
      setLoadingAgents(false)
    }
  }, [availableAgents.length, getToken])

  // Load agents on mount for mention suggestions
  useEffect(() => {
    if (isLoaded && userId && availableAgents.length === 0) {
      loadAvailableAgents()
    }
  }, [isLoaded, userId, loadAvailableAgents, availableAgents.length])

  // Refresh groups after changes in modal
  const handleGroupsChange = useCallback(async () => {
    if (!userId) return
    try {
      const response = await listAgentGroups(userId, getToken)
      if (response.success && response.groups) {
        setGroups(response.groups)
      }
    } catch (error) {
      console.error('Failed to refresh groups:', error)
    }
  }, [userId, getToken])

  // Group management entry points
  const handleCreateGroup = useCallback(() => {
    if (!userId) {
      onRequireAuth?.()
      return
    }
    loadAvailableAgents()
    setGroupAction({ type: 'create' })
    setGroupManagementOpen(true)
  }, [userId, onRequireAuth, loadAvailableAgents])

  const handleEditGroup = useCallback((group: AgentGroup) => {
    if (!userId) {
      onRequireAuth?.()
      return
    }
    loadAvailableAgents()
    setGroupAction({ type: 'edit', group })
    setGroupManagementOpen(true)
  }, [userId, onRequireAuth, loadAvailableAgents])

  const handleDeleteGroup = useCallback((group: AgentGroup) => {
    if (!userId) {
      onRequireAuth?.()
      return
    }
    loadAvailableAgents()
    setGroupAction({ type: 'delete', group })
    setGroupManagementOpen(true)
  }, [userId, onRequireAuth, loadAvailableAgents])

  const handleGroupCreated = useCallback((group: AgentGroup) => {
    setGroups(prev => {
      const exists = prev.some(g => g.group_id === group.group_id)
      return exists
        ? prev.map(g => g.group_id === group.group_id ? group : g)
        : [...prev, group]
    })
    setOverrideGroup(group.group_id)

    // Persist to localStorage for room pages
    if (roomId) {
      localStorage.setItem(`room-${roomId}-override-group`, group.group_id)
    }
  }, [roomId])

  // Handle group change (override)
  const handleGroupChange = useCallback((groupId: string) => {
    setOverrideGroup(groupId)

    // Persist to localStorage for room pages
    if (roomId) {
      localStorage.setItem(`room-${roomId}-override-group`, groupId)
    }
  }, [roomId])

  // Handle clear override - revert to derived default
  const handleClearOverride = useCallback(() => {
    setOverrideGroup(null)

    // Clear from localStorage for room pages
    if (roomId) {
      localStorage.removeItem(`room-${roomId}-override-group`)
    }
  }, [roomId])

  const resolvedTargetMode: TargetModeDispatchInput = useMemo(
    () => resolveSelectedGroupDispatch(selectedGroup),
    [selectedGroup],
  )

  return {
    // State
    groups,
    loadingGroups,
    selectedGroup,
    isOverride,
    resolvedTargetMode,
    groupManagementOpen,
    groupAction,
    availableAgents,
    loadingAgents,
    agentsError,
    // Actions
    handleGroupsChange,
    handleCreateGroup,
    handleEditGroup,
    handleDeleteGroup,
    handleGroupCreated,
    handleGroupChange,
    handleClearOverride,
    setGroupManagementOpen,
    setGroupAction,
    loadAvailableAgents,
    setAvailableAgents,
  }
}
