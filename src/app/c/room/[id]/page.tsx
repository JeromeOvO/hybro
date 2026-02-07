'use client'

import { useState, useEffect, useRef, useMemo } from 'react'
import { useParams } from 'next/navigation'
import { useUser, useClerk, useAuth } from '@clerk/nextjs'
import { Settings, Users } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { RoomSettingForm } from '@/components/room-setting-form'
import { RoomMessages } from '@/components/room-messages'
import { RoomChatInput } from '@/components/room-chat-input'
import { GroupManagementModal } from '@/components/group-management-modal'
import { useRoomWebhook } from '@/hooks/useRoomWebhook'
import { useGroupManagement } from '@/hooks/useGroupManagement'
import { useRoomUiStore } from '@/stores/room-ui-store'
import type { Agent } from '@/lib/types/agent'
import { BUILTIN_GROUP_ROOM_TEAM, BUILTIN_GROUP_ALL_AGENTS } from '@/lib/types/agent-group'
import { isWaitlistEnabled } from "@/lib/utils"

export default function RoomChatPage() {
  const params = useParams()
  const roomId = params.id as string
  const { user, isLoaded } = useUser()
  const { getToken } = useAuth()
  const [dialogOpen, setDialogOpen] = useState(false)
  const { openWaitlist } = useClerk()
  // Ref to track if initial message has been sent
  const initialMessageSentRef = useRef(false)

  const {
    room,
    messages,
    loading,
    sending,
    processing,
    updatingRoom,
    sendUserMessage,
    cancelProcessing,
    updateRoomSettings,
    getRoomFormData,
    // SSE state
    sseConnected,
    sseConnecting,
    sseEnabled,
    toggleSSE,
    // Debate mode
    debateMode,
  } = useRoomWebhook({
    roomId,
    userId: user?.id,
    userName: user?.firstName || user?.username || 'User',
    getToken
  })

  // Room agent count
  const roomAgentCount = room?.room_agent_set ? Object.keys(room.room_agent_set).length : 0

  // Group management (extracted hook)
  const gm = useGroupManagement({
    userId: user?.id,
    getToken,
    isLoaded,
    defaultGroup: roomAgentCount > 0 ? BUILTIN_GROUP_ROOM_TEAM : BUILTIN_GROUP_ALL_AGENTS,
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

    initialMessageSentRef.current = true

    const targetGroup = pendingData.targetGroup || (
      room.room_agent_set && Object.keys(room.room_agent_set).length > 0
        ? BUILTIN_GROUP_ROOM_TEAM
        : BUILTIN_GROUP_ALL_AGENTS
    )

    sendUserMessage(pendingData.initialMessage, targetGroup).then((success) => {
      if (!success) {
        // Re-store on failure so it can be retried
        useRoomUiStore.getState().setPendingRoomData(roomId, pendingData)
        initialMessageSentRef.current = false
      }
    })
  }, [room, loading, roomId, user?.id, sendUserMessage])

  // This function will be called when user clicks send button
  const handleSendMessage = async (userInput: string, targetGroup?: string) => {
    await sendUserMessage(userInput, targetGroup || gm.selectedGroup)
  }

  // Handle room settings update
  const handleRoomSettingsUpdate = async (
    roomName: string,
    selectedAgents: { [agentId: string]: Agent },
    debateMode: boolean
  ) => {
    const success = await updateRoomSettings(roomName, selectedAgents, debateMode)
    if (success) {
      setDialogOpen(false)
    }
  }

  // Open room settings dialog (prefetch agents)
  const handleOpenRoomSettings = async () => {
    if (gm.availableAgents.length === 0 && !gm.loadingAgents) {
      await gm.loadAvailableAgents()
    }
    setDialogOpen(true)
  }

  // Agent list for @mentions
  const agentList = useMemo(() => {
    return gm.availableAgents.map(agent => ({
      id: agent.agent_id,
      name: agent.agent_card.name
    }))
  }, [gm.availableAgents])

  // Get room form data for initialization
  const roomFormData = getRoomFormData()

  if (!isLoaded || loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-muted-foreground">Loading room...</div>
      </div>
    )
  }

  if (!room) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-destructive">Room not found</div>
      </div>
    )
  }

  if (isLoaded && !user?.id) {
    if (isWaitlistEnabled()) {
      openWaitlist()
    } else {
      if (typeof window !== "undefined") {
        window.location.href = "/sign-in"
      }
    }
    return
  }

  return (
    <div className="flex flex-col h-screen bg-background">
      <div className="flex-1 overflow-hidden">
        <div className="w-full h-full flex flex-col">
          {/* Fixed Header */}
          <header className="shrink-0 flex items-center justify-between py-4 bg-background/95 backdrop-blur supports-backdrop-filter:bg-background/60 z-10 px-4 sm:px-6 max-w-4xl mx-auto w-full">
            <div className="flex items-center gap-3">
              <div className="space-y-1">
                <h1 className="text-xl font-semibold">{room.room_name}</h1>

                <div className="flex items-center gap-2 flex-wrap">
                  {roomAgentCount > 0 && (
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <div className="flex items-center gap-1 text-xs text-muted-foreground">
                            <Users className="h-3 w-3" />
                            <span>Team: {roomAgentCount} agent{roomAgentCount !== 1 ? 's' : ''}</span>
                          </div>
                        </TooltipTrigger>
                        <TooltipContent>
                          <div className="space-y-1">
                            <p className="font-medium">Room team:</p>
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

              <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                <div className="flex items-center gap-2">
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={handleOpenRoomSettings}
                          className="text-primary hover:text-primary hover:bg-primary/10"
                          aria-label="Room settings"
                          onMouseEnter={() => {
                            if (gm.availableAgents.length === 0 && !gm.loadingAgents) {
                              gm.loadAvailableAgents()
                            }
                          }}
                        >
                          <Settings className="h-5 w-5" />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>
                        <p>Configure room settings</p>
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </div>
                <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto bg-background/80 backdrop-blur-md border shadow-lg">
                  <DialogHeader>
                    <DialogTitle>Room Settings</DialogTitle>
                  </DialogHeader>
                  <div className="mt-4">
                    <RoomSettingForm
                      onSubmit={handleRoomSettingsUpdate}
                      availableAgents={gm.availableAgents}
                      loadingAgents={gm.loadingAgents}
                      agentsError={gm.agentsError}
                      isSubmitting={updatingRoom}
                      isEditing={true}
                      onRetryLoadAgents={gm.loadAvailableAgents}
                      initialData={roomFormData}
                    />
                  </div>
                </DialogContent>
              </Dialog>
            </div>
          </header>

          <main className="flex-1 overflow-hidden">
            <RoomMessages
              messages={messages}
              loading={false}
            />
          </main>
        </div>
      </div>

      <div className="bg-background p-4">
        <div className="max-w-4xl mx-auto px-4 sm:px-6">
          <RoomChatInput
            onSubmit={handleSendMessage}
            disableSend={sending || processing}
            sending={sending}
            processing={processing}
            onCancel={cancelProcessing}
            agents={agentList}
            showGroupSelector={true}
            groups={gm.groups}
            loadingGroups={gm.loadingGroups}
            selectedGroup={gm.selectedGroup}
            onGroupChange={gm.handleGroupChange}
            roomAgentCount={roomAgentCount}
            onCreateGroup={gm.handleCreateGroup}
            onEditGroup={gm.handleEditGroup}
            onDeleteGroup={gm.handleDeleteGroup}
            isOverride={gm.isOverride}
            onClearOverride={gm.handleClearOverride}
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
  )
}
