import { useCallback, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getAllActiveAgents } from '@/lib/api/agent'
import { SYSTEM_AGENTS } from '@/lib/system-agents'
import type { Agent } from '@/lib/types/agent'

export function useAgentCatalog(userId?: string, getToken?: () => Promise<string | null>) {
  const agentNameCache = useRef<{ [agentId: string]: string }>({})

  const allAgentsQuery = useQuery<Agent[], Error>({
    queryKey: ['agents', 'active'] as const,
    staleTime: 1000 * 60 * 60 * 24,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: 0,
    enabled: !!userId,
    queryFn: async ({ signal }): Promise<Agent[]> => {
      console.log('🤖 Loading global active agents catalog')
      try {
        const res = await getAllActiveAgents(signal, 15000, getToken)
        if (!res.success || !res.agents) {
          throw new Error(res.error || 'Failed to load agents')
        }
        console.log(`✅ Loaded ${res.agents.length} agents`)
        return res.agents
      } catch (error: unknown) {
        if (error instanceof Error && error.name === 'AbortError') {
          return []
        }
        console.error('❌ Failed to load agents:', error)
        throw error
      }
    },
  })

  const getAgentName = useCallback(async (agentId: string): Promise<string> => {
    if (SYSTEM_AGENTS[agentId]) {
      return SYSTEM_AGENTS[agentId].name
    }
    if (agentNameCache.current[agentId]) {
      return agentNameCache.current[agentId]
    }
    const agents = allAgentsQuery.data
    if (agents) {
      const found = agents.find(a => a.agent_id === agentId)
      if (found?.agent_card?.name) {
        const name = found.agent_card.name
        agentNameCache.current[agentId] = name
        return name
      }
    }
    return `Agent ${agentId.slice(0, 6)}`
  }, [allAgentsQuery.data])

  const getAgentSource = useCallback((agentId: string | undefined): 'cloud' | 'hub' | undefined => {
    if (!agentId) return undefined
    const agents = allAgentsQuery.data
    if (agents) {
      const found = agents.find(a => a.agent_id === agentId)
      if (found?.source) return found.source as 'cloud' | 'hub'
    }
    return undefined
  }, [allAgentsQuery.data])

  // Refresh agent name cache when agent catalog loads
  useEffect(() => {
    if (allAgentsQuery.data) {
      allAgentsQuery.data.forEach((agent: Agent) => {
        if (agent.agent_id && agent.agent_card?.name) {
          agentNameCache.current[agent.agent_id] = agent.agent_card.name
        }
      })
    }
  }, [allAgentsQuery.data])

  const primeAgentNameCache = useCallback((entries: Record<string, string>) => {
    Object.assign(agentNameCache.current, entries)
  }, [])

  const resetAgentNameCache = useCallback(() => {
    agentNameCache.current = {}
  }, [])

  return {
    availableAgents: allAgentsQuery.data || [],
    allAgentsData: allAgentsQuery.data,
    getAgentName,
    getAgentSource,
    primeAgentNameCache,
    resetAgentNameCache,
  }
}
