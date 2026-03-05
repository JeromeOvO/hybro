import { useState, useCallback, useEffect, useRef } from 'react'
import {
  inquiryRoomSetting,
  SendMessage,
  inquiryRoomMessagesByRoomId,
  updateRoomAgentSet,
  updateRoomName,
  updateRoomExtendInfo
} from '@/lib/api/room'
import { cancelMessage } from '@/lib/api/sse'
import { fetchPendingHitlRequests } from '@/lib/api/hitl'
import { ApiError } from '@/lib/api-client'
import { banner } from "@/components/ui/banner"
import { useQuery } from '@tanstack/react-query'
import type { QuoteData } from '@/components/message-bubble'
import type { Agent } from '@/lib/types/agent'
import { useRoomSSE } from './useRoomSSE'
import type { SSEMessage, TaskState, ProcessingStatus } from '@/lib/types/sse'
import { isTerminalState, PROCESSING_STATUS, isProcessingDone, TASK_STATE } from '@/lib/types/sse'
import { useRoomUiStore } from '@/stores/room-ui-store'
import { useMessageStore, detectAndMarkStaleTasks, filterHydrationMessages, convertApiMessageToIncoming } from '@/stores/message-store'
import type { ArtifactPart, ArtifactData, MessageEntity } from '@/stores/message-store/types'
import { mergeArtifacts } from '@/stores/message-store/upsert'
import type { PendingAttachment } from '@/lib/types/attachments'
import { streamingBuffer } from '@/stores/streaming-buffer'
import { TypewriterManager } from '@/stores/typewriter'
import { getAllActiveAgents } from '@/lib/api/agent'
import { normalizeTimestampOrNow, isStale } from '@/lib/time'
import { SYSTEM_AGENTS } from '@/lib/system-agents'

