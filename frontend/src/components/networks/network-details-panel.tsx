'use client'

import { useMemo, useState } from 'react'
import { Check, Copy, Pencil, X } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Empty, EmptyDescription } from '@/components/ui/empty'
import { Separator } from '@/components/ui/separator'
import type { NetworkAgent, NetworkDetailsPanelProps } from './types'

const statusLabels: Record<NetworkAgent['status'], string> = {
  active: 'Active',
  inactive: 'Inactive',
  deleted: 'Deleted',
  unavailable: 'Unavailable',
  unknown: 'Unverified',
}

export function NetworkDetailsPanel({ selection, scope, agents, pending, onClose, onManage }: NetworkDetailsPanelProps) {
  const agentsById = useMemo(() => new Map(agents.map(agent => [agent.id, agent])), [agents])
  const subject = selection.type === 'agent' ? selection.agent : selection.network
  const [copyResult, setCopyResult] = useState<{ id: string; copied: boolean } | null>(null)

  async function copyId(id: string) {
    try {
      await navigator.clipboard.writeText(id)
      setCopyResult({ id, copied: true })
    } catch {
      setCopyResult({ id, copied: false })
    }
  }

  return (
    <Card className="network-details gap-4 py-4" role="region" aria-label={selection.type === 'network' ? 'Subnet details' : 'Agent summary'}
      onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); onClose() } }}>
      <div className="network-details-body flex flex-col gap-4">
        <CardHeader className="px-4">
          <CardTitle className="min-w-0 wrap-anywhere" role="heading" aria-level={2}>{subject.name}</CardTitle>
          <CardDescription className="whitespace-pre-wrap wrap-anywhere">
            {subject.description || 'No description yet'}
          </CardDescription>
          <CardAction>
            <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close details"><X aria-hidden="true" /></Button>
          </CardAction>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 px-4">
          {selection.type === 'agent' ? (
            <>
              <div className="flex flex-wrap gap-2">
                <Badge variant={selection.agent.status === 'active' ? 'outline' : 'inactive'}>{statusLabels[selection.agent.status]}</Badge>
                {scope ? <Badge variant="secondary">
                  {scope.agents.includes(selection.agent.id) ? 'In selected subnet' : 'Outside selected scope'}
                </Badge> : null}
              </div>
              <p className="text-xs leading-relaxed text-muted-foreground">
                Registration status does not indicate live connectivity. Open Agents for full details.
              </p>
            </>
          ) : (
            <>
              <div className="flex flex-col gap-1">
                <h3 className="text-xs text-muted-foreground">Subnet ID</h3>
                <div className="flex items-center gap-2">
                  <code className="min-w-0 flex-1 select-text break-all text-xs">{selection.network.group_id}</code>
                  <Button variant="ghost" size="icon" onClick={() => copyId(selection.network.group_id)} aria-label="Copy subnet ID">
                    {copyResult?.id === selection.network.group_id && copyResult.copied ? <Check /> : <Copy />}
                  </Button>
                </div>
                {copyResult?.id === selection.network.group_id ? (
                  copyResult.copied ? <p role="status" className="text-xs text-muted-foreground">Subnet ID copied</p> : (
                    <Alert variant="destructive">
                      <AlertDescription>Unable to copy. Select the ID above and copy it manually.</AlertDescription>
                    </Alert>
                  )
                ) : null}
              </div>
              <h3 className="text-xs font-medium">Members · {selection.network.agents.length}</h3>
              <ul className="network-member-list">
                {selection.network.agents.map((id, index) => {
                  const agent = agentsById.get(id)
                  return (
                    <li key={id}>
                      {index > 0 ? <Separator /> : null}
                      <div className="network-member-row">
                        <div className="min-w-0 flex-1">
                          <p className="text-xs font-medium wrap-anywhere">{agent?.name ?? id}</p>
                          <p className="mt-1 text-xs text-muted-foreground wrap-anywhere">{agent?.skills.join(', ') || 'No skills listed'}</p>
                        </div>
                        <Badge variant={agent?.status === 'active' ? 'outline' : 'inactive'}>{statusLabels[agent?.status ?? 'unknown']}</Badge>
                      </div>
                    </li>
                  )
                })}
              </ul>
              {selection.network.agents.length === 0 ? (
                <Empty className="p-0 md:p-0">
                  <EmptyDescription>No members yet. Add agents through Manage subnet.</EmptyDescription>
                </Empty>
              ) : null}
            </>
          )}
        </CardContent>
      </div>
      {selection.type === 'network' ? (
        <>
          <Separator />
          <CardFooter className="network-details-footer px-4">
            <Button variant="outline" className="w-full" onClick={onManage} disabled={pending}>
              <Pencil data-icon="inline-start" />Manage subnet
            </Button>
          </CardFooter>
        </>
      ) : null}
    </Card>
  )
}
