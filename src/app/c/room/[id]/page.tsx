'use client'

import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { useUser, useAuth } from '@clerk/nextjs'
import { RequireAuth } from '@/components/require-auth'
import { toast } from 'sonner'
import { Users, Pencil, Check, X as XIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { GroupManagementModal } from '@/components/group-management-modal'
import { RoomDefaultAgentsEditor } from '@/components/room-default-agents-editor'
import { RoomPageShell, type TimelineAdapter } from '@/components/room-page-shell'
import { useRoomWebhook } from '@/hooks/useRoomWebhook'
import { useGroupManagement } from '@/hooks/useGroupManagement'
import { useRoomUiStore, useRoomFlags } from '@/stores/room-ui-store'
import type { QuoteData } from '@/components/message-bubble'
import type { PendingAttachment } from '@/lib/types/attachments'
import { BUILTIN_GROUP_ROOM_TEAM, BUILTIN_GROUP_ALL_AGENTS, isBuiltinGroup } from '@/lib/types/agent-group'
import type { MessageDispatchInput } from '@/lib/types/agent-group'
import { updateRoomExtendInfo, inquiryRoomSetting, updateRoomAgentSet, updateRoomName } from '@/lib/api/room'
import type { ChatMode } from '@/lib/types/chat-mode'
import { chatModeToFlags, flagsToChatMode } from '@/lib/types/chat-mode'

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

  // Inline room name editing
  const [editingName, setEditingName] = useState(false)
  const [editNameValue, setEditNameValue] = useState('')
  const nameInputRef = useRef<HTMLInputElement>(null)

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

  // Feature flag: turn-based timeline
  const roomFlags = useRoomFlags(roomId)
  const turnBasedTimeline = roomFlags.turnBasedTimeline

  // Detect ?newui=1 URL parameter to activate turn-based timeline
  useEffect(() => {
    if (typeof window === 'undefined') return
    const params = new URLSearchParams(window.location.search)
    const newUi = params.get('newui')
    useRoomUiStore.getState().setTurnBasedTimeline(roomId, newUi === '1')
  }, [roomId])

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
      const pendingGroup = pendingData?.targetGroup

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

    // Default (autosend) mode: existing behavior unchanged
    initialMessageSentRef.current = true

    const targetGroup = pendingData.targetGroup || (
      room.room_agent_set && Object.keys(room.room_agent_set).length > 0
        ? BUILTIN_GROUP_ROOM_TEAM
        : BUILTIN_GROUP_ALL_AGENTS
    )

    // Extract inline mentions so the backend uses canonical mention dispatch
    // instead of the legacy parse that filters against room_agent_set.
    let dispatch: MessageDispatchInput | undefined
    const mentionPattern = /<@([^|]+)\|[^>]+>/g
    const ids: string[] = []
    let m: RegExpExecArray | null
    while ((m = mentionPattern.exec(pendingData.initialMessage)) !== null) {
      ids.push(m[1])
    }
    if (ids.length > 0) {
      dispatch = { mentioned_agent_ids: ids }
    }

    sendUserMessage(pendingData.initialMessage, targetGroup, undefined, pendingData.attachments, dispatch).then((success) => {
      if (!success) {
        useRoomUiStore.getState().setPendingRoomData(roomId, pendingData)
        initialMessageSentRef.current = false
      }
    })
  }, [room, loading, roomId, user?.id, sendUserMessage])

  // This function will be called when user clicks send button
  const handleSendMessage = async (userInput: string, targetGroup?: string, quoteData?: QuoteData | null, attachments?: PendingAttachment[]) => {
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
    const effectiveTarget = targetGroup || gm.selectedGroup || "all_agents"
    if (roomAgentCount === 0 && !isBuiltinGroup(effectiveTarget)) {
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
        await sendUserMessage(userInput, BUILTIN_GROUP_ROOM_TEAM, quoteData ?? undefined, attachments, { message_target_mode: "room_default" })
        return
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to set room agents')
        return
      }
    }

    // When targetGroup is undefined the composer detected inline mentions —
    // build a MentionDispatchInput so the backend uses canonical mention routing.
    let dispatch: MessageDispatchInput | null = gm.resolvedTargetMode
    if (!targetGroup) {
      const mentionPattern = /<@([^|]+)\|[^>]+>/g
      const ids: string[] = []
      let m: RegExpExecArray | null
      while ((m = mentionPattern.exec(userInput)) !== null) {
        ids.push(m[1])
      }
      if (ids.length > 0) {
        dispatch = { mentioned_agent_ids: ids }
      }
    }
    await sendUserMessage(userInput, targetGroup || gm.selectedGroup || "all_agents", quoteData ?? undefined, attachments, dispatch ?? undefined)
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

  // Inline room name editing
  const startEditingName = useCallback(() => {
    if (!room) return
    setEditNameValue(room.room_name || '')
    setEditingName(true)
    setTimeout(() => nameInputRef.current?.focus(), 0)
  }, [room])

  const saveRoomName = useCallback(async () => {
    if (!room || !editNameValue.trim()) {
      setEditingName(false)
      return
    }
    if (editNameValue.trim() === room.room_name) {
      setEditingName(false)
      return
    }
    try {
      const result = await updateRoomName(roomId, editNameValue.trim(), getToken)
      if (result.success) {
        await refreshRoomSetting()
      } else {
        toast.error(result.error || 'Failed to update room name')
      }
    } catch {
      toast.error('Failed to update room name')
    }
    setEditingName(false)
  }, [room, roomId, editNameValue, getToken, refreshRoomSetting])

  const cancelEditingName = useCallback(() => {
    setEditingName(false)
  }, [])

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
          {/* Fixed Header */}
          <header className="shrink-0 flex items-center justify-between py-4 bg-background/95 backdrop-blur supports-backdrop-filter:bg-background/60 z-10 max-w-4xl mx-auto w-full">
            <div className="flex items-center gap-3">
              <div className="space-y-1">
                {/* Inline-editable room name */}
                {editingName ? (
                  <div className="flex items-center gap-1.5">
                    <input
                      ref={nameInputRef}
                      type="text"
                      value={editNameValue}
                      onChange={(e) => setEditNameValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') saveRoomName()
                        if (e.key === 'Escape') cancelEditingName()
                      }}
                      onBlur={saveRoomName}
                      className="text-xl font-semibold bg-transparent border-b-2 border-primary outline-none px-0 py-0 min-w-[120px]"
                    />
                    <Button variant="ghost" size="icon" className="h-6 w-6" onMouseDown={(e) => { e.preventDefault(); saveRoomName() }}>
                      <Check className="h-3.5 w-3.5" />
                    </Button>
                    <Button variant="ghost" size="icon" className="h-6 w-6" onMouseDown={(e) => { e.preventDefault(); cancelEditingName() }}>
                      <XIcon className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ) : (
                  <button
                    onClick={startEditingName}
                    className="flex items-center gap-1.5 group text-left"
                    title="Click to edit room name"
                  >
                    <h1 className="text-xl font-semibold">{room.room_name}</h1>
                    <Pencil className="h-3.5 w-3.5 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
                  </button>
                )}

                <div className="flex items-center gap-2 flex-wrap">
                  {roomAgentCount > 0 && (
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <div className="flex items-center gap-1 text-xs text-muted-foreground">
                            <Users className="h-3 w-3" />
                            <span>Room: {roomAgentCount} agent{roomAgentCount !== 1 ? 's' : ''}</span>
                          </div>
                        </TooltipTrigger>
                        <TooltipContent>
                          <div className="space-y-1">
                            <p className="font-medium">Room agents:</p>
                            {Object.values(room.room_agent_set || {}).map((name, i) => (
                              <p key={i} className="text-xs">{name}</p>
                            ))}
                          </div>
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  )}

                  {debateMode && (
                    <div className="flex items-center gap-1">
                      <div className="w-2 h-2 bg-purple-500 rounded-full animate-pulse" />
                      <span className="text-xs text-purple-600 dark:text-purple-400 font-medium">
                        Debate Mode
                      </span>
                    </div>
                  )}
                </div>
              </div>

              <div
                className={`w-2 h-2 rounded-full transition-colors duration-200 ${sseConnected ? 'bg-green-500' :
                  sseConnecting ? 'bg-yellow-500 animate-pulse' :
                    'bg-red-500'
                  }`}
                title={
                  sseConnected ? 'Live updates connected' :
                    sseConnecting ? 'Connecting to live updates...' :
                      'Live updates disconnected'
                }
              />
            </div>

            <div className="flex items-center gap-2 self-start">
              {!sseEnabled && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={toggleSSE}
                  className="text-xs text-muted-foreground"
                >
                  Enable Live Updates
                </Button>
              )}
            </div>
          </header>

          <RoomPageShell
            adapter={timelineAdapter}
            turnBasedTimeline={turnBasedTimeline}
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
