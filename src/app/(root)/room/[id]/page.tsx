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
  DialogTrigger,
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
import { getAllAgents } from '@/lib/api/agent'
import { listAgentGroups } from '@/lib/api/agent-group'
import type { Agent } from '@/lib/types/agent'
import type { AgentGroup } from '@/lib/types/agent-group'
import { BUILTIN_GROUP_ROOM_TEAM, BUILTIN_GROUP_ALL_AGENTS } from '@/lib/types/agent-group'
import { isWaitlistEnabled } from "@/lib/utils"

export default function RoomChatPage() {
  const params = useParams()
  const roomId = params.id as string
  const { user, isLoaded } = useUser()
  const { getToken } = useAuth()
  // State for agents in dialog
  const [availableAgents, setAvailableAgents] = useState<Agent[]>([])
  const [loadingAgents, setLoadingAgents] = useState(false)
  const [agentsError, setAgentsError] = useState<string | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const { openWaitlist } = useClerk()
  // Ref to track if initial message has been sent
  const initialMessageSentRef = useRef(false)
  
  // Group selector state
  const [groups, setGroups] = useState<AgentGroup[]>([])
  const [loadingGroups, setLoadingGroups] = useState(false)
  const [selectedGroup, setSelectedGroup] = useState<string>(BUILTIN_GROUP_ROOM_TEAM)
  const [isOverride, setIsOverride] = useState(false)  // Track if override is active
  const [groupManagementOpen, setGroupManagementOpen] = useState(false)
  
  const {
    room,
    messages,
    loading,
    sending,
    processing,
    updatingRoom,
    sendUserMessage,
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

  // Load agents when dialog opens
  const loadAvailableAgents = async () => {
    try {
      setLoadingAgents(true)
      setAgentsError(null)
      const response = await getAllAgents()
      
      if (response.success && response.agents) {
        setAvailableAgents(response.agents)
      } else {
        throw new Error(response.error || 'Failed to load agents')
      }
    } catch (error) {
      console.error('Failed to load agents:', error)
      setAgentsError(error instanceof Error ? error.message : 'Failed to load agents')
    } finally {
      setLoadingAgents(false)
    }
  }

  // Load agents on mount for mention suggestions
  useEffect(() => {
    if (isLoaded && user?.id && availableAgents.length === 0) {
      loadAvailableAgents()
    }
  }, [isLoaded, user?.id, availableAgents.length])

  // Load user's groups
  useEffect(() => {
    const loadGroups = async () => {
      if (!user?.id) return
      
      setLoadingGroups(true)
      try {
        const response = await listAgentGroups(user.id, getToken)
        if (response.success && response.groups) {
          setGroups(response.groups)
        }
      } catch (error) {
        console.error('Failed to load groups:', error)
      } finally {
        setLoadingGroups(false)
      }
    }

    if (isLoaded && user?.id) {
      loadGroups()
    }
  }, [isLoaded, user?.id, getToken])

  // Refresh groups after changes in modal
  const handleGroupsChange = async () => {
    if (!user?.id) return
    try {
      const response = await listAgentGroups(user.id, getToken)
      if (response.success && response.groups) {
        setGroups(response.groups)
      }
    } catch (error) {
      console.error('Failed to refresh groups:', error)
    }
  }

  // Open group management modal
  const handleManageGroups = () => {
    if (availableAgents.length === 0) {
      loadAvailableAgents()
    }
    setGroupManagementOpen(true)
  }

  // Handle group change (override) with localStorage persistence
  const handleGroupChange = (groupId: string) => {
    setSelectedGroup(groupId)
    setIsOverride(true)
    // Persist to localStorage
    localStorage.setItem(`room-${roomId}-override-group`, groupId)
  }

  // Handle clear override - revert to default
  const handleClearOverride = () => {
    setIsOverride(false)
    // Default: Room Team if agents exist, otherwise All Agents
    const hasRoomAgents = room?.room_agent_set && Object.keys(room.room_agent_set).length > 0
    const defaultGroup = hasRoomAgents ? BUILTIN_GROUP_ROOM_TEAM : BUILTIN_GROUP_ALL_AGENTS
    setSelectedGroup(defaultGroup)
    // Clear from localStorage
    localStorage.removeItem(`room-${roomId}-override-group`)
  }

  // Set default group based on stored selection or room's agent set (runs once when room loads)
  const initialGroupSetRef = useRef(false)
  useEffect(() => {
    if (room && !initialGroupSetRef.current) {
      initialGroupSetRef.current = true
      
      // Priority: localStorage (persistent override) > sessionStorage (from chat page) > default
      const localStorageKey = `room-${roomId}-override-group`
      const sessionStorageKey = `room-${roomId}-target-group`
      
      const localStorageOverride = localStorage.getItem(localStorageKey)
      const sessionStorageGroup = sessionStorage.getItem(sessionStorageKey)
      
      if (localStorageOverride) {
        // Use persisted override from localStorage
        setSelectedGroup(localStorageOverride)
        setIsOverride(true)
      } else if (sessionStorageGroup) {
        // Use the stored selection from chat page (first navigation)
        setSelectedGroup(sessionStorageGroup)
        // If it's not the default, mark as override and persist to localStorage
        const hasRoomAgents = room.room_agent_set && Object.keys(room.room_agent_set).length > 0
        const defaultGroup = hasRoomAgents ? BUILTIN_GROUP_ROOM_TEAM : BUILTIN_GROUP_ALL_AGENTS
        if (sessionStorageGroup !== defaultGroup) {
          setIsOverride(true)
          localStorage.setItem(localStorageKey, sessionStorageGroup)
        }
        // Note: sessionStorage is cleared by the "send initial message" effect after successful send
      } else {
        // Fall back to determining based on room agents (default, no override)
        const hasRoomAgents = room.room_agent_set && Object.keys(room.room_agent_set).length > 0
        setSelectedGroup(hasRoomAgents ? BUILTIN_GROUP_ROOM_TEAM : BUILTIN_GROUP_ALL_AGENTS)
        setIsOverride(false)
      }
    }
  }, [room, roomId])

  // Check for and send initial message from chat page
  useEffect(() => {
    // Only proceed if:
    // 1. Room is loaded
    // 2. Not currently loading
    // 3. Initial message hasn't been sent yet
    // 4. User is available
    if (!room || loading || initialMessageSentRef.current || !user?.id) {
      return
    }

    // Check sessionStorage for initial message
    const storageKey = `room-${roomId}-initial-message`
    const initialMessage = sessionStorage.getItem(storageKey)

    if (initialMessage) {
      console.log('📨 Found initial message, sending automatically:', initialMessage)
      
      // Mark as sent immediately to prevent duplicate sends
      initialMessageSentRef.current = true
      
      // Get the target group from sessionStorage (set by chat page)
      const targetGroupKey = `room-${roomId}-target-group`
      const storedTargetGroup = sessionStorage.getItem(targetGroupKey)
      
      // Use the stored target group, or fall back to determining based on room agents
      const targetGroup = storedTargetGroup || (
        room.room_agent_set && Object.keys(room.room_agent_set).length > 0 
          ? BUILTIN_GROUP_ROOM_TEAM 
          : BUILTIN_GROUP_ALL_AGENTS
      )
      
      console.log('📨 Sending with target group:', targetGroup, 'storedTargetGroup:', storedTargetGroup)
      
      sendUserMessage(initialMessage, targetGroup).then((success) => {
        if (success) {
          console.log('✅ Initial message sent successfully')
          // Only clear sessionStorage AFTER successful send
          sessionStorage.removeItem(storageKey)
          sessionStorage.removeItem(targetGroupKey)
        } else {
          console.error('❌ Failed to send initial message')
          // Reset the flag so user can retry (message still in sessionStorage)
          initialMessageSentRef.current = false
        }
      })
    }
  }, [room, loading, roomId, user?.id, sendUserMessage])

  // This function will be called when user clicks send button
  const handleSendMessage = async (userInput: string, targetGroup?: string) => {
    console.log('handleSendMessage called with:', userInput, 'targetGroup:', targetGroup)
    const success = await sendUserMessage(userInput, targetGroup || selectedGroup)
    console.log('Message send result:', success)
  }

  // Handle room settings update - now includes debate mode
  const handleRoomSettingsUpdate = async (
    roomName: string, 
    selectedAgents: { [agentId: string]: Agent }, 
    debateMode: boolean
  ) => {
    const success = await updateRoomSettings(roomName, selectedAgents, debateMode)
    if (success) {
      setDialogOpen(false) // Close dialog on success
    }
  }

  // Extract agent list for @mentions - always show all available agents
  const agentList = useMemo(() => {
    return availableAgents.map(agent => ({
      id: agent.agent_id,
      name: agent.agent_card.name
    }))
  }, [availableAgents])

  // Get room form data for initialization
  const roomFormData = getRoomFormData()

  // Room agent count
  const roomAgentCount = room?.room_agent_set ? Object.keys(room.room_agent_set).length : 0

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
        <div className="w-full max-w-4xl mx-auto px-4 sm:px-6 h-full flex flex-col">
          {/* Fixed Header - Never scrolls */}
          <header className="flex-shrink-0 flex items-center justify-between py-4 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 z-10">
            <div className="flex items-center gap-3">
              <div className="space-y-1">
                <h1 className="text-xl font-semibold">{room.room_name}</h1>
                
                {/* Room team / Debate mode info */}
                <div className="flex items-center gap-2 flex-wrap">
                  {/* Show agent count if room has agents */}
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
              
              {/* Simple SSE Connection Status - Just a dot */}
              <div 
                className={`w-2 h-2 rounded-full transition-colors duration-200 ${
                  sseConnected ? 'bg-green-500' : 
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
            
            {/* Settings Button */}
            <div className="flex items-center gap-2">
              {/* Optional: Toggle SSE button (can be hidden if not needed) */}
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
                <DialogTrigger asChild>
                  <Button variant="ghost" size="icon">
                    <Settings className="h-5 w-5 icon-neutral" />
                  </Button>
                </DialogTrigger>
                <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto bg-background/80 backdrop-blur-md border shadow-lg">
                  <DialogHeader>
                    <DialogTitle>Room Settings</DialogTitle>
                  </DialogHeader>
                  <div className="mt-4">
                    <RoomSettingForm
                      onSubmit={handleRoomSettingsUpdate}
                      availableAgents={availableAgents}
                      loadingAgents={loadingAgents}
                      agentsError={agentsError}
                      isSubmitting={updatingRoom}
                      isEditing={true}
                      onRetryLoadAgents={loadAvailableAgents}
                      initialData={roomFormData}
                    />
                  </div>
                </DialogContent>
              </Dialog>
            </div>
          </header>

          {/* Scrollable Messages Area - Only this area scrolls */}
          <main className="flex-1 overflow-hidden">
            <RoomMessages 
              messages={messages} 
              loading={false} 
              processing={processing}
            />
          </main>
        </div>
      </div>
      
      <div className="bg-background p-4">
        <div className="max-w-4xl mx-auto px-4 sm:px-6">
          <RoomChatInput
            onSubmit={handleSendMessage}
            disableSend={sending || processing}
            agents={agentList}
            showGroupSelector={true}
            groups={groups}
            loadingGroups={loadingGroups}
            selectedGroup={selectedGroup}
            onGroupChange={handleGroupChange}
            roomAgentCount={roomAgentCount}
            onManageGroups={handleManageGroups}
            isOverride={isOverride}
            onClearOverride={handleClearOverride}
          />
        </div>
      </div>

      {/* Group Management Modal */}
      <GroupManagementModal
        open={groupManagementOpen}
        onOpenChange={setGroupManagementOpen}
        groups={groups}
        onGroupsChange={handleGroupsChange}
        availableAgents={availableAgents}
        loadingAgents={loadingAgents}
        userId={user?.id || ''}
        getToken={getToken}
      />
    </div>
  )
}
