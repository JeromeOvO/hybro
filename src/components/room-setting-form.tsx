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
import { MessageSquareMore } from "lucide-react"
import type { Agent } from "@/lib/types/agent"

// Schema with required room name (for editing existing rooms)
const formSchemaRequired = z.object({
  roomName: z.string().min(2, {
    message: "Room name must be at least 2 characters.",
  }).max(50, {
    message: "Room name must be less than 50 characters.",
  }),
  debateMode: z.boolean(),
})

// Schema with optional room name (for pre-configuration)
const formSchemaOptional = z.object({
  roomName: z.string().max(50, {
    message: "Room name must be less than 50 characters.",
  }).optional().or(z.literal('')),
  debateMode: z.boolean(),
})

interface RoomFormData {
  roomName: string
  selectedAgents: { [agentId: string]: string } // agent_id -> agent_name mapping
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
  requireRoomName?: boolean
  submitButtonText?: string
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
  initialData = null,
  requireRoomName = true,
  submitButtonText,
}, ref) => {
  const [selectedAgents, setSelectedAgents] = useState<{ [agentId: string]: Agent }>({})
  
  // Use appropriate schema based on requireRoomName prop
  const formSchema = requireRoomName ? formSchemaRequired : formSchemaOptional

  // Use optional schema as the form type to cover both required and optional cases
  const form = useForm<z.infer<typeof formSchemaOptional>>({
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
      
      // initialData.selectedAgents is { [agentId]: agentName }
      // We need to find the corresponding Agent objects by agentId
      Object.entries(initialData.selectedAgents).forEach(([agentId]) => {
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

  function handleSubmit(values: z.infer<typeof formSchemaOptional>) {
    // roomName can be optional when requireRoomName is false; fall back to empty string
    const roomName = values.roomName ?? ""
    const debateMode = values.debateMode ?? false
    onSubmit(roomName, selectedAgents, debateMode)
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
              <FormLabel>
                Room Name
                {!requireRoomName && <span className="text-muted-foreground font-normal ml-1">(optional)</span>}
              </FormLabel>
              <FormControl>
                <Input 
                  placeholder={requireRoomName ? "Enter your room name" : "Auto-generated from first message if empty"} 
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
        <Button 
          type="submit" 
          variant="outline"
          className="w-full"
          disabled={isSubmitting}
        >
          {isSubmitting 
            ? (submitButtonText ? `${submitButtonText}...` : (isEditing ? "Updating Room..." : "Creating Room..."))
            : (submitButtonText || (isEditing ? "Update Room" : "Create Room"))
          }
        </Button>
      </form>
    </Form>
  )
})

RoomSettingForm.displayName = "RoomSettingForm"