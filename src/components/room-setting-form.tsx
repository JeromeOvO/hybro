"use client"

import React, { useState, useImperativeHandle, forwardRef } from "react"
import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { AgentSelector } from "@/components/agent-selector"
import type { Agent } from "@/lib/types/agent"

const formSchema = z.object({
  roomName: z.string().min(2, {
    message: "Room name must be at least 2 characters.",
  }).max(50, {
    message: "Room name must be less than 50 characters.",
  }),
})

interface RoomSettingFormProps {
  onSubmit: (roomName: string, selectedAgents: { [agentId: string]: Agent }) => void
  isSubmitting?: boolean
  availableAgents?: Agent[]
  loadingAgents?: boolean
  agentsError?: string | null
  onRetryLoadAgents?: () => void
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
  onRetryLoadAgents
}, ref) => {
  const [selectedAgents, setSelectedAgents] = useState<{ [agentId: string]: Agent }>({})

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      roomName: "",
    },
  })

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
    onSubmit(values.roomName, selectedAgents)
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
          {isSubmitting ? "Creating Room..." : "Create Room"}
        </Button>
      </form>
    </Form>
  )
})

RoomSettingForm.displayName = "RoomSettingForm"