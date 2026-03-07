'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import { Plus, Minus, Pencil, Trash2, Users, Loader2, AlertTriangle, Bot, Search, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { deduplicateIcons } from '@/components/agent-card'
import { banner } from "@/components/ui/banner"
import type { Agent } from '@/lib/types/agent'
import type { AgentGroup, StaleAgentRef } from '@/lib/types/agent-group'
import {
  createAgentGroup,
  updateAgentGroup,
  deleteAgentGroup,
} from '@/lib/api/agent-group'

interface GroupManagementModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  groups: AgentGroup[]
  onGroupsChange: () => void
  onGroupCreated?: (group: AgentGroup) => void
  availableAgents: Agent[]
  loadingAgents: boolean
  userId: string
  getToken?: () => Promise<string | null>
  loadAgents?: () => Promise<void>
  agentsError?: string | null
  initialAction?: {
    type: 'create' | 'edit' | 'delete'
    group?: AgentGroup
  }
}

type Mode = 'list' | 'create' | 'edit' | 'delete-confirm'

export function GroupManagementModal({
  open,
  onOpenChange,
  groups,
  onGroupsChange,
  onGroupCreated,
  availableAgents,
  loadingAgents,
  userId,
  getToken,
  loadAgents,
  agentsError,
  initialAction,
}: GroupManagementModalProps) {
  const [mode, setMode] = useState<Mode>('list')
  const [editingGroup, setEditingGroup] = useState<AgentGroup | null>(null)
  const [groupName, setGroupName] = useState('')
  const [groupDescription, setGroupDescription] = useState('')
  const [selectedAgents, setSelectedAgents] = useState<{ [agentId: string]: Agent }>({})
  const [staleAgentRefs, setStaleAgentRefs] = useState<StaleAgentRef[]>([])
  const [saving, setSaving] = useState(false)
  const [groupToDelete, setGroupToDelete] = useState<AgentGroup | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [agentSearch, setAgentSearch] = useState('')
  const didRequestAgents = useRef(false)
  const lastActionKeyRef = useRef<string | null>(null)

  // Filter out built-in groups for the list
  const userGroups = groups.filter(g => g.type === 'user')

  const resetForm = useCallback(() => {
    setGroupName('')
    setGroupDescription('')
    setSelectedAgents({})
    setStaleAgentRefs([])
    setAgentSearch('')
  }, [])

  const handleCreate = useCallback(() => {
    setMode('create')
    resetForm()
  }, [resetForm])

  const handleEdit = useCallback((group: AgentGroup) => {
    setMode('edit')
    setEditingGroup(group)
    setGroupName(group.name)
    setGroupDescription(group.description || '')
    
    const agentsMap: { [agentId: string]: Agent } = {}
    const staleRefs: StaleAgentRef[] = []
    for (const agentId of group.agents) {
      const agent = availableAgents.find(a => a.agent_id === agentId)
      if (agent) {
        agentsMap[agentId] = agent
      } else {
        staleRefs.push({
          id: agentId,
          name: `Agent ...${agentId.slice(-6)}`,
          availability: "inaccessible",
        })
      }
    }
    setSelectedAgents(agentsMap)
    setStaleAgentRefs(staleRefs)
  }, [availableAgents])

  const handleDeleteClick = useCallback((group: AgentGroup) => {
    setGroupToDelete(group)
    setMode('delete-confirm')
  }, [])

  // Reset all state when modal closes and ensure body styles are cleaned up
  useEffect(() => {
    if (!open) {
      setMode('list')
      setEditingGroup(null)
      setGroupToDelete(null)
      resetForm()
      lastActionKeyRef.current = null
      
      // Force cleanup of any stuck body styles from Radix UI
      // This fixes issues where nested portals (HoverCards) can prevent proper cleanup
      const cleanup = () => {
        document.body.style.pointerEvents = ''
        document.body.style.overflow = ''
      }
      // Run cleanup after animation completes (200ms is the dialog animation duration)
      const timeoutId = setTimeout(cleanup, 250)
      return () => clearTimeout(timeoutId)
    }
  }, [open, resetForm])
  
  // Safe close handler that ensures proper state reset
  const handleOpenChange = useCallback((newOpen: boolean) => {
    if (!newOpen) {
      // Reset state before closing to ensure clean unmount
      setMode('list')
      setEditingGroup(null)
      setGroupToDelete(null)
      resetForm()
    }
    onOpenChange(newOpen)
  }, [onOpenChange, resetForm])

  // Apply initial action when the modal is opened from external triggers
  useEffect(() => {
    if (!open || !initialAction) return

    const key = `${initialAction.type}:${initialAction.group?.group_id || ''}`
    if (lastActionKeyRef.current === key) return
    lastActionKeyRef.current = key

    if (initialAction.type === 'create') {
      handleCreate()
    } else if (initialAction.type === 'edit' && initialAction.group) {
      handleEdit(initialAction.group)
    } else if (initialAction.type === 'delete' && initialAction.group) {
      handleDeleteClick(initialAction.group)
    }
  }, [open, initialAction, handleCreate, handleEdit, handleDeleteClick])

  // Ensure agents are loaded when modal opens
  useEffect(() => {
    if (open && availableAgents.length === 0 && !loadingAgents && !didRequestAgents.current) {
      didRequestAgents.current = true
      loadAgents?.().finally(() => {
        didRequestAgents.current = false
      })
    }
  }, [open, availableAgents.length, loadingAgents, loadAgents])

  // Ensure agents are loaded when entering create/edit if still missing
  useEffect(() => {
    if ((mode === 'create' || mode === 'edit') && availableAgents.length === 0 && !loadingAgents && !didRequestAgents.current) {
      didRequestAgents.current = true
      loadAgents?.().finally(() => {
        didRequestAgents.current = false
      })
    }
  }, [mode, availableAgents.length, loadingAgents, loadAgents])

  // When editing and agents arrive later, repopulate selected agents
  useEffect(() => {
    if (mode === 'edit' && editingGroup && availableAgents.length > 0) {
      const agentsMap: { [agentId: string]: Agent } = {}
      const staleRefs: StaleAgentRef[] = []
      for (const agentId of editingGroup.agents) {
        const agent = availableAgents.find((a) => a.agent_id === agentId)
        if (agent) {
          agentsMap[agentId] = agent
        } else {
          staleRefs.push({
            id: agentId,
            name: `Agent ...${agentId.slice(-6)}`,
            availability: "inaccessible",
          })
        }
      }
      setSelectedAgents(agentsMap)
      setStaleAgentRefs(staleRefs)
    }
  }, [mode, editingGroup, availableAgents])

  const handleBack = () => {
    handleOpenChange(false)
  }

  const handleAgentAdd = (agent: Agent) => {
    setSelectedAgents(prev => ({
      ...prev,
      [agent.agent_id]: agent
    }))
  }

  const handleAgentRemove = (agentId: string) => {
    setSelectedAgents(prev => {
      const newAgents = { ...prev }
      delete newAgents[agentId]
      return newAgents
    })
  }

  const handleRemoveStaleRef = (agentId: string) => {
    setStaleAgentRefs(prev => prev.filter(r => r.id !== agentId))
  }

  const handleSave = async () => {
    if (!groupName.trim()) {
      banner.error('Group name is required')
      return
    }

    const activeCount = Object.keys(selectedAgents).length
    if (activeCount === 0 && staleAgentRefs.length === 0) {
      banner.error('Please select at least one agent')
      return
    }

    setSaving(true)
    try {
      const agentIds = [
        ...Object.keys(selectedAgents),
        ...staleAgentRefs.map(r => r.id),
      ]

      if (mode === 'create') {
        const response = await createAgentGroup({
          name: groupName.trim(),
          description: groupDescription.trim() || undefined,
          owner_id: userId,
          agents: agentIds,
        }, getToken)

        if (response.success) {
          banner.success('Group created successfully')
          if (response.group) {
            onGroupCreated?.(response.group)
          }
          onGroupsChange()
          handleBack()
        } else {
          throw new Error(response.error || 'Failed to create group')
        }
      } else if (mode === 'edit' && editingGroup) {
        const response = await updateAgentGroup({
          group_id: editingGroup.group_id,
          name: groupName.trim(),
          description: groupDescription.trim() || undefined,
          agents: agentIds,
        }, getToken)

        if (response.success) {
          banner.success('Group updated successfully')
          onGroupsChange()
          handleBack()
        } else {
          throw new Error(response.error || 'Failed to update group')
        }
      }
    } catch (error) {
      console.error('Failed to save group:', error)
      banner.error(error instanceof Error ? error.message : 'Failed to save group')
    } finally {
      setSaving(false)
    }
  }

  const handleDeleteConfirm = async () => {
    if (!groupToDelete) return

    setDeleting(true)
    try {
      const response = await deleteAgentGroup(groupToDelete.group_id, getToken)

      if (response.success) {
        banner.success('Group deleted successfully')
        onGroupsChange()
        handleBack()
      } else {
        throw new Error(response.error || 'Failed to delete group')
      }
    } catch (error) {
      console.error('Failed to delete group:', error)
      banner.error(error instanceof Error ? error.message : 'Failed to delete group')
    } finally {
      setDeleting(false)
    }
  }

  // Render content based on current mode
  const renderContent = () => {
    switch (mode) {
      case 'list':
        return (
          <>
            <DialogHeader>
              <DialogTitle>Manage Saved Groups</DialogTitle>
              <DialogDescription>
                Create and manage reusable agent groups. Seed a room from a saved group or use it as a send-time override.
              </DialogDescription>
            </DialogHeader>

            <div className="py-4 space-y-4">
              {/* Create button */}
              <Button onClick={handleCreate} className="w-full gap-2">
                <Plus className="h-4 w-4" />
                Create New Group
              </Button>

              {/* Groups list */}
              {userGroups.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground">
                  <Users className="h-12 w-12 mx-auto mb-3 opacity-50" />
                  <p>No saved groups yet</p>
                  <p className="text-sm">Create a saved group to seed rooms or use as a send-time override</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {userGroups.map(group => (
                    <div
                      key={group.group_id}
                      className="flex items-center justify-between p-3 rounded-lg border bg-card hover:bg-accent/50 transition-colors"
                    >
                      <div className="flex-1 min-w-0">
                        <h4 className="font-medium truncate">{group.name}</h4>
                        <p className="text-sm text-muted-foreground">
                          {group.agents.length} agent{group.agents.length !== 1 ? 's' : ''}
                        </p>
                      </div>
                      <div className="flex items-center gap-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleEdit(group)}
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleDeleteClick(group)}
                          className="text-amber-600 hover:text-amber-700 hover:bg-amber-50"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )

      case 'create':
      case 'edit': {
        const selectedAgentsList = Object.values(selectedAgents)
        const uniqueAgents = Array.from(
          new Map(availableAgents.map(a => [a.agent_id, a])).values()
        )
        const unselectedAgents = uniqueAgents.filter(a => !selectedAgents[a.agent_id])
        const searchLower = agentSearch.toLowerCase().trim()
        const filteredUnselected = searchLower
          ? unselectedAgents.filter(a =>
              a.agent_card.name.toLowerCase().includes(searchLower) ||
              (a.agent_card.description?.toLowerCase().includes(searchLower))
            )
          : unselectedAgents

        const renderAgentRow = (agent: Agent, action: 'add' | 'remove') => {
          const isActive = agent.agent_status === 'active'
          const allModes = [
            ...(agent.agent_card.defaultInputModes ?? []),
            ...(agent.agent_card.defaultOutputModes ?? []),
          ]
          const modeIcons = deduplicateIcons(allModes)

          return (
            <div
              key={agent.agent_id}
              className="group flex items-center gap-2 px-2.5 py-1.5 rounded-lg cursor-pointer
                         transition-all duration-200 ease-out
                         border border-primary/15 dark:border-primary/10
                         hover:border-primary/60 dark:hover:border-primary/50
                         hover:bg-secondary/50 dark:hover:bg-muted/40
                         bg-secondary/30 dark:bg-muted/20"
              onClick={() => action === 'add' ? handleAgentAdd(agent) : handleAgentRemove(agent.agent_id)}
            >
              <div className="relative flex-shrink-0">
                <Avatar className="h-8 w-8 rounded-md shadow-sm shadow-primary/10 dark:shadow-white/5">
                  <AvatarImage src={agent.agent_card.iconUrl || undefined} alt={agent.agent_card.name} className="rounded-md" />
                  <AvatarFallback className="rounded-md text-xs">
                    <Bot className="h-4 w-4" />
                  </AvatarFallback>
                </Avatar>
                <span
                  className={`absolute -bottom-0.5 -right-0.5 h-1.5 w-1.5 rounded-full border border-background
                              ${isActive ? 'bg-green-500' : 'bg-muted-foreground/30'}`}
                />
              </div>

              <div className="flex flex-col justify-center gap-0 min-w-0 flex-1">
                <span className="text-[13px] font-medium leading-tight truncate
                                 group-hover:text-primary transition-colors duration-200">
                  {agent.agent_card.name}
                </span>
                {agent.agent_card.description && (
                  <p className="text-[11px] text-muted-foreground line-clamp-1 leading-snug">
                    {agent.agent_card.description}
                  </p>
                )}
                {modeIcons.length > 0 && (
                  <div className="flex items-center gap-1">
                    {modeIcons.map((Icon, i) => (
                      <Icon key={i} className="h-2.5 w-2.5 shrink-0 text-muted-foreground" />
                    ))}
                  </div>
                )}
              </div>

              <button
                type="button"
                className={`flex-shrink-0 p-1 rounded transition-colors duration-150 ${
                  action === 'remove'
                    ? 'text-destructive hover:bg-destructive/10'
                    : 'text-primary hover:bg-primary/10'
                }`}
                onClick={(e) => {
                  e.stopPropagation()
                  if (action === 'add') {
                    handleAgentAdd(agent)
                  } else {
                    handleAgentRemove(agent.agent_id)
                  }
                }}
              >
                {action === 'remove'
                  ? <Minus className="h-3.5 w-3.5" />
                  : <Plus className="h-3.5 w-3.5" />
                }
              </button>
            </div>
          )
        }

        return (
          <>
            <DialogHeader>
              <DialogTitle>
                {mode === 'create' ? 'Create New Group' : 'Edit Group'}
              </DialogTitle>
            </DialogHeader>

            <div className="py-4 space-y-4">
              <div className="space-y-2">
                <Label htmlFor="group-name">Group Name</Label>
                <Input
                  id="group-name"
                  value={groupName}
                  onChange={(e) => setGroupName(e.target.value)}
                  placeholder="e.g., Research Team"
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="group-description">Description (optional)</Label>
                <Textarea
                  id="group-description"
                  value={groupDescription}
                  onChange={(e) => setGroupDescription(e.target.value)}
                  placeholder="What is this group for?"
                  rows={2}
                />
              </div>

              <div className="space-y-3">
                {selectedAgentsList.length > 0 && (
                  <div className="space-y-1.5">
                    <Label className="text-muted-foreground">
                      Selected Agents ({selectedAgentsList.length})
                    </Label>
                    <div className="grid grid-cols-2 gap-1.5">
                      {selectedAgentsList.map(agent => renderAgentRow(agent, 'remove'))}
                    </div>
                  </div>
                )}

                {staleAgentRefs.length > 0 && (
                  <div className="space-y-1.5">
                    <Label className="text-muted-foreground text-xs">
                      Unavailable members (preserved on save)
                    </Label>
                    <div className="grid grid-cols-2 gap-1.5">
                      {staleAgentRefs.map(ref => (
                        <div
                          key={ref.id}
                          className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg
                                     border border-dashed border-muted-foreground/30
                                     bg-muted/30 opacity-60"
                        >
                          <Avatar className="h-8 w-8 rounded-md">
                            <AvatarFallback className="rounded-md text-xs">
                              <Bot className="h-4 w-4" />
                            </AvatarFallback>
                          </Avatar>
                          <div className="flex flex-col min-w-0 flex-1">
                            <span className="text-[13px] text-muted-foreground truncate">
                              {ref.name}
                            </span>
                            <span className="text-[11px] text-muted-foreground/70">
                              {ref.availability === "deleted" ? "Deleted" :
                               ref.availability === "inactive" ? "Inactive" : "Unavailable"}
                            </span>
                          </div>
                          <button
                            type="button"
                            onClick={() => handleRemoveStaleRef(ref.id)}
                            className="rounded-full p-0.5 text-muted-foreground/70 hover:text-destructive hover:bg-destructive/10 transition-colors shrink-0"
                            aria-label={`Remove ${ref.name}`}
                          >
                            <X className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                <div className="space-y-1.5">
                  <Label className="text-muted-foreground">Available Agents</Label>
                  <div className="relative">
                    <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
                    <Input
                      value={agentSearch}
                      onChange={(e) => setAgentSearch(e.target.value)}
                      placeholder="Search agents..."
                      className="pl-8 h-8 text-sm"
                    />
                  </div>
                  {loadingAgents ? (
                    <div className="text-center py-6 text-muted-foreground flex items-center justify-center">
                      <Loader2 className="h-4 w-4 animate-spin mr-2" />
                      Loading agents...
                    </div>
                  ) : agentsError ? (
                    <div className="p-3 rounded-lg border border-destructive/20 bg-destructive/10 text-destructive text-sm">
                      {agentsError}
                      {loadAgents && (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={loadAgents}
                          className="ml-2 h-auto p-1 text-destructive hover:text-destructive"
                        >
                          Retry
                        </Button>
                      )}
                    </div>
                  ) : filteredUnselected.length === 0 ? (
                    <div className="text-center py-6 text-muted-foreground text-sm">
                      {uniqueAgents.length === 0
                        ? 'No agents available'
                        : searchLower
                          ? 'No agents match your search'
                          : 'All agents selected'}
                    </div>
                  ) : (
                    <div className="grid grid-cols-2 gap-1.5 max-h-52 overflow-y-auto pr-0.5">
                      {filteredUnselected.map(agent => renderAgentRow(agent, 'add'))}
                    </div>
                  )}
                </div>
              </div>
            </div>

            <DialogFooter className="gap-2 sm:gap-0">
              <Button
                variant="outline"
                onClick={handleBack}
                disabled={saving}
              >
                Back
              </Button>
              <Button
                onClick={handleSave}
                disabled={saving || !groupName.trim() || (Object.keys(selectedAgents).length === 0 && staleAgentRefs.length === 0)}
              >
                {saving ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Saving...
                  </>
                ) : (
                  mode === 'create' ? 'Create Group' : 'Save Changes'
                )}
              </Button>
            </DialogFooter>
          </>
        )
      }

      case 'delete-confirm':
        return (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2 text-amber-700">
                <AlertTriangle className="h-5 w-5 text-amber-600" />
                Delete Group
              </DialogTitle>
              <DialogDescription>
                Are you sure you want to delete &quot;{groupToDelete?.name}&quot;? This action cannot be undone.
              </DialogDescription>
            </DialogHeader>

            <div className="py-4">
              <div className="p-4 rounded-lg border border-destructive/20 bg-destructive/5">
                <p className="text-sm text-muted-foreground">
                  This will permanently delete the saved group. 
                  Rooms that were seeded from this group keep their snapshot and are not affected.
                </p>
              </div>
            </div>

            <DialogFooter className="gap-2 sm:gap-0">
              <Button
                variant="outline"
                onClick={handleBack}
                disabled={deleting}
              >
                Cancel
              </Button>
              <Button
                variant="default"
                onClick={handleDeleteConfirm}
                disabled={deleting}
                className="bg-amber-600 hover:bg-amber-700 text-white"
              >
                {deleting ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Deleting...
                  </>
                ) : (
                  'Delete Group'
                )}
              </Button>
            </DialogFooter>
          </>
        )
    }
  }

  // Don't render the dialog content when closed to ensure clean unmount of nested portals
  if (!open) {
    return null
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[720px] max-h-[85vh] overflow-y-auto bg-background backdrop-blur-md border border-border/50 shadow-lg">
        {renderContent()}
      </DialogContent>
    </Dialog>
  )
}
