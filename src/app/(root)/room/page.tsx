'use client'

import { useState, useEffect, useRef } from 'react'
import { useUser } from '@clerk/nextjs'
import { useRouter } from 'next/navigation'
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { RoomSettingForm, type RoomSettingFormHandle } from "@/components/room-setting-form"
import { CheckCircle, AlertCircle } from "lucide-react"
import { getAllAgents } from "@/lib/api/agent"
import { createNewRoom } from "@/lib/api/room"
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

  // State for messages
  const [message, setMessage] = useState<{
    type: 'success' | 'error'
    content: string
  } | null>(null)

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
      setAgentsError(error instanceof Error ? error.message : 'Failed to load agents')
    } finally {
      setLoadingAgents(false)
    }
  }

  const handleFormSubmit = async (roomName: string, selectedAgents: { [agentId: string]: Agent }) => {
    // Check if user is loaded and available
    if (!isLoaded || !user) {
      setMessage({
        type: 'error',
        content: 'User information not available. Please try again.'
      })
      return
    }

    try {
      setIsCreatingRoom(true)
      setMessage(null)
      
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
        // Show success message briefly
        setMessage({
          type: 'success',
          content: `Room "${roomName}" created successfully! Redirecting to home...`
        })
        
        // Reset form
        formRef.current?.reset()
        
        // Redirect to home page after a short delay
        setTimeout(() => {
          router.push('/')
        }, 1500)
        
      } else {
        throw new Error(response.error || 'Failed to create room')
      }
    } catch (error) {
      console.error('Failed to create room:', error)
      const errorMessage = error instanceof Error ? error.message : 'Failed to create room'
      setMessage({
        type: 'error',
        content: errorMessage
      })
      
      // Auto-hide error message after 8 seconds
      setTimeout(() => setMessage(null), 8000)
    } finally {
      setIsCreatingRoom(false)
    }
  }

  const clearMessage = () => {
    setMessage(null)
  }

  // Show loading if user info is not loaded yet
  if (!isLoaded) {
    return (
      <div className="container mx-auto items-center justify-center h-full p-4">
        <Card>
          <CardContent className="flex items-center justify-center py-8">
            <div className="text-muted-foreground">Loading...</div>
          </CardContent>
        </Card>
      </div>
    )
  }

  // Show error if user is not authenticated
  if (!user) {
    return (
      <div className="container mx-auto items-center justify-center h-full p-4">
        <Card>
          <CardContent className="flex items-center justify-center py-8">
            <div className="text-destructive">Please sign in to create a room.</div>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="container mx-auto items-center justify-center h-full p-4">
      {/* Success/Error Messages */}
      {message && (
        <div className="mb-6">
          <Alert 
            variant={message.type === 'error' ? 'destructive' : 'default'}
            className={message.type === 'success' ? 'border-green-200 bg-green-50 text-green-700' : ''}
          >
            {message.type === 'success' ? (
              <CheckCircle className="h-4 w-4" />
            ) : (
              <AlertCircle className="h-4 w-4" />
            )}
            <AlertDescription className="flex justify-between items-center">
              {message.content}
              <Button
                variant="ghost"
                size="sm"
                onClick={clearMessage}
                className="h-auto p-1 text-current hover:text-current"
              >
                ×
              </Button>
            </AlertDescription>
          </Alert>
        </div>
      )}

      {/* Room Creation Form */}
      <Card>
        <CardHeader>
          <CardTitle>Set up your room with a name and invite agents to join</CardTitle>
          <CardDescription>
            Welcome {user.firstName || user.username}! Create your AI-powered chat room.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <RoomSettingForm 
            ref={formRef}
            onSubmit={handleFormSubmit}
            isSubmitting={isCreatingRoom}
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
