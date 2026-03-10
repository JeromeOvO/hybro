"use client"

import { useState, useEffect, useRef, useMemo } from "react"
import { Search, ChevronDown, Check, Bot, Cloud, Home } from "lucide-react"
import { ConsumerAgentCard } from "@/components/consumer-agent-card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useQuery } from "@tanstack/react-query"

import { useAuth } from "@clerk/nextjs"
import { getAllAgents } from "@/lib/api"
import type { Agent, AgentCenterResponse } from "@/lib/types"

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
      if (agent.agent_status === "deleted") return false

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

export default function ConsumerAgentsPage() {
  const { getToken } = useAuth()
  const [searchTerm, setSearchTerm] = useState("")
  const [statusFilter, setStatusFilter] = useState<string>("all")
  const { isOpen: isDropdownOpen, setIsOpen: setIsDropdownOpen, ref: dropdownRef } = useDropdown()

  const { data, isLoading } = useQuery<AgentCenterResponse>({
    queryKey: ["allAgents"],
    queryFn: () => getAllAgents(undefined, undefined, getToken),
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  })

  const allAgents = data?.success && data.agents ? data.agents : []
  const filteredAgents = useFilteredAgents(allAgents, searchTerm, statusFilter)

  if (isLoading && allAgents.length === 0) {
    return (
      <div className="page-container">
        <div className="page-content flex items-center justify-center min-h-[60vh]">
          <div className="flex flex-col items-center justify-center gap-4">
            <div className="relative">
              <div className="h-12 w-12 rounded-full border-4 border-primary/20 animate-spin border-t-primary" />
              <Bot className="h-6 w-6 absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-primary/60" />
            </div>
            <span className="text-base font-medium text-muted-foreground animate-pulse">
              Loading Agents...
            </span>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="page-container">
      <div className="page-content space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">Explore Agents</h1>
            <p className="text-sm text-muted-foreground">Discover AI agents on the HYBRO network</p>
          </div>
        </div>

        {/* Search & Filter */}
        <div className="flex flex-col sm:flex-row gap-2">
          <div className="relative" ref={dropdownRef}>
            <Button
              variant="outline"
              className="h-10 w-[130px] justify-between"
              onClick={() => setIsDropdownOpen(!isDropdownOpen)}
            >
              <span>{getStatusLabel(statusFilter)}</span>
              <ChevronDown className={`h-4 w-4 transition-transform duration-200 ${isDropdownOpen ? 'rotate-180' : ''}`} />
            </Button>
            {isDropdownOpen && (
              <div className="absolute top-full left-0 mt-1 z-50 w-[130px] py-1 bg-background/95 backdrop-blur-md border border-muted-foreground/30 shadow-lg rounded-md overflow-hidden animate-[fadeSlideIn_150ms_ease-out]">
                {STATUS_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    onClick={() => {
                      setStatusFilter(option.value)
                      setIsDropdownOpen(false)
                    }}
                    className="w-full px-3 py-1.5 text-sm hover:bg-accent hover:text-accent-foreground flex items-center gap-2 transition-colors"
                  >
                    <Check className={`h-3 w-3 shrink-0 transition-opacity ${statusFilter === option.value ? 'opacity-100' : 'opacity-0'}`} />
                    {option.label}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search agents..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="h-10 pl-9 text-sm"
            />
          </div>
        </div>

        {/* Agent Cards — split by source */}
        {(() => {
          const cloudAgents = filteredAgents.filter(a => a.source !== 'hub')
          const localAgents = filteredAgents.filter(a => a.source === 'hub')
          return (
            <div className="space-y-8">
              {cloudAgents.length > 0 && (
                <section>
                  <div className="flex items-center gap-2 mb-4 pb-2 border-b border-muted-foreground/30">
                    <Cloud className="h-4 w-4 text-sky-500" />
                    <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
                      Cloud Agents
                    </h2>
                    <span className="text-xs text-muted-foreground/60">{cloudAgents.length}</span>
                  </div>
                  <div className="grid grid-auto-fill-cards gap-4">
                    {cloudAgents.map((agent) => (
                      <ConsumerAgentCard key={agent.agent_id} agent={agent} />
                    ))}
                  </div>
                </section>
              )}

              {localAgents.length > 0 && (
                <section>
                  <div className="flex items-center gap-2 mb-4 pb-2 border-b border-muted-foreground/30">
                    <Home className="h-4 w-4 text-emerald-500" />
                    <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
                      Local Agents
                    </h2>
                    <span className="text-xs text-muted-foreground/60">{localAgents.length}</span>
                  </div>
                  <div className="grid grid-auto-fill-cards gap-4">
                    {localAgents.map((agent) => (
                      <ConsumerAgentCard key={agent.agent_id} agent={agent} />
                    ))}
                  </div>
                </section>
              )}
            </div>
          )
        })()}

        {/* Empty State */}
        {filteredAgents.length === 0 && !isLoading && (
          <div className="flex flex-col items-center justify-center py-8 gap-3">
            <Bot className="h-10 w-10 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">
              {allAgents.length === 0
                ? "No agents found on the network yet."
                : "No agents found matching your criteria."
              }
            </p>
            {allAgents.length > 0 && (
              <Button
                variant="outline"
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
        )}
      </div>
    </div>
  )
}
