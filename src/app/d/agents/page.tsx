"use client"

import { useState, useEffect, useMemo, useCallback } from "react"
import { Plus, RefreshCw, Search, Settings, SquareArrowOutUpRight } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useRouter } from "next/navigation"
import { useAuth } from "@clerk/nextjs"
import { banner } from "@/components/ui/banner"
import { getAgentsByProviderId } from "@/lib/api"
import type { Agent } from "@/lib/types"
import { consumerUrl } from "@/lib/urls"

export default function DeveloperAgentsPage() {
  const router = useRouter()
  const { getToken } = useAuth()
  const [myAgents, setMyAgents] = useState<Agent[]>([])
  const [loading, setLoading] = useState(true)
  const [searchTerm, setSearchTerm] = useState("")

  const loadMyAgents = useCallback(async () => {
    try {
      setLoading(true)
      const response = await getAgentsByProviderId(getToken)

      if (response.success && response.agents) {
        setMyAgents(response.agents)
      } else {
        banner.error(response.error || 'Failed to load your agents')
      }
    } catch {
      banner.error('Failed to load your agents')
    } finally {
      setLoading(false)
    }
  }, [getToken])

  useEffect(() => {
    loadMyAgents()
  }, [loadMyAgents])

  const filteredAgents = useMemo(() => {
    if (!searchTerm) return myAgents
    return myAgents.filter(agent =>
      agent.agent_card.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      agent.agent_card.description.toLowerCase().includes(searchTerm.toLowerCase())
    )
  }, [myAgents, searchTerm])

  if (loading && myAgents.length === 0) {
    return (
      <div className="page-loading">
        <div className="flex flex-col items-center justify-center gap-4">
          <RefreshCw className="h-8 w-8 animate-spin text-primary" />
          <span className="text-base font-medium text-muted-foreground">
            Loading Your Agents...
          </span>
        </div>
      </div>
    )
  }

  return (
    <div className="page-container">
      <div className="page-content space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold">My Agents</h1>
            <p className="text-muted-foreground mt-1">Manage your registered agents</p>
          </div>
          <Button
            className="btn-brand-gradient"
            onClick={() => router.push('/register')}
          >
            <Plus className="h-4 w-4 mr-2" />
            Register New Agent
          </Button>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="rounded-lg border border-border/50 bg-background/80 p-4">
            <div className="text-sm text-muted-foreground">Total Agents</div>
            <div className="text-2xl font-semibold text-[hsl(var(--color-hybro-hy))]">{myAgents.length}</div>
          </div>
          <div className="rounded-lg border border-border/50 bg-background/80 p-4">
            <div className="text-sm text-muted-foreground">Active</div>
            <div className="text-2xl font-semibold text-green-600 dark:text-green-400">
              {myAgents.filter(a => a.agent_status === 'active').length}
            </div>
          </div>
          <div className="rounded-lg border border-border/50 bg-background/80 p-4">
            <div className="text-sm text-muted-foreground">Inactive</div>
            <div className="text-2xl font-semibold text-yellow-600 dark:text-yellow-400">
              {myAgents.filter(a => a.agent_status === 'inactive').length}
            </div>
          </div>
        </div>

        {/* Search */}
        {myAgents.length > 0 && (
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search your agents..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="pl-10"
            />
          </div>
        )}

        {/* Agents Table */}
        {filteredAgents.length > 0 ? (
          <div className="rounded-lg border border-border/50 overflow-hidden">
            <table className="w-full table-fixed">
              <thead>
                <tr className="bg-muted/50 border-b border-border/50">
                  <th className="text-left text-xs font-semibold text-muted-foreground uppercase tracking-wider px-3 sm:px-4 py-3 w-[35%]">Agent</th>
                  <th className="text-left text-xs font-semibold text-muted-foreground uppercase tracking-wider px-3 sm:px-4 py-3 w-[15%]">Status</th>
                  <th className="text-left text-xs font-semibold text-muted-foreground uppercase tracking-wider px-3 sm:px-4 py-3 hidden sm:table-cell w-[25%]">Provider</th>
                  <th className="text-center text-xs font-semibold text-muted-foreground uppercase tracking-wider px-3 sm:px-4 py-3 w-[12%]">Manage</th>
                  <th className="text-center text-xs font-semibold text-muted-foreground uppercase tracking-wider px-3 sm:px-4 py-3 w-[13%]">View</th>
                </tr>
              </thead>
              <tbody>
                {filteredAgents.map((agent) => (
                  <tr
                    key={agent.agent_id}
                    className="border-b border-border/30 last:border-0 hover:bg-muted/30 transition-colors cursor-pointer"
                    onClick={() => router.push(`/agents/${agent.agent_id}`)}
                  >
                    <td className="px-3 sm:px-4 py-3 min-w-0">
                      <div className="font-medium truncate">{agent.agent_card.name}</div>
                      <div className="text-xs text-muted-foreground truncate">
                        {agent.agent_card.description}
                      </div>
                    </td>
                    <td className="px-3 sm:px-4 py-3">
                      <Badge
                        variant={agent.agent_status === 'active' ? 'success' : 'inactive'}
                      >
                        {agent.agent_status}
                      </Badge>
                    </td>
                    <td className="px-3 sm:px-4 py-3 hidden sm:table-cell">
                      <span className="text-sm text-muted-foreground truncate block">
                        {agent.agent_card.provider?.organization || 'Unknown'}
                      </span>
                    </td>
                    <td className="px-3 sm:px-4 py-3 text-center">
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8"
                              onClick={(e) => {
                                e.stopPropagation()
                                router.push(`/agents/${agent.agent_id}`)
                              }}
                            >
                              <Settings className="h-4 w-4" />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>Manage</TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    </td>
                    <td className="px-3 sm:px-4 py-3 text-center">
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8"
                              asChild
                              onClick={(e) => e.stopPropagation()}
                            >
                              <a href={consumerUrl(`/agents/${agent.agent_id}`)} target="_blank" rel="noopener noreferrer">
                                <SquareArrowOutUpRight className="h-4 w-4" />
                              </a>
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>View as User</TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-center py-12">
            <div className="text-muted-foreground mb-4">
              {myAgents.length === 0
                ? "You haven't registered any agents yet."
                : "No agents found matching your search."
              }
            </div>
            {myAgents.length === 0 ? (
              <Button onClick={() => router.push('/register')}>
                <Plus className="h-4 w-4 mr-2" />
                Register Your First Agent
              </Button>
            ) : (
              <Button variant="outline" onClick={() => setSearchTerm("")}>
                Clear search
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
