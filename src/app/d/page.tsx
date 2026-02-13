"use client"

import { useState, useEffect, useCallback } from "react"
import { useUser, useAuth } from "@clerk/nextjs"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  ArrowRight,
  Shield,
  BookOpen,
  Plus,
  Bot,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  Settings,
  SquareArrowOutUpRight,
} from "lucide-react"
import { useRouter } from "next/navigation"
import { getAgentsByProviderId } from "@/lib/api"
import type { Agent } from "@/lib/types"
import { consumerUrl } from "@/lib/urls"
import { DeveloperDocsContent } from "@/components/developer-docs-content"

// Authenticated dashboard view
function DeveloperDashboard() {
  const router = useRouter()
  const { user } = useUser()
  const { getToken } = useAuth()
  const [myAgents, setMyAgents] = useState<Agent[]>([])
  const [loading, setLoading] = useState(true)
  const [agentPage, setAgentPage] = useState(0)
  const AGENTS_PER_PAGE = 5

  const loadMyAgents = useCallback(async () => {
    try {
      setLoading(true)
      const response = await getAgentsByProviderId(getToken)
      if (response.success && response.agents) {
        setMyAgents(response.agents)
      }
    } catch {
      // silent
    } finally {
      setLoading(false)
    }
  }, [getToken])

  useEffect(() => {
    loadMyAgents()
  }, [loadMyAgents])

  const activeCount = myAgents.filter(a => a.agent_status === 'active').length
  const totalPages = Math.ceil(myAgents.length / AGENTS_PER_PAGE)
  const paginatedAgents = myAgents.slice(
    agentPage * AGENTS_PER_PAGE,
    (agentPage + 1) * AGENTS_PER_PAGE
  )

  return (
    <div className="page-container">
      <div className="page-content space-y-8">
        {/* Welcome */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold">
              Welcome back{user?.firstName ? `, ${user.firstName}` : ''}
            </h1>
            <p className="text-muted-foreground mt-1">
              Manage your agents and build on the HYBRO network.
            </p>
          </div>
          <Button
            className="btn-brand-gradient"
            onClick={() => router.push('/register')}
          >
            <Plus className="h-4 w-4 mr-2" />
            Register New Agent
          </Button>
        </div>

        {/* Quick Stats */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <Card>
            <CardContent className="pt-6">
              <div className="text-sm text-muted-foreground">Total Agents</div>
              <div className="text-3xl font-bold text-[hsl(var(--color-hybro-hy))]">
                {loading ? <RefreshCw className="h-6 w-6 animate-spin" /> : myAgents.length}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <div className="text-sm text-muted-foreground">Active</div>
              <div className="text-3xl font-bold text-green-600 dark:text-green-400">
                {loading ? <RefreshCw className="h-6 w-6 animate-spin" /> : activeCount}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <div className="text-sm text-muted-foreground">Inactive</div>
              <div className="text-3xl font-bold text-yellow-600 dark:text-yellow-400">
                {loading ? <RefreshCw className="h-6 w-6 animate-spin" /> : myAgents.length - activeCount}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Quick Actions */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Button variant="outline" className="h-auto py-4 flex flex-col gap-2" onClick={() => router.push('/docs')}>
            <BookOpen className="h-5 w-5" />
            <span>View Docs</span>
          </Button>
          <Button variant="outline" className="h-auto py-4 flex flex-col gap-2" onClick={() => router.push('/inspector')}>
            <Shield className="h-5 w-5 text-amber-500 dark:text-amber-400" />
            <span>Open Inspector</span>
          </Button>
        </div>

        {/* My Agents Summary */}
        <div>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <Bot className="h-5 w-5" />
              My Agents
            </h2>
            <Button variant="ghost" size="sm" onClick={() => router.push('/agents')}>
              View All <ArrowRight className="ml-1 h-3.5 w-3.5" />
            </Button>
          </div>
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <RefreshCw className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : myAgents.length === 0 ? (
            <div className="text-center py-12">
              <div className="text-muted-foreground mb-4">
                You haven&apos;t registered any agents yet.
              </div>
              <Button onClick={() => router.push('/register')}>
                <Plus className="h-4 w-4 mr-2" />
                Register Your First Agent
              </Button>
            </div>
          ) : (
            <>
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
                    {paginatedAgents.map((agent) => (
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
                            variant="outline"
                            className={
                              agent.agent_status === 'active'
                                ? 'badge-success'
                                : 'badge-inactive'
                            }
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
              {totalPages > 1 && (
                <div className="flex items-center justify-between pt-3">
                  <p className="text-xs text-muted-foreground">
                    {agentPage * AGENTS_PER_PAGE + 1}-{Math.min((agentPage + 1) * AGENTS_PER_PAGE, myAgents.length)} of {myAgents.length} agents
                  </p>
                  <div className="flex items-center gap-1">
                    <Button
                      variant="outline"
                      size="icon"
                      className="h-7 w-7"
                      disabled={agentPage === 0}
                      onClick={() => setAgentPage(p => p - 1)}
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="outline"
                      size="icon"
                      className="h-7 w-7"
                      disabled={agentPage >= totalPages - 1}
                      onClick={() => setAgentPage(p => p + 1)}
                    >
                      <ChevronRight className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

export default function DeveloperLandingPage() {
  const { isLoaded, isSignedIn } = useUser()

  if (!isLoaded) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
      </div>
    )
  }

  return isSignedIn ? <DeveloperDashboard /> : <DeveloperDocsContent />
}
