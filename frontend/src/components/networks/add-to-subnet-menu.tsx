'use client'

import { useState } from 'react'
import { ChevronDown, Plus, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { toast } from 'sonner'
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import type { AgentGroup } from '@/lib/types/agent-group'

export interface AddToSubnetMenuProps {
  agentId: string
  networks: readonly AgentGroup[]
  pending: boolean
  onSetMembers: (networkId: string, agentIds: string[]) => Promise<unknown>
  onCreateWithAgent: () => void
}

export function AddToSubnetMenu({ agentId, networks, pending, onSetMembers, onCreateWithAgent }: AddToSubnetMenuProps) {
  const [failed, setFailed] = useState<{ networkId: string; add: boolean } | null>(null)
  const failedNetwork = failed ? networks.find(network => network.group_id === failed.networkId) : undefined

  async function toggle(networkId: string, add: boolean) {
    // Read members at click time so a refreshed cache is never overwritten with stale membership.
    const network = networks.find(item => item.group_id === networkId)
    if (!network) return
    const next = add ? [...network.agents.filter(id => id !== agentId), agentId] : network.agents.filter(id => id !== agentId)
    setFailed(null)
    try {
      await onSetMembers(networkId, next)
      toast.success(add ? `Added to ${network.name}` : `Removed from ${network.name}`)
    } catch {
      setFailed({ networkId, add })
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="secondary" size="sm">
          <Plus data-icon="inline-start" aria-hidden="true" />
          Add to subnet
          <ChevronDown aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-56">
        {networks.map(network => {
          const member = network.agents.includes(agentId)
          return (
            <DropdownMenuCheckboxItem
              key={network.group_id}
              checked={member}
              disabled={pending}
              onCheckedChange={() => { void toggle(network.group_id, !member) }}
            >
              {network.name}
            </DropdownMenuCheckboxItem>
          )
        })}
        {failed && failedNetwork ? (
          <DropdownMenuItem onSelect={() => { void toggle(failed.networkId, failed.add) }} className="text-destructive">
            <RefreshCw aria-hidden="true" />
            {`Couldn't update ${failedNetwork.name}. Retry`}
          </DropdownMenuItem>
        ) : null}
        {networks.length ? <DropdownMenuSeparator /> : null}
        <DropdownMenuItem onSelect={onCreateWithAgent}>Create subnet…</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
