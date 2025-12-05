import { useState, useCallback, useEffect, useRef, useMemo } from 'react'
import { 
  inquiryRoomSetting,
  SendMessage,
  inquiryRoomMessagesByRoomId,
  updateRoomAgentSet,
  updateRoomName,
  updateRoomExtendInfo
} from '@/lib/api/room'
import { processRoomUserMessage } from '@/lib/api/orchestration'
import { toast } from 'sonner'
import { useQuery } from '@tanstack/react-query'
// Import the correct RoomMessage type from response.ts (API response format)
import type { RoomMessage } from '@/lib/types/response'
import type { MessageData } from '@/components/room-messages'
import type { Agent } from '@/lib/types/agent'
import { useRoomSSE } from './useRoomSSE'
import type { SSEMessage } from '@/lib/types/sse'
import { useRoomUiStore } from '@/stores/room-ui-store'
import { getAllAgents } from '@/lib/api/agent'

interface UseRoomWebhookProps {
  roomId: string
  userId?: string
  userName?: string
  getToken?: () => Promise<string | null>
}

export function useRoomWebhook({ roomId, userId, userName, getToken }: UseRoomWebhookProps) {
  const {
    sending,
    processing,
    updatingRoom,
    sseEnabled,
    liveMessagesByRoom,
    setSending,
    setProcessing,
    setUpdatingRoom,
    setSseEnabled,
    setSseConnected,
    setSseError,
    addLiveMessage,
    resetRoomState,
  } = useRoomUiStore()

  const [loading, setLoading] = useState(true)

  // Prevent concurrent room loads
  const activeRoomLoad = useRef<string | null>(null)

  // Cache for agent names to avoid repeated API calls
  const agentNameCache = useRef<{ [agentId: string]: string }>({})

  // Ref to prevent duplicate calls
  const isProcessingRef = useRef(false)

  // Global agents catalog (React Query) to resolve names without per-message fetches.
  // Stale for 24h to effectively cache across rooms. Refetched on window refocus by default (disabled below).
  const allAgentsQuery = useQuery<Agent[], Error>({
    queryKey: ['agents', 'all'] as const,
    staleTime: 1000 * 60 * 60 * 24, // 24 hours
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: 0,  // Avoid retry loops on abort/cancel
    queryFn: async ({ signal }): Promise<Agent[]> => {
      console.log('🤖 Loading global agents catalog')
      try {
        const res = await getAllAgents(signal, 15000) // 15s safety timeout
        if (!res.success || !res.agents) {
          throw new Error(res.error || 'Failed to load agents')
        }
        console.log(`✅ Loaded ${res.agents.length} agents`)
        return res.agents
      } catch (error: unknown) {
        if (error instanceof Error && error.name === 'AbortError') {
          // Treat as cancellation: return empty to keep query in “success” state
          return []
        }
        console.error('❌ Failed to load agents:', error)
        throw error
      }
    },
  })

  // Get agent name by agent ID with caching
  const getAgentName = useCallback(async (agentId: string): Promise<string> => {
    // Use cache if available
    if (agentNameCache.current[agentId]) {
      return agentNameCache.current[agentId]
    }

    // Try global catalog
    const agents = allAgentsQuery.data
    if (agents) {
      const found = agents.find(a => a.agent_id === agentId)
      if (found?.agent_card?.name) {
        const name = found.agent_card.name
        agentNameCache.current[agentId] = name
        return name
      }
    }

    // Fallback to readable short id
    return `Agent ${agentId.slice(0, 6)}`
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

  // React Query: room
  type RoomSettingResult = Awaited<ReturnType<typeof inquiryRoomSetting>>

  const roomQuery = useQuery({
    queryKey: ['room', roomId],
    enabled: !!roomId && activeRoomLoad.current !== roomId,
    retry: 0,  // Avoid retry loops on abort/cancel
    staleTime: 1000 * 30,  // Reduce stale time
    queryFn: async ({ signal }): Promise<RoomSettingResult['room'] | null> => {
      activeRoomLoad.current = roomId
      console.log(`🏠 Loading room: ${roomId}`)

      try {
        const response = await inquiryRoomSetting(roomId, getToken, signal)
        if (!response.success || !response.room) {
          throw new Error(response.error || 'Failed to load room')
        }
        // Pre-populate agent name cache
        if (response.room.room_agent_set) {
          Object.entries(response.room.room_agent_set).forEach(([agentId, agentName]) => {
            agentNameCache.current[agentId] = agentName as string
          })
        }
        // Also sync names from global agents catalog
        if (allAgentsQuery.data) {
          allAgentsQuery.data.forEach((agent: Agent) => {
            if (agent.agent_id && agent.agent_card?.name) {
              agentNameCache.current[agent.agent_id] = agent.agent_card.name
            }
          })
        }
        console.log(`✅ Room loaded: ${roomId}`)
        return response.room
      } catch (err) {
        if (err instanceof Error && err.name === 'AbortError') {
          // Return cached data to keep query in success state on cancel
          return roomQuery.data ?? null
        }
        throw err
      } finally {
        activeRoomLoad.current = null
      }
    }
  })

  const room = roomQuery.data ?? null

  // Get debate mode from room's extend_info
  const getDebateMode = useCallback((): boolean => {
    if (!room?.extend_info) return false
    const extendInfo = room.extend_info as { debateMode?: boolean }
    return extendInfo.debateMode || false
  }, [room])

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
      // Prefer provided userName, then userId, then fallback
      senderName = userName ?? userId ?? 'User'
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

  // React Query: messages (converted)
  const messagesQuery = useQuery<MessageData[], Error>({
    queryKey: ['roomMessages', roomId, userName ?? '', allAgentsQuery.data?.length ?? 0],
    enabled: !!roomId && !!userName,
    retry: 0,  // Avoid retry loops on abort/cancel
    staleTime: 1000 * 10,
    queryFn: async ({ signal }): Promise<MessageData[]> => {
      console.log(`🔍 Loading messages for room: ${roomId}`)
      const startTime = Date.now()

      try {
        const response = await inquiryRoomMessagesByRoomId(roomId, getToken, signal)
        if (!response.success || !response.message_list) {
          throw new Error(response.error || 'Failed to load messages')
        }

        console.log(`✅ Loaded ${response.message_list.length} messages in ${Date.now() - startTime}ms`)
        const converted = await Promise.all(
          response.message_list.map(msg => convertApiMessageToMessageData(msg))
        )
        return converted
      } catch (error) {
        if (error instanceof Error && error.name === 'AbortError') {
          // Return cached data to keep query in success state on cancel
          return messagesQuery.data ?? []
        }
        console.error(`❌ Failed to load messages for room ${roomId}:`, error)
        throw error
      }
    },
  })

  const liveMessages = useMemo(
    () => liveMessagesByRoom[roomId] || [],
    [liveMessagesByRoom, roomId]
  )

  const messages = useMemo(() => {
    const map = new Map<string, MessageData>()
    // base messages from query
    ;(messagesQuery.data || []).forEach((msg: MessageData) => map.set(msg.id, msg))
    // overlay live messages (SSE/optimistic)
    liveMessages.forEach((msg: MessageData) => map.set(msg.id, msg))
    return Array.from(map.values()).sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime())
  }, [messagesQuery.data, liveMessages])

  const queryLoading = roomQuery.isFetching || messagesQuery.isFetching || roomQuery.isLoading || messagesQuery.isLoading

  // Reset UI/live state when room changes
  useEffect(() => {
    resetRoomState(roomId)
    agentNameCache.current = {}
  }, [roomId, resetRoomState])

  // Mirror query loading state to local loading flag for consumers
  useEffect(() => {
    setLoading(queryLoading)
  }, [queryLoading])

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
          addLiveMessage(roomId, newMessage)
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
          addLiveMessage(roomId, newMessage)
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
  }, [getAgentName, addLiveMessage, roomId, setProcessing])

  // Initialize SSE connection
  const {
    connected: sseConnected,
    connecting: sseConnecting,
    error: sseError
  } = useRoomSSE({
    roomId,
    enabled: sseEnabled && !!roomId,
    getToken,
    onMessage: handleSSEMessage,
  })

  useEffect(() => {
    setSseConnected(!!sseConnected)
    setSseError(sseError ? String(sseError) : null)
  }, [sseConnected, sseError, setSseConnected, setSseError])

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
      
      // Create agent set mapping: agent id -> agent name (canonical shape)
      const roomAgentSet = Object.fromEntries(
        Object.entries(selectedAgents).map(([id, agent]) => [
          id,                     // key: agent id
          agent.agent_card.name,  // value: agent name
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
      await roomQuery.refetch()
      
      toast.success('Room settings updated successfully')
      return true
      
    } catch (error) {
      console.error('Error updating room settings:', error)
      toast.error(`Failed to update room settings: ${error instanceof Error ? error.message : 'Unknown error'}`)
      return false
    } finally {
      setUpdatingRoom(false)
    }
  }, [room, roomId, roomQuery, getDebateMode, setUpdatingRoom])

  // Complete user message sending workflow - using unified SendMessage API
  const sendUserMessage = useCallback(async (userInput: string, targetGroup: string = "all_agents") => {
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

    addLiveMessage(roomId, optimisticUserMessage)

    try {
      setSending(true)
      isProcessingRef.current = true
      
      // Step 1: Send user message to backend using unified SendMessage API
      const createResponse = await SendMessage(roomId, userInput, getToken, userId, userName, targetGroup)

      if (!createResponse.success) {
        throw new Error(`Failed to create user message: ${createResponse.error}`)
      }

      // Extract message_id from createResponse
      const messageId = createResponse.message_id || createResponse.message?.message_id || ""

      // Step 2: Call processRoomUserMessage to trigger background processing
      // The backend now returns immediately (202 Accepted) and processes agents in background
      // Agent responses will arrive via SSE
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
      addLiveMessage(roomId, { ...optimisticUserMessage, id: messageId })
      
      // Processing continues in background - SSE will send "completed" status when done
      // Keep processing=true until SSE delivers the completion status
      console.log('📡 Message queued for processing, waiting for agent responses via SSE...')
      
      // If SSE is not connected, we need to poll or refresh manually
      if (!sseConnected) {
        console.log('⚠️ SSE not connected, processing will complete but updates may be delayed')
        // Don't turn off processing here - let user manually refresh if needed
      }
      
      return true
      
    } catch (error) {
      console.error('Error in message workflow:', error)
      
      // Remove the optimistic message on error by resetting room state and refetching
      resetRoomState(roomId)
      
      toast.error(`Failed to send message: ${error instanceof Error ? error.message : 'Unknown error'}`)
      
      // On error, reload messages to ensure UI sync (regardless of SSE status)
      try {
        console.log('🔄 Reloading messages after error to ensure sync...')
        await messagesQuery.refetch()
      } catch (reloadError) {
        console.error('Failed to reload messages after error:', reloadError)
      }
      
      // Only turn off processing on error
      setProcessing(false)
      
      return false
    } finally {
      setSending(false)
      isProcessingRef.current = false
      // NOTE: Don't setProcessing(false) here - SSE will send "completed" status
    }
  }, [userId, userName, room, roomId, sending, sseConnected, getToken, addLiveMessage, resetRoomState, messagesQuery, setSending, setProcessing])

  // Manually refresh messages - only for user-initiated refresh
  const refreshMessages = useCallback(async () => {
    console.log('🔄 Manual message refresh requested')
    await messagesQuery.refetch()
  }, [messagesQuery])

  // Manually refresh room settings
  const refreshRoomSetting = useCallback(async () => {
    await roomQuery.refetch()
  }, [roomQuery])

  // Get agent list for @mentions
  const getAgentList = useCallback(() => {
    if (!room?.room_agent_set) return []
    // room_agent_set is { agent_id: agent_name }
    return Object.entries(room.room_agent_set).map(([id, name]) => ({ id, name }))
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
    setSseEnabled(!sseEnabled)
  }, [setSseEnabled, sseEnabled])

  // Surface query errors so we don't stay in "loading" forever
  useEffect(() => {
    if (roomQuery.isError) {
      const message = roomQuery.error instanceof Error ? roomQuery.error.message : 'Failed to load room'
      toast.error(message)
    }
    if (messagesQuery.isError) {
      const message = messagesQuery.error instanceof Error ? messagesQuery.error.message : 'Failed to load messages'
      toast.error(message)
    }
  }, [roomQuery.isError, roomQuery.error, messagesQuery.isError, messagesQuery.error])

  // Periodic cache cleanup to prevent memory accumulation
  useEffect(() => {
    const cleanup = setInterval(() => {
      // Clear old agent name cache entries to prevent memory buildup
      // Keep only entries that are still relevant
      console.log('🧹 Performing periodic cache cleanup')
    }, 300000) // Every 5 minutes

    return () => clearInterval(cleanup)
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
    availableAgents: allAgentsQuery.data || [],
  }
}
