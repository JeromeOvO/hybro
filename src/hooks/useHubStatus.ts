"use client"

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useUser, useAuth } from "@clerk/nextjs"
import { getMyHubStatus } from "@/lib/api/hub"
import type { HubStatus } from "@/lib/api/hub"
import { useCallback } from "react"

export const HUB_STATUS_QUERY_KEY = ["hub", "status"] as const

export function useHubStatus() {
  const { isLoaded, isSignedIn } = useUser()
  const { getToken } = useAuth()
  const queryClient = useQueryClient()

  const query = useQuery({
    queryKey: HUB_STATUS_QUERY_KEY,
    enabled: isLoaded && isSignedIn,
    staleTime: 1000 * 15,
    refetchInterval: 1000 * 30,
    refetchOnWindowFocus: true,
    queryFn: () => getMyHubStatus(getToken),
  })

  const primaryHub: HubStatus | null = query.data?.hubs?.[0] ?? null

  const invalidate = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: HUB_STATUS_QUERY_KEY })
  }, [queryClient])

  return {
    hub: primaryHub,
    hubs: query.data?.hubs ?? [],
    isOnline: primaryHub?.is_online ?? false,
    hasHub: primaryHub !== null,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    invalidate,
  }
}
