"use client"

import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { useUser, useAuth } from '@/lib/auth'
import { RequireAuth } from '@/components/require-auth'
import { GroupManagementModal } from '@/components/group-management-modal'
import { RoomPageShell, type TimelineAdapter } from '@/components/room-page-shell'
import { useRoomWebhook } from '@/hooks/useRoomWebhook'
import { useGroupManagement } from '@/hooks/useGroupManagement'
import { useRoomUiStore } from '@/stores/room-ui-store'
import type { QuoteData } from '@/lib/types/quote'
import type { PendingAttachment } from '@/lib/types/attachments'
import {
  BUILTIN_GROUP_ALL_AGENTS,
  dispatchToAgentScope,
} from '@/lib/types/agent-group'
import type { MessageDispatchInput } from '@/lib/types/agent-group'
import type { ChatMode } from '@/lib/types/chat-mode'
import { chatModeToExecutionMode, roomDefaultToChatMode } from '@/lib/types/chat-mode'

export default function RoomChatPage() {
  const params = useParams()
  const roomId = params.id as string
  const { user, isLoaded } = useUser()
  const { getToken } = useAuth()
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


  const {
    room,
    loading,
    sending,
    processing,
    cancelling,
    sendUserMessage,
    cancelProcessing,
    respondToHitlRequest,
    respondToHitlBatch,
    cancelHitlRequest,
    refreshMessages,
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
      setLocalChatMode(roomDefaultToChatMode(roomSupervisorMode))
    }
  }, [room, roomId, roomSupervisorMode])

  // Room setting is only the UI default; every send carries its own mode.
  const effectiveChatMode = localChatMode ?? roomDefaultToChatMode(roomSupervisorMode)

  // A room seeded from a saved team follows that team while it still exists.
  const roomDefaultTeamId = room?.source_group_id
    ?? room?.applied_from_group
    ?? undefined

  // Group management (extracted hook)
  const gm = useGroupManagement({
    userId: user?.id,
    getToken,
    isLoaded,
    defaultGroup: roomDefaultTeamId ?? BUILTIN_GROUP_ALL_AGENTS,
    defaultGroupName: room?.source_group_name ?? undefined,
    defaultTargetMode: roomAgentCount > 0
      ? { message_target_mode: 'room_default' }
      : { message_target_mode: 'all_agents' },
    roomId,
  })

  // Set an explicit override from local or pending state (runs once per room).
  const initialGroupSetRef = useRef(false)
  useEffect(() => {
    if (room && !initialGroupSetRef.current) {
      initialGroupSetRef.current = true

      // A local selector override is independent from the immutable pending request.
      const localStorageKey = `room-${roomId}-override-group`
      const localStorageOverride = localStorage.getItem(localStorageKey)
      const localStorageOverrideName = localStorage.getItem(`${localStorageKey}-name`)

      if (localStorageOverride) {
        gm.handleGroupChange(localStorageOverride, localStorageOverrideName ?? undefined)
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

    if (!pendingData.mode || !pendingData.agentScope || !pendingData.clientRequestId) {
      console.debug('Blocked pending room autosend without immutable execution contract')
      return
    }

    sendUserMessage({
      userInput: pendingData.initialMessage,
      pendingAttachments: pendingData.attachments,
      mode: pendingData.mode,
      agentScope: pendingData.agentScope,
      clientRequestId: pendingData.clientRequestId,
    }).then((success) => {
      if (!success) {
        useRoomUiStore.getState().setPendingRoomData(roomId, pendingData)
        initialMessageSentRef.current = false
      }
    })
  }, [room, loading, roomId, user?.id, sendUserMessage])

  // This function will be called when user clicks send button
  const handleSendMessage = async (userInput: string, dispatchInput: MessageDispatchInput, quoteData?: QuoteData | null, attachments?: PendingAttachment[]) => {
    await sendUserMessage({
      userInput,
      quoteData: quoteData ?? undefined,
      pendingAttachments: attachments,
      mode: chatModeToExecutionMode(effectiveChatMode),
      agentScope: dispatchToAgentScope(dispatchInput),
    })
  }

  // Current room agent IDs for snapshot-scoped mentions
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

  // Build TimelineAdapter for RoomPageShell
  const timelineAdapter: TimelineAdapter = {
    roomId,
    getToken,
    onSendMessage: handleSendMessage,
    onCancelProcessing: cancelProcessing,
    onRespondToHitl: respondToHitlRequest,
    onRespondToHitlBatch: respondToHitlBatch,
    onCancelHitl: cancelHitlRequest,
    onRefreshHitl: refreshMessages,
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
      selectedGroupName: gm.selectedGroupName,
      resolvedTargetMode: gm.resolvedTargetMode,
      handleGroupChange: gm.handleGroupChange,
      handleCreateGroup: gm.handleCreateGroup,
      handleEditGroup: gm.handleEditGroup,
      handleDeleteGroup: gm.handleDeleteGroup,
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
    <div className="flex h-[calc(100dvh-3.5rem)] flex-col bg-background md:h-screen">
      <div className="flex-1 overflow-hidden">
        <div className="w-full h-full flex flex-col">
          <RoomPageShell
            adapter={timelineAdapter}
          />
        </div>
      </div>

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
