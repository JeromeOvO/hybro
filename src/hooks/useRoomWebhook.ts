import { useState, useCallback, useEffect, useRef } from 'react'
import { 
  inquiryRoomSetting,
  SendMessage,
  inquiryRoomMessagesByRoomId,
  updateRoomAgentSet,
  updateRoomName,
  updateRoomExtendInfo
} from '@/lib/api/room'
import { getAgent } from '@/lib/api/agent'

import { processRoomUserMessage } from '@/lib/api/orchestration'
import { toast } from 'sonner'
import type { Room } from '@/lib/types/room'
// Import the correct RoomMessage type from response.ts (API response format)
import type { RoomMessage } from '@/lib/types/response'
import type { MessageData } from '@/components/room-messages'
import type { Agent } from '@/lib/types/agent'
import { useRoomSSE } from './useRoomSSE'
import type { SSEMessage } from '@/lib/types/sse'

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
  
  // SSE state
  const [sseEnabled, setSseEnabled] = useState(true)
  
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

  // Get debate mode from room's extend_info
  const getDebateMode = useCallback((): boolean => {
    if (!room?.extend_info) return false
    const extendInfo = room.extend_info as { debateMode?: boolean }
    return extendInfo.debateMode || false
  }, [room])

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

  // Handle SSE messages - REMOVED loadRoomMessages calls
  const handleSSEMessage = useCallback(async (sseMessage: SSEMessage) => {
    console.log('🔔 Room webhook received SSE message:', sseMessage)
    
    switch (sseMessage.type) {
      case 'user_message':
        console.log('📨 User message received via SSE')
        // SSE provides the message data, no need to reload
        if (sseMessage.data?.content) {
          const newMessage: MessageData = {
            id: sseMessage.data.message_id || `sse-${Date.now()}`,
            type: 'user',
            content: sseMessage.data.content,
            sender_name: sseMessage.data.user_id || 'User',
            timestamp: sseMessage.timestamp,
            user_id: sseMessage.data.user_id,
          }
          setMessages(prev => {
            // Check if message with this ID or content already exists
            const existingIndex = prev.findIndex(msg => 
              msg.id === newMessage.id || 
              (msg.type === 'user' && msg.content === newMessage.content)
            )
            
            if (existingIndex !== -1) {
              // Update existing message with real data from SSE
              const updated = [...prev]
              updated[existingIndex] = { ...updated[existingIndex], ...newMessage }
              return updated
            }
            
            // Add new message if it doesn't exist
            return [...prev, newMessage]
          })
        }
        break
        
      case 'agent_response':
        console.log('🤖 Agent response received via SSE')
        // SSE provides the agent response data, no need to reload
        if (sseMessage.data?.content !== undefined && sseMessage.data?.agent_id) {
          const agentName = await getAgentName(sseMessage.data.agent_id)
          const newMessage: MessageData = {
            id: sseMessage.data.message_id || `sse-agent-${Date.now()}`,
            type: 'agent',
            content: sseMessage.data.content,
            sender_name: agentName,
            timestamp: sseMessage.timestamp,
            agent_id: sseMessage.data.agent_id,
          }
          setMessages(prev => {
            // Check if message already exists
            if (prev.some(msg => msg.id === newMessage.id)) {
              return prev
            }
            return [...prev, newMessage]
          })
        }
        break
        
      case 'processing_status':
        console.log('⚙️ Processing status update:', sseMessage.data?.status)
        if (sseMessage.data?.status) {
          if (sseMessage.data.status === 'processing') {
            setProcessing(true)
          } else if (sseMessage.data.status === 'completed') {
            setProcessing(false)
            // Don't reload messages, they should come via SSE
          } else if (sseMessage.data.status === 'failed') {
            setProcessing(false)
            toast.error(`Processing failed: ${sseMessage.data.details || 'Unknown error'}`)
          }
        }
        break
        
      case 'error':
        console.error('❌ SSE error message:', sseMessage.data)
        toast.error(`Real-time update error: ${sseMessage.data?.details || 'Unknown error'}`)
        break
        
      case 'heartbeat':
        // Heartbeat message, no action needed
        console.log('💓 SSE heartbeat received')
        break
        
      default:
        console.log('❓ Unknown SSE message type:', sseMessage.type)
    }
  }, [getAgentName])

  // Initialize SSE connection
  const {
    connected: sseConnected,
    connecting: sseConnecting,
    error: sseError
  } = useRoomSSE({
    roomId,
    enabled: sseEnabled && !!roomId,
    onMessage: handleSSEMessage,
  })

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

  // Update room settings - now includes debate mode
  const updateRoomSettings = useCallback(async (
    roomName: string, 
    selectedAgents: { [agentId: string]: Agent }, 
    debateMode: boolean
  ) => {
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
      console.log('🔄 Updating room settings:', { roomName, roomAgentSet, debateMode })

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

      // Update extend_info with debate mode using the new API
      const currentDebateMode = getDebateMode()
      if (debateMode !== currentDebateMode) {
        const updatedExtendInfo = {
          ...(room.extend_info as object || {}),
          debateMode
        }
        
        const extendInfoResponse = await updateRoomExtendInfo(roomId, updatedExtendInfo)
        if (!extendInfoResponse.success) {
          throw new Error(`Failed to update debate mode: ${extendInfoResponse.error}`)
        }
        
        console.log('✅ Debate mode updated:', debateMode ? 'ENABLED' : 'DISABLED')
      }

      // Reload room settings to get updated data from backend
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
  }, [room, roomId, loadRoomSetting, getDebateMode])

  // Complete user message sending workflow - using unified SendMessage API
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
      
      // Step 1: Send user message to backend using unified SendMessage API
      const createResponse = await SendMessage(roomId, userInput, userId, userName)

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
      
      // Step 3: Replace optimistic message ID with real message ID
      // Keep the message in UI regardless of SSE status to prevent it from disappearing
      setMessages(prevMessages => 
        prevMessages.map(msg => 
          msg.id === tempMessageId 
            ? { ...msg, id: messageId } 
            : msg
        )
      )
      
      // If SSE is not connected, refresh all messages to ensure sync
      if (!sseConnected) {
        console.log('📡 SSE not connected, manually refreshing messages as fallback...')
        await loadRoomMessages()
      } else {
        console.log('📡 SSE connected, user message kept in UI, waiting for agent responses...')
      }
      
      toast.success('Message sent successfully')
      
      return true
      
    } catch (error) {
      console.error('Error in message workflow:', error)
      
      // Remove the optimistic message on error
      setMessages(prevMessages => prevMessages.filter(msg => msg.id !== tempMessageId))
      
      toast.error(`Failed to send message: ${error instanceof Error ? error.message : 'Unknown error'}`)
      
      // On error, reload messages to ensure UI sync (regardless of SSE status)
      try {
        console.log('🔄 Reloading messages after error to ensure sync...')
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
  }, [userId, userName, room, roomId, sending, loadRoomMessages, sseConnected])

  // Manually refresh messages - only for user-initiated refresh
  const refreshMessages = useCallback(async () => {
    console.log('🔄 Manual message refresh requested')
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

  // Get current room data for form initialization - now includes debate mode
  const getRoomFormData = useCallback(() => {
    if (!room) return null
    
    return {
      roomName: room.room_name || '',
      roomId: room.room_id || '',
      selectedAgents: room.room_agent_set || {},
      roomOwnerId: room.room_owner_id || '',
      roomOwnerName: room.room_owner_name || '',
      debateMode: getDebateMode()
    }
  }, [room, getDebateMode])

  // Toggle SSE connection
  const toggleSSE = useCallback(() => {
    setSseEnabled(prev => !prev)
  }, [])

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
    
    // SSE State
    sseConnected,
    sseConnecting,
    sseError,
    sseEnabled,
    
    // Debate Mode
    debateMode: getDebateMode(),
    
    // Actions
    sendUserMessage,
    updateRoomSettings,
    refreshMessages,
    refreshRoomSetting,
    getAgentList,
    getRoomFormData,
    toggleSSE,
    
    // Utility functions
    loadRoomSetting,
    loadRoomMessages,
  }
}
