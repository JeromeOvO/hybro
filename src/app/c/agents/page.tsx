"use client"

import { useState, useEffect, useRef, useMemo, useCallback, Suspense } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import { Search, ChevronDown, Check, Bot, Cloud, Home, Terminal, KeyRound, Download, Play } from "lucide-react"
import { ConsumerAgentCard } from "@/components/consumer-agent-card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useQuery } from "@tanstack/react-query"
import { cn } from "@/lib/utils"

import { useAuth } from "@clerk/nextjs"
import { getAllAgents } from "@/lib/api"
import type { Agent, AgentCenterResponse } from "@/lib/types"

const STATUS_OPTIONS = [
  { value: "all", label: "All Status" },
  { value: "active", label: "Active" },
  { value: "inactive", label: "Inactive" }
] as const

type SourceTab = "all" | "cloud" | "local"

const SOURCE_TABS: { value: SourceTab; label: string; icon: typeof Cloud | null; iconColor: string }[] = [
  { value: "all", label: "All", icon: null, iconColor: "" },
  { value: "local", label: "Local", icon: Home, iconColor: "text-emerald-500" },
  { value: "cloud", label: "Cloud", icon: Cloud, iconColor: "text-sky-500" },
]

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

const VALID_SOURCE_TABS = new Set<SourceTab>(["all", "cloud", "local"])
const TAB_STORAGE_KEY = 'agents-source-tab'

function parseSourceTab(value: string | null): SourceTab | null {
  if (value && VALID_SOURCE_TABS.has(value as SourceTab)) return value as SourceTab
  return null
}

function getStoredTab(): SourceTab | null {
  if (typeof window === 'undefined') return null
  return parseSourceTab(localStorage.getItem(TAB_STORAGE_KEY))
}

