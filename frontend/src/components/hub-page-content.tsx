"use client"

import { useAuth } from "@/lib/auth"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  House,
  RefreshCw,
  Terminal,
  KeyRound,
  Download,
  Play,
  ExternalLink,
  Activity,
  ChevronRight,
} from "lucide-react"
import Link from "next/link"
import { useEffect, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { InlineCopyButton } from "@/components/inline-copy-button"
import { useHubStatus, HUB_STATUS_QUERY_KEY } from "@/hooks/useHubStatus"
import { getAllActiveAgents } from "@/lib/api/agent"
import { formatTimestamp } from "@/lib/time"
import { getAgentAvatarUri } from "@/lib/agent-avatar"
import { cn } from "@/lib/utils"
import type { Agent } from "@/lib/types"

interface HubPageContentProps {
  apiKeysPath: string
  /** Portal base path used for agent detail links (e.g. "/c" or "/d") */
  basePath: string
}

const MANUAL_REFRESH_SPIN_MS = 600

export function HubPageContent({ apiKeysPath, basePath }: HubPageContentProps) {
  const { getToken } = useAuth()
  const queryClient = useQueryClient()
  const { hub, isOnline, hasHub, isLoading: hubLoading, isFetching: hubFetching } = useHubStatus()
  const [isManualRefreshAnimating, setIsManualRefreshAnimating] = useState(false)
  const manualRefreshTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (manualRefreshTimeoutRef.current) {
        clearTimeout(manualRefreshTimeoutRef.current)
      }
    }
  }, [])

  const agentsQuery = useQuery<Agent[]>({
    queryKey: ["agents", "active"],
    staleTime: 1000 * 60 * 60 * 24,
    queryFn: async () => {
      const res = await getAllActiveAgents(undefined, undefined, getToken)
      return res.agents ?? []
    },
  })

  const hubAgents = (agentsQuery.data ?? []).filter(a => a.source === "hub")
  const isRefreshing = hubLoading || hubFetching || agentsQuery.isFetching || isManualRefreshAnimating

  const handleRefresh = () => {
    setIsManualRefreshAnimating(true)
    if (manualRefreshTimeoutRef.current) {
      clearTimeout(manualRefreshTimeoutRef.current)
    }
    manualRefreshTimeoutRef.current = setTimeout(() => {
      setIsManualRefreshAnimating(false)
      manualRefreshTimeoutRef.current = null
    }, MANUAL_REFRESH_SPIN_MS)
    queryClient.invalidateQueries({ queryKey: HUB_STATUS_QUERY_KEY })
    queryClient.invalidateQueries({ queryKey: ["agents", "active"] })
  }

  return (
    <div className="page-container">
      <div className="page-content space-y-8">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold flex items-center gap-3">
              <House className="h-7 w-7" />
              My Hub
            </h1>
            <p className="text-muted-foreground mt-1">
              Run agents locally on your machine for privacy and lower latency.
            </p>
          </div>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                onClick={handleRefresh}
                disabled={isRefreshing}
                className="transition-all duration-150 ease-out hover:bg-black/10 hover:text-foreground dark:hover:bg-white/15"
              >
                <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${isRefreshing ? "animate-spin" : ""}`} />
                Refresh
              </Button>
            </TooltipTrigger>
            <TooltipContent side="top">Refresh hub status</TooltipContent>
          </Tooltip>
        </div>

        {/* Status Card */}
        <Card>
          <CardContent>
            {hubLoading ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <RefreshCw className="h-4 w-4 animate-spin" />
                Loading hub status...
              </div>
            ) : hasHub ? (
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <div className="flex flex-1 items-center gap-3">
                    {isOnline ? (
                      <>
                        <div className="flex items-center justify-center h-10 w-10 rounded-lg bg-emerald-500/10">
                          <House className="h-5 w-5 text-emerald-500" />
                        </div>
                        <div className="flex min-w-0 flex-1 items-center gap-3">
                          <p className="shrink-0 font-medium text-emerald-600 dark:text-emerald-400">Hub Connected</p>
                          {hub?.last_connected_at && (
                            <p className="ml-auto shrink-0 text-right font-medium text-foreground">
                              Connected since {formatTimestamp(hub.last_connected_at)}
                            </p>
                          )}
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="flex items-center justify-center h-10 w-10 rounded-lg bg-amber-500/10">
                          <House className="h-5 w-5 text-amber-500" />
                        </div>
                        <div className="flex min-w-0 flex-1 items-center gap-3">
                          <p className="shrink-0 font-medium text-amber-600 dark:text-amber-400">Hub Offline</p>
                          {hub?.last_connected_at && (
                            <p className="ml-auto shrink-0 text-right font-medium text-foreground">
                              Last seen {formatTimestamp(hub.last_connected_at)}
                            </p>
                          )}
                        </div>
                      </>
                    )}
                  </div>
                </div>

                {!isOnline && (
                  <div className="rounded-lg bg-muted/50 p-3 space-y-1.5">
                    <p className="text-xs font-medium text-foreground">Restart your hub</p>
                    <div className="flex items-center gap-1.5">
                      <code className="font-mono text-xs bg-muted px-1.5 py-0.5 rounded">
                        hybro-hub start
                      </code>
                      <InlineCopyButton text="hybro-hub start" />
                    </div>
                  </div>
                )}
              </div>
            ) : (
              /* No hub setup state */
              <div className="space-y-6">
                <div className="flex items-center gap-4">
                  <div className="flex items-center justify-center h-12 w-12 rounded-lg border border-dashed border-muted-foreground/30">
                    <House className="h-6 w-6 text-muted-foreground/50" />
                  </div>
                  <div>
                    <p className="font-medium">No hub connected</p>
                    <p className="text-sm text-muted-foreground">
                      Install and start the Hybro Hub to run agents locally.
                    </p>
                  </div>
                </div>

                <div className="rounded-lg bg-muted/50 p-5 space-y-4">
                  <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                    <Terminal className="h-4 w-4" />
                    Setup Guide
                  </div>
                  <ol className="space-y-3 text-sm">
                    <li className="flex gap-3">
                      <KeyRound className="h-4 w-4 text-primary shrink-0 mt-0.5" />
                      <div>
                        <span className="font-medium text-foreground">Create an API key</span>
                        {" — "}
                        <Link href={apiKeysPath} className="text-primary hover:underline inline-flex items-center gap-1">
                          API Keys <ExternalLink className="h-3 w-3" />
                        </Link>
                      </div>
                    </li>
                    <li className="flex gap-3">
                      <Download className="h-4 w-4 text-primary shrink-0 mt-0.5" />
                      <div className="flex items-center gap-1.5">
                        <code className="font-mono bg-muted px-1.5 py-0.5 rounded text-foreground">
                          pip install hybro-hub
                        </code>
                        <InlineCopyButton text="pip install hybro-hub" />
                      </div>
                    </li>
                    <li className="flex gap-3">
                      <Play className="h-4 w-4 text-primary shrink-0 mt-0.5" />
                      <div className="flex items-center gap-1.5">
                        <code className="font-mono bg-muted px-1.5 py-0.5 rounded text-foreground">
                          hybro-hub start --api-key YOUR_KEY
                        </code>
                        <InlineCopyButton text="hybro-hub start --api-key " />
                      </div>
                    </li>
                    <li className="flex gap-3">
                      <House className="h-4 w-4 text-primary shrink-0 mt-0.5" />
                      <div className="flex items-center gap-1.5">
                        <code className="font-mono bg-muted px-1.5 py-0.5 rounded text-foreground">
                          hybro-hub agent start ollama
                        </code>
                        <InlineCopyButton text="hybro-hub agent start ollama" />
                      </div>
                    </li>
                  </ol>
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Local Agents Section */}
        {hubLoading ? (
          <div>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">Local Agents</h2>
            </div>
            <div className="flex items-center justify-center py-8">
              <RefreshCw className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          </div>
        ) : hasHub && (
          <div>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">
                Local Agents
                {hubAgents.length > 0 && (
                  <span className="ml-2 text-sm font-normal text-muted-foreground">
                    ({hubAgents.length})
                  </span>
                )}
              </h2>
            </div>

            {agentsQuery.isLoading ? (
              <div className="flex items-center justify-center py-8">
                <RefreshCw className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : hubAgents.length === 0 ? (
              <Card>
                <CardContent className="pt-6">
                  <p className="text-center text-sm text-muted-foreground py-4">
                    {isOnline
                      ? "Hub connected but no agents registered yet."
                      : "Hub is offline. Start your hub to see local agents."}
                  </p>
                </CardContent>
              </Card>
            ) : (
              <div className="grid gap-3">
                {hubAgents.map(agent => (
                  <Link
                    key={agent.agent_id}
                    href={`${basePath}/agents/${agent.agent_id}`}
                    className={cn(
                      "block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                      !isOnline && "pointer-events-none opacity-50",
                    )}
                  >
                    <Card className="transition-all duration-150 ease-out hover:bg-black/10 dark:hover:bg-white/15">
                      <CardContent className="pt-4 pb-4">
                        <div className="flex items-center gap-3">
                          <Avatar className="h-9 w-9 shrink-0 rounded-lg">
                            <AvatarImage
                              src={agent.agent_card.iconUrl || getAgentAvatarUri(agent.agent_id)}
                              alt={agent.agent_card.name}
                            />
                            <AvatarFallback className="rounded-lg text-xs">
                              {agent.agent_card.name?.charAt(0)?.toUpperCase() ?? "A"}
                            </AvatarFallback>
                          </Avatar>

                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                              <p className="font-medium truncate">{agent.agent_card.name}</p>
                              <span className={cn(
                                "shrink-0 h-2 w-2 rounded-full",
                                isOnline ? "bg-emerald-500" : "bg-muted-foreground/30"
                              )} />
                            </div>
                            {agent.agent_card.description && (
                              <p className="text-xs text-muted-foreground truncate">
                                {agent.agent_card.description}
                              </p>
                            )}
                          </div>

                          {typeof agent.call_count === "number" && agent.call_count > 0 && (
                            <div className="hidden sm:flex items-center gap-1 text-xs text-muted-foreground shrink-0">
                              <Activity className="h-3 w-3" />
                              {agent.call_count}
                            </div>
                          )}

                          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground/50" />
                        </div>
                      </CardContent>
                    </Card>
                  </Link>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
