'use client'

import { useState, useEffect, useRef } from 'react'
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
import { createNewRoom, inquiryRoomsByRoomOwnerId } from "@/lib/api/room"
import type { Agent } from "@/lib/types/agent"
import type { Room } from "@/lib/types/room"

export default function RoomPage() {
  // Ref for form reset
  const formRef = useRef<RoomSettingFormHandle>(null)

  // State for agents
  const [availableAgents, setAvailableAgents] = useState<Agent[]>([])
  const [loadingAgents, setLoadingAgents] = useState(false)
  const [agentsError, setAgentsError] = useState<string | null>(null)

  // State for room creation
  const [isCreatingRoom, setIsCreatingRoom] = useState(false)
  const [rooms, setRooms] = useState<Room[]>([])
  const [loadingRooms, setLoadingRooms] = useState(false)

  // State for messages
  const [message, setMessage] = useState<{
    type: 'success' | 'error'
    content: string
  } | null>(null)

  // Mock user data - replace with actual user context
  const currentUser = {
    id: 'user_123',
    name: 'Current User'
  }

  // Load agents and rooms on component mount
  useEffect(() => {
    loadAvailableAgents()
    loadRooms()
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

  const loadRooms = async () => {
    try {
      setLoadingRooms(true)
      const response = await inquiryRoomsByRoomOwnerId(currentUser.id)
      if (response.success && response.room_list) {
        setRooms(response.room_list)
      }
    } catch (error) {
      console.error('Failed to load rooms:', error)
    } finally {
      setLoadingRooms(false)
    }
  }

  const handleFormSubmit = async (roomName: string, selectedAgents: { [agentId: string]: Agent }) => {
    try {
      setIsCreatingRoom(true)
      setMessage(null)
      
      // Create agent set mapping
      const roomAgentSet = Object.fromEntries(
        Object.entries(selectedAgents).map(([id, agent]) => [
          id, 
          agent.agent_card.name
        ])
      )

      const response = await createNewRoom(
        roomName,
        currentUser.id,
        currentUser.name,
        roomAgentSet
      )
      
      if (response.success && response.room) {
        // Update rooms list
        setRooms(prev => [...prev, response.room!])
        
        // Reset form
        formRef.current?.reset()
        
        // Show success message
        setMessage({
          type: 'success',
          content: `Room "${roomName}" created successfully! Room ID: ${response.room.room_id}`
        })
        
        // Auto-hide success message after 5 seconds
        setTimeout(() => setMessage(null), 5000)
        
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

  return (
    <div className="container mx-auto items-center justify-center h-full p-4">
        {/* Room Creation Form */}
        <Card>
          <CardHeader>
            <CardTitle>Set up your room with a name and invite agents to join</CardTitle>
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
