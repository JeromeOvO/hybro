'use client'

import { useState, useEffect } from 'react'
import { useParams } from 'next/navigation'
import { useUser } from '@clerk/nextjs'
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

  // This function will be called when user clicks send button
  const handleSendMessage = async (userInput: string) => {
    console.log('handleSendMessage called with:', userInput)
    const success = await sendUserMessage(userInput)
    console.log('Message send result:', success)
  }

  // Handle room settings update
  const handleRoomSettingsUpdate = async (roomName: string, selectedAgents: { [agentId: string]: Agent }) => {
    const success = await updateRoomSettings(roomName, selectedAgents)
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

  return (
    <div className="flex flex-col h-screen bg-background">
      <div className="flex-1 overflow-hidden">
        <div className="w-full max-w-4xl mx-auto px-4 sm:px-6 h-full flex flex-col">
          {/* Fixed Header - Never scrolls */}
          <header className="flex-shrink-0 flex items-center justify-between py-4 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 z-10">
            <div>
              <h1 className="text-xl font-semibold">{room.room_name}</h1>
              <p className="text-sm text-muted-foreground">
                {processing && (
                  <span className="text-blue-600">Processing messages...</span>
                )}
              </p>
            </div>
            
            {/* Settings Button */}
            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
              <DialogTrigger asChild>
                <Button variant="ghost" size="icon">
                  <Settings className="h-5 w-5" />
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
          </header>

          {/* Scrollable Messages Area - Only this area scrolls */}
          <main className="flex-1 overflow-hidden">
            <RoomMessages messages={messages} loading={false} />
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
