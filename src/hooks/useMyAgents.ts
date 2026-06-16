"use client"

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useUser, useAuth } from "@clerk/nextjs"
import { getAgentsByProviderId } from "@/lib/api"
import type { Agent } from "@/lib/types"
import { useCallback } from "react"

export const MY_AGENTS_QUERY_KEY = ["agents", "my"] as const

export function useMyAgents() {
  const { user, isLoaded, isSignedIn } = useUser()
  const { getToken } = useAuth()
  const queryClient = useQueryClient()

  const query = useQuery<Agent[], Error>({
    queryKey: MY_AGENTS_QUERY_KEY,
    enabled: isLoaded && isSignedIn && !!user?.id,
    staleTime: 1000 * 30, // 30 seconds
    refetchOnWindowFocus: false,
    queryFn: async (): Promise<Agent[]> => {
      const response = await getAgentsByProviderId(getToken)
      if (response.success && response.agents) {
        return response.agents
      }
      throw new Error(response.error || "Failed to load agents")
    },
  })

  // Call this after registering/deleting an agent to refresh the sidebar list
  const invalidate = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: MY_AGENTS_QUERY_KEY })
  }, [queryClient])

  return {
    agents: query.data ?? [],
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    invalidate,
  }
}
