'use client'

import { useEffect, useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { banner } from '@/components/ui/banner'
import { createAgentGroup, deleteAgentGroup, updateAgentGroup } from '@/lib/api/agent-group'
import { projectAgent } from '@/lib/network-agent'
import type { Agent } from '@/lib/types/agent'
import type { AgentGroup, AgentGroupResponse } from '@/lib/types/agent-group'
import { SubnetDialog } from './subnet-dialog'
import { SubnetDeleteDialog } from './subnet-delete-dialog'

export interface SubnetDialogHostProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  action: { type: 'create' | 'edit' | 'delete'; group?: AgentGroup } | null
  availableAgents: Agent[]
  loadingAgents: boolean
  agentsError?: string | null
  loadAgents?: () => Promise<void>
  userId: string
  getToken?: () => Promise<string | null>
  onSaved: (group: AgentGroup, kind: 'create' | 'edit') => void
  onDeleted: (group: AgentGroup) => void
}

function confirmed(response: AgentGroupResponse): AgentGroup {
  if (!response.success || !response.group) throw new Error(response.error || "The server didn't confirm the change.")
  return response.group
}

export function SubnetDialogHost(props: SubnetDialogHostProps) {
  const { open, onOpenChange, action, availableAgents, loadingAgents, agentsError, loadAgents, userId, getToken, onSaved, onDeleted } = props
  const queryClient = useQueryClient()
  const agents = useMemo(() => availableAgents.map(projectAgent), [availableAgents])
  const [pending, setPending] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  useEffect(() => { if (!open) setDeleteError(null) }, [open])
  // Keep the Network workspace cache in step with writes made from rooms.
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['networks'] })

  if (!open || !action) return null

  if (action.type === 'delete' && action.group) {
    const subnet = action.group
    return (
      <SubnetDeleteDialog
        subnet={subnet}
        pending={pending}
        error={deleteError}
        onCancel={() => onOpenChange(false)}
        onConfirm={async () => {
          setPending(true)
          setDeleteError(null)
          try {
            const response = await deleteAgentGroup(subnet.group_id, getToken)
            if (!response.success) throw new Error(response.error || "The server didn't confirm the subnet was deleted.")
            await invalidate()
            onDeleted(subnet)
            banner.success('Subnet deleted')
            onOpenChange(false)
          } catch (error) {
            setDeleteError(error instanceof Error ? error.message : "Couldn't delete this subnet. Try again.")
          } finally {
            setPending(false)
          }
        }}
      />
    )
  }

  const network = action.type === 'edit' ? action.group : undefined
  return (
    <SubnetDialog
      key={network?.group_id ?? 'create'}
      open
      network={network}
      agents={agents}
      agentsLoading={loadingAgents}
      agentsError={agentsError ?? null}
      pending={pending}
      onOpenChange={onOpenChange}
      onRetryAgents={() => { void loadAgents?.() }}
      onSave={async values => {
        setPending(true)
        try {
          const membersChanged = !network || values.agentIds.length !== network.agents.length ||
            values.agentIds.some(id => !network.agents.includes(id))
          const saved = confirmed(network
            ? await updateAgentGroup({ group_id: network.group_id, name: values.name, description: values.description, ...(membersChanged ? { agents: values.agentIds } : {}) }, getToken)
            : await createAgentGroup({ owner_id: userId, name: values.name, description: values.description, agents: values.agentIds }, getToken))
          await invalidate()
          onSaved(saved, network ? 'edit' : 'create')
          banner.success(network ? 'Subnet updated' : 'Subnet created')
          onOpenChange(false)
        } finally {
          setPending(false)
        }
      }}
    />
  )
}
