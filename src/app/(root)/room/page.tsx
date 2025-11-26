 'use client'
 
 import { useState, useEffect, useRef, useCallback } from 'react'
 import { useUser, useAuth } from '@clerk/nextjs'
 import { useRouter } from 'next/navigation'
 import {
   Card,
   CardContent,
   CardDescription,
   CardHeader,
   CardTitle,
 } from "@/components/ui/card"
 import { RoomSettingForm, type RoomSettingFormHandle } from "@/components/room-setting-form"
 import { CheckCircle, Loader2 } from "lucide-react"
 import { getAllAgents } from "@/lib/api/agent"
 import { createNewRoom } from "@/lib/api/room"
 import { toast } from "sonner"
 import type { Agent } from "@/lib/types/agent"
 import { useClerk } from '@clerk/nextjs'
 import { isWaitlistEnabled } from "@/lib/utils"
 
 export default function RoomPage() {
   const router = useRouter()
   const { user, isLoaded } = useUser()
   const { getToken } = useAuth()
   const { openWaitlist } = useClerk()
  // Ref for form reset
  const formRef = useRef<RoomSettingFormHandle>(null)

  // State for agents
  const [availableAgents, setAvailableAgents] = useState<Agent[]>([])
  const [loadingAgents, setLoadingAgents] = useState(false)
  const [agentsError, setAgentsError] = useState<string | null>(null)

  // State for room creation
  const [isCreatingRoom, setIsCreatingRoom] = useState(false)
  const [roomCreated, setRoomCreated] = useState(false) // New state for success
  const [, setCreatedRoomName] = useState('')

  const loadAvailableAgents = useCallback(async () => {
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
      const errorMessage = error instanceof Error ? error.message : 'Failed to load agents'
      setAgentsError(errorMessage)
      toast.error(errorMessage)
    } finally {
      setLoadingAgents(false)
    }
  }, [])

  // Load agents on component mount
  useEffect(() => {
    loadAvailableAgents()
  }, [loadAvailableAgents])

  const handleFormSubmit = async (roomName: string, selectedAgents: { [agentId: string]: Agent }, debateMode: boolean) => {
    // 1) Do nothing while Clerk is still loading to avoid unexpected waitlist popup
    if (!isLoaded) {
      return
    }

    // 2) Once loaded, if there's no user, either open the waitlist or redirect to sign-in
    if (!user) {
      if (isWaitlistEnabled()) {
        openWaitlist()
      } else {
        router.push("/sign-in")
      }
      return
    }

    try {
      setIsCreatingRoom(true)
      
      // Create agent set mapping: agent id -> agent name (canonical shape)
      const roomAgentSet = Object.fromEntries(
        Object.entries(selectedAgents).map(([id, agent]) => [
          id,                     // key: agent id
          agent.agent_card.name,  // value: agent name
        ])
      )

      // Get user info from Clerk
      const roomOwnerId = user.id
      const roomOwnerName = user.fullName || user.firstName || user.username || 'Unknown User'

      // Create extend_info with debate mode
      const extendInfo = {
        debateMode
      }

      const response = await createNewRoom(
        roomName,
        roomOwnerId,
        roomOwnerName,
        getToken,
        roomAgentSet,
        extendInfo // Pass debate mode in extend_info
      )
      
      if (response.success && response.room) {
        // Set success state and room name
        setCreatedRoomName(roomName)
        setRoomCreated(true)
        
        // Show success toast with mode info
        const modeText = debateMode ? ' with debate mode enabled' : ''
        toast.success(`Room "${roomName}" created successfully${modeText}!`)
        
        // Reset form
        formRef.current?.reset()
        
        // Redirect to room chat page after a short delay
        setTimeout(() => {
          router.push(`/room/${response.room?.room_id}`)
        }, 1000)
        
      } else {
        throw new Error(response.error || 'Failed to create room')
      }
    } catch (error) {
      console.error('Failed to create room:', error)
      const errorMessage = error instanceof Error ? error.message : 'Failed to create room'
      toast.error(errorMessage)
    } finally {
      setIsCreatingRoom(false)
    }
  }

  // Show loading if user info is not loaded yet
  if (!isLoaded) {
    return (
      <div className="container mx-auto flex items-center justify-center min-h-screen p-4">
        <Loader2 className="h-8 w-8 animate-spin icon-action" />
      </div>
    )
  }

  // Show success loading screen after room creation
  if (roomCreated) {
    return (
      <div className="container mx-auto flex flex-col items-center justify-center min-h-screen p-4">
        <div className="text-center space-y-6">
          <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-green-100 dark:bg-green-900">
            <CheckCircle className="h-8 w-8 icon-success" />
          </div>
          <div className="flex items-center justify-center">
            <div className="flex items-center gap-3 px-4 py-2 bg-muted rounded-lg">
              <Loader2 className="h-4 w-4 animate-spin icon-action" />
              <span className="text-sm">Entering room...</span>
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="container mx-auto flex items-center justify-center min-h-screen p-4">
      {/* Room Creation Form */}
      <Card className="w-full max-w-3xl">
        <CardHeader className="space-y-3">
          <CardTitle className="text-2xl">Set up your room with a name and invite agents to join</CardTitle>
          <CardDescription className="text-base">
            Welcome {user?.firstName || user?.username}! Create your AI-powered chat room.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isCreatingRoom && (
            <div className="flex items-center justify-center mb-6 p-4">
              <div className="flex flex-col items-center gap-3">
                <div className="flex items-center justify-center w-12 h-12 rounded-full bg-primary/10">
                  <Loader2 className="h-6 w-6 animate-spin text-primary" />
                </div>
                <span className="text-sm font-medium text-muted-foreground">Creating your room...</span>
              </div>
            </div>
          )}
          
          <RoomSettingForm 
            ref={formRef}
            onSubmit={handleFormSubmit}
            isSubmitting={isCreatingRoom}
            isEditing={false}
            availableAgents={availableAgents}
            loadingAgents={loadingAgents}
            agentsError={agentsError}
            onRetryLoadAgents={loadAvailableAgents}
          />
        </CardContent>
      </Card>
    </div>
  )
}
