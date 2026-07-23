"use client"

import React, { useState, useImperativeHandle, forwardRef, useEffect, useCallback, useRef } from "react"
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
import { MessageCircleMore, X } from "lucide-react"
import type { Agent } from "@/lib/types/agent"
import type { StaleAgentRef, AgentAvailability } from "@/lib/types/agent-group"
import type { RoomAgentRefWire } from "@/lib/types/response"

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
  selectedAgents: { [agentId: string]: string }
  debateMode?: boolean
  resolvedAgents?: RoomAgentRefWire[] | null
}

export interface RoomModeOptions {
  debateMode: boolean
}

interface RoomSettingFormProps {
  onSubmit: (roomName: string, membershipAgentIds: string[], options: RoomModeOptions) => void
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
  const [staleAgentRefs, setStaleAgentRefs] = useState<StaleAgentRef[]>([])

  // Guard to ensure form is only initialized once per mount (dialog open).
  // Prevents re-renders (e.g. from setUpdatingRoom) from overwriting user edits
  // with stale initialData before the backend refetch completes.
  const initializedRef = useRef(false)
  
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

  // Initialize form with room data (runs once per mount / dialog open).
  // Runs even when availableAgents is empty so all-stale rooms still seed
  // roomName and stale placeholders. Re-runs when availableAgents arrives
  // to promote stale refs to active agents.
  const catalogReady = !loadingAgents
  useEffect(() => {
    if (!initialData || !catalogReady) return

    const isFirstInit = !initializedRef.current
    if (!isFirstInit && availableAgents.length === 0) return
    initializedRef.current = true

    if (isFirstInit) {
      form.setValue('roomName', initialData.roomName)
      form.setValue('debateMode', initialData.debateMode || false)
    }

    const agentMapping: { [agentId: string]: Agent } = {}
    const staleRefs: StaleAgentRef[] = []

    const resolvedMap = new Map<string, { availability: AgentAvailability; name?: string | null }>()
    if (initialData.resolvedAgents) {
      for (const ref of initialData.resolvedAgents) {
        resolvedMap.set(ref.id, { availability: ref.availability, name: ref.name })
      }
    }

    Object.entries(initialData.selectedAgents).forEach(([agentId, agentName]) => {
      const agent = availableAgents.find(a => a.agent_id === agentId)
      if (agent) {
        agentMapping[agentId] = agent
      } else {
        const resolved = resolvedMap.get(agentId)
        staleRefs.push({
          id: agentId,
          name: resolved?.name || agentName || agentId,
          availability: resolved?.availability ?? "inaccessible",
        })
      }
    })

    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSelectedAgents(agentMapping)
    setStaleAgentRefs(staleRefs)
  }, [initialData, availableAgents, catalogReady, form])


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

  const handleRemoveStaleRef = (agentId: string) => {
    setStaleAgentRefs(prev => prev.filter(r => r.id !== agentId))
  }

  function handleSubmit(values: z.infer<typeof formSchemaOptional>) {
    const roomName = values.roomName ?? ""
    const membershipAgentIds = [
      ...Object.keys(selectedAgents),
      ...staleAgentRefs.map(r => r.id),
    ]
    onSubmit(roomName, membershipAgentIds, {
      debateMode: values.debateMode ?? false,
    })
  }

  // Reset form function
  const resetForm = useCallback(() => {
    form.reset()
    setSelectedAgents({})
    setStaleAgentRefs([])
    initializedRef.current = false
  }, [form])

  // Expose reset function to parent
  useImperativeHandle(ref, () => ({
    reset: resetForm
  }), [resetForm])

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
                  <MessageCircleMore className="h-4 w-4" />
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

          {/* Stale / unavailable members */}
          {staleAgentRefs.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-xs text-muted-foreground">
                Unavailable members (preserved on save)
              </p>
              {staleAgentRefs.map(ref => (
                <div
                  key={ref.id}
                  className="flex items-center justify-between rounded-md border border-dashed border-muted-foreground/30 bg-muted/30 px-3 py-2 opacity-60"
                >
                  <span className="text-sm text-muted-foreground truncate">
                    {ref.name}
                  </span>
                  <div className="flex items-center gap-1.5 ml-2 shrink-0">
                    <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                      {ref.availability === "deleted" ? "Deleted" :
                       ref.availability === "inactive" ? "Inactive" : "Unavailable"}
                    </span>
                    <button
                      type="button"
                      onClick={() => handleRemoveStaleRef(ref.id)}
                      className="rounded-full p-0.5 text-muted-foreground/70 hover:text-destructive hover:bg-destructive/10 transition-colors"
                      aria-label={`Remove ${ref.name}`}
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
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