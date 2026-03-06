"use client"

import { useState, useEffect, useRef, useMemo, useCallback } from "react"
import { Search, RefreshCw, ChevronDown, Check, Bot } from "lucide-react"
import { AgentCard } from "@/components/agent-card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

import { useAuth } from "@clerk/nextjs"
import { banner } from "@/components/ui/banner"
import { getAllAgents } from "@/lib/api"
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

export default function ConsumerAgentsPage() {
  const { getToken } = useAuth()
  const [allAgents, setAllAgents] = useState<Agent[]>([])
  const [loadingAll, setLoadingAll] = useState(true)
  const [searchTerm, setSearchTerm] = useState("")
  const [statusFilter, setStatusFilter] = useState<string>("all")
  const { isOpen: isDropdownOpen, setIsOpen: setIsDropdownOpen, ref: dropdownRef } = useDropdown()

  const filteredAgents = useFilteredAgents(allAgents, searchTerm, statusFilter)

  const loadAllAgents = useCallback(async () => {
    try {
      setLoadingAll(true)
      const response = await getAllAgents(undefined, undefined, getToken)

      if (response.success && response.agents) {
        setAllAgents(response.agents)
      } else {
        banner.error(response.error || 'Failed to load agents')
      }
    } catch {
      banner.error('Failed to load agents')
    } finally {
      setLoadingAll(false)
    }
  }, [getToken])

  // Load all agents on mount
  useEffect(() => {
    loadAllAgents()
  }, [loadAllAgents])

  if (loadingAll && allAgents.length === 0) {
    return (
      <div className="page-loading">
        <div className="flex flex-col items-center justify-center gap-4">
          <RefreshCw className="h-8 w-8 animate-spin text-primary" />
          <span className="text-base font-medium text-muted-foreground">
            Loading Network...
          </span>
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

        {/* Search, Filter & Results Count */}
        <div className="flex flex-col sm:flex-row gap-3 items-center">
          <div className="relative flex-1">
            <div className="relative" ref={dropdownRef}>
              <Input
                placeholder="Search agents..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="h-[72px] pl-36 pr-12 text-base"
              />
              <button
                onClick={() => setIsDropdownOpen(!isDropdownOpen)}
                className="absolute left-2 top-1/2 -translate-y-1/2 w-[120px] h-14 px-3 border-r border-border/50 rounded-l-md bg-transparent hover:bg-muted/50 flex items-center text-sm text-muted-foreground transition-colors z-20"
              >
                <span>{getStatusLabel(statusFilter)}</span>
                <ChevronDown className={`h-4 w-4 ml-auto transition-transform duration-200 ${isDropdownOpen ? 'rotate-180' : ''}`} />
              </button>
              <Search className="absolute right-4 top-1/2 transform -translate-y-1/2 h-5 w-5 text-muted-foreground z-10" />
              {isDropdownOpen && (
                <div className="absolute top-full left-1.5 mt-1 z-50 w-[130px] py-1 bg-background/95 backdrop-blur-md border border-border/50 shadow-lg rounded-md overflow-hidden animate-[fadeSlideIn_150ms_ease-out]">
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
          </div>
        </div>

        {/* Agent Cards Grid */}
        <div className="grid grid-auto-fill-cards gap-3">
          {filteredAgents.map((agent) => (
            <AgentCard key={agent.agent_id} agent={agent} />
          ))}
        </div>

        {/* Empty State */}
        {filteredAgents.length === 0 && !loadingAll && (
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
