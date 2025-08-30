import { useState, useCallback, useEffect, useRef } from 'react'
import { 
  inquiryRoomSetting,
  createAndParseUserMessage,
  inquiryRoomMessagesByRoomId
} from '@/lib/api/room'

import { processRoomUserMessage } from '@/lib/api/orchestration'
import { toast } from 'sonner'
import type { Room, RoomMessage } from '@/lib/types/room'
import type { MessageData } from '@/components/room-messages'

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

  // Convert API message format to component message format
  const convertApiMessageToMessageData = useCallback((apiMessage: RoomMessage): MessageData => {
    return {
      id: apiMessage.message_id,
      type: apiMessage.message_type as 'user' | 'agent',
      content: apiMessage.message_content,
      sender_name: apiMessage.message_type === 'user' 
        ? (apiMessage.user_name || 'User')
        : (apiMessage.agent_name || 'Agent'),
      timestamp: apiMessage.message_created_at,
      user_id: apiMessage.message_type === 'user' ? userId : undefined,
      agent_id: apiMessage.message_type === 'agent' ? 'agent_id' : undefined,
    }
  }, [userId])

  // Load room settings
  const loadRoomSetting = useCallback(async () => {
    try {
      console.log('Loading room setting for room:', roomId)
      const response = await inquiryRoomSetting(roomId)
      if (response.success && response.room) {
        setRoom(response.room)
        console.log('Room setting loaded:', response.room)
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

  // Load room messages
  const loadRoomMessages = useCallback(async () => {
    try {
      console.log('Loading messages for room:', roomId)
      const response = await inquiryRoomMessagesByRoomId(roomId)
      if (response.success && response.message_list) {
        const convertedMessages = response.message_list.map(convertApiMessageToMessageData)
        setMessages(convertedMessages)
        console.log('Messages loaded:', convertedMessages.length, 'messages')
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

  // Complete user message sending workflow
  const sendUserMessage = useCallback(async (userInput: string) => {
    console.log('🚀 sendUserMessage called with input:', userInput)
    console.log('🔍 Current state:', { 
      userId: !!userId, 
      userName: !!userName, 
      room: !!room, 
      sending, 
      isProcessing: isProcessingRef.current 
    })

    if (!userId || !userName || !room || sending || isProcessingRef.current) {
      console.log('❌ Cannot send message - conditions not met')
      return false
    }

    try {
      setSending(true)
      isProcessingRef.current = true
      
      console.log('📤 Step 1: Creating and parsing user message')
      console.log('📤 Request data will be:', {
        room_id: roomId,
        message: {
          room_id: roomId,
          message_id: "",
          related_message_id: null,
          user_id: userId,
          user_name: userName,
          message_content: {
            message_text: userInput
          },
          extend_info: null
        }
      })
      
      // Step 1: Send user message to backend
      const createResponse = await createAndParseUserMessage(
        roomId,
        userInput,
        userId,
        userName
      )

      console.log('📤 Step 1 response:', createResponse)

      if (!createResponse.success) {
        throw new Error(`Failed to create user message: ${createResponse.error}`)
      }

      // Extract message_id from createResponse
      const messageId = createResponse.message_id || createResponse.message?.message_id || ""
      console.log('📤 Step 1 extracted message_id:', messageId)

      console.log('✅ Step 1 completed: User message created successfully')
      
      // Step 2: Call processRoomUserMessage to process the message using returned message_id
      console.log('⚡ Step 2: Processing room user message with message_id:', messageId)
      setProcessing(true)
      
      const processResponse = await processRoomUserMessage({
        room_id: roomId,
        room_user_message_id: messageId,
        room_related_message_id: ""
      })
      
      console.log('⚡ Step 2 response:', processResponse)

      if (!processResponse.success) {
        throw new Error(`Failed to process user message: ${processResponse.error}`)
      }
      
      console.log('✅ Step 2 completed: Room user message processed successfully')
      
      // Step 3: Reload messages to get latest state (including agent replies)
      console.log('🔄 Step 3: Reloading messages')
      await loadRoomMessages()
      
      console.log('🎉 Complete workflow finished successfully')
      toast.success('Message sent successfully')
      
      return true
      
    } catch (error) {
      console.error('❌ Error in message workflow:', error)
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
        console.log('Initializing room webhook for roomId:', roomId)
        
        // Load room settings and messages in parallel
        const [roomData, messagesData] = await Promise.all([
          loadRoomSetting(),
          loadRoomMessages()
        ])
        
        if (!roomData) {
          console.error('Failed to load room data')
        }
        if (!messagesData || messagesData.length === 0) {
          console.log('No messages found or failed to load messages')
        }
        
        // Mark initialization complete
        initRef.initialized = true
        initRef.initializing = false
        
        console.log('Room webhook initialization completed')
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
    
    // Actions
    sendUserMessage,
    refreshMessages,
    refreshRoomSetting,
    getAgentList,
    
    // Utility functions
    loadRoomSetting,
    loadRoomMessages,
  }
}
