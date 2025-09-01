import { useState, useCallback, useEffect, useRef } from 'react'
import { 
  inquiryRoomSetting,
  createAndParseUserMessage,
  inquiryRoomMessagesByRoomId,
  updateRoomAgentSet,
  updateRoomName
} from '@/lib/api/room'
import { getAgent } from '@/lib/api/agent'

import { processRoomUserMessage } from '@/lib/api/orchestration'
import { toast } from 'sonner'
import type { Room } from '@/lib/types/room'
// Import the correct RoomMessage type from response.ts (API response format)
import type { RoomMessage } from '@/lib/types/response'
import type { MessageData } from '@/components/room-messages'
import type { Agent } from '@/lib/types/agent'

interface UseRoomWebhookProps {
  roomId: string
  userId?: string
  userName?: string
}

export function useRoomWebhook({ roomId, userId, userName }: UseRoomWebhookProps) {
  const [room, setRoom] = useState<Room | null>(null)
  const [messages, setMessages] = useState<MessageData[]>([])
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [processing, setProcessing] = useState(false)
  const [updatingRoom, setUpdatingRoom] = useState(false)
  
  // Cache for agent names to avoid repeated API calls
  const agentNameCache = useRef<{ [agentId: string]: string }>({})
  
  // Ref to prevent duplicate calls
  const isProcessingRef = useRef(false)
  const initializationRef = useRef<{
    initialized: boolean
    initializing: boolean
    roomId: string | null
  }>({
    initialized: false,
    initializing: false,
    roomId: null
  })

  // Get agent name by agent ID with caching
  const getAgentName = useCallback(async (agentId: string): Promise<string> => {
    // Check cache first to avoid duplicate API calls
    if (agentNameCache.current[agentId]) {
      return agentNameCache.current[agentId]
    }

    try {
      const response = await getAgent(agentId)
      if (response.success && response.agent?.agent_card?.name) {
        const agentName = response.agent.agent_card.name
        // Cache the result for future use
        agentNameCache.current[agentId] = agentName
        return agentName
      } else {
        console.warn('Failed to get agent name for ID:', agentId, response.error)
        return 'Agent'
      }
    } catch (error) {
      console.error('Error fetching agent name for ID:', agentId, error)
      return 'Agent'
    }
  }, [])

  // Convert API message format to component message format
  const convertApiMessageToMessageData = useCallback(async (apiMessage: RoomMessage): Promise<MessageData> => {
    // Extract message content - both user and agent messages use message_text field
    let content: string = ''
    let senderName: string = ''
    
    // Extract content from MessageContent object
    // For both user and agent messages, we use message_content.message_text as the display content
    if (apiMessage.message_content?.message_text) {
      content = apiMessage.message_content.message_text
    } else {
      // Fallback to empty string if message_text is not available
      content = ''
    }

    // Determine sender name based on message type
    if (apiMessage.message_type === 'user') {
      // For user messages, use the provided userName or default to 'User'
      senderName = userName || 'User'
    } else if (apiMessage.message_type === 'agent') {
      // For agent messages, extract agent_id and fetch the real agent name
      let agentId: string | undefined
      
      // Try to get agent_id from different possible locations in the message
      if (apiMessage.agent_id) {
        agentId = apiMessage.agent_id
      } else if (apiMessage.message_content?.message_task?.metadata?.agent_id) {
        agentId = apiMessage.message_content.message_task.metadata.agent_id as string
      }
      
      // Fetch agent name using the agent_id
      if (agentId) {
        try {
          senderName = await getAgentName(agentId)
        } catch (error) {
          console.error('Failed to get agent name for ID:', agentId, error)
          senderName = 'Agent'
        }
      } else {
        console.warn('No agent_id found in agent message:', apiMessage)
        senderName = 'Agent'
      }
    } else {
      senderName = 'Unknown'
    }

    // Return the standardized message data format for the component
    return {
      id: apiMessage.message_id,
      type: apiMessage.message_type as 'user' | 'agent',
      content,
      sender_name: senderName,
      timestamp: apiMessage.message_created_at || new Date().toISOString(),
      user_id: apiMessage.message_type === 'user' ? userId : undefined,
      agent_id: apiMessage.message_type === 'agent' ? (apiMessage.agent_id || 'agent_id') : undefined,
    }
  }, [userId, userName, getAgentName])

  // Load room settings
  const loadRoomSetting = useCallback(async () => {
    try {
      const response = await inquiryRoomSetting(roomId)
      if (response.success && response.room) {
        setRoom(response.room)
        return response.room
      } else {
        console.error('Failed to load room setting:', response.error)
        toast.error('Failed to load room settings')
        return null
      }
    } catch (error) {
      console.error('Error loading room setting:', error)
      toast.error('Failed to load room settings')
      return null
    }
  }, [roomId])

  // Load room messages and convert them to display format
  const loadRoomMessages = useCallback(async () => {
    try {
      // Fetch messages from the API
      const response = await inquiryRoomMessagesByRoomId(roomId)
      if (response.success && response.message_list) {
        // Convert all messages with async agent name fetching
        // This processes both user and agent messages uniformly
        const convertedMessages = await Promise.all(
          response.message_list.map(msg => convertApiMessageToMessageData(msg))
        )
        setMessages(convertedMessages)
        return convertedMessages
      } else {
        console.error('Failed to load messages:', response.error)
        toast.error('Failed to load messages')
        return []
      }
    } catch (error) {
      console.error('Error loading messages:', error)
      toast.error('Failed to load messages')
      return []
    }
  }, [roomId, convertApiMessageToMessageData])

  // Update room settings
  const updateRoomSettings = useCallback(async (roomName: string, selectedAgents: { [agentId: string]: Agent }) => {
    if (!room) {
      toast.error('Room data not available')
      return false
    }

    try {
      setUpdatingRoom(true)
      
      // Create agent set mapping: agent name -> agent id (same as creation)
      const roomAgentSet = Object.fromEntries(
        Object.entries(selectedAgents).map(([id, agent]) => [
          agent.agent_card.name, // key: agent name
          id                     // value: agent id
        ])
      )

      // Update room name if changed
      if (roomName !== room.room_name) {
        const nameResponse = await updateRoomName(roomId, roomName)
        if (!nameResponse.success) {
          throw new Error(`Failed to update room name: ${nameResponse.error}`)
        }
      }

      // Update agent set
      const agentResponse = await updateRoomAgentSet(roomId, roomAgentSet)
      if (!agentResponse.success) {
        throw new Error(`Failed to update room agents: ${agentResponse.error}`)
      }

      // Reload room settings to get updated data
      await loadRoomSetting()
      
      toast.success('Room settings updated successfully')
      return true
      
    } catch (error) {
      console.error('Error updating room settings:', error)
      toast.error(`Failed to update room settings: ${error instanceof Error ? error.message : 'Unknown error'}`)
      return false
    } finally {
      setUpdatingRoom(false)
    }
  }, [room, roomId, loadRoomSetting])

  // Complete user message sending workflow
  const sendUserMessage = useCallback(async (userInput: string) => {
    if (!userId || !userName || !room || sending || isProcessingRef.current) {
      return false
    }

    // Generate temporary message ID for optimistic update
    const tempMessageId = `temp-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
    const currentTime = new Date().toISOString()

    // Step 0: Immediately add user message to UI (optimistic update)
    const optimisticUserMessage: MessageData = {
      id: tempMessageId,
      type: 'user',
      content: userInput,
      sender_name: userName,
      timestamp: currentTime,
      user_id: userId,
    }

    setMessages(prevMessages => [...prevMessages, optimisticUserMessage])

    try {
      setSending(true)
      isProcessingRef.current = true
      
      // Step 1: Send user message to backend
      const createResponse = await createAndParseUserMessage(
        roomId,
        userInput,
        userId,
        userName
      )

      if (!createResponse.success) {
        throw new Error(`Failed to create user message: ${createResponse.error}`)
      }

      // Extract message_id from createResponse
      const messageId = createResponse.message_id || createResponse.message?.message_id || ""

      // Step 2: Call processRoomUserMessage to process the message using returned message_id
      setProcessing(true)
      
      const processResponse = await processRoomUserMessage({
        room_id: roomId,
        room_user_message_id: messageId,
        room_related_message_id: ""
      })

      if (!processResponse.success) {
        throw new Error(`Failed to process user message: ${processResponse.error}`)
      }
      
      // Step 3: Reload messages to get latest state (including agent replies)
      // This will replace the optimistic message with the real one and add any agent responses
      await loadRoomMessages()
      
      toast.success('Message sent successfully')
      
      return true
      
    } catch (error) {
      console.error('Error in message workflow:', error)
      
      // Remove the optimistic message on error
      setMessages(prevMessages => prevMessages.filter(msg => msg.id !== tempMessageId))
      
      toast.error(`Failed to send message: ${error instanceof Error ? error.message : 'Unknown error'}`)
      
      // Even if error occurs, try to reload messages to ensure UI sync
      try {
        await loadRoomMessages()
      } catch (reloadError) {
        console.error('Failed to reload messages after error:', reloadError)
      }
      
      return false
    } finally {
      setSending(false)
      setProcessing(false)
      isProcessingRef.current = false
    }
  }, [userId, userName, room, roomId, sending, loadRoomMessages])

  // Manually refresh messages
  const refreshMessages = useCallback(async () => {
    await loadRoomMessages()
  }, [loadRoomMessages])

  // Manually refresh room settings
  const refreshRoomSetting = useCallback(async () => {
    await loadRoomSetting()
  }, [loadRoomSetting])

  // Get agent list for @mentions
  const getAgentList = useCallback(() => {
    if (!room?.room_agent_set) return []
    return Object.entries(room.room_agent_set).map(([name, id]) => ({ id, name }))
  }, [room])

  // Get current room data for form initialization
  const getRoomFormData = useCallback(() => {
    if (!room) return null
    
    return {
      roomName: room.room_name || '',
      roomId: room.room_id || '',
      selectedAgents: room.room_agent_set || {},
      roomOwnerId: room.room_owner_id || '',
      roomOwnerName: room.room_owner_name || ''
    }
  }, [room])

  // Initialize room data
  useEffect(() => {
    const initRef = initializationRef.current
    
    // Check if initialization is needed
    if (!roomId || 
        initRef.initialized || 
        initRef.initializing || 
        initRef.roomId === roomId) {
      return
    }

    // Mark initialization start
    initRef.initializing = true
    initRef.roomId = roomId

    const initializeRoom = async () => {
      try {
        setLoading(true)
        
        // Load room settings and messages in parallel
        const [roomData, messagesData] = await Promise.all([
          loadRoomSetting(),
          loadRoomMessages()
        ])
        
        if (!roomData) {
          console.error('Failed to load room data')
        }
        if (!messagesData || messagesData.length === 0) {
        }
        
        // Mark initialization complete
        initRef.initialized = true
        initRef.initializing = false
        
      } catch (error) {
        console.error('Error initializing room webhook:', error)
        toast.error('Failed to initialize room')
        initRef.initialized = true
        initRef.initializing = false
      } finally {
        setLoading(false)
      }
    }

    initializeRoom()
  }, [roomId, loadRoomSetting, loadRoomMessages])

  // Reset initialization state when roomId changes
  useEffect(() => {
    const initRef = initializationRef.current
    if (initRef.roomId !== roomId) {
      initRef.initialized = false
      initRef.initializing = false
      initRef.roomId = null
      // Reset state
      setRoom(null)
      setMessages([])
      setLoading(true)
      setSending(false)
      setProcessing(false)
      setUpdatingRoom(false)
    }
  }, [roomId])

  // Cleanup function
  useEffect(() => {
    return () => {
      // Clear state when component unmounts
      isProcessingRef.current = false
      const initRef = initializationRef.current
      initRef.initialized = false
      initRef.initializing = false
      initRef.roomId = null
    }
  }, [])

  return {
    // State
    room,
    messages,
    loading,
    sending,
    processing,
    updatingRoom,
    
    // Actions
    sendUserMessage,
    updateRoomSettings,
    refreshMessages,
    refreshRoomSetting,
    getAgentList,
    getRoomFormData,
    
    // Utility functions
    loadRoomSetting,
    loadRoomMessages,
  }
}
