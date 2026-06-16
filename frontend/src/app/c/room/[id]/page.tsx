'use client'

import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { useUser, useAuth } from '@clerk/nextjs'
import { RequireAuth } from '@/components/require-auth'
import { toast } from 'sonner'
import { GroupManagementModal } from '@/components/group-management-modal'
import { RoomDefaultAgentsEditor } from '@/components/room-default-agents-editor'
import { RoomPageShell, type TimelineAdapter } from '@/components/room-page-shell'
import { useRoomWebhook } from '@/hooks/useRoomWebhook'
import { useGroupManagement } from '@/hooks/useGroupManagement'
import { useRoomUiStore } from '@/stores/room-ui-store'
import type { QuoteData } from '@/lib/types/quote'
import type { PendingAttachment } from '@/lib/types/attachments'
import {
  BUILTIN_GROUP_ROOM_TEAM,
  BUILTIN_GROUP_ALL_AGENTS,
  isBuiltinGroup,
  isMentionDispatchInput,
} from '@/lib/types/agent-group'
import type { MessageDispatchInput } from '@/lib/types/agent-group'
import { updateRoomExtendInfo, inquiryRoomSetting, updateRoomAgentSet } from '@/lib/api/room'
import type { ChatMode } from '@/lib/types/chat-mode'
import { chatModeToFlags, flagsToChatMode } from '@/lib/types/chat-mode'

function selectedGroupFromDispatch(dispatch: MessageDispatchInput | undefined): string | undefined {
  if (!dispatch || isMentionDispatchInput(dispatch)) return undefined
  if (dispatch.message_target_mode === 'room_default') return BUILTIN_GROUP_ROOM_TEAM
  if (dispatch.message_target_mode === 'all_agents') return BUILTIN_GROUP_ALL_AGENTS
  return dispatch.target_group_id
}

