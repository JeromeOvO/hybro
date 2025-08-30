'use client'

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

export default function RoomChatPage() {
  const params = useParams()
  const roomId = params.id as string
  const { user } = useUser()
  
  const {
    room,
    messages,
    loading,
    sending,
    processing,
    sendUserMessage,
    getAgentList,
  } = useRoomWebhook({
    roomId,
    userId: user?.id,
    userName: user?.firstName || user?.username || 'User'
  })

  // This function will be called when user clicks send button
  const handleSendMessage = async (userInput: string) => {
    console.log('handleSendMessage called with:', userInput)
    const success = await sendUserMessage(userInput)
    console.log('Message send result:', success)
  }

  // Extract agent list for @mentions
  const agentList = getAgentList()

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
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div>
          <h1 className="text-xl font-semibold">{room.room_name}</h1>
          <p className="text-sm text-muted-foreground">
            {processing && (
              <span className="text-blue-600">Processing messages...</span>
            )}
          </p>
        </div>
        
        {/* Settings Button */}
        <Dialog>
          <DialogTrigger asChild>
            <Button variant="ghost" size="icon">
              <Settings className="h-5 w-5" />
            </Button>
          </DialogTrigger>
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle>Room Settings</DialogTitle>
            </DialogHeader>
            <div className="mt-4">
              <RoomSettingForm
                onSubmit={() => {}}
                availableAgents={[]}
                isSubmitting={false}
                isEditing={true}
              />
            </div>
          </DialogContent>
        </Dialog>
      </div>

      {/* Messages Area */}
      <RoomMessages messages={messages} loading={false} />

      {/* Chat Input */}
      <div className="p-4 border-t bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <RoomChatInput
          onSubmit={handleSendMessage}
          disabled={sending || processing}
          agents={agentList}
        />
      </div>
    </div>
  )
}
