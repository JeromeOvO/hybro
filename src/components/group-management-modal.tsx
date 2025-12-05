'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import { Plus, Pencil, Trash2, Users, Loader2, AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { AgentSelector } from '@/components/agent-selector'
import { banner } from "@/components/ui/banner"
import type { Agent } from '@/lib/types/agent'
import type { AgentGroup } from '@/lib/types/agent-group'
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
  availableAgents: Agent[]
  loadingAgents: boolean
  userId: string
  getToken?: () => Promise<string | null>
  loadAgents?: () => Promise<void>
  agentsError?: string | null
}

type Mode = 'list' | 'create' | 'edit' | 'delete-confirm'

export function GroupManagementModal({
  open,
  onOpenChange,
  groups,
  onGroupsChange,
  availableAgents,
  loadingAgents,
  userId,
  getToken,
  loadAgents,
  agentsError,
}: GroupManagementModalProps) {
  const [mode, setMode] = useState<Mode>('list')
  const [editingGroup, setEditingGroup] = useState<AgentGroup | null>(null)
  const [groupName, setGroupName] = useState('')
  const [groupDescription, setGroupDescription] = useState('')
  const [selectedAgents, setSelectedAgents] = useState<{ [agentId: string]: Agent }>({})
  const [saving, setSaving] = useState(false)
  const [groupToDelete, setGroupToDelete] = useState<AgentGroup | null>(null)
  const [deleting, setDeleting] = useState(false)
  const didRequestAgents = useRef(false)

  // Filter out built-in groups for the list
  const userGroups = groups.filter(g => g.type === 'user')

  // Reset all state when modal closes and ensure body styles are cleaned up
  useEffect(() => {
    if (!open) {
      setMode('list')
      setEditingGroup(null)
      setGroupToDelete(null)
      resetForm()
      
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
  }, [open])
  
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
  }, [onOpenChange])

  const resetForm = () => {
    setGroupName('')
    setGroupDescription('')
    setSelectedAgents({})
  }

  const handleCreate = () => {
    setMode('create')
    resetForm()
  }

  const handleEdit = (group: AgentGroup) => {
    setMode('edit')
    setEditingGroup(group)
    setGroupName(group.name)
    setGroupDescription(group.description || '')
    
    // Build selected agents from group's agent IDs
    const agentsMap: { [agentId: string]: Agent } = {}
    for (const agentId of group.agents) {
      const agent = availableAgents.find(a => a.agent_id === agentId)
      if (agent) {
        agentsMap[agentId] = agent
      }
    }
    setSelectedAgents(agentsMap)
  }

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
      for (const agentId of editingGroup.agents) {
        const agent = availableAgents.find((a) => a.agent_id === agentId)
        if (agent) {
          agentsMap[agentId] = agent
        }
      }
      setSelectedAgents(agentsMap)
    }
  }, [mode, editingGroup, availableAgents])

  const handleBack = () => {
    setMode('list')
    setEditingGroup(null)
    setGroupToDelete(null)
    resetForm()
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

  const handleSave = async () => {
    if (!groupName.trim()) {
      banner.error('Group name is required')
      return
    }

    if (Object.keys(selectedAgents).length === 0) {
      banner.error('Please select at least one agent')
      return
    }

    setSaving(true)
    try {
      const agentIds = Object.keys(selectedAgents)

      if (mode === 'create') {
        const response = await createAgentGroup({
          name: groupName.trim(),
          description: groupDescription.trim() || undefined,
          owner_id: userId,
          agents: agentIds,
        }, getToken)

        if (response.success) {
          banner.success('Group created successfully')
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

  const handleDeleteClick = (group: AgentGroup) => {
    setGroupToDelete(group)
    setMode('delete-confirm')
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
              <DialogTitle>Manage Agent Groups</DialogTitle>
              <DialogDescription>
                Create and manage your custom agent groups for quick access.
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
                  <p>No custom groups yet</p>
                  <p className="text-sm">Create a group to quickly access your favorite agents</p>
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
                          className="text-destructive hover:text-destructive"
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
      case 'edit':
        return (
          <>
            <DialogHeader>
              <DialogTitle>
                {mode === 'create' ? 'Create New Group' : 'Edit Group'}
              </DialogTitle>
              <DialogDescription>
                {mode === 'create' 
                  ? 'Create a custom group of agents for quick access.'
                  : 'Update your group settings and agents.'
                }
              </DialogDescription>
            </DialogHeader>

            <div className="py-4 space-y-4">
              {/* Group name */}
              <div className="space-y-2">
                <Label htmlFor="group-name">Group Name</Label>
                <Input
                  id="group-name"
                  value={groupName}
                  onChange={(e) => setGroupName(e.target.value)}
                  placeholder="e.g., Research Team"
                />
              </div>

              {/* Group description */}
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

              {/* Agent selector */}
              <div className="space-y-2">
                <Label>Agents</Label>
                <AgentSelector
                  selectedAgents={selectedAgents}
                  onAgentAdd={handleAgentAdd}
                  onAgentRemove={handleAgentRemove}
                  availableAgents={availableAgents}
                  loading={loadingAgents}
                  error={agentsError || undefined}
                  onRetry={loadAgents}
                />
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
                disabled={saving || !groupName.trim() || Object.keys(selectedAgents).length === 0}
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

      case 'delete-confirm':
        return (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <AlertTriangle className="h-5 w-5 text-destructive" />
                Delete Group
              </DialogTitle>
              <DialogDescription>
                Are you sure you want to delete &quot;{groupToDelete?.name}&quot;? This action cannot be undone.
              </DialogDescription>
            </DialogHeader>

            <div className="py-4">
              <div className="p-4 rounded-lg border border-destructive/20 bg-destructive/5">
                <p className="text-sm text-muted-foreground">
                  This will permanently delete the group and remove it from your saved groups. 
                  Any rooms using this group will not be affected.
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
                variant="destructive"
                onClick={handleDeleteConfirm}
                disabled={deleting}
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
      <DialogContent className="sm:max-w-[600px] max-h-[85vh] overflow-y-auto bg-background/95 backdrop-blur-md border border-border/50 shadow-lg">
        {renderContent()}
      </DialogContent>
    </Dialog>
  )
}
