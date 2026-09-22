'use client'

import { useMemo, useState } from 'react'
import { ArrowRight, Check, Copy, Minus, Pencil, Plus, Trash2, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import type { NetworkAgent, NetworkDetailsPanelProps } from './types'

const statusLabels: Record<NetworkAgent['status'], string> = {
  active: 'Active',
  inactive: 'Inactive',
  deleted: 'Deleted',
  unavailable: 'Unavailable',
  unknown: 'Unverified'
}

export function NetworkDetailsPanel({
  selection,
  networks,
  agents,
  pending,
  onClose,
  onEdit,
  onAddAgents,
  onDelete,
  onRemoveAgent,
  onSelectNetwork,
  onSelectAgent
}: NetworkDetailsPanelProps) {
  const agentsById = useMemo(() => new Map(agents.map((agent) => [agent.id, agent])), [agents])
  const subject = selection.type === 'agent' ? selection.agent : selection.network
  const memberships =
    selection.type === 'agent' ? networks.filter((network) => network.agents.includes(selection.agent.id)) : []
  const [copyResult, setCopyResult] = useState<{ id: string; copied: boolean } | null>(null)
  const copied = selection.type === 'network' && copyResult?.id === selection.network.group_id && copyResult.copied
  const copyFailed = selection.type === 'network' && copyResult?.id === selection.network.group_id && !copyResult.copied

  async function copyId(id: string) {
    try {
      await navigator.clipboard.writeText(id)
      setCopyResult({ id, copied: true })
    } catch {
      setCopyResult({ id, copied: false })
    }
  }

  return (
    <section className="network-details flex flex-col" aria-label="Selection details">
      <div className="flex items-start justify-between gap-3 p-4">
        <div className="min-w-0 flex-1">
          <h2 className="text-base font-semibold wrap-anywhere">{subject.name}</h2>
          <p className="mt-2 text-xs leading-relaxed whitespace-pre-wrap text-muted-foreground wrap-anywhere">
            {subject.description || 'No description yet'}
          </p>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close details">
          <X aria-hidden="true" />
        </Button>
      </div>
      <Separator />
      <div className="network-details-body flex min-h-0 flex-1 flex-col overflow-y-auto">
        {selection.type === 'agent' ? (
          <div className="flex flex-col gap-4 p-4">
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-xs font-medium">Registration status</h3>
              <Badge variant={selection.agent.status === 'active' ? 'outline' : 'inactive'}>
                {statusLabels[selection.agent.status]}
              </Badge>
            </div>
            <p className="text-xs leading-relaxed text-muted-foreground">
              {selection.agent.status === 'unknown'
                ? 'This agent’s details could not be verified.'
                : selection.agent.status === 'deleted' || selection.agent.status === 'unavailable'
                  ? 'This agent is currently unavailable.'
                  : 'Registration status does not indicate live connectivity.'}
            </p>
            <div className="flex flex-col gap-2">
              <h3 className="text-xs font-medium">Agent ID</h3>
              <code className="break-all text-xs text-muted-foreground">{selection.agent.id}</code>
            </div>
            <h3 className="text-xs font-medium">Networks · {memberships.length}</h3>
            {memberships.map((network) => (
              <div key={network.group_id} className="flex items-center gap-1">
                <Button
                  variant="outline"
                  className="min-w-0 flex-1 justify-between"
                  onClick={() => onSelectNetwork(network.group_id)}
                  title={network.name}
                >
                  <span className="min-w-0 truncate">{network.name}</span>
                  <ArrowRight data-icon="inline-end" aria-hidden="true" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  disabled={pending}
                  onClick={() => onRemoveAgent(network.group_id, selection.agent.id)}
                  aria-label={`Remove from ${network.name}`}
                >
                  <Minus aria-hidden="true" />
                </Button>
              </div>
            ))}
            <p className="text-xs leading-relaxed text-muted-foreground">
              {memberships.length
                ? 'One agent node is shared by all of these networks.'
                : 'This agent is not in any network.'}
            </p>
          </div>
        ) : (
          <>
            <div className="flex flex-col gap-2 p-4">
              <h3 className="text-xs font-medium">Network ID</h3>
              <div className="flex items-center gap-2">
                <code className="min-w-0 flex-1 select-text break-all text-xs text-muted-foreground">
                  {selection.network.group_id}
                </code>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => copyId(selection.network.group_id)}
                  aria-label="Copy network ID"
                >
                  {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
                </Button>
              </div>
              {copied ? (
                <p role="status" className="text-xs text-muted-foreground">
                  Network ID copied
                </p>
              ) : null}
              {copyFailed ? (
                <p role="alert" className="text-xs text-destructive">
                  Unable to copy. Select and copy the network ID manually.
                </p>
              ) : null}
            </div>
            <Separator />
            <div className="flex flex-col gap-2 p-4">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-xs font-medium">Agents · {selection.network.agents.length}</h3>
                <Button variant="ghost" size="icon" onClick={onAddAgents} disabled={pending} aria-label="Add agents">
                  <Plus aria-hidden="true" />
                </Button>
              </div>
              {selection.network.agents.map((id) => {
                const member = agentsById.get(id)
                const name = member?.name ?? id
                const status = member?.status ?? 'unknown'
                return (
                  <div key={id} className="flex items-center gap-1">
                    <Button
                      variant="ghost"
                      className="h-auto min-h-9 min-w-0 flex-1 justify-start py-2"
                      onClick={() => onSelectAgent(id)}
                      title={name}
                    >
                      <span className="size-1.5 shrink-0 rounded-full bg-muted-foreground" aria-hidden="true" />
                      <span className="flex min-w-0 flex-1 flex-col items-start gap-1">
                        <span className="w-full truncate text-left">{name}</span>
                        {status !== 'active' ? <Badge variant="inactive">{statusLabels[status]}</Badge> : null}
                      </span>
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => onRemoveAgent(selection.network.group_id, id)}
                      disabled={pending}
                      aria-label={`Remove ${name}`}
                    >
                      <Minus aria-hidden="true" />
                    </Button>
                  </div>
                )
              })}
              {selection.network.agents.length === 0 ? (
                <p className="py-2 text-xs leading-relaxed text-muted-foreground">
                  No members yet. Use + to add your first agent.
                </p>
              ) : null}
            </div>
          </>
        )}
      </div>
      {selection.type === 'network' ? (
        <>
          <Separator />
          <div className="network-details-footer flex items-center justify-between gap-2 p-4">
            <Button variant="outline" size="sm" onClick={onEdit} disabled={pending}>
              <Pencil data-icon="inline-start" aria-hidden="true" />
              Edit
            </Button>
            <Button variant="ghost" size="icon" onClick={onDelete} disabled={pending} aria-label="Delete network">
              <Trash2 aria-hidden="true" />
            </Button>
          </div>
        </>
      ) : null}
    </section>
  )
}
