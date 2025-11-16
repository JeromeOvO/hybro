'use client'

import { useState, useEffect, useRef } from 'react'
import { useParams } from 'next/navigation'
import { useUser, useClerk } from '@clerk/nextjs'
import { Settings } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { RoomSettingForm } from '@/components/room-setting-form'
import { RoomMessages } from '@/components/room-messages'
import { RoomChatInput } from '@/components/room-chat-input'
import { useRoomWebhook } from '@/hooks/useRoomWebhook'
import { getAllAgents } from '@/lib/api/agent'
import type { Agent } from '@/lib/types/agent'

export default function RoomChatPage() {
  const params = useParams()
  const roomId = params.id as string
  const { user } = useUser()
  // State for agents in dialog
  const [availableAgents, setAvailableAgents] = useState<Agent[]>([])
  const [loadingAgents, setLoadingAgents] = useState(false)
  const [agentsError, setAgentsError] = useState<string | null>(null)
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
    updateRoomSettings,
    getAgentList,
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
    userName: user?.firstName || user?.username || 'User'
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

  // Load agents when dialog opens
  useEffect(() => {
    if (dialogOpen && availableAgents.length === 0) {
      loadAvailableAgents()
    }
  }, [dialogOpen])

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
      
      // Clear from sessionStorage
      sessionStorage.removeItem(storageKey)
      
      // Send the message
      sendUserMessage(initialMessage).then((success) => {
        if (success) {
          console.log('✅ Initial message sent successfully')
        } else {
          console.error('❌ Failed to send initial message')
          // Reset the flag if sending failed so user can retry
          initialMessageSentRef.current = false
        }
      })
    }
  }, [room, loading, roomId, user?.id, sendUserMessage])

  // This function will be called when user clicks send button
  const handleSendMessage = async (userInput: string) => {
    console.log('handleSendMessage called with:', userInput)
    const success = await sendUserMessage(userInput)
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

  // Extract agent list for @mentions
  const agentList = getAgentList()

  // Get room form data for initialization
  const roomFormData = getRoomFormData()

  if (loading) {
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

  if (!user?.id) {
    openWaitlist()
    return
  }

  return (
    <div className="flex flex-col h-screen bg-background">
      <div className="flex-1 overflow-hidden">
        <div className="w-full max-w-4xl mx-auto px-4 sm:px-6 h-full flex flex-col">
          {/* Fixed Header - Never scrolls */}
          <header className="flex-shrink-0 flex items-center justify-between py-4 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 z-10">
            <div className="flex items-center gap-3">
              <div>
                <h1 className="text-xl font-semibold">{room.room_name}</h1>
                {debateMode && (
                  <div className="flex items-center gap-1 mt-1">
                    <div className="w-2 h-2 bg-purple-500 rounded-full animate-pulse" />
                    <span className="text-xs text-purple-600 dark:text-purple-400 font-medium">
                      Debate Mode Active
                    </span>
                  </div>
                )}
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
            disabled={sending || processing}
            agents={agentList}
          />
        </div>
      </div>
    </div>
  )
}
