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
import { listRoomTasks, getTaskStatus, extractTaskContent, extractTaskError } from '@/lib/api/a2a-tasks'
import type { A2ATaskStatus } from '@/lib/api/a2a-tasks'
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
    resetRoomState,
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

  const normalizeTimestamp = useCallback((value?: string | null): string => {
    if (!value) return new Date().toISOString()

    const trimmed = value.trim()
    const hasZone = /[zZ]|[+-]\d{2}:?\d{2}$/.test(trimmed)
    const withT = trimmed.includes('T') ? trimmed : trimmed.replace(' ', 'T')
    const candidate = hasZone ? withT : `${withT}Z`
    const parsed = new Date(candidate)

    if (Number.isNaN(parsed.getTime())) {
      return new Date().toISOString()
    }

    return parsed.toISOString()
  }, [])

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
    let taskInternalId: string | undefined
    let taskStatus: string | undefined
    
    // Extract content from MessageContent object
    // For both user and agent messages, we use message_content.message_text as the display content
    if (apiMessage.message_content?.message_text) {
      content = apiMessage.message_content.message_text
    } else {
      // Fallback to empty string if message_text is not available
      content = ''
    }

    // If message_text is empty but a task exists, derive content from task artifacts/status
    if (!content && apiMessage.message_content?.message_task) {
      const messageTask = apiMessage.message_content
        .message_task as A2ATaskStatus['task']
      const taskContent = extractTaskContent(messageTask)
      const taskError = extractTaskError(messageTask)
      if (taskContent) {
        content = taskContent
      } else if (taskError) {
        content = taskError
      }
    }

    // Capture internal task id if present (used to reconcile placeholders)
    const messageTask = apiMessage.message_content?.message_task
    const maybeInternalId = messageTask?.metadata?.internal_id
    if (typeof maybeInternalId === 'string') {
      taskInternalId = maybeInternalId
    }
    const maybeStatus = messageTask?.status?.state
    if (typeof maybeStatus === 'string') {
      taskStatus = maybeStatus
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
      timestamp: normalizeTimestamp(apiMessage.message_created_at),
      user_id: apiMessage.message_type === 'user' ? userId : undefined,
      agent_id: apiMessage.message_type === 'agent' ? (apiMessage.agent_id || 'agent_id') : undefined,
      task_internal_id: taskInternalId,
      task_status: taskStatus,
    }
  }, [userId, userName, getAgentName, normalizeTimestamp])

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

        // Also fetch A2A tasks for this room and convert to task messages
        try {
          const tasksResponse = await listRoomTasks(roomId, 50, getToken)
          if (tasksResponse.success && tasksResponse.tasks && tasksResponse.tasks.length > 0) {
            console.log(`📋 Found ${tasksResponse.tasks.length} A2A tasks for room`)
            
            // Fetch full task details for each task to get content
            const taskMessages: MessageData[] = []
            const existingTasksById = new Map<string, MessageData>()
            converted.forEach(msg => {
              if (msg.task_internal_id) {
                existingTasksById.set(msg.task_internal_id, msg)
              }
            })
            const completedAgentMessages = converted.filter(
              msg =>
                msg.type === 'agent' &&
                !!msg.content &&
                !!msg.task_status &&
                msg.task_status === 'completed'
            )
            for (const taskItem of tasksResponse.tasks) {
              try {
                const taskDetail = await getTaskStatus(taskItem.internal_id, getToken)
                if (taskDetail.success && taskDetail.task) {
                  const task = taskDetail.task
                  const content = extractTaskContent(task.task) || ''
                  const error = extractTaskError(task.task)
                  let resolvedAgentName = task.agent_name
                  if (!resolvedAgentName && task.agent_id) {
                    try {
                      resolvedAgentName = await getAgentName(task.agent_id)
                    } catch (agentError) {
                      console.warn('Failed to resolve agent name for task:', agentError)
                    }
                  }
                  const existingTaskMessage = existingTasksById.get(task.internal_id)
                  const hasCompletedAgentMessage = completedAgentMessages.some(msg => {
                    if (task.agent_id && msg.agent_id && task.agent_id !== msg.agent_id) return false
                    return true
                  })
                  const taskTime = new Date(task.created_at).getTime()
                  const hasNearbyContentMessage = converted.some(msg => {
                    if (msg.type !== 'agent' || !msg.content || msg.task_internal_id) return false
                    if (task.agent_id && msg.agent_id && task.agent_id !== msg.agent_id) return false
                    const msgTime = new Date(msg.timestamp).getTime()
                    return Math.abs(taskTime - msgTime) < 5000
                  })

                  // If we already have a room agent message for this task, prefer it.
                  if (existingTaskMessage?.content || hasCompletedAgentMessage || hasNearbyContentMessage) {
                    continue
                  }
                  
                  taskMessages.push({
                    id: `task-${task.internal_id}`,
                    type: 'task',
                    content: content,
                    sender_name: resolvedAgentName || 'Agent',
                    timestamp: task.created_at,
                    agent_id: task.agent_id,
                    task_internal_id: task.internal_id,
                    task_status: task.status,
                    task_error: error || null,
                  })
                }
              } catch (taskError) {
                console.warn(`Failed to fetch task ${taskItem.internal_id}:`, taskError)
              }
            }
            
            // Merge task messages with regular messages, removing empty placeholders
            if (taskMessages.length > 0) {
              const taskIds = new Set(
                taskMessages.map(msg => msg.task_internal_id).filter(Boolean) as string[]
              )
              const taskByAgent = taskMessages.map(msg => ({
                agent: msg.sender_name,
                time: new Date(msg.timestamp).getTime(),
              }))

              const filtered = converted.filter(msg => {
                // Keep non-agent/non-task messages, or messages with content
                if ((msg.type !== 'agent' && msg.type !== 'task') || msg.content) return true
                if (msg.task_internal_id && taskIds.has(msg.task_internal_id)) return false

                // Fallback: drop empty placeholders that align with a task message
                const msgTime = new Date(msg.timestamp).getTime()
                const hasNearbyTask = taskByAgent.some(task => {
                  return task.agent === msg.sender_name && Math.abs(task.time - msgTime) < 5000
                })
                if (hasNearbyTask && msg.task_status && msg.task_status !== 'completed') {
                  return false
                }
                return true
              })

              console.log(`✅ Added ${taskMessages.length} task messages`)
              filtered.push(...taskMessages)
              return filtered
            }
          }
        } catch (tasksError) {
          console.warn('Failed to fetch A2A tasks:', tasksError)
          // Don't fail the whole query if tasks fail
        }

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
            timestamp: normalizeTimestamp(sseMessage.timestamp),
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
            timestamp: normalizeTimestamp(sseMessage.timestamp),
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
        // Add a task message to the UI
        if (sseMessage.data?.internal_id) {
          // Use message_id if provided (for consistent ID with database messages),
          // otherwise fall back to task-based ID for backwards compatibility
          const messageId = sseMessage.data.message_id || `task-${sseMessage.data.internal_id}`
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
            timestamp: normalizeTimestamp(taskTimestamp),
            task_internal_id: sseMessage.data.internal_id,
            task_status: sseMessage.data.status || 'working',
            agent_id: sseMessage.data.agent_id,
            step_number: sseMessage.data.step_number,
            total_steps: sseMessage.data.total_steps,
            task_content: sseMessage.data.task_content,
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
        if (sseMessage.data?.internal_id) {
          // Use message_id if provided (for consistent ID with database messages),
          // otherwise fall back to task-based ID for backwards compatibility
          const messageId = sseMessage.data.message_id || `task-${sseMessage.data.internal_id}`
          const oldTaskId = `task-${sseMessage.data.internal_id}`
          const status = sseMessage.data.status as TaskState
          let resolvedAgentName = sseMessage.data.agent_name
          if (!resolvedAgentName && sseMessage.data.agent_id) {
            resolvedAgentName = await getAgentName(sseMessage.data.agent_id)
          }
          
          // Use created_at for consistent ordering (preserves original task position)
          // This ensures the task bubble doesn't jump around when it completes
          const taskTimestamp = sseMessage.data.created_at || sseMessage.timestamp
          
          // Get task_content from SSE event, or preserve from existing message as fallback
          let taskContent = sseMessage.data.task_content
          if (!taskContent) {
            // Try to find existing message and preserve its task_content
            const existingMessages = liveMessagesByRoom[roomId] || []
            const existingMessage = existingMessages.find(
              m => m.id === messageId || m.id === oldTaskId || m.task_internal_id === sseMessage.data?.internal_id
            )
            if (existingMessage) {
              taskContent = existingMessage.task_content
            }
          }
          
          // Find and update the task message
          const updatedTaskMessage: MessageData = {
            id: messageId,
            type: 'task',
            content: sseMessage.data.content || '',
            sender_name: resolvedAgentName || 'Agent',
            timestamp: normalizeTimestamp(taskTimestamp),
            task_internal_id: sseMessage.data.internal_id,
            task_status: status,
            task_error: sseMessage.data.error || null,
            task_status_message: sseMessage.data.status_message || null,
            task_requires_input: sseMessage.data.requires_input || false,
            task_requires_auth: sseMessage.data.requires_auth || false,
            agent_id: sseMessage.data.agent_id,
            step_number: sseMessage.data.step_number,
            total_steps: sseMessage.data.total_steps,
            task_content: taskContent,
          }
          
          // Replace the existing task message with updated one
          // Try both the old task-based ID and the new message_id to handle transitions
          replaceLiveMessage(roomId, oldTaskId, updatedTaskMessage)
          if (messageId !== oldTaskId) {
            // Also replace by message_id in case it was already using that
            replaceLiveMessage(roomId, messageId, updatedTaskMessage)
          }
          
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
  }, [getAgentName, addLiveMessage, replaceLiveMessage, roomId, setProcessing, normalizeTimestamp, liveMessagesByRoom])

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
      setSending(false)  // Parsing done - stop showing spinner
      setProcessing(true)  // Now show Stop button (cancellation works from here)
      
      const processResponse = await processRoomUserMessage({
        room_id: roomId,
        room_user_message_id: messageId,
        room_related_message_id: ""
      })

      if (!processResponse.success) {
        throw new Error(`Failed to process user message: ${processResponse.error}`)
      }
      
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
  }, [userId, userName, room, roomId, sending, sseConnected, getToken, addLiveMessage, replaceLiveMessage, resetRoomState, messagesQuery, setSending, setProcessing])

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
