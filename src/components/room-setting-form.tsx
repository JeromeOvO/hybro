"use client"

import React, { useState, useImperativeHandle, forwardRef, useEffect } from "react"
import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  FormDescription,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { Separator } from "@/components/ui/separator"
import { AgentSelector } from "@/components/agent-selector"
import { MessageSquareMore, Users } from "lucide-react"
import type { Agent } from "@/lib/types/agent"

const formSchema = z.object({
  roomName: z.string().min(2, {
    message: "Room name must be at least 2 characters.",
  }).max(50, {
    message: "Room name must be less than 50 characters.",
  }),
  debateMode: z.boolean(),
})

interface RoomFormData {
  roomName: string
  selectedAgents: { [agentName: string]: string } // agent_name -> agent_id mapping
  debateMode?: boolean // Add debateMode to interface
}

interface RoomSettingFormProps {
  onSubmit: (roomName: string, selectedAgents: { [agentId: string]: Agent }, debateMode: boolean) => void
  isSubmitting?: boolean
  availableAgents?: Agent[]
  loadingAgents?: boolean
  agentsError?: string | null
  isEditing?: boolean
  onRetryLoadAgents?: () => void
  initialData?: RoomFormData | null
}

export interface RoomSettingFormHandle {
  reset: () => void
}

export const RoomSettingForm = forwardRef<RoomSettingFormHandle, RoomSettingFormProps>(({
  onSubmit,
  isSubmitting = false,
  availableAgents = [],
  loadingAgents = false,
  agentsError = null,
  isEditing = false,
  onRetryLoadAgents,
  initialData = null
}, ref) => {
  const [selectedAgents, setSelectedAgents] = useState<{ [agentId: string]: Agent }>({})

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      roomName: "",
      debateMode: false,
    },
  })

  // Initialize form with room data
  useEffect(() => {
    if (initialData && availableAgents.length > 0) {
      // Set room name
      form.setValue('roomName', initialData.roomName)
      
      // Set debate mode from initialData
      form.setValue('debateMode', initialData.debateMode || false)
      
      // Convert agent mapping back to selected agents
      const agentMapping: { [agentId: string]: Agent } = {}
      
      // initialData.selectedAgents is { [agentName]: agentId }
      // We need to find the corresponding Agent objects
      Object.entries(initialData.selectedAgents).forEach(([, agentId]) => {
        const agent = availableAgents.find(a => a.agent_id === agentId)
        if (agent) {
          agentMapping[agentId] = agent
        }
      })
      
      setSelectedAgents(agentMapping)
      console.log('Form initialized with data:', {
        roomName: initialData.roomName,
        debateMode: initialData.debateMode || false,
        selectedAgents: agentMapping,
        originalAgentSet: initialData.selectedAgents
      })
    }
  }, [initialData, availableAgents, form])

  const handleAddAgent = (agent: Agent) => {
    setSelectedAgents(prev => ({
      ...prev,
      [agent.agent_id]: agent
    }))
  }

  const handleRemoveAgent = (agentId: string) => {
    setSelectedAgents(prev => {
      const newAgents = { ...prev }
      delete newAgents[agentId]
      return newAgents
    })
  }

  function handleSubmit(values: z.infer<typeof formSchema>) {
    onSubmit(values.roomName, selectedAgents, values.debateMode)
  }

  // Reset form function
  const resetForm = () => {
    form.reset()
    setSelectedAgents({})
  }

  // Expose reset function to parent
  useImperativeHandle(ref, () => ({
    reset: resetForm
  }), [form])

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-6">
        {/* Room Name Field */}
        <FormField
          control={form.control}
          name="roomName"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Room Name</FormLabel>
              <FormControl>
                <Input 
                  placeholder="Enter your room name" 
                  {...field} 
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <Separator />

        {/* Debate Mode Switch */}
        <FormField
          control={form.control}
          name="debateMode"
          render={({ field }) => (
            <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4 bg-card">
              <div className="space-y-0.5 flex-1">
                <FormLabel className="text-base flex items-center gap-2">
                  <MessageSquareMore className="h-4 w-4" />
                  Debate Mode
                </FormLabel>
                <FormDescription className="text-sm">
                  Enable debate mode for enhanced agent discussions and collaborative problem-solving
                </FormDescription>
              </div>
              <FormControl>
                <Switch
                  checked={field.value}
                  onCheckedChange={field.onChange}
                />
              </FormControl>
            </FormItem>
          )}
        />

        <Separator />

        {/* Agent Selection */}
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <Users className="h-4 w-4" />
            <FormLabel className="text-base">Select Agents</FormLabel>
          </div>
          <AgentSelector
            selectedAgents={selectedAgents}
            onAgentAdd={handleAddAgent}
            onAgentRemove={handleRemoveAgent}
            availableAgents={availableAgents}
            loading={loadingAgents}
            error={agentsError}
            onRetry={onRetryLoadAgents}
          />
        </div>

        <Separator />

        {/* Submit Button */}
        {!isEditing ? (
          <Button 
            type="submit" 
            variant="outline"
            className="w-full"
            disabled={isSubmitting}
          >
            {isSubmitting ? "Creating Room..." : "Create Room"}
          </Button>
        ) : (
          <Button 
            type="submit" 
            variant="outline"
            className="w-full"
            disabled={isSubmitting}
          >
            {isSubmitting ? "Updating Room..." : "Update Room"}
          </Button>
        )}
      </form>
    </Form>
  )
})

RoomSettingForm.displayName = "RoomSettingForm"