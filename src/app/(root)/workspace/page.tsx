'use client'

import { useState, useEffect, useRef, useMemo } from 'react'
import { useRouter } from "next/navigation"
import { useAuth } from '@clerk/nextjs'
import { Plus, Search, RefreshCw, ChevronDown, Users, ThumbsUp } from 'lucide-react'
import { AgentCard } from '@/components/agent-card'
import { Button } from '@/components/ui/button'
import { Input } from "@/components/ui/input"
import { banner } from "@/components/ui/banner"
import { getAgentsByProviderId } from "@/lib/api"
import type { Agent } from "@/lib/types"

const STATUS_OPTIONS = [
  { value: "all", label: "All Status" },
  { value: "active", label: "Active" },
  { value: "inactive", label: "Inactive" }
] as const

function useDropdown() {
  const [isOpen, setIsOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [isOpen])

  return { isOpen, setIsOpen, ref }
}

function getStatusLabel(status: string) {
  return STATUS_OPTIONS.find(option => option.value === status)?.label || "All Status"
}

function useFilteredAgents(agents: Agent[], searchTerm: string, statusFilter: string) {
  return useMemo(() => {
    const existingIds = new Set<string>()

    return agents.filter(agent => {
      if (existingIds.has(agent.agent_id)) {
        return false
      }
      existingIds.add(agent.agent_id)

      const matchesSearch =
        agent.agent_card.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        agent.agent_card.description.toLowerCase().includes(searchTerm.toLowerCase()) ||
        agent.agent_card.skills.some(skill =>
          skill.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
          skill.tags.some(tag => tag.toLowerCase().includes(searchTerm.toLowerCase()))
        )

      const matchesStatus = statusFilter === "all" || agent.agent_status === statusFilter

      return matchesSearch && matchesStatus
    })
  }, [agents, searchTerm, statusFilter])
}

export default function Workspace() {
  const router = useRouter()
  const [agents, setAgents] = useState<Agent[]>([])
  const [loading, setLoading] = useState(true)
  const [searchTerm, setSearchTerm] = useState("")
  const [statusFilter, setStatusFilter] = useState<"all" | "active" | "inactive">("all");
  const { getToken } = useAuth()
  const { isOpen: isDropdownOpen, setIsOpen: setIsDropdownOpen, ref: dropdownRef } = useDropdown()

  const filteredAgents = useFilteredAgents(agents, searchTerm, statusFilter)

  // total agent counts of user
  const agentCount = agents.length

  // total likes the user received
  const totalLikes = useMemo(() => {
    return agents.reduce((sum, agent) => sum + (agent.like_count ?? 0), 0)
  }, [agents])

  const loadAgentsbyProvider = async () => {
    try {
      setLoading(true)
      const response = await getAgentsByProviderId(getToken)

      if (response.success && response.agents) {
        setAgents(response.agents)
      } else {
        banner.error(response.error || 'Failed to load agents')
      }
    } catch {
      banner.error('Failed to load agents')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadAgentsbyProvider()
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[85vh]">
        <div className="flex flex-col items-center justify-center gap-4">
          <RefreshCw className="h-8 w-8 animate-spin text-primary" />
          <span className="text-base font-medium text-muted-foreground">Loading Workspace...</span>
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
            <h1 className="text-3xl font-bold">Agent Workspace</h1>
          </div>
          <div className="flex gap-2">
            <Button
              className="bg-linear-to-r from-[hsl(var(--color-hybro-bro-strong))] to-[hsl(var(--color-hybro-hy-strong))] hover:from-[hsl(var(--color-hybro-bro))] hover:to-[hsl(var(--color-hybro-hy))] text-white font-semibold"
              onClick={() => router.push('/agent/registry')}
            >
              <Plus className="h-4 w-4 mr-2" />
              Register Agent
            </Button>
          </div>
        </div>

        {/* Stats Section */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          
          {/* My Agents Count */}
          <div className="rounded-lg border border-border/50 bg-background/80 p-5">
            <div className="flex items-center gap-3">
              <Users className="h-10 w-10 text-muted-foreground" />
              <div>
                <div className="text-sm text-muted-foreground">
                  My Agents
                </div>
                <div className="text-2xl font-semibold text-[hsl(var(--color-hybro-hy))]">
                  {agentCount}
                </div>
              </div>
            </div>
          </div>

          {/* Total Likes */}
          <div className="rounded-lg border border-border/50 bg-background/80 p-5">
            <div className="flex items-center gap-3">
              <ThumbsUp className="h-10 w-10 text-muted-foreground" />
              <div>
                <div className="text-sm text-muted-foreground">
                  Total Likes
                </div>
                <div className="text-2xl font-semibold text-[hsl(var(--color-hybro-bro))]">
                  {totalLikes}
                </div>
              </div>
            </div>
          </div>

          {/* Placeholder */}
          <div className="rounded-lg border border-dashed border-border/50 bg-background/40 p-5 flex items-center justify-center text-sm text-muted-foreground">
            Coming soon
          </div>
        </div>

        {/* Search and Filter Section */}
        <div className="flex flex-col sm:flex-row gap-4 items-center justify-between">
          <div className="flex flex-1 gap-4">
            <div className="relative flex-1">
              <div className="relative" ref={dropdownRef}>
                <Input
                  placeholder="Search agents..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="pl-32 pr-10"
                />
                <button
                  onClick={() => setIsDropdownOpen(!isDropdownOpen)}
                  className="absolute left-2 top-1/2 transform -translate-y-1/2 w-[110px] h-7 px-2 justify-start border-0 bg-transparent shadow-none focus:ring-0 hover:bg-transparent flex items-center text-sm text-muted-foreground z-20"
                >
                  <span>{getStatusLabel(statusFilter)}</span>
                  <ChevronDown className={`h-3 w-3 ml-auto transition-transform ${isDropdownOpen ? 'rotate-180' : ''}`} />
                </button>
                <Search className="absolute right-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground z-10" />
                {isDropdownOpen && (
                  <div className="absolute top-full left-2 z-50 w-[120px] bg-background/95 backdrop-blur-md border border-border/50 shadow-lg rounded-md overflow-hidden">
                    {STATUS_OPTIONS.map((option) => (
                      <button
                        key={option.value}
                        onClick={() => {
                          setStatusFilter(option.value)
                          setIsDropdownOpen(false)
                        }}
                        className="w-full px-3 py-2 text-sm text-left hover:bg-accent hover:text-accent-foreground"
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Results Count and Clear Filters */}
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

        <div className="flex gap-4 mb-4">
          {STATUS_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              className={`px-2 py-1 text-sm ${
                statusFilter === opt.value ? "font-semibold underline" : "text-gray-500"
              }`}
              onClick={() => setStatusFilter(opt.value)}
            >
              {opt.label}
            </button>
          ))}
        </div>


        {/* Agent Cards Grid */}
        <div className="flex flex-col sm:flex-row gap-4 items-center justify-between">
        <div className="flex flex-1 gap-4">
          <div className="relative flex-1">
            <div className="grid grid-cols-15 sm:grid-cols-2 lg:grid-cols-2 xl:grid-cols-3 gap-3">
              {filteredAgents.map((agent) => (
                <AgentCard key={agent.agent_id} agent={agent} />
              ))}
            </div>
          </div>
        </div>
      </div>

        {/* Empty State */}
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
                Register Agent
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
    </div>
  )
}