function ConsumerAgentsPageContent() {
  const { getToken } = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const [searchTerm, setSearchTerm] = useState("")
  const [statusFilter, setStatusFilter] = useState<string>("all")
  const { isOpen: isDropdownOpen, setIsOpen: setIsDropdownOpen, ref: dropdownRef } = useDropdown()

  const urlSourceTab = parseSourceTab(searchParams.get("source"))

  const setSourceTab = useCallback((tab: SourceTab) => {
    localStorage.setItem(TAB_STORAGE_KEY, tab)
    const params = new URLSearchParams(searchParams.toString())
    params.set("source", tab)
    router.replace(`?${params.toString()}`, { scroll: false })
  }, [router, searchParams])

  const { data, isLoading } = useQuery<AgentCenterResponse>({
    queryKey: ["allAgents"],
    queryFn: () => getAllAgents(undefined, undefined, getToken),
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  })

  const allAgents = data?.success && data.agents ? data.agents : []
  const filteredAgents = useFilteredAgents(allAgents, searchTerm, statusFilter)

  const cloudAgents = useMemo(() => filteredAgents.filter(a => a.source !== 'hub'), [filteredAgents])
  const localAgents = useMemo(() => filteredAgents.filter(a => a.source === 'hub'), [filteredAgents])

  const tabCounts: Record<SourceTab, number> = {
    all: filteredAgents.length,
    cloud: cloudAgents.length,
    local: localAgents.length,
  }

  const dataLoaded = !!data
  const storedTab = getStoredTab()
  const smartDefault: SourceTab = localAgents.length > 0 ? "local" : "all"
  const sourceTab: SourceTab = urlSourceTab
    ?? (storedTab && (!dataLoaded || tabCounts[storedTab] > 0) ? storedTab : null)
    ?? smartDefault

  const displayAgents = useMemo(() => {
    if (sourceTab === "cloud") return cloudAgents
    if (sourceTab === "local") return localAgents
    return filteredAgents
  }, [filteredAgents, cloudAgents, localAgents, sourceTab])

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

        {/* Source Tabs + Search & Filter */}
        <div className="space-y-3">
          <div className="flex items-center gap-1 rounded-lg bg-muted p-1 w-fit">
            {SOURCE_TABS.map((tab) => (
              <button
                key={tab.value}
                onClick={() => setSourceTab(tab.value)}
                className={cn(
                  "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm transition-all duration-150 ease-out",
                  sourceTab === tab.value
                    ? "bg-primary/10 font-medium text-foreground ring-1 ring-primary/20"
                    : "text-muted-foreground hover:bg-black/10 dark:hover:bg-white/10 hover:text-foreground"
                )}
              >
                {tab.icon && <tab.icon className={cn("h-3.5 w-3.5", tab.iconColor)} />}
                {tab.label}
                <span className={cn(
                  "text-xs tabular-nums",
                  sourceTab === tab.value ? "text-foreground/60" : "text-muted-foreground/60"
                )}>
                  {tabCounts[tab.value]}
                </span>
              </button>
            ))}
          </div>

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
        </div>

        {/* Agent Cards Grid */}
        {displayAgents.length > 0 && (
          <div className="grid grid-auto-fill-cards gap-4">
            {displayAgents.map((agent) => (
              <ConsumerAgentCard key={agent.agent_id} agent={agent} />
            ))}
          </div>
        )}

        {/* Empty State — Local tab setup guide */}
        {sourceTab === "local" && displayAgents.length === 0 && !isLoading && (
          <div className="rounded-xl border border-dashed p-6 md:p-8 space-y-6 max-w-lg mx-auto">
            <div className="text-center space-y-1.5">
              <Home className="h-8 w-8 mx-auto text-emerald-500/60 mb-2" />
              <h3 className="font-semibold text-base">Run agents locally</h3>
              <p className="text-sm text-muted-foreground">
                Keep your data private and reduce latency by running agents on your own machine.
              </p>
            </div>
            <ol className="space-y-4 text-sm">
              <li className="flex gap-3">
                <div className="flex items-center justify-center w-6 h-6 rounded-full bg-primary/10 text-primary text-xs font-bold shrink-0 mt-0.5">
                  <KeyRound className="h-3 w-3" />
                </div>
                <div>
                  <p className="font-medium">Create an API key</p>
                  <p className="text-muted-foreground text-xs mt-0.5">
                    Go to{' '}
                    <a href="/d/discovery-api-keys" className="text-primary hover:underline">
                      Developer Portal &rarr; API Keys
                    </a>{' '}
                    and create a new key.
                  </p>
                </div>
              </li>
              <li className="flex gap-3">
                <div className="flex items-center justify-center w-6 h-6 rounded-full bg-primary/10 text-primary text-xs font-bold shrink-0 mt-0.5">
                  <Download className="h-3 w-3" />
                </div>
                <div>
                  <p className="font-medium">Install Hybro Hub</p>
                  <code className="text-xs bg-muted px-2 py-1 rounded block mt-1 w-fit font-mono">
                    pip install hybro-hub
                  </code>
                </div>
              </li>
              <li className="flex gap-3">
                <div className="flex items-center justify-center w-6 h-6 rounded-full bg-primary/10 text-primary text-xs font-bold shrink-0 mt-0.5">
                  <Terminal className="h-3 w-3" />
                </div>
                <div>
                  <p className="font-medium">Start the hub</p>
                  <code className="text-xs bg-muted px-2 py-1 rounded block mt-1 w-fit font-mono">
                    hybro-hub start --api-key YOUR_KEY
                  </code>
                </div>
              </li>
              <li className="flex gap-3">
                <div className="flex items-center justify-center w-6 h-6 rounded-full bg-primary/10 text-primary text-xs font-bold shrink-0 mt-0.5">
                  <Play className="h-3 w-3" />
                </div>
                <div>
                  <p className="font-medium">Start a local agent</p>
                  <code className="text-xs bg-muted px-2 py-1 rounded block mt-1 w-fit font-mono">
                    hybro-hub agent start ollama
                  </code>
                  <p className="text-muted-foreground text-xs mt-1">
                    Your agent appears here automatically.
                  </p>
                </div>
              </li>
            </ol>
          </div>
        )}

        {/* Empty State — generic */}
        {!(sourceTab === "local" && displayAgents.length === 0 && !isLoading) && displayAgents.length === 0 && !isLoading && (
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
                  setSourceTab("all")
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

export default function ConsumerAgentsPage() {
  return (
    <Suspense>
      <ConsumerAgentsPageContent />
    </Suspense>
  )
}
