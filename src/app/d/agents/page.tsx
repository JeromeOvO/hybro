"use client"

import { useState, useEffect, useMemo, useCallback } from "react"
import { Plus, RefreshCw, Search, ExternalLink } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
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
      <div className="flex items-center justify-center min-h-[85vh]">
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
    <div className="px-4 sm:px-6 py-8">
      <div className="w-full max-w-4xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold">My Agents</h1>
            <p className="text-muted-foreground mt-1">Manage your registered agents</p>
          </div>
          <Button
            className="bg-linear-to-r from-[hsl(var(--color-hybro-bro-strong))] to-[hsl(var(--color-hybro-hy-strong))] hover:from-[hsl(var(--color-hybro-bro))] hover:to-[hsl(var(--color-hybro-hy))] text-white font-semibold"
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
            <table className="w-full">
              <thead>
                <tr className="bg-muted/50 border-b border-border/50">
                  <th className="text-left text-xs font-semibold text-muted-foreground uppercase tracking-wider px-4 py-3">Agent</th>
                  <th className="text-left text-xs font-semibold text-muted-foreground uppercase tracking-wider px-4 py-3">Status</th>
                  <th className="text-left text-xs font-semibold text-muted-foreground uppercase tracking-wider px-4 py-3 hidden sm:table-cell">Provider</th>
                  <th className="text-right text-xs font-semibold text-muted-foreground uppercase tracking-wider px-4 py-3">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredAgents.map((agent) => (
                  <tr
                    key={agent.agent_id}
                    className="border-b border-border/30 last:border-0 hover:bg-muted/30 transition-colors cursor-pointer"
                    onClick={() => router.push(`/agents/${agent.agent_id}`)}
                  >
                    <td className="px-4 py-3">
                      <div className="font-medium">{agent.agent_card.name}</div>
                      <div className="text-xs text-muted-foreground truncate max-w-[300px]">
                        {agent.agent_card.description}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <Badge
                        variant="outline"
                        className={
                          agent.agent_status === 'active'
                            ? 'bg-green-50 text-green-700 border-green-200 dark:bg-green-900/30 dark:text-green-300 dark:border-green-800'
                            : 'bg-gray-50 text-gray-700 border-gray-200 dark:bg-gray-800/50 dark:text-gray-400 dark:border-gray-700'
                        }
                      >
                        {agent.agent_status}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 hidden sm:table-cell">
                      <span className="text-sm text-muted-foreground">
                        {agent.agent_card.provider?.organization || 'Unknown'}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={(e) => {
                            e.stopPropagation()
                            router.push(`/agents/${agent.agent_id}`)
                          }}
                        >
                          Manage
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          asChild
                          onClick={(e) => e.stopPropagation()}
                        >
                          <a href={consumerUrl(`/agents/${agent.agent_id}`)} target="_blank" rel="noopener noreferrer">
                            <ExternalLink className="h-3.5 w-3.5" />
                          </a>
                        </Button>
                      </div>
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
