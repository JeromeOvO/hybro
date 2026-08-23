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
  /** Group ID shown when no override is active. */
  defaultGroup?: string
  /** Persisted source-team name used while the team catalog is unavailable. */
  defaultGroupName?: string
  /** Dispatch scope used when no explicit override is active. */
  defaultTargetMode?: TargetModeDispatchInput
  /** Room ID for localStorage persistence (room page only) */
  roomId?: string
  /** Called when an action requires authentication but user is not signed in */
  onRequireAuth?: () => void
}

interface GroupManagementState {
  // Group state
  groups: AgentGroup[]
  loadingGroups: boolean
  selectedGroup: string
  selectedGroupName?: string
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
  handleGroupChange: (groupId: string, groupName?: string) => void
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
  const {
    userId,
    getToken,
    isLoaded,
    defaultGroup,
    defaultGroupName,
    defaultTargetMode,
    roomId,
    onRequireAuth,
  } = options

  // Group state
  const [groups, setGroups] = useState<AgentGroup[]>([])
  const [loadingGroups, setLoadingGroups] = useState(false)
  const [groupsLoadStatus, setGroupsLoadStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [overrideGroup, setOverrideGroup] = useState<string | null>(null)
  const [overrideGroupName, setOverrideGroupName] = useState<string | null>(null)

  useEffect(() => {
    if (!roomId) {
      setOverrideGroup(null)
      setOverrideGroupName(null)
      return
    }

    setOverrideGroup(localStorage.getItem(`room-${roomId}-override-group`))
    setOverrideGroupName(localStorage.getItem(`room-${roomId}-override-group-name`))
  }, [roomId])

  const groupExists = useCallback(
    (groupId: string) => groups.some(group => group.type === 'user' && group.group_id === groupId),
    [groups],
  )

  // Do not mistake an unloaded or unavailable catalog for a deleted team.
  // Only a successful catalog response can invalidate a source team or override.
  const validateGroup = useCallback((groupId: string | undefined) => {
    if (!groupId || groupId === BUILTIN_GROUP_ROOM_TEAM) return BUILTIN_GROUP_ALL_AGENTS
    if (groupId === BUILTIN_GROUP_ALL_AGENTS) return BUILTIN_GROUP_ALL_AGENTS
    if (groupsLoadStatus !== 'success') return groupId
    return groupExists(groupId) ? groupId : BUILTIN_GROUP_ALL_AGENTS
  }, [groupExists, groupsLoadStatus])

  const validatedDefaultGroup = validateGroup(defaultGroup)
  const validatedOverrideGroup = overrideGroup === null
    ? null
    : validateGroup(overrideGroup)
  const isOverride = overrideGroup !== null
    && validatedOverrideGroup === overrideGroup
  const selectedGroup = isOverride ? overrideGroup : validatedDefaultGroup
  const selectedGroupRecord = groups.find(group => group.group_id === selectedGroup)
  const selectedGroupName = selectedGroupRecord?.name
    ?? (isOverride && selectedGroup !== BUILTIN_GROUP_ALL_AGENTS
      ? overrideGroupName ?? 'Selected Team'
      : selectedGroup === defaultGroup
        ? defaultGroupName
        : undefined)

  const clearOverrideState = useCallback(() => {
    setOverrideGroup(null)
    setOverrideGroupName(null)
    if (roomId) {
      localStorage.removeItem(`room-${roomId}-override-group`)
      localStorage.removeItem(`room-${roomId}-override-group-name`)
    }
  }, [roomId])

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
      setGroupsLoadStatus('loading')
      try {
        const response = await listAgentGroups(userId, getToken)
        if (response.success && response.groups) {
          setGroups(response.groups)
          setGroupsLoadStatus('success')
        } else {
          setGroupsLoadStatus('error')
        }
      } catch (error) {
        console.error('Failed to load groups:', error)
        setGroupsLoadStatus('error')
      } finally {
        setLoadingGroups(false)
      }
    }

    if (isLoaded && userId) {
      loadGroups()
    }
  }, [isLoaded, userId, getToken])

  // Remove stale persisted overrides only after the catalog confirms deletion.
  useEffect(() => {
    if (
      groupsLoadStatus !== 'success'
      || overrideGroup === null
      || overrideGroup === BUILTIN_GROUP_ALL_AGENTS
      || overrideGroup === BUILTIN_GROUP_ROOM_TEAM
      || groupExists(overrideGroup)
    ) {
      return
    }
    clearOverrideState()
  }, [clearOverrideState, groupExists, groupsLoadStatus, overrideGroup])

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
        setGroupsLoadStatus('success')
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
    setOverrideGroupName(group.name)

    // Persist to localStorage for room pages
    if (roomId) {
      localStorage.setItem(`room-${roomId}-override-group`, group.group_id)
      localStorage.setItem(`room-${roomId}-override-group-name`, group.name)
    }
  }, [roomId])

  // Handle group change (override)
  const handleGroupChange = useCallback((groupId: string, groupName?: string) => {
    const isConfirmedMissing = groupsLoadStatus === 'success'
      && groupId !== BUILTIN_GROUP_ALL_AGENTS
      && groupId !== BUILTIN_GROUP_ROOM_TEAM
      && !groupExists(groupId)
    if (groupId === validatedDefaultGroup || isConfirmedMissing) {
      clearOverrideState()
      return
    }

    const resolvedName = groupName
      ?? groups.find(group => group.group_id === groupId)?.name
      ?? null
    setOverrideGroup(groupId)
    setOverrideGroupName(resolvedName)

    // Persist to localStorage for room pages
    if (roomId) {
      localStorage.setItem(`room-${roomId}-override-group`, groupId)
      if (resolvedName) {
        localStorage.setItem(`room-${roomId}-override-group-name`, resolvedName)
      } else {
        localStorage.removeItem(`room-${roomId}-override-group-name`)
      }
    }
  }, [clearOverrideState, groupExists, groups, groupsLoadStatus, roomId, validatedDefaultGroup])

  // Handle clear override - revert to derived default
  const handleClearOverride = clearOverrideState

  const resolvedTargetMode: TargetModeDispatchInput = useMemo(() => {
    if (!isOverride) {
      return defaultTargetMode ?? resolveSelectedGroupDispatch(selectedGroup)
    }
    return resolveSelectedGroupDispatch(selectedGroup)
  }, [defaultTargetMode, isOverride, selectedGroup])

  return {
    // State
    groups,
    loadingGroups,
    selectedGroup,
    selectedGroupName,
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
