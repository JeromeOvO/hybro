import { useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { createNewRoom, suggestAgents, SuggestAgentsResponse } from '@/lib/api/room'
import { getAllActiveAgents } from '@/lib/api/agent'
import { banner } from "@/components/ui/banner"
import { useRoomUiStore } from '@/stores/room-ui-store'
import type { Agent } from '@/lib/types/agent'
import type { PendingAttachment } from '@/lib/types/attachments'
import { isBuiltinGroup } from '@/lib/types/agent-group'
import type { RoomMembershipWriteInput } from '@/lib/types/agent-group'

interface UseChatRoomCreationProps {
  userId?: string
  userName?: string
  getToken?: () => Promise<string | null>
}

interface CreateRoomOptions {
  selectedAgents?: Agent[]
  /** @deprecated Use membership instead. */
  appliedFromGroup?: string
  debateMode?: boolean
  useSupervisor?: boolean
  roomName?: string
  targetGroup?: string
  attachments?: PendingAttachment[]
  membership?: RoomMembershipWriteInput
}

export function useChatRoomCreation({ userId, userName, getToken }: UseChatRoomCreationProps) {
  const router = useRouter()
  const [creating, setCreating] = useState(false)
  const [loadingAgents, setLoadingAgents] = useState(false)
  const [suggestingAgents, setSuggestingAgents] = useState(false)
  const [defaultAgents, setDefaultAgents] = useState<Agent[]>([])

  // Load all available active agents
  const loadDefaultAgents = useCallback(async () => {
    try {
      setLoadingAgents(true)
      const response = await getAllActiveAgents(undefined, undefined, getToken)
      
      if (response.success && response.agents && response.agents.length > 0) {
        setDefaultAgents(response.agents)
        return response.agents
      } else {
        throw new Error(response.error || 'No agents available')
      }
    } catch (error) {
      console.error('Failed to load agents:', error)
      banner.error('Failed to load agents')
      return []
    } finally {
      setLoadingAgents(false)
    }
  }, [getToken])

  // Get agent suggestions (preview for All Agents mode)
  const getAgentSuggestions = useCallback(async (messageText: string): Promise<SuggestAgentsResponse | null> => {
    try {
      setSuggestingAgents(true)
      const response = await suggestAgents(messageText, 3, getToken)
      return response
    } catch (error) {
      console.error('Failed to get agent suggestions:', error)
      return null
    } finally {
      setSuggestingAgents(false)
    }
  }, [getToken])

  // Create room with user message
  const createRoomWithMessage = useCallback(async (
    userMessage: string,
    options: CreateRoomOptions = {}
  ) => {
    const { 
      selectedAgents = [], 
      appliedFromGroup,
      debateMode = false,
      useSupervisor = true,
      roomName: customRoomName,
      targetGroup,
      membership: explicitMembership,
    } = options

    if (!userId || !userName) {
      banner.error('User information not available')
      return null
    }

    if (!userMessage.trim() && !options.attachments?.length) {
      banner.error('Message cannot be empty')
      return null
    }

    try {
      setCreating(true)

      // Build room agent set if agents are selected
      let roomAgentSet: { [k: string]: string } = {}
      
      if (selectedAgents.length > 0) {
        roomAgentSet = Object.fromEntries(
          selectedAgents.map((agent) => [
            agent.agent_id,
            agent.agent_card.name,
          ])
        )
      }

      // Derive membership: Room Settings config (selectedAgents) takes priority,
      // then saved group selection, then empty snapshot.
      let membership: RoomMembershipWriteInput | undefined = explicitMembership
      if (!membership && selectedAgents.length === 0 && targetGroup && !isBuiltinGroup(targetGroup)) {
        membership = { membership_seed_input: "saved_group", seed_group_id: targetGroup }
      }

      // Use custom room name if provided, otherwise auto-generate from message
      // Strip mentions from room name (e.g., <@id|name> -> "")
      const displayMessage = userMessage.replace(/<@[^|]+\|[^>]+>\s*/g, '').trim()
      const roomName = customRoomName || (displayMessage.length > 30 
        ? `${displayMessage.substring(0, 30)}...` 
        : displayMessage) || 'New Chat'

      const extendInfo = {
        debateMode,
        use_supervisor: useSupervisor,
        initialMessage: userMessage
      }

      const response = await createNewRoom(
        roomName,
        userId,
        userName,
        getToken,
        roomAgentSet,
        extendInfo,
        appliedFromGroup,
        membership,
      )

      if (response.success && response.room) {
        const roomId = response.room.room_id
        if (!roomId) {
          throw new Error('Room created but no room_id returned')
        }
        console.log('Room created successfully:', roomId)
        
        // Store initial message and target group in Zustand for the room page to pick up.
        // Clear the override when the room was seeded with an explicit snapshot:
        //  - saved group seeded → room has snapshot → default to Room Default (Locked Decision #4)
        //  - manual selectedAgents → room has snapshot → default to Room Default
        const handoffTargetGroup =
          membership?.membership_seed_input === "saved_group" || selectedAgents.length > 0
            ? undefined
            : targetGroup
        useRoomUiStore.getState().setPendingRoomData(roomId, {
          initialMessage: userMessage,
          targetGroup: handoffTargetGroup,
          attachments: options.attachments,
        })
        
        return roomId
      } else {
        throw new Error(response.error || 'Failed to create room')
      }
    } catch (error) {
      console.error('Failed to create room:', error)
      banner.error(error instanceof Error ? error.message : 'Failed to create room')
      return null
    } finally {
      setCreating(false)
    }
  }, [userId, userName, getToken])

  // Create room and navigate (main entry point)
  const createAndNavigate = useCallback(async (
    userMessage: string, 
    options: CreateRoomOptions = {}
  ) => {
    const roomId = await createRoomWithMessage(userMessage, options)
    
    if (roomId) {
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new Event('rooms:refresh'))
      }
      router.push(`/room/${roomId}`)
      return true
    }
    
    return false
  }, [createRoomWithMessage, router])

  // Create room with specific agents and navigate
  const createWithAgentsAndNavigate = useCallback(async (
    userMessage: string,
    selectedAgents: Agent[],
    options: Omit<CreateRoomOptions, 'selectedAgents'> = {}
  ) => {
    if (selectedAgents.length === 0) {
      banner.error('Please select at least one agent')
      return false
    }

    return createAndNavigate(userMessage, { 
      ...options,
      selectedAgents,
    })
  }, [createAndNavigate])

  return {
    creating,
    loadingAgents,
    suggestingAgents,
    defaultAgents,
    createRoomWithMessage,
    createAndNavigate,
    createWithAgentsAndNavigate,
    loadDefaultAgents,
    getAgentSuggestions,
  }
}