export default function RoomChatPage() {
  const params = useParams()
  const roomId = params.id as string
  const { user, isLoaded } = useUser()
  const { getToken } = useAuth()
  const [editorOpen, setEditorOpen] = useState(false)
  // Ref to track if initial message has been sent
  const initialMessageSentRef = useRef(false)

  // Prefill state
  const [prefillValue, setPrefillValue] = useState("")

  // Quote state
  const [quote, setQuote] = useState<QuoteData | null>(null)
  const handleQuote = useCallback((data: QuoteData) => setQuote(data), [])
  const clearQuote = useCallback(() => setQuote(null), [])

  // Local chat mode (synced from room, user can override before sending)
  const [localChatMode, setLocalChatMode] = useState<ChatMode | null>(null)

  const confirmedChatModeRef = useRef<ChatMode | null>(null)

  const {
    room,
    loading,
    sending,
    processing,
    cancelling,
    sendUserMessage,
    cancelProcessing,
    respondToHitlRequest,
    getRoomFormData,
    refreshRoomSetting,
    // SSE state
    sseConnected,
    sseConnecting,
    sseEnabled,
    toggleSSE,
    // Debate mode
    debateMode,
    // Supervisor mode (from room extend_info)
    supervisorMode: roomSupervisorMode,
  } = useRoomWebhook({
    roomId,
    userId: user?.id,
    userName: user?.firstName || user?.username || 'User',
    getToken
  })

  // Room agent count
  const roomAgentCount = room?.room_agent_set ? Object.keys(room.room_agent_set).length : 0

  // Sync local chat mode from room data (re-syncs when roomId changes)
  const lastSyncedRoomRef = useRef<string | null>(null)
  useEffect(() => {
    if (room && lastSyncedRoomRef.current !== roomId) {
      lastSyncedRoomRef.current = roomId
      const synced = flagsToChatMode(roomSupervisorMode, debateMode)
      setLocalChatMode(synced)
      confirmedChatModeRef.current = synced
    }
  }, [room, roomId, roomSupervisorMode, debateMode])

  // Derived chat mode: local selection falls back to room's persisted value (anti-flicker)
  const effectiveChatMode = localChatMode ?? flagsToChatMode(roomSupervisorMode, debateMode)

  // Group management (extracted hook)
  const gm = useGroupManagement({
    userId: user?.id,
    getToken,
    isLoaded,
    defaultGroup: roomAgentCount > 0 ? BUILTIN_GROUP_ROOM_TEAM : undefined,
    roomId,
    roomAgentCount,
  })

  // Set default group based on stored selection or room's agent set (runs once when room loads)
  const initialGroupSetRef = useRef(false)
  useEffect(() => {
    if (room && !initialGroupSetRef.current) {
      initialGroupSetRef.current = true

      // Priority: localStorage (persistent override) > pending data from chat page > default
      const localStorageKey = `room-${roomId}-override-group`
      const localStorageOverride = localStorage.getItem(localStorageKey)

      // Peek at pending room data for target group (don't consume yet)
      const pendingData = useRoomUiStore.getState().pendingRoomData[roomId]
      const pendingGroup = selectedGroupFromDispatch(pendingData?.dispatch)
        ?? pendingData?.targetGroup

      if (localStorageOverride) {
        gm.handleGroupChange(localStorageOverride)
      } else if (pendingGroup) {
        const hasRoomAgents = room.room_agent_set && Object.keys(room.room_agent_set).length > 0
        const defaultGroup = hasRoomAgents ? BUILTIN_GROUP_ROOM_TEAM : BUILTIN_GROUP_ALL_AGENTS
        if (pendingGroup !== defaultGroup) {
          gm.handleGroupChange(pendingGroup)
        }
      }
    }
  }, [room, roomId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Check for and send initial message from chat page (via Zustand store)
  useEffect(() => {
    if (!room || loading || initialMessageSentRef.current || !user?.id) {
      return
    }

    const pendingData = useRoomUiStore.getState().consumePendingRoomData(roomId)
    if (!pendingData) {
      return
    }

    // Prefill mode: inject into input, don't auto-send
    if (pendingData.handoffMode === "prefill") {
      setPrefillValue(pendingData.initialMessage)
      initialMessageSentRef.current = true
      return
    }

    initialMessageSentRef.current = true

    if (!pendingData.dispatch) {
      console.debug('Blocked pending room autosend without final MessageDispatchInput')
      return
    }

    sendUserMessage({
      userInput: pendingData.initialMessage,
      pendingAttachments: pendingData.attachments,
      dispatch: pendingData.dispatch,
    }).then((success) => {
      if (!success) {
        useRoomUiStore.getState().setPendingRoomData(roomId, pendingData)
        initialMessageSentRef.current = false
      }
    })
  }, [room, loading, roomId, user?.id, sendUserMessage])

  // This function will be called when user clicks send button
  const handleSendMessage = async (userInput: string, dispatchInput: MessageDispatchInput, quoteData?: QuoteData | null, attachments?: PendingAttachment[]) => {
    // Lazy-persist chat mode changes
    const baseline = confirmedChatModeRef.current ?? effectiveChatMode
    const modeChanged = room && effectiveChatMode !== baseline
    if (modeChanged) {
      let freshExtendInfo: object = {}
      try {
        const freshRoom = await inquiryRoomSetting(roomId, getToken)
        if (freshRoom.success && freshRoom.room?.extend_info) {
          freshExtendInfo = freshRoom.room.extend_info as object
        }
      } catch {
        freshExtendInfo = (room?.extend_info as object) || {}
      }
      const modeFlags = chatModeToFlags(effectiveChatMode)
      const updatedExtendInfo: Record<string, unknown> = {
        ...freshExtendInfo,
        use_supervisor: modeFlags.use_supervisor,
        debateMode: modeFlags.debateMode,
      }
      try {
        const result = await updateRoomExtendInfo(roomId, updatedExtendInfo, getToken)
        if (result.success) {
          confirmedChatModeRef.current = effectiveChatMode
        } else {
          setLocalChatMode(confirmedChatModeRef.current)
          toast.warning('Failed to update mode settings — message sent with previous setting')
        }
      } catch {
        setLocalChatMode(confirmedChatModeRef.current)
        toast.warning('Failed to update mode settings — message sent with previous setting')
      }
    }

    // Empty room + saved group override: pre-write room_agent_set before sending (Matrix B5)
    const dispatchGroup = selectedGroupFromDispatch(dispatchInput)
    const effectiveTarget = dispatchGroup || gm.selectedGroup || "all_agents"
    if (
      roomAgentCount === 0
      && !isMentionDispatchInput(dispatchInput)
      && dispatchInput.message_target_mode === 'saved_group'
      && !isBuiltinGroup(effectiveTarget)
    ) {
      try {
        const preWriteResult = await updateRoomAgentSet(
          roomId, {}, getToken,
          { membership_seed_input: "saved_group", seed_group_id: effectiveTarget },
        )
        if (!preWriteResult.success) {
          toast.error(preWriteResult.error || 'Failed to set room agents from group')
          return
        }
        // Refetch room so roomAgentCount updates and selector transitions to Room Default
        await refreshRoomSetting()
        gm.handleClearOverride()
        await sendUserMessage({
          userInput,
          quoteData: quoteData ?? undefined,
          pendingAttachments: attachments,
          dispatch: { message_target_mode: "room_default" },
        })
        return
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to set room agents')
        return
      }
    }

    await sendUserMessage({
      userInput,
      quoteData: quoteData ?? undefined,
      pendingAttachments: attachments,
      dispatch: dispatchInput,
    })
  }

  // Open room default agents editor (prefetch agents)
  const handleEditRoomAgents = useCallback(async () => {
    if (gm.availableAgents.length === 0 && !gm.loadingAgents) {
      await gm.loadAvailableAgents()
    }
    setEditorOpen(true)
  }, [gm])

  // Save handler for the room default agents editor
  const handleEditorSave = useCallback(async (membershipAgentIds: string[]) => {
    const result = await updateRoomAgentSet(
      roomId, {}, getToken,
      { membership_seed_input: "manual", room_agent_ids: membershipAgentIds },
    )
    if (!result.success) {
      throw new Error(result.error || 'Failed to update room agents')
    }
    setEditorOpen(false)
  }, [roomId, getToken])

  // Current room agent IDs for the editor
  const currentRoomAgentIds = useMemo(
    () => room ? Object.keys(room.room_agent_set || {}) : [],
    [room]
  )

  // Agent list for @mentions
  const agentList = useMemo(() => {
    return gm.availableAgents.map(agent => ({
      id: agent.agent_id,
      name: agent.agent_card.name,
      iconUrl: agent.agent_card.iconUrl,
    }))
  }, [gm.availableAgents])

  // Get room form data for initialization (memoized to avoid unstable references)
  const roomFormData = useMemo(() => getRoomFormData(), [getRoomFormData])

  // Build TimelineAdapter for RoomPageShell
  const timelineAdapter: TimelineAdapter = {
    roomId,
    getToken,
    onSendMessage: handleSendMessage,
    onCancelProcessing: cancelProcessing,
    onRespondToHitl: respondToHitlRequest,
    onChatModeChange: setLocalChatMode,
    isSending: sending,
    isProcessing: processing,
    isCancelling: cancelling,
    agents: agentList,
    roomAgentIds: currentRoomAgentIds,
    groupManagement: {
      groups: gm.groups,
      loadingGroups: gm.loadingGroups,
      selectedGroup: gm.selectedGroup,
      isOverride: gm.isOverride,
      handleGroupChange: gm.handleGroupChange,
      handleClearOverride: gm.handleClearOverride,
      handleCreateGroup: gm.handleCreateGroup,
      handleEditGroup: gm.handleEditGroup,
      handleDeleteGroup: gm.handleDeleteGroup,
      onEditRoomAgents: handleEditRoomAgents,
    },
    quoteState: {
      quote,
      setQuote: handleQuote,
      clearQuote,
    },
    chatMode: effectiveChatMode,
    externalValue: prefillValue,
    onExternalValueConsumed: () => setPrefillValue(""),
  }

  if (!isLoaded || loading) {
    return (
      <RequireAuth>
        <div className="flex items-center justify-center h-full">
          <div className="text-muted-foreground">Loading room...</div>
        </div>
      </RequireAuth>
    )
  }

  if (!room) {
    return (
      <RequireAuth>
        <div className="flex items-center justify-center h-full">
          <div className="text-destructive">Room not found</div>
        </div>
      </RequireAuth>
    )
  }

  return (
    <RequireAuth>
    <div className="flex flex-col h-screen bg-background">
      <div className="flex-1 overflow-hidden">
        <div className="w-full h-full flex flex-col">
          <RoomPageShell
            adapter={timelineAdapter}
          />
        </div>
      </div>

      <RoomDefaultAgentsEditor
        open={editorOpen}
        onOpenChange={setEditorOpen}
        currentRoomAgentIds={currentRoomAgentIds}
        availableAgents={gm.availableAgents}
        loadingAgents={gm.loadingAgents}
        savedGroups={gm.groups}
        resolvedAgents={roomFormData?.resolvedAgents ?? undefined}
        onSave={handleEditorSave}
      />

      <GroupManagementModal
        open={gm.groupManagementOpen}
        onOpenChange={(open) => {
          gm.setGroupManagementOpen(open)
          if (!open) {
            gm.setGroupAction(null)
          }
        }}
        groups={gm.groups}
        onGroupsChange={gm.handleGroupsChange}
        onGroupCreated={gm.handleGroupCreated}
        availableAgents={gm.availableAgents}
        loadingAgents={gm.loadingAgents}
        userId={user?.id || ''}
        getToken={getToken}
        loadAgents={gm.loadAvailableAgents}
        agentsError={gm.agentsError}
        initialAction={gm.groupAction || undefined}
      />
    </div>
    </RequireAuth>
  )
}
