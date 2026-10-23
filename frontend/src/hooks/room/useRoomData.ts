import { useCallback, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  inquiryRoomSetting,
} from '@/lib/api/room'
import { toast } from 'sonner'
import type { Agent } from '@/lib/types/agent'
import type { Room, ActiveRunRefWire } from '@/lib/types/response'
import { roomUsesSupervisorByDefault } from '@/lib/types/chat-mode'

export type RoomWithActiveRuns = Room & { active_runs?: ActiveRunRefWire[] | null }

export function useRoomData(
  conversationId: string,
  getToken: (() => Promise<string | null>) | undefined,
  primeAgentNameCache: (entries: Record<string, string>) => void,
  allAgentsData: Agent[] | undefined,
) {
  const activeRoomLoad = useRef<string | null>(null)

  const roomQuery = useQuery({
    queryKey: ['room', conversationId],
    enabled: !!conversationId && activeRoomLoad.current !== conversationId,
    retry: 0,
    staleTime: 1000 * 30,
    queryFn: async ({ signal }): Promise<RoomWithActiveRuns | null> => {
      activeRoomLoad.current = conversationId

      try {
        const response = await inquiryRoomSetting(conversationId, getToken, signal)
        if (!response.success || !response.room) {
          throw new Error(response.error || 'Failed to load room')
        }
        // Pre-populate agent name cache
        if (response.room.room_agent_set) {
          const entries: Record<string, string> = {}
          Object.entries(response.room.room_agent_set).forEach(([agentId, agentName]) => {
            entries[agentId] = agentName as string
          })
          primeAgentNameCache(entries)
        }
        // Also sync names from global agents catalog
        if (allAgentsData) {
          const catalogEntries: Record<string, string> = {}
          allAgentsData.forEach((agent) => {
            if (agent.agent_id && agent.name) {
              catalogEntries[agent.agent_id] = agent.name
            }
          })
          primeAgentNameCache(catalogEntries)
        }
        return { ...response.room, active_runs: response.active_runs ?? null }
      } catch (err) {
        if (err instanceof Error && err.name === 'AbortError') {
          return roomQuery.data ?? null
        }
        throw err
      } finally {
        activeRoomLoad.current = null
      }
    }
  })

  const room = roomQuery.data ?? null

  const getSupervisorMode = useCallback((): boolean => {
    const extendInfo = room?.extend_info as { use_supervisor?: boolean } | undefined
    return roomUsesSupervisorByDefault(extendInfo?.use_supervisor)
  }, [room])

  const loading = roomQuery.isLoading

  // Surface query errors so we don't stay in "loading" forever
  useEffect(() => {
    if (roomQuery.isError) {
      const message = roomQuery.error instanceof Error ? roomQuery.error.message : 'Failed to load room'
      toast.error(message)
    }
  }, [roomQuery.isError, roomQuery.error])

  return {
    room,
    loading,
    getSupervisorMode,
  }
}
