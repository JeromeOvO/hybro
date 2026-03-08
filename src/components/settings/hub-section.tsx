'use client'

import { useAuth } from '@clerk/nextjs'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { House, RefreshCw, Wifi, WifiOff, Terminal } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { SettingsCard } from '@/components/settings/settings-card'
import { getMyHubStatus, type HubStatusResponse } from '@/lib/api/hub'
import { getAllActiveAgents } from '@/lib/api/agent'
import { formatTimestamp } from '@/lib/time'
import type { Agent } from '@/lib/types'

export function HubSection() {
  const { getToken } = useAuth()
  const queryClient = useQueryClient()

  const hubQuery = useQuery<HubStatusResponse>({
    queryKey: ['hub', 'status'],
    staleTime: 1000 * 30,
    queryFn: () => getMyHubStatus(getToken),
  })

  const agentsQuery = useQuery<Agent[]>({
    queryKey: ['agents', 'active'],
    staleTime: 1000 * 60 * 60 * 24,
    queryFn: async () => {
      const res = await getAllActiveAgents(undefined, undefined, getToken)
      return res.agents ?? []
    },
  })

  const hubAgents = (agentsQuery.data ?? []).filter(a => a.source === 'hub')
  const hub = hubQuery.data?.hubs?.[0]
  const isRefreshing = hubQuery.isFetching

  const handleRefresh = () => {
    queryClient.invalidateQueries({ queryKey: ['hub', 'status'] })
    queryClient.invalidateQueries({ queryKey: ['agents', 'active'] })
  }

  if (hubQuery.isLoading) {
    return (
      <SettingsCard title="My Hub" description="Local agent hub status">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <RefreshCw className="h-4 w-4 animate-spin" />
          Loading hub status...
        </div>
      </SettingsCard>
    )
  }

  if (!hub) {
    return (
      <SettingsCard
        title="My Hub"
        description="Run agents locally on your machine for privacy and lower latency"
      >
        <div className="space-y-4">
          <div className="flex items-center gap-3 rounded-lg border border-dashed p-4">
            <House className="h-8 w-8 shrink-0 text-muted-foreground/50" />
            <div className="space-y-1">
              <p className="text-sm font-medium">No hub connected</p>
              <p className="text-xs text-muted-foreground">
                Install and start the Hybro Hub daemon to run agents locally.
              </p>
            </div>
          </div>

          <div className="rounded-lg bg-muted/50 p-3 space-y-2">
            <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <Terminal className="h-3.5 w-3.5" />
              Quick Start
            </div>
            <div className="font-mono text-xs space-y-1 text-muted-foreground">
              <p>pip install hybro-hub</p>
              <p>hybro-hub start --api-key &lt;your-key&gt;</p>
            </div>
            <p className="text-xs text-muted-foreground">
              Generate an API key in{' '}
              <a href="/d/discovery-api-keys" className="underline hover:text-foreground">
                Developer Portal &rarr; API Keys
              </a>
            </p>
          </div>
        </div>
      </SettingsCard>
    )
  }

  const isOnline = hub.is_online

  return (
    <SettingsCard
      title="My Hub"
      description="Local agent hub status"
    >
      <div className="space-y-4">
        {/* Status header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {isOnline ? (
              <>
                <Wifi className="h-4 w-4 text-emerald-500" />
                <span className="text-sm font-medium text-emerald-600 dark:text-emerald-400">
                  Hub Connected
                </span>
              </>
            ) : (
              <>
                <WifiOff className="h-4 w-4 text-amber-500" />
                <span className="text-sm font-medium text-amber-600 dark:text-amber-400">
                  Hub Offline
                </span>
              </>
            )}
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={handleRefresh}
            disabled={isRefreshing}
          >
            <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${isRefreshing ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
        </div>

        {/* Last connected */}
        {hub.last_connected_at && (
          <p className="text-xs text-muted-foreground">
            {isOnline ? 'Connected since' : 'Last seen'}:{' '}
            {formatTimestamp(hub.last_connected_at)}
          </p>
        )}

        {!isOnline && (
          <p className="text-xs text-muted-foreground">
            Start your hub to use local agents.
          </p>
        )}

        {/* Hub agents list */}
        {hubAgents.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
              Local Agents ({hubAgents.length})
            </p>
            <div className="space-y-1.5">
              {hubAgents.map(agent => (
                <div
                  key={agent.agent_id}
                  className={`flex items-center gap-3 rounded-md border px-3 py-2 text-sm ${
                    !isOnline ? 'opacity-50' : ''
                  }`}
                >
                  <House className={`h-3.5 w-3.5 shrink-0 ${
                    isOnline ? 'text-emerald-500' : 'text-muted-foreground/50'
                  }`} />
                  <div className="min-w-0 flex-1">
                    <p className="font-medium truncate">{agent.agent_card.name}</p>
                    {agent.agent_card.description && (
                      <p className="text-xs text-muted-foreground truncate">
                        {agent.agent_card.description}
                      </p>
                    )}
                  </div>
                  <span className={`shrink-0 h-2 w-2 rounded-full ${
                    isOnline ? 'bg-emerald-500' : 'bg-muted-foreground/30'
                  }`} />
                </div>
              ))}
            </div>
          </div>
        )}

        {hubAgents.length === 0 && isOnline && (
          <p className="text-xs text-muted-foreground">
            Hub connected but no agents registered yet.
          </p>
        )}
      </div>
    </SettingsCard>
  )
}
