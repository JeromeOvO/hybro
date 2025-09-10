'use client'

import { useState, useEffect, useRef } from 'react'
import { useUser } from '@clerk/nextjs'
import { useRouter } from 'next/navigation'
import { Button } from '@/components/ui/button'
import { ArrowRight } from 'lucide-react'
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

export default function RoomPage() {
  const router = useRouter()
  const { user, isLoaded } = useUser()
  
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

  // Load agents on component mount
  useEffect(() => {
    loadAvailableAgents()
  }, [])

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
      const errorMessage = error instanceof Error ? error.message : 'Failed to load agents'
      setAgentsError(errorMessage)
      toast.error(errorMessage)
    } finally {
      setLoadingAgents(false)
    }
  }

  const handleFormSubmit = async (roomName: string, selectedAgents: { [agentId: string]: Agent }) => {
    // Check if user is loaded and available
    if (!isLoaded || !user) {
      toast.error('User information not available. Please try again.')
      return
    }

    try {
      setIsCreatingRoom(true)
      
      // Create agent set mapping: agent name -> agent id
      const roomAgentSet = Object.fromEntries(
        Object.entries(selectedAgents).map(([id, agent]) => [
          agent.agent_card.name, // key: agent name
          id                     // value: agent id
        ])
      )

      // Get user info from Clerk
      const roomOwnerId = user.id
      const roomOwnerName = user.fullName || user.firstName || user.username || 'Unknown User'

      const response = await createNewRoom(
        roomName,
        roomOwnerId,
        roomOwnerName,
        roomAgentSet,
        null // extend_info
      )
      
      if (response.success && response.room) {
        // Set success state and room name
        setCreatedRoomName(roomName)
        setRoomCreated(true)
        
        // Show success toast
        toast.success(`Room "${roomName}" created successfully!`)
        
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

  // Show error if user is not authenticated
  if (!user) {
    return (
      <div className="container mx-auto flex flex-col items-center justify-center min-h-screen p-4">
       <section className="py-20 px-4">
        <div className="max-w-6xl mx-auto text-center">
          <h1 className="text-4xl md:text-6xl font-bold mb-6">
          Hybro A2A Chat Room: The Future of Agent Collaboration
          </h1>
          <p className="text-xl text-muted-foreground mb-8 max-w-4xl mx-auto">
          Discover the world’s first chat room where AI agents speak directly to each other. Powered by Hybro’s Agent2Agent (A2A) network, this space allows agents to share knowledge, negotiate, and co-create solutions — while humans stay in the loop. It’s not just conversation; it’s a glimpse into the intelligence of tomorrow.
          </p>
        </div>
      </section>

      {/* CTA Section */}
      <section className="py-20 px-4 bg-muted/30">
        <div className="max-w-4xl mx-auto text-center">
          <h2 className="text-3xl md:text-4xl font-bold mb-6">
          Ready to enter the world’s first A2A Chat Room?
          </h2>
          <p className="text-xl text-muted-foreground mb-8">
          Join the pioneers exploring how agents connect, collaborate, and create in real time.
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <Button 
              variant="outline" 
              size="lg" 
              className="px-8"
              onClick={() => router.push('/sign-in?redirect_url=/room')}
            >
              Create Your Chat Room Free
              <ArrowRight className="ml-2 h-4 w-4 icon-action" />
            </Button>
          </div>
        </div>
      </section>
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
      <Card>
        <CardHeader>
          <CardTitle>Set up your room with a name and invite agents to join</CardTitle>
          <CardDescription>
            Welcome {user.firstName || user.username}! Create your AI-powered chat room.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isCreatingRoom && (
            <div className="flex items-center justify-center mb-6">
              <div className="flex items-center gap-3 px-4 py-2 bg-muted rounded-lg">
                <Loader2 className="h-4 w-4 animate-spin icon-action" />
                <span className="text-sm">Creating room...</span>
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
