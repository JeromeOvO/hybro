'use client'

import Link from 'next/link'
import type { ReactNode } from 'react'
import { ChevronRight, RefreshCw } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { StatusIndicator } from '@/components/status-indicator'
import { MiniTopology } from './mini-topology'
import { getAgentAvatarUri } from '@/lib/agent-avatar'
import { cn } from '@/lib/utils'
import { useNetworks } from '@/hooks/useNetworks'
import { useRoomHistory } from '@/hooks/useRoomHistory'
import { useMcpStatus } from '@/hooks/useMcpStatus'
import { useUser } from '@/lib/auth'
import { routes } from '@/lib/routes'
import { agentStatusView, gatewayStatusView, roomStatusView } from '@/lib/status'
import { deriveAttention, overviewMetrics, topAgentsByRoomCalls, type AttentionItem } from '@/lib/overview'

const ROW = 'flex min-h-12 items-center justify-between gap-3 rounded-lg px-2 py-2 hover:bg-muted'

type Tone = 'primary' | 'brand' | 'online' | 'offline' | 'warning'
const TONE: Record<Tone, string> = {
  primary: 'text-primary',
  brand: 'text-hybro-bro',
  online: 'text-status-online',
  offline: 'text-status-offline',
  warning: 'text-status-warning',
}

function Tile({ label, value, detail, tone }: { label: string; value: ReactNode; detail: ReactNode; tone: Tone }) {
  return (
    <div className="rounded-xl border bg-card p-4">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={cn('mt-1 text-2xl font-semibold tabular-nums', TONE[tone])}>{value}</p>
      <div className="mt-1 text-xs text-muted-foreground">{detail}</div>
    </div>
  )
}

function SectionTitle({ id, children }: { id: string; children: ReactNode }) {
  return <h2 id={id} className="text-[15px] font-semibold">{children}</h2>
}

function attentionRow(item: AttentionItem) {
  switch (item.kind) {
    case 'room-input':
      return { href: routes.room(item.roomId), label: `${item.title} needs input`, status: roomStatusView('awaiting_input'), detail: 'Room' }
    case 'gateway':
      return { href: routes.connectMcp, label: 'MCP gateway is not ready', status: gatewayStatusView(item.status), detail: 'Connect' }
    case 'agent-offline':
      return { href: routes.networkWith(`select=${encodeURIComponent(`agent:${item.agentId}`)}`), label: `${item.name} is offline`, status: agentStatusView(item.status), detail: 'Agent' }
    case 'unassigned':
      return { href: routes.networkWith('view=structure&scope=unassigned'), label: item.count === 1 ? '1 agent is in no subnet' : `${item.count} agents are in no subnet`, status: null, detail: 'Assign' }
  }
}

