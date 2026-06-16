import { useCallback, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  inquiryRoomSetting,
} from '@/lib/api/room'
import { banner } from '@/components/ui/banner'
import type { Agent } from '@/lib/types/agent'
import type { Room, RoomAgentRefWire, ActiveRunRefWire } from '@/lib/types/response'

export type RoomWithActiveRuns = Room & { active_runs?: ActiveRunRefWire[] | null }

export function useRoomData(
  roomId: string,
  getToken: (() => Promise<string | null>) | undefined,
  primeAgentNameCache: (entries: Record<string, string>) => void,
  allAgentsData: Agent[] | undefined,
) {
  const activeRoomLoad = useRef<string | null>(null)
  const resolvedAgentsRef = useRef<RoomAgentRefWire[] | null>(null)

  const roomQuery = useQuery({
    queryKey: ['room', roomId],
    enabled: !!roomId && activeRoomLoad.current !== roomId,
    retry: 0,
    staleTime: 1000 * 30,
    queryFn: async ({ signal }): Promise<RoomWithActiveRuns | null> => {
      activeRoomLoad.current = roomId

      try {
        const response = await inquiryRoomSetting(roomId, getToken, signal)
        if (!response.success || !response.room) {
          throw new Error(response.error || 'Failed to load room')
        }
        resolvedAgentsRef.current = response.resolved_agents ?? null
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
            if (agent.agent_id && agent.agent_card?.name) {
              catalogEntries[agent.agent_id] = agent.agent_card.name
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

  const getDebateMode = useCallback((): boolean => {
    if (!room?.extend_info) return false
    const extendInfo = room.extend_info as { debateMode?: boolean }
    return extendInfo.debateMode || false
  }, [room])

  const getSupervisorMode = useCallback((): boolean => {
    if (!room?.extend_info) return false
    const extendInfo = room.extend_info as { use_supervisor?: boolean }
    return extendInfo.use_supervisor || false
  }, [room])

  const loading = roomQuery.isLoading

  const getAgentList = useCallback(() => {
    if (!room?.room_agent_set) return []
    return Object.entries(room.room_agent_set).map(([id, name]) => ({ id, name }))
  }, [room])

  const getRoomFormData = useCallback(() => {
    if (!room) return null
    return {
      roomName: room.room_name || '',
      roomId: room.room_id || '',
      selectedAgents: room.room_agent_set || {},
      roomOwnerId: room.room_owner_id || '',
      roomOwnerName: room.room_owner_name || '',
      debateMode: getDebateMode(),
      resolvedAgents: resolvedAgentsRef.current,
    }
  }, [room, getDebateMode])

  const refreshRoomSetting = useCallback(async () => {
    await roomQuery.refetch()
  }, [roomQuery])

  // Surface query errors so we don't stay in "loading" forever
  useEffect(() => {
    if (roomQuery.isError) {
      const message = roomQuery.error instanceof Error ? roomQuery.error.message : 'Failed to load room'
      banner.error(message)
    }
  }, [roomQuery.isError, roomQuery.error])

  return {
    room,
    roomQuery,
    resolvedAgentsRef,
    loading,
    getDebateMode,
    getSupervisorMode,
    getAgentList,
    getRoomFormData,
    refreshRoomSetting,
  }
}
