'use client'

import { useState, useEffect } from 'react'
import { Plus, Pencil, Trash2, Users, Loader2, X } from 'lucide-react'
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
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { AgentSelector } from '@/components/agent-selector'
import { toast } from 'sonner'
import type { Agent } from '@/lib/types/agent'
import type { AgentGroup } from '@/lib/types/agent-group'
import { isBuiltinGroup } from '@/lib/types/agent-group'
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
}

type Mode = 'list' | 'create' | 'edit'

export function GroupManagementModal({
  open,
  onOpenChange,
  groups,
  onGroupsChange,
  availableAgents,
  loadingAgents,
  userId,
  getToken,
}: GroupManagementModalProps) {
  const [mode, setMode] = useState<Mode>('list')
  const [editingGroup, setEditingGroup] = useState<AgentGroup | null>(null)
  const [groupName, setGroupName] = useState('')
  const [groupDescription, setGroupDescription] = useState('')
  const [selectedAgents, setSelectedAgents] = useState<{ [agentId: string]: Agent }>({})
  const [saving, setSaving] = useState(false)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [groupToDelete, setGroupToDelete] = useState<AgentGroup | null>(null)
  const [deleting, setDeleting] = useState(false)

  // Filter out built-in groups for the list
  const userGroups = groups.filter(g => g.type === 'user')

  // Reset form when modal closes
  useEffect(() => {
    if (!open) {
      setMode('list')
      setEditingGroup(null)
      resetForm()
    }
  }, [open])

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

  const handleBack = () => {
    setMode('list')
    setEditingGroup(null)
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
      toast.error('Group name is required')
      return
    }

    if (Object.keys(selectedAgents).length === 0) {
      toast.error('Please select at least one agent')
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
          toast.success('Group created successfully')
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
          toast.success('Group updated successfully')
          onGroupsChange()
          handleBack()
        } else {
          throw new Error(response.error || 'Failed to update group')
        }
      }
    } catch (error) {
      console.error('Failed to save group:', error)
      toast.error(error instanceof Error ? error.message : 'Failed to save group')
    } finally {
      setSaving(false)
    }
  }

  const handleDeleteClick = (group: AgentGroup) => {
    setGroupToDelete(group)
    setDeleteConfirmOpen(true)
  }

  const handleDeleteConfirm = async () => {
    if (!groupToDelete) return

    setDeleting(true)
    try {
      const response = await deleteAgentGroup(groupToDelete.group_id, getToken)

      if (response.success) {
        toast.success('Group deleted successfully')
        onGroupsChange()
        setDeleteConfirmOpen(false)
        setGroupToDelete(null)
      } else {
        throw new Error(response.error || 'Failed to delete group')
      }
    } catch (error) {
      console.error('Failed to delete group:', error)
      toast.error(error instanceof Error ? error.message : 'Failed to delete group')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-[600px] max-h-[85vh] overflow-y-auto">
          {mode === 'list' ? (
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
          ) : (
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
          )}
        </DialogContent>
      </Dialog>

      {/* Delete confirmation dialog */}
      <AlertDialog open={deleteConfirmOpen} onOpenChange={setDeleteConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Group</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete &quot;{groupToDelete?.name}&quot;? This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeleteConfirm}
              disabled={deleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deleting ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Deleting...
                </>
              ) : (
                'Delete'
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