export function OverviewPage() {
  const { user, isLoaded, isSignedIn } = useUser()
  const data = useNetworks()
  const history = useRoomHistory({ userId: user?.id ?? '', enabled: Boolean(isLoaded && isSignedIn && user?.id) })
  const gateway = useMcpStatus()
  const rooms = history.data?.items ?? []
  const loading = data.loading || data.agentsLoading || history.isLoading
  const metrics = overviewMetrics({ agents: data.agents, networks: data.networks, rooms })
  const attention = deriveAttention({ agents: data.agents, networks: data.networks, rooms, gateway: gateway.status })
  const failed = data.error || data.agentsError || (history.isError ? "Rooms couldn't load." : null)
  const recentRooms = [...rooms]
    .sort((a, b) => Date.parse(b.last_activity_at) - Date.parse(a.last_activity_at))
    .slice(0, 5)

  return (
    <div className="page-container">
      <div className="page-content flex flex-col gap-6">
        <h1 className="sr-only">Overview</h1>
        {failed ? (
          <Alert variant="destructive">
            <AlertDescription className="flex flex-wrap items-center gap-2">
              <span>{failed}</span>
              <Button variant="secondary" size="sm" onClick={() => { void data.refresh() }}><RefreshCw data-icon="inline-start" aria-hidden="true" />Retry</Button>
            </AlertDescription>
          </Alert>
        ) : null}
        {history.dataUpdatedAt ? (
          <p className="text-xs text-muted-foreground">
            {`Updated ${new Date(history.dataUpdatedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`}
          </p>
        ) : null}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {loading ? Array.from({ length: 4 }, (_, index) => <Skeleton key={index} className="h-[104px] rounded-xl" />) : (
            <>
              <Tile label="Agents" tone="primary" value={metrics.agents.total} detail={<span><span className="text-status-online">{metrics.agents.online} online</span> · {metrics.agents.offline} offline</span>} />
              <Tile label="Subnets" tone="brand" value={metrics.subnets.total} detail={`${metrics.subnets.unassigned} unassigned agents`} />
              <Tile label="Gateway" tone={gateway.status === 'ready' ? 'online' : 'offline'} value={<span className="text-base">MCP</span>} detail={<StatusIndicator view={gatewayStatusView(gateway.status)} />} />
              <Tile
                label="Rooms"
                tone={metrics.rooms.needsInput ? 'warning' : 'primary'}
                value={metrics.rooms.total}
                detail={<span>{metrics.rooms.running} running · <span className={metrics.rooms.needsInput ? 'font-medium text-status-warning' : undefined}>{metrics.rooms.needsInput} need input</span></span>}
              />
            </>
          )}
        </div>
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
          <section aria-labelledby="attention-heading" className="flex flex-col gap-2">
            <SectionTitle id="attention-heading">Needs attention</SectionTitle>
            {attention.length === 0 && !loading ? <p className="flex items-center gap-2 text-sm text-muted-foreground"><span aria-hidden="true" className="size-2 rounded-full bg-status-online" />Everything is running</p> : (
              <ul>
                {attention.map(item => {
                  const row = attentionRow(item)
                  return (
                    <li key={item.key}>
                      <Link href={row.href} className={ROW}>
                        <span className="flex min-w-0 items-center gap-2">
                          {row.status ? <StatusIndicator view={row.status} hideLabel /> : <span aria-hidden="true" className="size-2 rounded-full bg-status-offline" />}
                          <span className="truncate text-sm">{row.label}</span>
                        </span>
                        <span className="flex items-center gap-1 text-xs text-muted-foreground">{row.detail}<ChevronRight className="size-4" aria-hidden="true" /></span>
                      </Link>
                    </li>
                  )
                })}
              </ul>
            )}
          </section>
          <section aria-labelledby="map-heading" className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <SectionTitle id="map-heading">Network map</SectionTitle>
              <Button variant="ghost" size="sm" asChild><Link href={routes.network}>Open network<ChevronRight aria-hidden="true" /></Link></Button>
            </div>
            <Link href={routes.network} aria-label="Open the network map" className="rounded-xl border bg-card p-2 hover:bg-muted">
              <MiniTopology networks={data.networks} agents={data.agents} />
            </Link>
          </section>
        </div>
        <section aria-labelledby="rooms-heading" className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <SectionTitle id="rooms-heading">Recent rooms</SectionTitle>
            <Button variant="ghost" size="sm" asChild><Link href={routes.chat}>All rooms<ChevronRight aria-hidden="true" /></Link></Button>
          </div>
          {recentRooms.length === 0 && !loading ? <p className="text-sm text-muted-foreground">Start a room to try several agents at once.</p> : (
            <ul className="rounded-xl border bg-card p-1">
              {recentRooms.map(room => {
                const status = roomStatusView(room.status)
                return (
                  <li key={room.room_id}>
                    <Link href={routes.room(room.room_id)} className={ROW}>
                      <span className="truncate text-sm font-medium">{room.title}</span>
                      <span className="flex shrink-0 items-center gap-3 text-xs text-muted-foreground">
                        {status ? <StatusIndicator view={status} /> : null}
                        <ChevronRight className="size-4" aria-hidden="true" />
                      </span>
                    </Link>
                  </li>
                )
              })}
            </ul>
          )}
        </section>
        <section aria-labelledby="agents-heading" className="flex flex-col gap-2">
          <SectionTitle id="agents-heading">Most-called agents</SectionTitle>
          {topAgentsByRoomCalls(data.agents, 5).length === 0 && !loading ? <p className="text-sm text-muted-foreground">No room calls yet.</p> : (
            <ul className="rounded-xl border bg-card p-1">
              {topAgentsByRoomCalls(data.agents, 5).map(agent => (
                <li key={agent.id}>
                  <Link href={routes.networkAgent(agent.id)} className={ROW}>
                    <span className="flex min-w-0 items-center gap-3">
                      <Avatar className="size-8 rounded-md">
                        <AvatarImage src={agent.iconUrl || getAgentAvatarUri(agent.id)} alt="" />
                        <AvatarFallback className="rounded-md bg-muted text-[11px]">{agent.name.slice(0, 2).toUpperCase()}</AvatarFallback>
                      </Avatar>
                      <span className="truncate text-sm font-medium">{agent.name}</span>
                    </span>
                    <span className="flex shrink-0 items-center gap-3 text-xs text-muted-foreground tabular-nums">
                      <span className="text-sm font-medium text-foreground">{agent.roomCalls}</span>
                      <ChevronRight className="size-4" aria-hidden="true" />
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  )
}
