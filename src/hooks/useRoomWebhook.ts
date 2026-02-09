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
import { cancelMessage } from '@/lib/api/sse'
import type { A2ATaskStatus } from '@/lib/api/a2a-tasks'
import { extractTaskContent, extractTaskError } from '@/lib/api/a2a-tasks'
import { banner } from "@/components/ui/banner"
import { useQuery } from '@tanstack/react-query'
// Import the correct RoomMessage type from response.ts (API response format)
import type { RoomMessage } from '@/lib/types/response'
import type { MessageData } from '@/components/room-messages'
import type { Agent } from '@/lib/types/agent'
import { useRoomSSE } from './useRoomSSE'
import type { SSEMessage, TaskState } from '@/lib/types/sse'
import { useRoomUiStore } from '@/stores/room-ui-store'
import { getAllActiveAgents } from '@/lib/api/agent'
import { normalizeTimestampOrNow, isStale } from '@/lib/time'

// Special system agent display names (module scoped for stable reference)
const SYSTEM_AGENT_NAMES: Record<string, string> = {
  'debate_summary': 'Debate Coordinator',
  'non_debate_summary': 'Summary Agent',
}

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
    replaceLiveMessage,
    removeLiveMessage,
    resetRoomLiveState,
  } = useRoomUiStore()

  const [loading, setLoading] = useState(true)

  // Prevent concurrent room loads
  const activeRoomLoad = useRef<string | null>(null)

  // Cache for agent names to avoid repeated API calls
  const agentNameCache = useRef<{ [agentId: string]: string }>({})

  // Ref to prevent duplicate calls
  const isProcessingRef = useRef(false)

  // Track current processing message ID for cancellation
  const currentProcessingMessageId = useRef<string | null>(null)

  // Processing placeholder ID - used to show "Processing your request" before first task arrives
  const getProcessingPlaceholderId = useCallback(() => `processing-placeholder-${roomId}`, [roomId])

  // Global agents catalog (React Query) to resolve names without per-message fetches.
  // Stale for 24h to effectively cache across rooms. Refetched on window refocus by default (disabled below).
  const allAgentsQuery = useQuery<Agent[], Error>({
    queryKey: ['agents', 'active'] as const,
    staleTime: 1000 * 60 * 60 * 24, // 24 hours
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: 0,  // Avoid retry loops on abort/cancel
    queryFn: async ({ signal }): Promise<Agent[]> => {
      console.log('🤖 Loading global active agents catalog')
      try {
        const res = await getAllActiveAgents(signal, 15000) // 15s safety timeout
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
    // Check for system agents first
    if (SYSTEM_AGENT_NAMES[agentId]) {
      return SYSTEM_AGENT_NAMES[agentId]
    }

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
    let taskStatus: string | undefined
    let taskError: string | undefined
    let taskContent: string | undefined

    // Extract content from MessageContent object
    // For both user and agent messages, we use message_content.message_text as the display content
    if (apiMessage.message_content?.message_text) {
      content = apiMessage.message_content.message_text
    } else {
      // Fallback to empty string if message_text is not available
      content = ''
    }

    // Always extract task error from task status if a task exists (independent of content).
    // The error message lives in task.status.message.parts, which is separate from message_text.
    const messageTask = apiMessage.message_content?.message_task
    if (messageTask) {
      const messageTaskTyped = messageTask as A2ATaskStatus['task']
      const extractedError = extractTaskError(messageTaskTyped)
      if (extractedError) {
        taskError = extractedError
      }
      // If message_text was empty, also try to derive display content from task artifacts
      if (!content) {
        const extractedContent = extractTaskContent(messageTaskTyped)
        if (extractedContent) {
          content = extractedContent
        } else if (extractedError) {
          content = extractedError
        }
      }
    }

    // Capture task status if present
    const maybeStatus = messageTask?.status?.state
    if (typeof maybeStatus === 'string') {
      taskStatus = maybeStatus
    }

    // Extract task_content from API response field (preferred) or metadata (fallback for SSE)
    if (apiMessage.task_content) {
      taskContent = apiMessage.task_content
    } else {
      const maybeTaskContent = messageTask?.metadata?.task_content
      if (typeof maybeTaskContent === 'string') {
        taskContent = maybeTaskContent
      }
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

    // Check if timestamp is missing before normalization (which might default to Now)
    const timestampMissing = !apiMessage.message_created_at

    // Return the standardized message data format for the component
    return {
      timestamp_was_missing: timestampMissing,
      id: apiMessage.message_id,
      type: apiMessage.message_type as 'user' | 'agent',
      content,
      sender_name: senderName,
      timestamp: normalizeTimestampOrNow(apiMessage.message_created_at),
      user_id: apiMessage.message_type === 'user' ? userId : undefined,
      agent_id: apiMessage.message_type === 'agent' ? (apiMessage.agent_id || 'agent_id') : undefined,
      task_status: taskStatus,
      task_error: taskError,
      step_number: apiMessage.step_number ?? undefined,
      total_steps: apiMessage.total_steps ?? undefined,
      task_content: taskContent,
      task_updated_at: apiMessage.task_updated_at ? normalizeTimestampOrNow(apiMessage.task_updated_at) : undefined,
      task_created_at: apiMessage.message_created_at ? normalizeTimestampOrNow(apiMessage.message_created_at) : undefined,
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

        // Post-process messages to handle in-progress tasks consistently with live SSE behavior:
        // - Completed tasks with content: show as agent message bubbles
        // - Recent non-terminal tasks: show as TaskStatusMessage (type: 'task')
        // - Stale non-terminal tasks (older than threshold): convert to failed with timeout error
        // - Other incomplete tasks: hide them (shouldn't exist in normal operation)
        const terminalStates = ['completed', 'failed', 'canceled', 'rejected']

        // Threshold for considering a non-terminal task as "stale" (10 minutes)
        // This matches the backend's stale_check_minutes setting
        // Tasks not updated within this time are likely abandoned/crashed
        const STALE_TASK_THRESHOLD_MS = 10 * 60 * 1000

        // Identify ALL stale non-terminal tasks (not just the last one)
        const staleTaskIds = new Set<string>()
        const recentNonTerminalTaskIds = new Set<string>()

        converted.forEach(msg => {
          if (msg.type === 'agent' && msg.task_status && !terminalStates.includes(msg.task_status)) {
            // Use task_updated_at for staleness check (preferred), fall back to message timestamp
            // task_updated_at is refreshed by the backend whenever the agent reports progress
            // or when the stale task checker polls and confirms the task is still working
            // Force stale if timestamp was missing (timestamp_was_missing flag)
            const timestampToCheck = msg.task_updated_at || (msg.timestamp_was_missing ? null : msg.timestamp)
            const taskIsStale = isStale(timestampToCheck, STALE_TASK_THRESHOLD_MS)

            if (taskIsStale) {
              staleTaskIds.add(msg.id)
            } else {
              recentNonTerminalTaskIds.add(msg.id)
            }
          }
        })

        if (staleTaskIds.size > 0) {
          console.log(`🕒 Found ${staleTaskIds.size} stale task(s), converting to failed state`)
        }

        // Filter and transform messages
        const processed = converted.filter(msg => {
          // Always keep user messages
          if (msg.type === 'user') return true

          // For agent messages, check task status
          const hasContent = msg.content && msg.content.trim().length > 0
          const isTerminal = !msg.task_status || terminalStates.includes(msg.task_status)
          const isRecentTask = recentNonTerminalTaskIds.has(msg.id)
          const isStaleTask = staleTaskIds.has(msg.id)

          // Keep if: has content, is terminal, is a recent running task, or is a stale task (will convert to failed)
          return hasContent || isTerminal || isRecentTask || isStaleTask
        }).map(msg => {
          // Convert stale tasks to failed state with timeout error
          // Convert stale tasks to failed state with timeout error
          if (staleTaskIds.has(msg.id)) {
            return {
              ...msg,
              type: 'task' as const, // Use task type for better UI (Red task bubble)
              task_status: 'failed',
              task_error: 'Task timed out - no updates received within the expected timeframe',
              content: msg.content || 'Task failed due to timeout'
            }
          }

          // Convert recent running tasks to type: 'task' so they render as TaskStatusMessage
          if (msg.type === 'agent' && recentNonTerminalTaskIds.has(msg.id)) {
            return { ...msg, type: 'task' as const }
          }

          // Convert failed/rejected/canceled tasks to type: 'task' so they render as TaskStatusMessage
          // (API returns these as type: 'agent' but they should show the red task status bubble)
          if (msg.type === 'agent' && msg.task_status && ['failed', 'rejected', 'canceled'].includes(msg.task_status)) {
            return { ...msg, type: 'task' as const }
          }

          return msg
        })

        return processed
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
      ; (messagesQuery.data || []).forEach((msg: MessageData) => map.set(msg.id, msg))
    // overlay live messages (SSE/optimistic)
    liveMessages.forEach((msg: MessageData) => map.set(msg.id, msg))
    // Sort messages with special handling for task messages:
    // - For task messages (those with step_number), prioritize step_number over timestamp
    //   This ensures task bubbles maintain correct order even when timestamps differ slightly
    // - For non-task messages, sort by timestamp
    // - Use message_id as final tiebreaker for stability
    return Array.from(map.values()).sort((a, b) => {
      const aHasStep = a.step_number !== undefined && a.step_number !== null
      const bHasStep = b.step_number !== undefined && b.step_number !== null

      // If both messages have step_number, they're part of a workflow - sort by step_number
      if (aHasStep && bHasStep) {
        const stepDiff = a.step_number! - b.step_number!
        if (stepDiff !== 0) return stepDiff
        // Same step_number (shouldn't happen normally) - fall through to timestamp
      }

      // Primary sort by timestamp for non-task messages or when step_number is equal
      const timeDiff = new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
      if (timeDiff !== 0) return timeDiff

      // Secondary sort by step_number if only one has it (task messages after their trigger)
      const stepA = a.step_number ?? Infinity
      const stepB = b.step_number ?? Infinity
      if (stepA !== stepB) return stepA - stepB

      // Tertiary sort by message_id for stability
      return a.id.localeCompare(b.id)
    })
  }, [messagesQuery.data, liveMessages])

  const queryLoading = roomQuery.isFetching || messagesQuery.isFetching || roomQuery.isLoading || messagesQuery.isLoading

  // Reset UI/live state when room changes
  useEffect(() => {
    resetRoomLiveState(roomId)
    agentNameCache.current = {}
  }, [roomId, resetRoomLiveState])

  // Mirror query loading state to local loading flag for consumers
  useEffect(() => {
    setLoading(queryLoading)
  }, [queryLoading])

  // Restore processing placeholder on page load if room has an active processing_message_id
  // This ensures the "Processing your request..." bubble persists across page refreshes
  useEffect(() => {
    // Only run when room data is loaded and not currently loading
    if (!room || queryLoading) return
    
    // Check if room has an active processing state
    if (room.processing_message_id) {
      console.log('🔄 Restoring processing placeholder for message:', room.processing_message_id)
      
      // Check if any task messages already exist in the loaded messages
      // If tasks exist, the placeholder should not be shown (tasks have already started)
      const loadedMessages = messagesQuery.data || []
      const hasTaskMessages = loadedMessages.some(m => m.type === 'task')
      
      if (hasTaskMessages) {
        console.log('🔄 Skipping placeholder - tasks already exist')
        return
      }
      
      // Check if placeholder already exists in live messages
      const existingMessages = liveMessagesByRoom[roomId] || []
      const placeholderId = getProcessingPlaceholderId()
      const hasPlaceholder = existingMessages.some(m => m.id === placeholderId)
      
      if (!hasPlaceholder) {
        // Add the processing placeholder
        const processingPlaceholder: MessageData = {
          id: placeholderId,
          type: 'task',
          content: '',
          sender_name: 'HYBRO AI',
          timestamp: new Date().toISOString(),
          task_status: 'working',
          task_content: 'Processing your request...',
        }
        addLiveMessage(roomId, processingPlaceholder)
        setProcessing(true)
      }
    }
  }, [room, queryLoading, roomId, messagesQuery.data, liveMessagesByRoom, getProcessingPlaceholderId, addLiveMessage, setProcessing])

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
            timestamp: normalizeTimestampOrNow(sseMessage.timestamp),
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
            timestamp: normalizeTimestampOrNow(sseMessage.timestamp),
            agent_id: sseMessage.data.agent_id,
          }
          addLiveMessage(roomId, newMessage)
        }
        break

      case 'processing_status':
        console.log('⚙️ Processing status update:', sseMessage.data?.status)
        if (sseMessage.data?.status) {
          const status = sseMessage.data.status

          if (status === 'processing') {
            setProcessing(true)
          } else if (status === 'completed' || status === 'cancelled' || status === 'failed' || status === 'rate_limited') {
            setProcessing(false)
            // Remove processing placeholder if it's still showing
            removeLiveMessage(roomId, getProcessingPlaceholderId())
            // Only clear ref if this event is for the message we're tracking
            if (sseMessage.data.message_id === currentProcessingMessageId.current) {
              currentProcessingMessageId.current = null
            }
            // Show appropriate notification
            if (status === 'cancelled') {
              banner.info('Processing stopped by user')
            } else if (status === 'failed') {
              banner.error(`Processing failed: ${sseMessage.data.details || 'Unknown error'}`)
            } else if (status === 'rate_limited') {
              // Rate limit error is handled separately via 'error' event with more details
              console.log('Rate limit reached, processing stopped')
            }
            // 'completed' has no banner - messages come via SSE
          }
        }
        break

      case 'error':
        console.error('❌ SSE error message:', sseMessage.data)
        // Handle rate limit errors with a specific message
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const errorData = sseMessage.data as any
        if (errorData?.error_type === 'rate_limit_exceeded') {
          const retryAfter = errorData.retry_after_seconds
          const retryMinutes = retryAfter ? Math.ceil(retryAfter / 60) : 60
          // Show rate limit error for longer (15 seconds) so user has time to read it
          banner.error(
            errorData.error || `Rate limit exceeded. Please try again in ${retryMinutes} minutes.`,
            { duration: 15000 }
          )
        } else {
          banner.error(errorData?.error || errorData?.details || 'Unknown error')
        }
        break

      case 'heartbeat':
        // Heartbeat message, no action needed
        console.log('💓 SSE heartbeat received')
        break

      case 'task_submitted':
        console.log('📋 Task submitted via SSE:', sseMessage.data)
        // Remove the generic processing placeholder when first real task arrives
        removeLiveMessage(roomId, getProcessingPlaceholderId())

        // Add a task message to the UI
        if (sseMessage.data?.message_id) {
          const messageId = sseMessage.data.message_id
          let resolvedAgentName = sseMessage.data.agent_name
          if (!resolvedAgentName && sseMessage.data.agent_id) {
            resolvedAgentName = await getAgentName(sseMessage.data.agent_id)
          }
          // Use created_at for consistent ordering, fallback to SSE timestamp
          const taskTimestamp = sseMessage.data.created_at || sseMessage.timestamp
          const taskMessage: MessageData = {
            id: messageId,
            type: 'task',
            content: '', // Will be filled when task completes
            sender_name: resolvedAgentName || 'Agent',
            timestamp: normalizeTimestampOrNow(taskTimestamp),
            task_status: sseMessage.data.status || 'working',
            agent_id: sseMessage.data.agent_id,
            step_number: sseMessage.data.step_number,
            total_steps: sseMessage.data.total_steps,
            task_content: sseMessage.data.task_content,
            task_created_at: normalizeTimestampOrNow(taskTimestamp), // Use same timestamp for creation
          }
          addLiveMessage(roomId, taskMessage)
          // Note: Don't setProcessing(true) here - the "AI Agents Processing..." bubble
          // should only show at the beginning of a workflow (when user sends a message),
          // not every time a task status bubble appears. Task bubbles have their own
          // visual indicators for showing work in progress.
        }
        break

      case 'task_update':
        console.log('📋 Task update via SSE:', sseMessage.data)
        // Update the existing task message
        if (sseMessage.data?.message_id) {
          const messageId = sseMessage.data.message_id
          const status = sseMessage.data.status as TaskState
          let resolvedAgentName = sseMessage.data.agent_name
          if (!resolvedAgentName && sseMessage.data.agent_id) {
            resolvedAgentName = await getAgentName(sseMessage.data.agent_id)
          }

          // Use created_at for consistent ordering (preserves original task position)
          // This ensures the task bubble doesn't jump around when it completes
          const taskTimestamp = sseMessage.data.created_at || sseMessage.timestamp

          // Get task_content, step_number, total_steps from SSE event, or preserve from existing message as fallback
          // This ensures ordering remains consistent when tasks complete (step_number is used for sorting)
          let taskContent = sseMessage.data.task_content
          let stepNumber = sseMessage.data.step_number
          let totalSteps = sseMessage.data.total_steps
          
          // Try to find existing message and preserve fields that might be missing from SSE event
          const existingMessages = liveMessagesByRoom[roomId] || []
          const existingMessage = existingMessages.find(m => m.id === messageId)
          if (existingMessage) {
            if (taskContent === undefined || taskContent === null) {
              taskContent = existingMessage.task_content
            }
            if (stepNumber === undefined || stepNumber === null) {
              stepNumber = existingMessage.step_number
            }
            if (totalSteps === undefined || totalSteps === null) {
              totalSteps = existingMessage.total_steps
            }
          }

          // Find and update the task message
          const updatedTaskMessage: MessageData = {
            id: messageId,
            type: 'task',
            content: sseMessage.data.content || '',
            sender_name: resolvedAgentName || 'Agent',
            timestamp: normalizeTimestampOrNow(taskTimestamp),
            task_status: status,
            task_error: sseMessage.data.error || null,
            task_status_message: sseMessage.data.status_message || null,
            task_requires_input: sseMessage.data.requires_input || false,
            task_requires_auth: sseMessage.data.requires_auth || false,
            agent_id: sseMessage.data.agent_id,
            step_number: stepNumber,
            total_steps: totalSteps,
            task_content: taskContent,
            task_created_at: normalizeTimestampOrNow(taskTimestamp), // Preserve creation timestamp
          }

          // Replace the existing task message with updated one
          replaceLiveMessage(roomId, messageId, updatedTaskMessage)

          // If task is terminal, stop processing indicator
          const terminalStates = ['completed', 'failed', 'canceled', 'rejected']
          if (terminalStates.includes(status)) {
            setProcessing(false)

            // Show appropriate notification
            if (status === 'failed') {
              banner.error(sseMessage.data.error || 'Task failed')
            } else if (status === 'rejected') {
              banner.error(sseMessage.data.error || 'Task was rejected')
            } else if (status === 'canceled') {
              banner.info('Task was canceled')
            }
          }
        }
        break

      default:
        console.log('❓ Unknown SSE message type:', sseMessage.type)
    }
  }, [getAgentName, addLiveMessage, replaceLiveMessage, removeLiveMessage, getProcessingPlaceholderId, roomId, setProcessing, liveMessagesByRoom])

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
      banner.error('Room data not available')
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

      banner.success('Room settings updated successfully')
      return true

    } catch (error) {
      console.error('Error updating room settings:', error)
      banner.error(`Failed to update room settings: ${error instanceof Error ? error.message : 'Unknown error'}`)
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

    // Step 0.5: Add processing placeholder that will be removed when first task arrives
    const processingPlaceholderId = getProcessingPlaceholderId()
    const processingPlaceholder: MessageData = {
      id: processingPlaceholderId,
      type: 'task',
      content: '',
      sender_name: 'HYBRO AI',
      timestamp: new Date(Date.now() + 1).toISOString(), // Slightly after user message for ordering
      task_status: 'working',
      task_content: 'Processing your request...',
    }
    addLiveMessage(roomId, processingPlaceholder)

    try {
      setSending(true)  // Show spinner during message creation & parsing
      isProcessingRef.current = true

      // Step 1: Send user message to backend using unified SendMessage API
      const createResponse = await SendMessage(roomId, userInput, getToken, userId, userName, targetGroup)

      if (!createResponse.success) {
        throw new Error(`Failed to create user message: ${createResponse.error}`)
      }

      // Extract message_id from createResponse
      const messageId = createResponse.message_id || createResponse.message?.message_id || ""

      if (!messageId) {
        console.warn('SendMessage returned no message_id; keeping optimistic message')
        return true
      }

      // Step 2: Immediately swap temp ID to real ID so SSE agent replies can parent correctly
      replaceLiveMessage(roomId, tempMessageId, { ...optimisticUserMessage, id: messageId })

      // Store message ID for potential cancellation
      currentProcessingMessageId.current = messageId

      // Step 3: Trigger background processing; agent responses will arrive via SSE
      // NOTE: The backend now auto-triggers processing when sendMessage completes.
      // This call is kept for backward compatibility but is no longer critical.
      // If it fails, the backend will still process the message.
      setSending(false)  // Parsing done - stop showing spinner
      setProcessing(true)  // Now show Stop button (cancellation works from here)

      // Fire-and-forget: processRoomUserMessage is now auto-triggered by backend
      // We still call it for redundancy, but don't fail on error
      processRoomUserMessage({
        room_id: roomId,
        room_user_message_id: messageId,
        room_related_message_id: ""
      }).catch(error => {
        // Log but don't fail - backend auto-triggers processing now
        console.log('📡 processRoomUserMessage returned error (backend auto-processes anyway):', error)
      })

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
      resetRoomLiveState(roomId)

      banner.error(`Failed to send message: ${error instanceof Error ? error.message : 'Unknown error'}`)

      // On error, reload messages to ensure UI sync (regardless of SSE status)
      try {
        console.log('🔄 Reloading messages after error to ensure sync...')
        await messagesQuery.refetch()
      } catch (reloadError) {
        console.error('Failed to reload messages after error:', reloadError)
      }

      // Only turn off processing on error
      setProcessing(false)
      currentProcessingMessageId.current = null

      return false
    } finally {
      setSending(false)
      isProcessingRef.current = false
      // NOTE: Don't setProcessing(false) here - SSE will send "completed" status
    }
  }, [userId, userName, room, roomId, sending, sseConnected, getToken, addLiveMessage, replaceLiveMessage, resetRoomLiveState, messagesQuery, setSending, setProcessing, getProcessingPlaceholderId])

  // Cancel ongoing message processing
  const cancelProcessing = useCallback(async () => {
    const messageId = currentProcessingMessageId.current
    if (!messageId) {
      console.warn('No message to cancel')
      return false
    }

    try {
      console.log('🛑 Cancelling message:', messageId)
      await cancelMessage(messageId, getToken)

      // Note: Don't clear currentProcessingMessageId or setProcessing here
      // The SSE 'cancelled' status event will handle state cleanup
      return true
    } catch (error) {
      console.error('Error cancelling message:', error)
      banner.error(`Failed to stop processing: ${error instanceof Error ? error.message : 'Unknown error'}`)
      return false
    }
  }, [getToken])

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
      banner.error(message)
    }
    if (messagesQuery.isError) {
      const message = messagesQuery.error instanceof Error ? messagesQuery.error.message : 'Failed to load messages'
      banner.error(message)
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
    cancelProcessing,
    updateRoomSettings,
    refreshMessages,
    refreshRoomSetting,
    getAgentList,
    getRoomFormData,
    toggleSSE,
    availableAgents: allAgentsQuery.data || [],
  }
}
