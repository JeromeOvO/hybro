"use client"

import { useState, useEffect } from "react"
import { Plus, Search, Grid, List, RefreshCw } from "lucide-react"
import { AgentCard, StatsCards } from "@/components/agent-card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useRouter } from "next/navigation"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "sonner"
import { getAllAgents } from "@/lib/api"
import type { Agent, AgentCenterResponse } from "@/lib/types"
import type { AgentProvider } from "@/lib/types"

export default function AgentPage() {
  const router = useRouter()
  const [agents, setAgents] = useState<Agent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchTerm, setSearchTerm] = useState("")
  const [statusFilter, setStatusFilter] = useState<string>("all")
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid")

  const loadAgents = async () => {
    try {
      setLoading(true)
      const response = await getAllAgents()
      if (response.success && response.agents) {
        setAgents(response.agents)
      } else {
        toast.error(response.error || 'Failed to load agents')
      }
    } catch (error) {
      toast.error('Failed to load agents')
    } finally {
      setLoading(false)
    }
  }

  // Initial load
  useEffect(() => {
    loadAgents()
  }, [])

    const providers = Array.from(new Set(
      agents
          .map(agent => agent.agent_card.provider?.organization)
          .filter((org): org is string => org !== undefined && org !== null)
    ))

  const filteredAgents = agents.filter(agent => {
    const matchesSearch = 
      agent.agent_card.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      agent.agent_card.description.toLowerCase().includes(searchTerm.toLowerCase()) ||
      agent.agent_card.skills.some(skill => 
        skill.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        skill.tags.some(tag => tag.toLowerCase().includes(searchTerm.toLowerCase()))
      )
    
    const matchesStatus = statusFilter === "all" || agent.agent_status === statusFilter
    const matchesProvider = providers.length === 0 || 
      providers.includes(agent.agent_card.provider?.organization || "")
    
    return matchesSearch && matchesStatus && matchesProvider
  })

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[85vh]">
        <div className="flex flex-col items-center justify-center gap-4">
          <RefreshCw className="h-8 w-8 animate-spin text-primary" />
          <span className="text-base font-medium text-muted-foreground">Loading Network...</span>
        </div>
      </div>
    )
  }
    
  if (error) {
    return (
      <div className="flex items-center justify-center min-h-[85vh]">
        <div className="flex flex-col items-center justify-center gap-4">
          <div className="text-center">
            <h2 className="text-lg font-semibold mb-2">Failed to load network</h2>
            <p className="text-muted-foreground mb-4">{error}</p>
          </div>
          <Button onClick={loadAgents} variant="outline">
            <RefreshCw className="h-4 w-4 mr-2" />
            Retry
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="p-8 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Hybro Agent Network</h1>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={loadAgents}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
          <Button variant="outline" onClick={() => router.push('/agent/registry')}>
            <Plus className="h-4 w-4 mr-2" />
            Register Agent
          </Button>
        </div>
      </div>

      <StatsCards agents={agents} />

      <div className="flex flex-col sm:flex-row gap-4 items-center justify-between">
        <div className="flex flex-1 gap-4 max-w-md">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search agents..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="pl-10"
            />
          </div>
        </div>
        
        <div className="flex gap-2">
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-[120px]">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Status</SelectItem>
              <SelectItem value="active">Active</SelectItem>
              <SelectItem value="inactive">Inactive</SelectItem>
            </SelectContent>
          </Select>
          
          {providers.length > 0 && (
            <Select value={providers.find(p => p === (agents.find(a => a.agent_id === agents[0]?.agent_id)?.agent_card.provider?.organization || "")) || "all"} onValueChange={(value) => {
              const selectedProvider = providers.find(p => p === value);
              if (selectedProvider) {
                setStatusFilter("all"); // Reset status filter when provider changes
              }
            }}>
              <SelectTrigger className="w-[140px]">
                <SelectValue placeholder="Provider" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Providers</SelectItem>
                {providers.map(provider => (
                  <SelectItem key={provider} value={provider}>
                    {provider}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          
          <div className="flex border rounded-md">
            <Button
              variant={viewMode === "grid" ? "default" : "ghost"}
              size="sm"
              onClick={() => setViewMode("grid")}
            >
              <Grid className="h-4 w-4" />
            </Button>
            <Button
              variant={viewMode === "list" ? "default" : "ghost"}
              size="sm"
              onClick={() => setViewMode("list")}
            >
              <List className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <span className="text-sm text-muted-foreground">
          Showing {filteredAgents.length} of {agents.length} agents
        </span>
        {(searchTerm || statusFilter !== "all") && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setSearchTerm("")
              setStatusFilter("all")
            }}
          >
            Clear filters
          </Button>
        )}
      </div>

      <div className={`grid gap-6 ${
        viewMode === "grid" 
          ? "grid-cols-1 md:grid-cols-2 lg:grid-cols-3" 
          : "grid-cols-1"
      }`}>
        {filteredAgents.map((agent) => (
          <AgentCard
            key={agent.agent_id}
            agent={agent}
          />
        ))}
      </div>

      {filteredAgents.length === 0 && !loading && (
        <div className="text-center py-12">
          <div className="text-muted-foreground mb-4">
            {agents.length === 0 
              ? "No agents found. Register your first agent to get started."
              : "No agents found matching your criteria."
            }
          </div>
          {agents.length === 0 ? (
            <Button onClick={() => router.push('/agent/registry')}>
              <Plus className="h-4 w-4 mr-2" />
              Register First Agent
            </Button>
          ) : (
            <Button
              variant="outline"
              onClick={() => {
                setSearchTerm("")
                setStatusFilter("all")
              }}
            >
              Clear filters
            </Button>
          )}
        </div>
      )}
    </div>
  )
}