const typewriterManager = new TypewriterManager()

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
    cancelling,
    updatingRoom,
    sseEnabled,
    setSending,
    setProcessing,
    setCancelling,
    setUpdatingRoom,
    setSseEnabled,
    setSseConnected,
    setSseError,
  } = useRoomUiStore()

  const [loading, setLoading] = useState(true)

  // Prevent concurrent room loads
  const activeRoomLoad = useRef<string | null>(null)

  // Cache for agent names to avoid repeated API calls
  const agentNameCache = useRef<{ [agentId: string]: string }>({})

  // Ref to prevent duplicate calls
  const isProcessingRef = useRef(false)

  // Atomically clear both the Zustand processing flag and the synchronous
  // ref guard. Every callsite that ends the processing lifecycle must use
  // this helper instead of calling setProcessing(false) directly.
  const clearProcessing = useCallback(() => {
    setProcessing(false)
    isProcessingRef.current = false
  }, [setProcessing])

  // Track current processing message ID for cancellation
  const currentProcessingMessageId = useRef<string | null>(null)

  // Tracks whether the processing placeholder has been dismissed by SSE
  const placeholderDismissedRef = useRef(false)

  // Cancellation timeout ref (FE-3 safety net)
  const cancelTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Tracks whether the cancel timeout has already fired.
  // When true, SSE handlers skip showing cancel/done banners to avoid
  // contradictory messages after the "timed out" banner.
  const cancelTimedOutRef = useRef(false)

  // Tracks whether SSE disconnected at any point during the current processing cycle.
  // When true, a reconciliation refetch fires after processing completes to catch
  // any missed events. When false (happy path), the refetch is skipped entirely.
  const sseHadDisconnectionRef = useRef(false)

  // O(1) lookup index: maps HITL request_id → message entity id
  const hitlRequestIndex = useRef(new Map<string, string>())

  // Clean up cancel timeout on unmount
  useEffect(() => {
    return () => {
      if (cancelTimeoutRef.current) {
        clearTimeout(cancelTimeoutRef.current)
        cancelTimeoutRef.current = null
      }
    }
  }, [])

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
    enabled: !!userId,  // Don't fetch until user is authenticated
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
    if (SYSTEM_AGENTS[agentId]) {
      return SYSTEM_AGENTS[agentId].name
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

  // Get supervisor mode from room's extend_info
  const getSupervisorMode = useCallback((): boolean => {
    if (!room?.extend_info) return false
    const extendInfo = room.extend_info as { use_supervisor?: boolean }
    return extendInfo.use_supervisor || false
  }, [room])

  // ── DB Hydration: load messages into normalized store on room entry ──

  const hydrateFromDb = useCallback(async (targetRoomId: string) => {
    const store = useMessageStore.getState()
    if (store.hydratedFromDb && store.roomId === targetRoomId) return

    console.log(`🔍 Loading messages for room: ${targetRoomId}`)
    const startTime = Date.now()

    try {
      const response = await inquiryRoomMessagesByRoomId(targetRoomId, getToken)
      if (!response.success || !response.message_list) {
        console.error(`❌ Failed to load messages for room ${targetRoomId}`)
        // Mark as hydrated even on failure so we don't stay in loading forever
        const s = useMessageStore.getState()
        if (s.roomId === targetRoomId) s.markDbSynced()
        return
      }

      console.log(`✅ Loaded ${response.message_list.length} messages in ${Date.now() - startTime}ms`)

      const incomingMessages = await Promise.all(
        response.message_list.map(msg =>
          convertApiMessageToIncoming(msg, { userId, userName, getAgentName })
        )
      )
      const withStaleDetection = detectAndMarkStaleTasks(incomingMessages)
      const filtered = filterHydrationMessages(withStaleDetection)

      const msgStore = useMessageStore.getState()
      if (msgStore.roomId === targetRoomId) {
        msgStore.upsertMany(filtered, 'db')
        msgStore.markDbSynced()
        console.log(
          `[NormalizedStore] DB hydration: ${filtered.length} messages written ` +
          `(${response.message_list.length} raw, ${incomingMessages.length} converted, ` +
          `${incomingMessages.length - filtered.length} filtered)`
        )
      }

      // Overlay HITL state after DB hydration to avoid race with SSE reconnect.
      // Pending requests get hitlResolved=false; input-required messages NOT in
      // the pending set are already resolved and get hitlResolved=true.
      try {
        const hitlRes = await fetchPendingHitlRequests(targetRoomId, getToken)
        const hitlStore = useMessageStore.getState()
        if (hitlStore.roomId !== targetRoomId) return

        const pendingMessageIds = new Set<string>()
        if (hitlRes.requests?.length) {
          console.log(`🔔 Hydration: overlaying ${hitlRes.requests.length} pending HITL request(s)`)
          for (const req of hitlRes.requests) {
            pendingMessageIds.add(req.message_id)
            let resolvedName = req.agent_name
            if (!resolvedName && req.agent_id) {
              resolvedName = await getAgentName(req.agent_id)
            }
            hitlStore.upsertMessage({
              id: req.message_id,
              roomId: targetRoomId,
              messageType: 'agent',
              content: req.prompt || '',
              senderName: resolvedName || 'Agent',
              timestamp: normalizeTimestampOrNow(req.created_at),
              agentId: req.agent_id,
              taskStatus: 'input-required' as TaskState,
              hitlRequestId: req.request_id,
              hitlPrompt: req.prompt,
              hitlPromptType: req.prompt_type || 'text',
              hitlChoices: req.choices,
              hitlExpiresAt: req.expires_at,
              hitlResolved: false,
              hitlGroupId: req.group_id ?? undefined,
              hitlGroupTotal: req.group_total ?? undefined,
              hitlGroupIndex: req.group_index ?? undefined,
            }, 'sse')
            hitlRequestIndex.current.set(req.request_id, req.message_id)
          }
        }

        // Mark input-required messages from DB hydration that are NOT pending as already resolved.
        // Only check messages that came from this hydration batch, not SSE-created entities.
        const hydratedIds = new Set(filtered.map(m => m.id))
        for (const entity of Object.values(hitlStore.entities)) {
          if (
            entity.roomId === targetRoomId &&
            entity.taskStatus === 'input-required' &&
            hydratedIds.has(entity.id) &&
            !pendingMessageIds.has(entity.id)
          ) {
            hitlStore.upsertMessage({
              id: entity.id,
              roomId: targetRoomId,
              messageType: 'agent',
              content: entity.content,
              senderName: entity.senderName,
              timestamp: entity.timestamp,
              hitlResolved: true,
            }, 'sse')
          }
        }
      } catch (hitlErr) {
        console.error('[HITL] Failed to overlay HITL state during hydration:', hitlErr)
      }
    } catch (error) {
      console.error(`❌ Failed to load messages for room ${targetRoomId}:`, error)
      // Mark as hydrated on error to avoid infinite loading
      const s = useMessageStore.getState()
      if (s.roomId === targetRoomId) s.markDbSynced()
    }
  }, [getToken, userId, userName, getAgentName])

  // ── Reconciliation: silent DB sync for gap-filling (Gap 14) ──

  const reconcileWithDb = useCallback(async (targetRoomId: string) => {
    try {
      const response = await inquiryRoomMessagesByRoomId(targetRoomId, getToken)
      if (!response.success || !response.message_list) return

      const incomingMessages = await Promise.all(
        response.message_list.map(msg =>
          convertApiMessageToIncoming(msg, { userId, userName, getAgentName })
        )
      )
      const withStaleDetection = detectAndMarkStaleTasks(incomingMessages)
      const filtered = filterHydrationMessages(withStaleDetection)

      const store = useMessageStore.getState()
      if (store.roomId === targetRoomId) {
        store.upsertMany(filtered, 'db')
        store.markDbSynced()
      }
    } catch (error) {
      console.error('[NormalizedStore] Reconciliation failed:', error)
    }
  }, [getToken, userId, userName, getAgentName])

  const queryLoading = roomQuery.isFetching || roomQuery.isLoading

  // Track whether initial hydration has been kicked off for this room
  const hydrationStartedRef = useRef<string | null>(null)

  // Reset UI state when room changes and trigger DB hydration
  useEffect(() => {
    agentNameCache.current = {}
    placeholderDismissedRef.current = false
    cancelTimedOutRef.current = false
    sseHadDisconnectionRef.current = false
    hitlRequestIndex.current.clear()
    if (cancelTimeoutRef.current) {
      clearTimeout(cancelTimeoutRef.current)
      cancelTimeoutRef.current = null
    }

    // Clear streaming buffer on room switch
    streamingBuffer.clear()
    typewriterManager.finishAll()

    // Reset UI flags so the new room starts with clean state.
    // The previous room's processing continues server-side; when the user
    // returns, the restore effect re-enables processing from room data.
    setSending(false)
    clearProcessing()
    setCancelling(false)
    setSseConnected(false)
    setSseError(null)
    currentProcessingMessageId.current = null

    // Initialize normalized store for this room
    useMessageStore.getState().setRoom(roomId)
    hydrationStartedRef.current = null
  }, [roomId, setSending, clearProcessing, setCancelling, setSseConnected, setSseError])

  // Hydrate from DB once room data is available.
  // Gating on `room` ensures the room query has completed and pre-populated
  // agentNameCache from room_agent_set, so agent names resolve correctly
  // instead of falling back to "Agent <id>" on page refresh.
  useEffect(() => {
    if (!roomId || !userName || !room) return
    if (hydrationStartedRef.current === roomId) return
    hydrationStartedRef.current = roomId
    hydrateFromDb(roomId)
  }, [roomId, userName, room, hydrateFromDb])

  // Mirror query loading state to local loading flag for consumers
  useEffect(() => {
    setLoading(queryLoading)
  }, [queryLoading])

  // Restore processing placeholder on page load if room has an active processing_message_id
  useEffect(() => {
    // Only run when room data is loaded and not currently loading
    if (!room || queryLoading) return

    // Wait for DB hydration to complete before checking for task messages
    const store = useMessageStore.getState()
    if (!store.hydratedFromDb || store.roomId !== roomId) return

    // Once SSE has dismissed the placeholder (via task_submitted or processing_status done),
    // never re-add it — the restore effect is only for page-load recovery.
    if (placeholderDismissedRef.current) return
    
    // Check if room has an active processing state
    if (room.processing_message_id) {
      console.log('🔄 Restoring processing placeholder for message:', room.processing_message_id)

      // Always restore the message ID so cancellation works after refresh,
      // regardless of whether the placeholder is shown below.
      currentProcessingMessageId.current = room.processing_message_id

      // Check if the triggering user message is stale (> 2 min).
      const PLACEHOLDER_STALE_MS = 2 * 60 * 1000
      const triggerMsg = store.entities[room.processing_message_id]
      if (triggerMsg && isStale(triggerMsg.timestamp, PLACEHOLDER_STALE_MS)) {
        console.log('🔄 Skipping placeholder - processing message is stale (>2min)')
        return
      }

      // Check if any task-status messages already exist in the store
      const hasTaskEntities = Object.values(store.entities).some(
        e => e.roomId === roomId && e.displayType === 'task-status'
      )
      
      if (hasTaskEntities) {
        console.log('🔄 Skipping placeholder - tasks already exist')
        return
      }
      
      // Check if placeholder already exists
      const placeholderId = getProcessingPlaceholderId()
      if (store.entities[placeholderId]) return

      // Restore placeholder via normalized store
      store.upsertMessage({
        id: placeholderId,
        roomId,
        messageType: 'agent',
        content: '',
        senderName: 'HYBRO AI',
        taskStatus: TASK_STATE.WORKING,
        taskContent: 'Processing your request...',
        timestamp: new Date().toISOString(),
        isEphemeral: true,
      }, 'optimistic')

      setProcessing(true)
    }
  }, [room, queryLoading, roomId, getProcessingPlaceholderId, setProcessing])

  // Handle SSE messages — writes go to normalized store only (Step 4)
  const handleSSEMessage = useCallback(async (sseMessage: SSEMessage) => {
    console.log('🔔 Room webhook received SSE message:', sseMessage)
    const store = useMessageStore.getState()

    // Convert raw SSE `parts` (file/data) into ArtifactData[].
    // Text parts are already carried in `content`, so only non-text parts
    // are materialized here. Returns undefined when there are no non-text parts.
    const partsToArtifacts = (
      rawParts: Record<string, unknown>[] | undefined,
      messageId: string,
      existing: MessageEntity | undefined,
    ): ArtifactData[] | undefined => {
      if (!rawParts || rawParts.length === 0) return existing?.artifacts
      const nonTextParts = rawParts
        .filter((p) => (p.kind as string) !== 'text')
        .map((p) => {
          const fileData = p.file as Record<string, unknown> | undefined
          return {
            kind: ((p.kind as string) || 'text') as ArtifactPart['kind'],
            text: p.text as string | undefined,
            file: fileData ? {
              uri: (fileData.uri as string | undefined),
              bytes: (fileData.bytes as string | undefined),
              mime_type: ((fileData.mime_type || fileData.mimeType) as string | undefined),
              name: (fileData.name as string | undefined),
            } : undefined,
            data: p.data as Record<string, unknown> | undefined,
          }
        })
      if (nonTextParts.length === 0) return existing?.artifacts
      const inline: ArtifactData = {
        artifactId: `${messageId}-parts`,
        name: 'Response files',
        parts: nonTextParts,
      }
      return mergeArtifacts(existing?.artifacts, inline, false)
    }

    switch (sseMessage.type) {
      case 'user_message':
        console.log('📨 User message received via SSE')
        if (sseMessage.data?.content) {
          store.upsertMessage({
            id: sseMessage.data.message_id || `sse-${Date.now()}`,
            roomId,
            messageType: 'user',
            content: sseMessage.data.content,
            senderName: sseMessage.data.user_id || 'User',
            userId: sseMessage.data.user_id,
            timestamp: normalizeTimestampOrNow(sseMessage.timestamp),
          }, 'sse')
        }
        break

      case 'agent_response':
        console.log('🤖 Agent response received via SSE')
        if (sseMessage.data?.message_id) {
          const messageId = sseMessage.data.message_id
          streamingBuffer.finalize(messageId)
          typewriterManager.finish(messageId)

          if (sseMessage.data?.content !== undefined && sseMessage.data?.agent_id) {
            const agentName = await getAgentName(sseMessage.data.agent_id)
            const content = sseMessage.data.content
            const msgTimestamp = normalizeTimestampOrNow(sseMessage.timestamp)
            const existing = store.entities[messageId]
            const artifacts = partsToArtifacts(
              sseMessage.data.parts as Record<string, unknown>[] | undefined,
              messageId,
              existing,
            )

            store.upsertMessage({
              id: messageId,
              roomId,
              messageType: 'agent',
              content,
              senderName: agentName,
              agentId: sseMessage.data.agent_id,
              timestamp: msgTimestamp,
              taskStatus: null,
              isEphemeral: false,
              ...(artifacts ? { artifacts } : {}),
            }, 'sse')
          }
        }
        break

      case 'agent_token': {
        // Token streaming: append to ephemeral buffer, not the message store
        const { message_id, agent_id, token } = sseMessage.data || {}
        if (!message_id || !token) break

        // Real streaming supersedes any active typewriter for this message
        typewriterManager.abort(message_id)

        // Ignore tokens for messages that already have authoritative content
        // (task_update/agent_response already arrived with final content)
        const existingEntity = store.entities[message_id]
        if (existingEntity && existingEntity.content && !existingEntity.isEphemeral) {
          break
        }

        // Ensure a placeholder entity exists so the message bubble renders.
        // If the entity was created by task_submitted (displayType: task-status),
        // re-upsert it as an ephemeral agent-bubble so streaming content is visible.
        // resolveDisplayType returns 'agent-bubble' for ephemeral entities.
        if (!existingEntity || existingEntity.displayType === 'task-status') {
          const agentName = agent_id ? await getAgentName(agent_id) : (existingEntity?.senderName || 'Agent')
          store.upsertMessage({
            id: message_id,
            roomId,
            messageType: 'agent',
            content: '',
            senderName: agentName,
            timestamp: existingEntity?.timestamp || normalizeTimestampOrNow(sseMessage.timestamp),
            agentId: agent_id,
            isEphemeral: true,
          }, 'sse')
        }

        streamingBuffer.append(message_id, token)
        break
      }

      case 'processing_status':
        console.log('⚙️ Processing status update:', sseMessage.data?.status)
        if (sseMessage.data?.status) {
          const status = sseMessage.data.status

          if (status === PROCESSING_STATUS.PROCESSING) {
            setProcessing(true)
            if (!currentProcessingMessageId.current && sseMessage.data.message_id) {
              currentProcessingMessageId.current = sseMessage.data.message_id
            }
            if (!placeholderDismissedRef.current) {
              store.upsertMessage({
                id: getProcessingPlaceholderId(),
                roomId,
                messageType: 'agent',
                content: '',
                senderName: 'HYBRO AI',
                taskStatus: TASK_STATE.WORKING,
                taskContent: 'Supervisor is analyzing your request…',
                timestamp: new Date().toISOString(),
                isEphemeral: true,
              }, 'optimistic')
            }
          } else if (isProcessingDone(status as ProcessingStatus) || status === PROCESSING_STATUS.RATE_LIMITED) {
            clearProcessing()
            setCancelling(false)
            if (cancelTimeoutRef.current) {
              clearTimeout(cancelTimeoutRef.current)
              cancelTimeoutRef.current = null
            }
            // Remove processing placeholder
            store.removeMessage(getProcessingPlaceholderId())
            placeholderDismissedRef.current = true

            if (sseMessage.data.message_id === currentProcessingMessageId.current) {
              currentProcessingMessageId.current = null
            }
            // Show appropriate notification (skip if cancel timeout already fired)
            if (!cancelTimedOutRef.current) {
              if (status === PROCESSING_STATUS.CANCELED) {
                banner.info('Processing stopped by user')

                // Insert cancel confirmation message
                store.upsertMessage({
                  id: `cancel-confirm-${Date.now()}`,
                  roomId,
                  messageType: 'agent',
                  content: 'Processing was stopped by the user.',
                  senderName: 'System',
                  taskStatus: TASK_STATE.CANCELED,
                  taskContent: 'Processing stopped by user',
                  timestamp: new Date().toISOString(),
                  isEphemeral: true,
                }, 'optimistic')

                // Batch cancel all non-terminal tasks in normalized store
                store.cancelAllNonTerminal(roomId)
              } else if (status === PROCESSING_STATUS.FAILED) {
                banner.error(`Processing failed: ${sseMessage.data.details || 'Unknown error'}`)
              } else if (status === PROCESSING_STATUS.RATE_LIMITED) {
                console.log('Rate limit reached, processing stopped')
              }
            }
            cancelTimedOutRef.current = false

            // Reconcile with DB only if SSE had a gap during this processing cycle
            if (sseHadDisconnectionRef.current) {
              console.log('🔄 SSE had disconnection during processing — reconciling with DB')
              setTimeout(() => {
                reconcileWithDb(roomId)
              }, 1500)
              sseHadDisconnectionRef.current = false
            }
          }
        }
        break

      case 'error':
        console.error('❌ SSE error message:', sseMessage.data)
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const errorData = sseMessage.data as any
        if (errorData?.error_type === 'rate_limit_exceeded') {
          const retryAfter = errorData.retry_after_seconds
          const retryMinutes = retryAfter ? Math.ceil(retryAfter / 60) : 60
          banner.error(
            errorData.error || `Rate limit exceeded. Please try again in ${retryMinutes} minutes.`,
            { duration: 15000 }
          )
        } else {
          banner.error(errorData?.error || errorData?.details || 'Unknown error')
        }
        break

      case 'heartbeat':
        console.log('💓 SSE heartbeat received')
        break

      case 'task_submitted':
        console.log('📋 Task submitted via SSE:', sseMessage.data)
        // Remove placeholder, dismiss tracking
        store.removeMessage(getProcessingPlaceholderId())
        placeholderDismissedRef.current = true

        if (sseMessage.data?.message_id) {
          const messageId = sseMessage.data.message_id
          let resolvedAgentName = sseMessage.data.agent_name
          if (!resolvedAgentName && sseMessage.data.agent_id) {
            resolvedAgentName = await getAgentName(sseMessage.data.agent_id)
          }
          const taskTimestamp = sseMessage.data.created_at || sseMessage.timestamp

          store.upsertMessage({
            id: messageId,
            roomId,
            messageType: 'agent',
            content: '',
            senderName: resolvedAgentName || 'Agent',
            agentId: sseMessage.data.agent_id,
            taskStatus: (sseMessage.data.status as TaskState) || TASK_STATE.WORKING,
            taskContent: sseMessage.data.task_content,
            stepNumber: sseMessage.data.step_number,
            totalSteps: sseMessage.data.total_steps,
            timestamp: normalizeTimestampOrNow(taskTimestamp),
            taskCreatedAt: normalizeTimestampOrNow(taskTimestamp),
          }, 'sse')
        }
        break

      case 'task_update':
        console.log('📋 Task update via SSE:', sseMessage.data)
        if (sseMessage.data?.message_id) {
          const messageId = sseMessage.data.message_id
          const status = sseMessage.data.status as TaskState
          let resolvedAgentName = sseMessage.data.agent_name
          if (!resolvedAgentName && sseMessage.data.agent_id) {
            resolvedAgentName = await getAgentName(sseMessage.data.agent_id)
          }
          const taskTimestamp = sseMessage.data.created_at || sseMessage.timestamp
          const content = sseMessage.data.content || ''

          const taskFields = {
            taskStatus: status,
            taskError: sseMessage.data.error !== undefined ? (sseMessage.data.error || null) : undefined,
            taskStatusMessage: sseMessage.data.status_message !== undefined
              ? (sseMessage.data.status_message || null) : undefined,
            taskRequiresInput: sseMessage.data.requires_input,
            taskRequiresAuth: sseMessage.data.requires_auth,
            taskContent: sseMessage.data.task_content,
            stepNumber: sseMessage.data.step_number,
            totalSteps: sseMessage.data.total_steps,
            timestamp: normalizeTimestampOrNow(taskTimestamp),
            taskCreatedAt: normalizeTimestampOrNow(taskTimestamp),
          }

          const baseMsg = {
            id: messageId,
            roomId,
            messageType: 'agent' as const,
            senderName: resolvedAgentName || 'Agent',
            agentId: sseMessage.data.agent_id,
            timestamp: new Date().toISOString(),
          }

          const existing = store.entities[messageId]
          const artifacts = partsToArtifacts(
            sseMessage.data.parts as Record<string, unknown>[] | undefined,
            messageId,
            existing,
          )

          if (isTerminalState(status)) {
            const hadRealStreaming = streamingBuffer.isStreaming(messageId)
            streamingBuffer.finalize(messageId)
            typewriterManager.finish(messageId)

            if (!hadRealStreaming && content && status === TASK_STATE.COMPLETED) {
              // Non-streaming agent: use typewriter for progressive reveal.
              // Create ephemeral placeholder so the agent-bubble renders immediately,
              // then feed content through StreamingBuffer via TypewriterManager.
              // Omit taskStatus so resolveDisplayType picks "agent-bubble" (ephemeral + no status).
              store.upsertMessage({
                ...baseMsg,
                content: '',
                isEphemeral: true,
                ...(artifacts ? { artifacts } : {}),
              }, 'sse')

              const finalContent = content
              const finalTaskFields = taskFields
              typewriterManager.start(messageId, finalContent, () => {
                streamingBuffer.finalize(messageId)
                store.upsertMessage({
                  ...baseMsg,
                  content: finalContent,
                  isEphemeral: false,
                  ...finalTaskFields,
                  ...(artifacts ? { artifacts } : {}),
                }, 'sse')
              })
            } else {
              store.upsertMessage({
                ...baseMsg,
                content,
                isEphemeral: false,
                ...taskFields,
                ...(artifacts ? { artifacts } : {}),
              }, 'sse')
            }

            clearProcessing()
            setCancelling(false)
            if (cancelTimeoutRef.current) {
              clearTimeout(cancelTimeoutRef.current)
              cancelTimeoutRef.current = null
            }
            if (!cancelTimedOutRef.current) {
              if (status === TASK_STATE.FAILED) {
                banner.error(sseMessage.data.error || 'Task failed')
              } else if (status === TASK_STATE.REJECTED) {
                banner.error(sseMessage.data.error || 'Task was rejected')
              }
            }
            cancelTimedOutRef.current = false
          } else {
            // Non-terminal status update (working, input-required, etc.)
            // Skip if the entity is currently streaming — the streaming buffer
            // has the real-time content and a non-terminal status update would
            // flip displayType back to task-status, causing UI thrash.
            const isCurrentlyStreaming = streamingBuffer.isStreaming(messageId)
            if (!isCurrentlyStreaming) {
              store.upsertMessage({
                ...baseMsg,
                content,
                ...taskFields,
                ...(artifacts ? { artifacts } : {}),
              }, 'sse')
            }
          }
        }
        break

      case 'artifact_update': {
        if (sseMessage.data?.message_id && sseMessage.data?.artifact) {
          const { message_id, artifact, append: isAppend, last_chunk } = sseMessage.data
          const existing = store.entities[message_id]
          const artifactData = {
            artifactId: artifact.artifact_id || (artifact as Record<string, unknown>).artifactId as string,
            name: artifact.name,
            parts: (artifact.parts || []).map((p: Record<string, unknown>) => {
              const fileData = p.file as Record<string, unknown> | undefined
              return {
                kind: ((p.kind as string) || 'text') as ArtifactPart['kind'],
                text: p.text as string | undefined,
                file: fileData ? {
                  uri: (fileData.uri as string | undefined),
                  bytes: (fileData.bytes as string | undefined),
                  mime_type: (fileData.mime_type || fileData.mimeType) as string | undefined,
                  name: (fileData.name as string | undefined),
                } : undefined,
                data: p.data as Record<string, unknown> | undefined,
              }
            }),
            isStreaming: isAppend ? !last_chunk : false,
          }
          const merged = mergeArtifacts(existing?.artifacts, artifactData, isAppend)
          store.upsertMessage({
            id: message_id,
            roomId,
            messageType: 'agent',
            content: existing?.content || '',
            senderName: existing?.senderName || 'Agent',
            timestamp: existing?.timestamp || normalizeTimestampOrNow(sseMessage.timestamp),
            artifacts: merged,
          }, 'sse')
        }
        break
      }

      case 'hitl_input_requested': {
        console.log('🔔 HITL input requested via SSE:', sseMessage.data)
        if (sseMessage.data) {
          const { request_id, message_id, prompt, prompt_type, choices,
                  agent_name, agent_id, step_number, total_steps, expires_at,
                  group_id, group_total, group_index } = sseMessage.data
          if (request_id && message_id) {
            store.removeMessage(getProcessingPlaceholderId())
            placeholderDismissedRef.current = true

            let resolvedAgentName = agent_name
            if (!resolvedAgentName && agent_id) {
              resolvedAgentName = await getAgentName(agent_id)
            }
            store.upsertMessage({
              id: message_id,
              roomId,
              messageType: 'agent',
              content: prompt || '',
              senderName: resolvedAgentName || 'Agent',
              timestamp: normalizeTimestampOrNow(sseMessage.timestamp),
              agentId: agent_id,
              taskStatus: 'input-required' as TaskState,
              hitlRequestId: request_id,
              hitlPrompt: prompt,
              hitlPromptType: (prompt_type as 'text' | 'choice' | 'confirmation') || 'text',
              hitlChoices: choices,
              hitlExpiresAt: expires_at,
              hitlResolved: false,
              hitlGroupId: group_id ?? undefined,
              hitlGroupTotal: group_total ?? undefined,
              hitlGroupIndex: group_index ?? undefined,
              stepNumber: step_number,
              totalSteps: total_steps,
            }, 'sse')
            hitlRequestIndex.current.set(request_id, message_id)
          }
        }
        break
      }

      case 'hitl_status_update': {
        console.log('🔔 HITL status update via SSE:', sseMessage.data)
        if (sseMessage.data) {
          const { request_id, status: hitlStatus, error_message } = sseMessage.data
          if (request_id) {
            const entityId = hitlRequestIndex.current.get(request_id)
            const entity = entityId ? store.entities[entityId] : undefined
            if (entity) {
              // Guard: if a newer HITL request has already replaced this one
              // on the same entity (same message_id, different request_id),
              // skip the stale status update to avoid hiding the new form.
              if (entity.hitlRequestId && entity.hitlRequestId !== request_id) {
                console.log('🔔 Skipping stale hitl_status_update for', request_id,
                  '— entity now owns', entity.hitlRequestId)
                hitlRequestIndex.current.delete(request_id)
                break
              }

              let resolvedTaskStatus = entity.taskStatus
              let resolvedTaskError: string | null = null
              let resolvedContent = entity.content
              let resolved = true
              if (hitlStatus === 'expired') {
                resolvedTaskStatus = 'failed' as TaskState
                resolvedTaskError = error_message || 'Request expired'
                resolvedContent = error_message || entity.content
              } else if (hitlStatus === 'canceled') {
                resolvedTaskStatus = 'canceled' as TaskState
                resolvedTaskError = error_message || 'Request canceled'
                resolvedContent = error_message || entity.content
              } else if (hitlStatus === 'error') {
                // Backend reverts request to PENDING on routing failure —
                // keep the form open so the user can retry.
                resolved = false
                resolvedTaskError = error_message || 'Delivery failed — you can retry'
              }

              store.upsertMessage({
                id: entity.id,
                roomId,
                messageType: 'agent',
                content: resolvedContent,
                senderName: entity.senderName,
                timestamp: normalizeTimestampOrNow(sseMessage.timestamp),
                hitlResolved: resolved,
                taskStatus: resolvedTaskStatus,
                taskError: resolvedTaskError,
              }, 'sse')
              if (resolved) {
                hitlRequestIndex.current.delete(request_id)
              }
            }
          }
        }
        break
      }

      default:
        console.log('❓ Unknown SSE message type:', sseMessage.type)
    }
  }, [getAgentName, getProcessingPlaceholderId, roomId, setProcessing, clearProcessing, setCancelling, reconcileWithDb])

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

  // Track SSE disconnections during active processing.
  // If SSE drops while agents are working, we may have missed events and need
  // to reconcile with DB after processing completes.
  // Also promote any partial streaming content to entity content.
  // On reconnect, restore any pending HITL requests.
  const prevSseConnectedRef = useRef(false)
  useEffect(() => {
    if (!sseConnected && processing) {
      console.log('⚠️ SSE disconnected during processing — will reconcile after completion')
      sseHadDisconnectionRef.current = true

      // Finish any active typewriters so their content is committed
      typewriterManager.finishAll()

      // Promote partial streaming content to entity content on disconnect.
      // Use 'optimistic' source so Rule 2 (SSE wins over DB for non-terminal)
      // doesn't block DB reconciliation later. Clear task fields so
      // cancelAllNonTerminal doesn't sweep these fallback messages.
      const store = useMessageStore.getState()
      for (const [messageId, partial] of streamingBuffer.entries()) {
        if (partial) {
          console.log(`📝 Promoting partial streaming content for message ${messageId}`)
          const existing = store.entities[messageId]
          store.upsertMessage({
            id: messageId,
            roomId,
            messageType: 'agent',
            content: partial,
            senderName: existing?.senderName || 'Agent',
            agentId: existing?.agentId,
            timestamp: existing?.timestamp || new Date().toISOString(),
            isEphemeral: false,
            taskStatus: null,
          }, 'optimistic')
        }
      }
      streamingBuffer.clear()
    }

    // HITL reconnect catch-up: restore pending HITL requests after SSE reconnects
    if (sseConnected && !prevSseConnectedRef.current && roomId) {
      fetchPendingHitlRequests(roomId, getToken)
        .then(async (res) => {
          if (res.requests?.length) {
            console.log(`🔔 Restoring ${res.requests.length} pending HITL request(s)`)
            const msgStore = useMessageStore.getState()
            for (const req of res.requests) {
              let resolvedName = req.agent_name
              if (!resolvedName && req.agent_id) {
                resolvedName = await getAgentName(req.agent_id)
              }
              msgStore.upsertMessage({
                id: req.message_id,
                roomId,
                messageType: 'agent',
                content: req.prompt || '',
                senderName: resolvedName || 'Agent',
                timestamp: normalizeTimestampOrNow(req.created_at),
                agentId: req.agent_id,
                taskStatus: 'input-required' as TaskState,
                hitlRequestId: req.request_id,
                hitlPrompt: req.prompt,
                hitlPromptType: req.prompt_type || 'text',
                hitlChoices: req.choices,
                hitlExpiresAt: req.expires_at,
                hitlResolved: false,
                hitlGroupId: req.group_id ?? undefined,
                hitlGroupTotal: req.group_total ?? undefined,
                hitlGroupIndex: req.group_index ?? undefined,
              }, 'sse')
              hitlRequestIndex.current.set(req.request_id, req.message_id)
            }
          }
        })
        .catch((err) => {
          console.error('[HITL] Failed to fetch pending requests on reconnect:', err)
        })
    }

    // Safety-net: if SSE reconnected after a gap during processing, the
    // terminal processing_status SSE may have been the event that was lost.
    // Schedule a deferred check against the room's persisted state. If the
    // backend already cleared processing_message_id (it writes to DB before
    // broadcasting), we know the terminal event was lost and can recover.
    let safetyTimer: ReturnType<typeof setTimeout> | null = null
    if (sseConnected && processing && sseHadDisconnectionRef.current) {
      safetyTimer = setTimeout(async () => {
        if (!sseHadDisconnectionRef.current) return
        try {
          const result = await roomQuery.refetch()
          const freshRoom = result.data
          if (freshRoom && !freshRoom.processing_message_id) {
            console.log('🔄 Safety-net: backend confirms processing ended — clearing stuck spinner')
            clearProcessing()
            sseHadDisconnectionRef.current = false
            reconcileWithDb(roomId)
          }
        } catch {
          // Network error — next reconnect cycle or page refresh will retry
        }
      }, 15_000)
    }

    prevSseConnectedRef.current = sseConnected

    return () => {
      if (safetyTimer) clearTimeout(safetyTimer)
    }
  }, [sseConnected, processing, roomId, getToken, getAgentName, roomQuery, clearProcessing, reconcileWithDb])

  // Update room settings - now includes debate mode
  const updateRoomSettings = useCallback(async (
    roomName: string,
    selectedAgents: { [agentId: string]: Agent },
    options: { debateMode: boolean }
  ) => {
    if (!room) {
      banner.error('Room data not available')
      return false
    }

    const { debateMode } = options

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

      // Only update debateMode in extend_info; supervisor mode is managed
      // separately by the chat input toggle and handleSendMessage.
      // Refetch room first to get the latest extend_info from backend,
      // avoiding stale use_supervisor from the React Query cache.
      const currentDebateMode = getDebateMode()
      if (debateMode !== currentDebateMode) {
        const freshRoom = await roomQuery.refetch()
        const freshExtendInfo = (freshRoom.data?.extend_info as object) || {}
        const updatedExtendInfo = {
          ...freshExtendInfo,
          debateMode,
        }

        const extendInfoResponse = await updateRoomExtendInfo(roomId, updatedExtendInfo)
        if (!extendInfoResponse.success) {
          throw new Error(`Failed to update room settings: ${extendInfoResponse.error}`)
        }

        console.log('✅ Room extend_info updated:', {
          debateMode: debateMode ? 'ENABLED' : 'DISABLED',
        })
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
  const sendUserMessage = useCallback(async (userInput: string, targetGroup: string = "all_agents", quoteData?: QuoteData, pendingAttachments?: PendingAttachment[]) => {
    if (!userId || !userName || !room || sending || isProcessingRef.current) {
      return false
    }

    // Generate temporary message ID for optimistic update
    const tempMessageId = `temp-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
    const currentTime = new Date().toISOString()

    // Step 0: Immediately add user message + placeholder to normalized store
    const processingPlaceholderId = getProcessingPlaceholderId()
    const msgStoreSend = useMessageStore.getState()
    msgStoreSend.upsertMessage({
      id: tempMessageId,
      roomId,
      messageType: 'user',
      content: userInput,
      senderName: userName,
      userId,
      timestamp: currentTime,
      attachments: pendingAttachments?.map(att => ({
        fileId: att.id,
        fileUrl: att.previewUrl || undefined,
        mimeType: att.file.type,
        fileName: att.file.name,
        sizeBytes: att.file.size,
      })),
    }, 'optimistic')
    msgStoreSend.upsertMessage({
      id: processingPlaceholderId,
      roomId,
      messageType: 'agent',
      content: '',
      senderName: 'HYBRO AI',
      taskStatus: TASK_STATE.WORKING,
      taskContent: 'Processing your request...',
      timestamp: new Date(Date.now() + 1).toISOString(),
      isEphemeral: true,
    }, 'optimistic')

    try {
      setSending(true)  // Show spinner during message creation & parsing
      isProcessingRef.current = true

      // Upload pending attachments
      let uploadedAttachments: Array<{ file_id: string }> | undefined
      let uploadResponses: Map<string, { fileId: string; fileUrl: string; mimeType: string; fileName: string; sizeBytes: number }> | undefined
      if (pendingAttachments && pendingAttachments.length > 0) {
        const { uploadFile } = await import('@/lib/api/files')
        uploadResponses = new Map()
        const results = await Promise.all(
          pendingAttachments.map(async (att) => {
            const res = await uploadFile(att.file, roomId, getToken)
            uploadResponses!.set(att.id, {
              fileId: res.file_id,
              fileUrl: res.file_url,
              mimeType: res.mime_type,
              fileName: res.file_name,
              sizeBytes: res.size_bytes,
            })
            return { file_id: res.file_id }
          })
        )
        uploadedAttachments = results
      }

      // Step 1: Send user message to backend using unified SendMessage API
      const createResponse = await SendMessage(
        roomId, userInput, getToken, userId, userName, targetGroup,
        quoteData?.messageId ?? null,
        quoteData?.content ?? null,
        uploadedAttachments,
      )

      if (!createResponse.success) {
        throw new Error(`Failed to create user message: ${createResponse.error}`)
      }

      // Extract message_id from createResponse
      const messageId = createResponse.message_id || createResponse.message?.message_id || ""

      if (!messageId) {
        console.error('SendMessage returned no message_id; treating as failure')

        // Rollback optimistic messages
        const msgStoreNoId = useMessageStore.getState()
        msgStoreNoId.removeMessage(tempMessageId)
        msgStoreNoId.removeMessage(getProcessingPlaceholderId())

        banner.error('Message sent but server returned no ID. Please try again.')

        // Revoke orphaned blob URLs since attachments have already been cleared
        // from the input component and are unreachable by its cleanup.
        if (pendingAttachments) {
          for (const att of pendingAttachments) {
            if (att.previewUrl) {
              try { URL.revokeObjectURL(att.previewUrl) } catch { /* ignore */ }
            }
          }
        }

        clearProcessing()
        currentProcessingMessageId.current = null
        isProcessingRef.current = false

        return false
      }

      // Step 2: Swap temp ID to real ID in normalized store
      const msgStoreSwap = useMessageStore.getState()
      msgStoreSwap.removeMessage(tempMessageId)
      msgStoreSwap.upsertMessage({
        id: messageId,
        roomId,
        messageType: 'user',
        content: userInput,
        senderName: userName,
        userId,
        timestamp: currentTime,
        attachments: pendingAttachments?.map(att => {
          const uploaded = uploadResponses?.get(att.id)
          return {
            fileId: uploaded?.fileId || att.id,
            fileUrl: uploaded?.fileUrl || att.previewUrl || undefined,
            mimeType: uploaded?.mimeType || att.file.type,
            fileName: uploaded?.fileName || att.file.name,
            sizeBytes: uploaded?.sizeBytes || att.file.size,
          }
        }),
      }, 'optimistic')

      // Blob preview URLs are no longer needed now that server URLs are in
      // the store.  Revoke them to free browser blob memory.
      if (pendingAttachments) {
        for (const att of pendingAttachments) {
          if (att.previewUrl) {
            try { URL.revokeObjectURL(att.previewUrl) } catch { /* ignore */ }
          }
        }
      }

      // Store message ID for potential cancellation
      currentProcessingMessageId.current = messageId

      // Step 3: Processing is auto-triggered by backend when sendMessage completes.
      setSending(false)
      setProcessing(true)
      setCancelling(false)
      cancelTimedOutRef.current = false
      sseHadDisconnectionRef.current = false
      if (cancelTimeoutRef.current) {
        clearTimeout(cancelTimeoutRef.current)
        cancelTimeoutRef.current = null
      }

      console.log('📡 Message queued for processing, waiting for agent responses via SSE...')

      if (!sseConnected) {
        console.log('⚠️ SSE not connected, processing will complete but updates may be delayed')
      }

      return true

    } catch (error) {
      console.error('Error in message workflow:', error)

      // Targeted rollback: remove only the specific optimistic messages (Gap 5)
      const msgStoreErr = useMessageStore.getState()
      msgStoreErr.removeMessage(tempMessageId)
      msgStoreErr.removeMessage(getProcessingPlaceholderId())

      banner.error(`Failed to send message: ${error instanceof Error ? error.message : 'Unknown error'}`)

      // Revoke orphaned blob URLs since attachments have already been cleared
      // from the input component and are unreachable by its cleanup.
      if (pendingAttachments) {
        for (const att of pendingAttachments) {
          if (att.previewUrl) {
            try { URL.revokeObjectURL(att.previewUrl) } catch { /* ignore */ }
          }
        }
      }

      // Reconcile to recover any messages that might have been lost
      try {
        console.log('🔄 Reconciling messages after error to ensure sync...')
        await reconcileWithDb(roomId)
      } catch (reloadError) {
        console.error('Failed to reconcile messages after error:', reloadError)
      }

      clearProcessing()
      currentProcessingMessageId.current = null

      return false
    } finally {
      setSending(false)
      // NOTE: Do NOT clear isProcessingRef here on the success path.
      // It stays true until processing completes (via SSE terminal events)
      // to prevent a race window where the user could double-send between
      // setProcessing(true) propagating through Zustand and the next render.
    }
  }, [userId, userName, room, roomId, sending, sseConnected, getToken, setSending, setProcessing, clearProcessing, setCancelling, getProcessingPlaceholderId, reconcileWithDb])

  // Cancel ongoing message processing
  const cancelProcessing = useCallback(async () => {
    const messageId = currentProcessingMessageId.current
    if (!messageId) {
      banner.warning('Unable to cancel — no active task found')
      return false
    }

    try {
      setCancelling(true)
      cancelTimedOutRef.current = false
      console.log('🛑 Cancelling message:', messageId)
      await cancelMessage(messageId, getToken)

      // Batch cancel all non-terminal tasks in the normalized store
      useMessageStore.getState().cancelAllNonTerminal(roomId)

      // Start cancellation timeout safety net (Gap 11)
      cancelTimeoutRef.current = setTimeout(() => {
        const { cancelling } = useRoomUiStore.getState()
        if (cancelling) {
          cancelTimedOutRef.current = true
          setCancelling(false)
          clearProcessing()
          banner.warning('Cancellation timed out — the agent may still be running')
        }
      }, 15000)

      return true
    } catch (error) {
      console.error('Error cancelling message:', error)
      setCancelling(false)
      banner.error(`Failed to stop processing: ${error instanceof Error ? error.message : 'Unknown error'}`)
      return false
    }
  }, [getToken, setCancelling, clearProcessing, roomId])

  // Respond to a HITL request — inline Q&A display pattern:
  // 1. Mark agent message resolved + embed answer  2. Optionally show processing placeholder (last in group)
  const respondToHitlRequest = useCallback(async (requestId: string, userInput: string) => {
    const entityId = hitlRequestIndex.current.get(requestId)
    const store = useMessageStore.getState()
    const entity = entityId ? store.entities[entityId] : undefined

    const processingPlaceholderId = getProcessingPlaceholderId()

    // Determine if this is the last unanswered question in its group
    const isGrouped = entity?.hitlGroupId != null
    let isLastInGroup = true
    if (isGrouped && entity?.hitlGroupId) {
      const allEntities = Object.values(store.entities)
      const siblings = allEntities.filter(e => e.hitlGroupId === entity.hitlGroupId && e.id !== entity.id)
      const unresolvedSiblings = siblings.filter(e => !e.hitlResolved && !e.hitlUserAnswer)
      isLastInGroup = unresolvedSiblings.length === 0
    }

    // Optimistic: mark resolved and embed the user's answer inline
    if (entity) {
      store.upsertMessage({
        id: entity.id,
        roomId,
        messageType: 'agent',
        content: entity.content,
        senderName: entity.senderName,
        timestamp: entity.timestamp,
        hitlResolved: true,
        hitlUserAnswer: userInput,
      }, 'optimistic')
    }
    hitlRequestIndex.current.delete(requestId)

    // Only show processing placeholder after the LAST question in a group (or non-grouped)
    if (isLastInGroup) {
      placeholderDismissedRef.current = false
      store.upsertMessage({
        id: processingPlaceholderId,
        roomId,
        messageType: 'agent',
        content: '',
        senderName: 'HYBRO AI',
        taskStatus: TASK_STATE.WORKING,
        taskContent: 'Processing your input...',
        timestamp: new Date(Date.now() + 1).toISOString(),
        isEphemeral: true,
      }, 'optimistic')
      setProcessing(true)
      isProcessingRef.current = true
    }

    try {
      const { respondToHitl } = await import('@/lib/api/hitl')
      await respondToHitl(roomId, requestId, userInput, getToken)
    } catch (err) {
      // 409 Conflict = request already responded/processing — treat as success.
      if (err instanceof ApiError && err.status === 409) {
        console.log('HITL respond returned 409 (already handled) — keeping optimistic state')
        return
      }

      // AbortError (timeout) — the backend is still processing the supervisor
      // resume which can take 60-120s. Keep the optimistic state; the eventual
      // hitl_status_update SSE will reconcile.
      if (err instanceof Error && err.name === 'AbortError') {
        console.log('HITL respond timed out — backend still processing, keeping optimistic state')
        return
      }

      // Genuine failure — rollback optimistic updates so the HITL form reappears
      if (entity) {
        store.upsertMessage({
          id: entity.id,
          roomId,
          messageType: 'agent',
          content: entity.content,
          senderName: entity.senderName,
          timestamp: entity.timestamp,
          hitlResolved: false,
          hitlUserAnswer: undefined,
        }, 'optimistic')
      }
      if (entityId) {
        hitlRequestIndex.current.set(requestId, entityId)
      }
      if (isLastInGroup) {
        store.removeMessage(processingPlaceholderId)
        clearProcessing()
      }

      throw err
    }
  }, [roomId, getToken, setProcessing, clearProcessing, getProcessingPlaceholderId])

  // Manually refresh messages — delegates to reconcileWithDb (Gap 14)
  const refreshMessages = useCallback(async () => {
    console.log('🔄 Manual message refresh requested')
    await reconcileWithDb(roomId)
  }, [roomId, reconcileWithDb])

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

  // Get current room data for form initialization
  const getRoomFormData = useCallback(() => {
    if (!room) return null

    return {
      roomName: room.room_name || '',
      roomId: room.room_id || '',
      selectedAgents: room.room_agent_set || {},
      roomOwnerId: room.room_owner_id || '',
      roomOwnerName: room.room_owner_name || '',
      debateMode: getDebateMode(),
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
  }, [roomQuery.isError, roomQuery.error])

  // Periodic cache cleanup to prevent memory accumulation
  useEffect(() => {
    const cleanup = setInterval(() => {
      console.log('🧹 Performing periodic cache cleanup')
    }, 300000) // Every 5 minutes

    return () => clearInterval(cleanup)
  }, [])


  return {
    // State
    room,
    loading,
    sending,
    processing,
    cancelling,
    updatingRoom,

    // SSE State
    sseConnected,
    sseConnecting,
    sseError,
    sseEnabled,

    // Debate Mode
    debateMode: getDebateMode(),

    // Supervisor Mode
    supervisorMode: getSupervisorMode(),

    // Actions
    sendUserMessage,
    cancelProcessing,
    respondToHitlRequest,
    updateRoomSettings,
    refreshMessages,
    refreshRoomSetting,
    getAgentList,
    getRoomFormData,
    toggleSSE,
    availableAgents: allAgentsQuery.data || [],
  }
}
