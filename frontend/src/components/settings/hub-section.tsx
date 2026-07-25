'use client'

import { useAuth } from '@/lib/auth'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { House, RefreshCw, Wifi, WifiOff, Terminal, KeyRound, Download, Play } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { SettingsCard } from '@/components/settings/settings-card'
import { InlineCopyButton } from '@/components/inline-copy-button'
import { getMyHubStatus, type HubStatusResponse } from '@/lib/api/hub'
import { getAllActiveAgents } from '@/lib/api/agent'
import { getAgentAvatarUri } from '@/lib/agent-avatar'
import { formatTimestamp } from '@/lib/time'
import type { Agent } from '@/lib/types'

export function HubSection() {
  const { getToken } = useAuth()
  const queryClient = useQueryClient()

  const hubQuery = useQuery<HubStatusResponse>({
    queryKey: ['hub', 'status'],
    staleTime: 1000 * 15,
    refetchInterval: 1000 * 30,
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
                Install and start the Hybro Hub to run agents locally.
              </p>
            </div>
          </div>

          <div className="rounded-lg bg-muted/50 p-4 space-y-3">
            <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <Terminal className="h-3.5 w-3.5" />
              Setup Guide
            </div>
            <ol className="space-y-2.5 text-xs">
              <li className="flex gap-2.5">
                <KeyRound className="h-3.5 w-3.5 text-primary shrink-0 mt-0.5" />
                <div>
                  <span className="font-medium text-foreground">Create an API key</span>
                  {' — '}
                  <a href="/manage/api-keys" className="text-primary hover:underline">
                    Manage &rarr; API Keys
                  </a>
                </div>
              </li>
              <li className="flex gap-2.5">
                <Download className="h-3.5 w-3.5 text-primary shrink-0 mt-0.5" />
                <div className="flex items-center gap-1.5">
                  <code className="font-mono bg-muted px-1.5 py-0.5 rounded text-foreground">pip install hybro-hub</code>
                  <InlineCopyButton text="pip install hybro-hub" />
                </div>
              </li>
              <li className="flex gap-2.5">
                <Play className="h-3.5 w-3.5 text-primary shrink-0 mt-0.5" />
                <div className="flex items-center gap-1.5">
                  <code className="font-mono bg-muted px-1.5 py-0.5 rounded text-foreground">hybro-hub start --api-key YOUR_KEY</code>
                  <InlineCopyButton text="hybro-hub start --api-key " />
                </div>
              </li>
              <li className="flex gap-2.5">
                <House className="h-3.5 w-3.5 text-primary shrink-0 mt-0.5" />
                <div className="flex items-center gap-1.5">
                  <code className="font-mono bg-muted px-1.5 py-0.5 rounded text-foreground">hybro-hub agent start ollama</code>
                  <InlineCopyButton text="hybro-hub agent start ollama" />
                </div>
              </li>
            </ol>
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
          <div className="rounded-lg bg-muted/50 p-3 space-y-1.5">
            <p className="text-xs font-medium text-foreground">Hub is offline</p>
            <p className="text-xs text-muted-foreground">
              Restart it with:{' '}
              <code className="font-mono bg-muted px-1.5 py-0.5 rounded">hybro-hub start</code>
            </p>
          </div>
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
                  <Avatar className="h-7 w-7 shrink-0 rounded-md">
                    <AvatarImage
                      src={agent.agent_card.iconUrl || getAgentAvatarUri(agent.agent_id)}
                      alt={agent.agent_card.name}
                    />
                    <AvatarFallback className="rounded-md text-[10px]">
                      {agent.agent_card.name?.charAt(0)?.toUpperCase() ?? 'A'}
                    </AvatarFallback>
                  </Avatar>
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
