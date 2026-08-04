'use client'

import { useState, useMemo, useCallback } from 'react'
import { Search, X, Users, Check, Plus, Minus, ChevronRight, AlertTriangle } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { deduplicateIcons } from '@/lib/agent-icon-utils'
import { getAgentAvatarUri } from '@/lib/agent-avatar'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
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
import type { Agent } from '@/lib/types/agent'
import type { AgentGroup } from '@/lib/types/agent-group'
import type { StaleAgentRef, AgentAvailability } from '@/lib/types/agent-group'
import type { RoomAgentRefWire } from '@/lib/types/response'

interface RoomDefaultAgentsEditorProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentRoomAgentIds: string[]
  availableAgents: Agent[]
  loadingAgents: boolean
  savedGroups: AgentGroup[]
  resolvedAgents?: RoomAgentRefWire[]
  onSave: (membershipAgentIds: string[]) => Promise<void>
}

function availabilityLabel(a: AgentAvailability): string {
  switch (a) {
    case 'deleted': return 'Deleted'
    case 'inactive': return 'Inactive'
    case 'inaccessible': return 'Unavailable'
    default: return a
  }
}

export function RoomDefaultAgentsEditor({
  open,
  onOpenChange,
  currentRoomAgentIds,
  availableAgents,
  loadingAgents,
  savedGroups,
  resolvedAgents,
  onSave,
}: RoomDefaultAgentsEditorProps) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [staleRefs, setStaleRefs] = useState<StaleAgentRef[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [groupPickerOpen, setGroupPickerOpen] = useState(false)
  const [confirmGroupId, setConfirmGroupId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [initialized, setInitialized] = useState(false)

  // Initialize from currentRoomAgentIds when dialog opens
  const initializeState = useCallback(() => {
    const resolvedMap = new Map<string, { availability: AgentAvailability; name?: string | null }>()
    if (resolvedAgents) {
      for (const ref of resolvedAgents) {
        resolvedMap.set(ref.id, { availability: ref.availability, name: ref.name })
      }
    }

    const catalogIds = new Set(availableAgents.map(a => a.agent_id))
    const active = new Set<string>()
    const stale: StaleAgentRef[] = []

    for (const id of currentRoomAgentIds) {
      if (catalogIds.has(id)) {
        active.add(id)
      } else {
        const resolved = resolvedMap.get(id)
        stale.push({
          id,
          name: resolved?.name || id,
          availability: resolved?.availability ?? 'inaccessible',
        })
      }
    }

    setSelectedIds(active)
    setStaleRefs(stale)
    setSearchQuery('')
    setGroupPickerOpen(false)
    setConfirmGroupId(null)
    setInitialized(true)
  }, [currentRoomAgentIds, availableAgents, resolvedAgents])

  // Re-initialize when dialog opens
  if (open && !initialized) {
    initializeState()
  }
  if (!open && initialized) {
    setInitialized(false)
  }

  const filteredAgents = useMemo(() => {
    const q = searchQuery.toLowerCase().trim()
    const unselected = availableAgents.filter(a => !selectedIds.has(a.agent_id))
    if (!q) return unselected
    return unselected.filter(a =>
      a.agent_card.name.toLowerCase().includes(q) ||
      a.agent_card.description?.toLowerCase().includes(q)
    )
  }, [availableAgents, searchQuery, selectedIds])

  const selectedAgentsList = useMemo(() =>
    availableAgents.filter(a => selectedIds.has(a.agent_id)),
    [availableAgents, selectedIds]
  )

  const userGroups = useMemo(() =>
    savedGroups.filter(g => g.type === 'user'),
    [savedGroups]
  )

  const confirmGroup = useMemo(() =>
    confirmGroupId ? userGroups.find(g => g.group_id === confirmGroupId) : null,
    [confirmGroupId, userGroups]
  )

  const toggleAgent = useCallback((agentId: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(agentId)) {
        next.delete(agentId)
      } else {
        next.add(agentId)
      }
      return next
    })
  }, [])

  const handleRemoveStaleRef = useCallback((id: string) => {
    setStaleRefs(prev => prev.filter(r => r.id !== id))
  }, [])

  const handleApplyGroup = useCallback(() => {
    if (!confirmGroup) return
    const catalogIds = new Set(availableAgents.map(a => a.agent_id))
    const active = new Set<string>()
    const stale: StaleAgentRef[] = []

    for (const agentId of confirmGroup.agents) {
      if (catalogIds.has(agentId)) {
        active.add(agentId)
      } else {
        stale.push({ id: agentId, name: agentId, availability: 'inaccessible' })
      }
    }

    setSelectedIds(active)
    setStaleRefs(stale)
    setConfirmGroupId(null)
    setGroupPickerOpen(false)
  }, [confirmGroup, availableAgents])

  const handleSave = useCallback(async () => {
    setSaving(true)
    try {
      const mergedIds = [...selectedIds, ...staleRefs.map(r => r.id)]
      await onSave(mergedIds)
    } finally {
      setSaving(false)
    }
  }, [selectedIds, staleRefs, onSave])

  const totalSelected = selectedIds.size + staleRefs.length

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col bg-background/95 backdrop-blur-md border shadow-lg p-0">
          <DialogHeader className="px-6 pt-6 pb-2">
            <DialogTitle className="flex items-center gap-2">
              <Users className="h-5 w-5" />
              Room Default Agents
              {totalSelected > 0 && (
                <Badge variant="secondary" className="ml-1">{totalSelected}</Badge>
              )}
            </DialogTitle>
            <DialogDescription>Choose which agents are available by default in this room</DialogDescription>
          </DialogHeader>

          <div className="px-6 pb-2">
            <div className="space-y-3">
              {/* Selected agents section */}
              {selectedAgentsList.length > 0 && (
                <div className="space-y-1.5">
                  <span className="text-sm text-muted-foreground">
                    Selected Agents ({selectedAgentsList.length})
                  </span>
                  <div className="grid grid-cols-2 gap-1.5">
                    {selectedAgentsList.map(agent => {
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
                          onClick={() => toggleAgent(agent.agent_id)}
                        >
                          <div className="relative flex-shrink-0">
                            <Avatar className="h-8 w-8 rounded-md shadow-sm shadow-primary/10 dark:shadow-white/5">
                              <AvatarImage src={agent.agent_card.iconUrl || undefined} alt={agent.agent_card.name} className="rounded-md" />
                              <AvatarFallback className="rounded-md text-xs p-0 overflow-hidden">
                                {/* eslint-disable-next-line @next/next/no-img-element */}
                                <img src={getAgentAvatarUri(agent.agent_id)} alt={agent.agent_card.name} className="h-full w-full" />
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
                            className="flex-shrink-0 p-1 rounded transition-colors duration-150 text-destructive hover:bg-destructive/10"
                            onClick={(e) => { e.stopPropagation(); toggleAgent(agent.agent_id) }}
                          >
                            <Minus className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}

              {/* Stale / unavailable members */}
              {staleRefs.length > 0 && (
                <div className="space-y-1.5">
                  <span className="text-xs text-muted-foreground flex items-center gap-1">
                    <AlertTriangle className="h-3 w-3" />
                    Unavailable Members (preserved on save)
                  </span>
                  <div className="grid grid-cols-2 gap-1.5">
                    {staleRefs.map(ref => (
                      <div
                        key={ref.id}
                        className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg
                                   border border-dashed border-muted-foreground/30
                                   bg-muted/30 opacity-60"
                      >
                        <Avatar className="h-8 w-8 rounded-md">
                          <AvatarFallback className="rounded-md text-xs p-0 overflow-hidden">
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img src={getAgentAvatarUri(ref.id)} alt={ref.name} className="h-full w-full" />
                          </AvatarFallback>
                        </Avatar>
                        <div className="flex flex-col min-w-0 flex-1">
                          <span className="text-[13px] text-muted-foreground truncate">{ref.name}</span>
                          <span className="text-[11px] text-muted-foreground/70">
                            {availabilityLabel(ref.availability)}
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
            </div>
          </div>

          <div className="flex-1 overflow-y-auto px-6 min-h-0">
            {/* Available agents section */}
            <div className="space-y-1.5">
              <span className="text-sm text-muted-foreground">Available Agents</span>
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
                <Input
                  placeholder="Search agents..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="pl-8 h-8 text-sm"
                />
              </div>
              {loadingAgents ? (
                <div className="text-center py-6 text-muted-foreground text-sm">Loading agents...</div>
              ) : filteredAgents.length === 0 ? (
                <div className="text-center py-6 text-muted-foreground text-sm">
                  {availableAgents.length === 0
                    ? 'No agents available'
                    : searchQuery.trim()
                      ? 'No agents match your search'
                      : 'All agents selected'}
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-1.5 max-h-52 overflow-y-auto pr-0.5 pb-2">
                  {filteredAgents.map(agent => {
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
                        onClick={() => toggleAgent(agent.agent_id)}
                      >
                        <div className="relative flex-shrink-0">
                          <Avatar className="h-8 w-8 rounded-md shadow-sm shadow-primary/10 dark:shadow-white/5">
                            <AvatarImage src={agent.agent_card.iconUrl || undefined} alt={agent.agent_card.name} className="rounded-md" />
                            <AvatarFallback className="rounded-md text-xs p-0 overflow-hidden">
                              {/* eslint-disable-next-line @next/next/no-img-element */}
                              <img src={getAgentAvatarUri(agent.agent_id)} alt={agent.agent_card.name} className="h-full w-full" />
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
                          className="flex-shrink-0 p-1 rounded transition-colors duration-150 text-primary hover:bg-primary/10"
                          onClick={(e) => { e.stopPropagation(); toggleAgent(agent.agent_id) }}
                        >
                          <Plus className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>

            {/* Group picker panel */}
            {groupPickerOpen && (
              <div className="border-t pt-3 mt-2">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium">Apply from Saved Team</span>
                  <button
                    onClick={() => setGroupPickerOpen(false)}
                    className="text-muted-foreground hover:text-foreground"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
                {userGroups.length === 0 ? (
                  <p className="text-sm text-muted-foreground py-4 text-center">No saved teams</p>
                ) : (
                  <div className="space-y-1 max-h-40 overflow-y-auto">
                    {userGroups.map(group => (
                      <button
                        key={group.group_id}
                        type="button"
                        onClick={() => setConfirmGroupId(group.group_id)}
                        className="flex items-center gap-3 w-full p-2 rounded-lg hover:bg-muted/50 text-left transition-colors"
                      >
                        <Users className="h-4 w-4 text-muted-foreground shrink-0" />
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-medium truncate">{group.name}</div>
                          <div className="text-xs text-muted-foreground">
                            {group.agents.length} agent{group.agents.length !== 1 ? 's' : ''}
                          </div>
                        </div>
                        <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0" />
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          <DialogFooter className="px-6 py-4 border-t gap-2 sm:gap-2">
            {!groupPickerOpen && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setGroupPickerOpen(true)}
                className="mr-auto"
              >
                <Users className="h-3.5 w-3.5 mr-1.5" />
                Use Saved Team
              </Button>
            )}
            <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={handleSave} disabled={saving || totalSelected === 0}>
              {saving ? 'Saving...' : (
                <>
                  <Check className="h-4 w-4 mr-1.5" />
                  Save ({totalSelected})
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Confirm group apply dialog */}
      <AlertDialog open={confirmGroupId !== null} onOpenChange={(open) => { if (!open) setConfirmGroupId(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Replace current selection?</AlertDialogTitle>
            <AlertDialogDescription>
              This will replace your current agent selection with the agents from
              {confirmGroup ? ` "${confirmGroup.name}"` : ' this team'}.
              You can still make changes before saving.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleApplyGroup}>Replace</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
