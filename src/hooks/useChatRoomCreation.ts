import { useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { createNewRoom, suggestAgents, SuggestAgentsResponse } from '@/lib/api/room'
import { getAllActiveAgents } from '@/lib/api/agent'
import { banner } from "@/components/ui/banner"
import type { Agent } from '@/lib/types/agent'

interface UseChatRoomCreationProps {
  userId?: string
  userName?: string
  getToken?: () => Promise<string | null>
}

interface CreateRoomOptions {
  selectedAgents?: Agent[]
  appliedFromGroup?: string
  debateMode?: boolean
  roomName?: string
  targetGroup?: string  // Group to use for the first message
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
      const response = await getAllActiveAgents()
      
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
  }, [])

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
      roomName: customRoomName,
      targetGroup
    } = options

    if (!userId || !userName) {
      banner.error('User information not available')
      return null
    }

    if (!userMessage.trim()) {
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
      // If no agents selected, room starts empty (messages use target_group)

      // Use custom room name if provided, otherwise auto-generate from message
      // Strip mentions from room name (e.g., <@id|name> -> "")
      const displayMessage = userMessage.replace(/<@[^|]+\|[^>]+>\s*/g, '').trim()
      const roomName = customRoomName || (displayMessage.length > 30 
        ? `${displayMessage.substring(0, 30)}...` 
        : displayMessage) || 'New Chat'

      // Create room with settings
      const extendInfo = {
        debateMode,
        initialMessage: userMessage
      }

      const response = await createNewRoom(
        roomName,
        userId,
        userName,
        getToken,
        roomAgentSet,
        extendInfo,
        appliedFromGroup
      )

      if (response.success && response.room) {
        const roomId = response.room.room_id
        console.log('✅ Room created successfully:', roomId)
        
        // Store initial message and target group in sessionStorage for the room page to pick up
        sessionStorage.setItem(`room-${roomId}-initial-message`, userMessage)
        if (targetGroup) {
          sessionStorage.setItem(`room-${roomId}-target-group`, targetGroup)
        }
        
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
