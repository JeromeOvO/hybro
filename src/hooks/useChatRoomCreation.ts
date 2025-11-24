import { useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { createNewRoom } from '@/lib/api/room'
import { getAllAgents } from '@/lib/api/agent'
import { toast } from 'sonner'
import type { Agent } from '@/lib/types/agent'

interface UseChatRoomCreationProps {
  userId?: string
  userName?: string
  getToken?: () => Promise<string | null>
}

export function useChatRoomCreation({ userId, userName, getToken }: UseChatRoomCreationProps) {
  const router = useRouter()
  const [creating, setCreating] = useState(false)
  const [loadingAgents, setLoadingAgents] = useState(false)
  const [defaultAgents, setDefaultAgents] = useState<Agent[]>([])

  // Load default agents (you can configure which agents to use by default)
  const loadDefaultAgents = useCallback(async () => {
    try {
      setLoadingAgents(true)
      const response = await getAllAgents(getToken)
      
      if (response.success && response.agents && response.agents.length > 0) {
        // Take first 2 agents as default, or configure as needed
        const selectedAgents = response.agents.slice(0, 2)
        setDefaultAgents(selectedAgents)
        return selectedAgents
      } else {
        throw new Error(response.error || 'No agents available')
      }
    } catch (error) {
      console.error('Failed to load default agents:', error)
      toast.error('Failed to load agents')
      return []
    } finally {
      setLoadingAgents(false)
    }
  }, [getToken])

  // Create room with user message
  const createRoomWithMessage = useCallback(async (userMessage: string) => {
    if (!userId || !userName) {
      toast.error('User information not available')
      return null
    }

    if (!userMessage.trim()) {
      toast.error('Message cannot be empty')
      return null
    }

    try {
      setCreating(true)

      // Load default agents if not loaded
      let agents = defaultAgents
      if (agents.length === 0) {
        agents = await loadDefaultAgents()
        if (agents.length === 0) {
          throw new Error('No agents available to create room')
        }
      }

      // Create room agent set mapping: agent name -> agent id
      const roomAgentSet = Object.fromEntries(
        agents.map((agent) => [
          agent.agent_card.name, // key: agent name
          agent.agent_id,        // value: agent id
        ])
      )

      // Generate a room name based on user message (truncated)
      const roomName = userMessage.length > 30 
        ? `${userMessage.substring(0, 30)}...` 
        : userMessage

      // Create room with default settings (debate mode off)
      const extendInfo = {
        debateMode: false,
        initialMessage: userMessage // Store initial message in extend_info
      }

      const response = await createNewRoom(
        roomName,
        userId,
        userName,
        getToken,
        roomAgentSet,
        extendInfo
      )

      if (response.success && response.room) {
        const roomId = response.room.room_id
        console.log('✅ Room created successfully:', roomId)
        
        // Store initial message in sessionStorage for the room page to pick up
        sessionStorage.setItem(`room-${roomId}-initial-message`, userMessage)
        
        return roomId
      } else {
        throw new Error(response.error || 'Failed to create room')
      }
    } catch (error) {
      console.error('Failed to create room:', error)
      toast.error(error instanceof Error ? error.message : 'Failed to create room')
      return null
    } finally {
      setCreating(false)
    }
  }, [userId, userName, defaultAgents, loadDefaultAgents])

  // Create room and navigate
  const createAndNavigate = useCallback(async (userMessage: string) => {
    const roomId = await createRoomWithMessage(userMessage)
    
    if (roomId) {
      toast.success('Room created successfully!')
      // Navigate to room page
      router.push(`/room/${roomId}`)
      return true
    }
    
    return false
  }, [createRoomWithMessage, router])

  return {
    creating,
    loadingAgents,
    createRoomWithMessage,
    createAndNavigate,
    loadDefaultAgents,
  }
}

