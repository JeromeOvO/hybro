"use client"

import { useState, useEffect, useCallback } from "react"
import { useParams, useRouter } from "next/navigation"
import {
  ArrowLeft,
  Bot,
  SquareArrowOutUpRight,
  Trash2,
  CheckCircle2,
  XCircle,
  AlertCircle,
  Save,
} from "lucide-react"
import { useAuth } from "@/lib/auth"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { getAgentAvatarUri } from "@/lib/agent-avatar"
import { Badge } from "@/components/ui/badge"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger
} from "@/components/ui/alert-dialog"
import { banner } from "@/components/ui/banner"
import { getAgent, deleteAgent, updateAgent } from "@/lib/api"
import type { Agent, AgentCenterResponse } from "@/lib/types"
import { consumerUrl } from "@/lib/urls"
import { AgentSourceBadge } from "@/components/agent-source-badge"
import { AgentSettingsCard, validateAgentSettings, settingsToUpdatePayload } from "@/components/developer/agent-settings-card"
import type { AgentSettingsValues } from "@/components/developer/agent-settings-card"
import { useMyAgents } from "@/hooks/useMyAgents"

export default function DeveloperAgentManagePage() {
  const params = useParams()
  const router = useRouter()
  const { userId, getToken } = useAuth()
  const agentId = params.id as string
  const { invalidate: refreshMyAgents } = useMyAgents()
  const [agentData, setAgentData] = useState<AgentCenterResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [deleting, setDeleting] = useState(false)
  const [saving, setSaving] = useState(false)
  
  // Agent settings state (managed by AgentSettingsCard)
  const [settingsValues, setSettingsValues] = useState<AgentSettingsValues>({
    isPublic: true,
    enableUserLimit: false,
    userLimitValue: "",
    enableSystemLimit: false,
    systemLimitValue: "",
  })

  const getStatusColor = (status: Agent['agent_status']) => {
    switch (status) {
      case 'active':
        return 'bg-green-500/10 text-green-600 hover:bg-green-500/20 border-green-500/20'
      case 'inactive':
        return 'bg-yellow-500/10 text-yellow-600 hover:bg-yellow-500/20 border-yellow-500/20'
      case 'deleted':
        return 'bg-red-500/10 text-red-600 hover:bg-red-500/20 border-red-500/20'
      default:
        return 'bg-gray-500/10 text-gray-600 hover:bg-gray-500/20 border-gray-500/20'
    }
  }

  const loadAgentDetail = useCallback(async () => {
    try {
      setLoading(true)
      const response = await getAgent(agentId)

      if (response.success && response.agent) {
        setAgentData(response)
        const agent = response.agent
        setSettingsValues({
          isPublic: agent.is_public !== false,
          enableUserLimit: agent.rate_limit_per_user_per_hour != null,
          userLimitValue: agent.rate_limit_per_user_per_hour != null
            ? agent.rate_limit_per_user_per_hour.toString()
            : "",
          enableSystemLimit: agent.rate_limit_system_per_hour != null,
          systemLimitValue: agent.rate_limit_system_per_hour != null
            ? agent.rate_limit_system_per_hour.toString()
            : "",
        })
      } else {
        const errorMessage = response.error || "Failed to load agent details"
        banner.error("Failed to load agent details", {
          description: errorMessage
        })
      }
    } catch {
      banner.error("Failed to load agent details")
    } finally {
      setLoading(false)
    }
  }, [agentId])

  const handleSaveSettings = async () => {
    if (!agentData?.agent) return

    try {
      setSaving(true)
      
      const validationError = validateAgentSettings(settingsValues)
      if (validationError) {
        banner.error("Invalid settings", { description: validationError })
        setSaving(false)
        return
      }

      const response = await updateAgent(
        agentId,
        settingsToUpdatePayload(settingsValues),
        getToken
      )

      if (response.success) {
        banner.success("Settings saved successfully")
        refreshMyAgents()
        await loadAgentDetail()
      } else {
        banner.error("Failed to save settings", {
          description: response.error || "An unexpected error occurred"
        })
      }
    } catch (error) {
      banner.error("Failed to save settings", {
        description: error instanceof Error ? error.message : "An unexpected error occurred"
      })
    } finally {
      setSaving(false)
    }
  }

  const handleDeleteAgent = async () => {
    if (!agentData?.agent) return

    try {
      setDeleting(true)
      const response = await deleteAgent(
        { agent_id: agentId },
        getToken
      )

      if (response.success) {
        banner.success("Agent deleted successfully")
        refreshMyAgents()
        router.push('/agents')
      } else {
        const errorMessage = response.error || "Failed to delete agent"
        banner.error("Failed to delete agent", {
          description: errorMessage
        })
      }
    } catch (error) {
      banner.error("Failed to delete agent", {
        description: error instanceof Error ? error.message : "An unexpected error occurred"
      })
    } finally {
      setDeleting(false)
    }
  }

  useEffect(() => {
    if (agentId) {
      loadAgentDetail()
    }
  }, [agentId, loadAgentDetail])

  if (loading) {
    return (
      <div className="page-loading">
        <div className="flex flex-col items-center justify-center gap-4">
          <div className="relative">
            <div className="h-12 w-12 rounded-full border-4 border-primary/20 animate-spin border-t-primary" />
            <Bot className="h-6 w-6 absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-primary/60" />
          </div>
          <span className="text-base font-medium text-muted-foreground animate-pulse">Loading Agent...</span>
        </div>
      </div>
    )
  }

  if (!agentData?.success || !agentData.agent) {
    return (
      <div className="page-loading">
        <Card className="w-full max-w-md border-dashed">
          <CardHeader className="text-center">
            <div className="mx-auto bg-muted rounded-full p-3 w-fit mb-4">
              <Bot className="h-8 w-8 text-muted-foreground" />
            </div>
            <CardTitle className="text-xl">Agent Not Found</CardTitle>
            <CardDescription>
              The agent you are looking for does not exist or has been removed.
            </CardDescription>
          </CardHeader>
          <CardFooter className="flex justify-center">
            <Button onClick={() => router.push('/agents')} variant="default">
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to My Agents
            </Button>
          </CardFooter>
        </Card>
      </div>
    )
  }

  const agent = agentData.agent
  const isOwner = userId && agent.provider_id === userId

  const getStatusBadge = (status: Agent['agent_status']) => {
    switch (status) {
      case 'active':
        return (
          <Badge variant="success" className="gap-1.5 pl-1.5 pr-2.5 py-0.5">
            <CheckCircle2 className="w-3.5 h-3.5 fill-current opacity-80" />
            Active
          </Badge>
        )
      case 'inactive':
        return (
          <Badge variant="inactive" className="gap-1.5 pl-1.5 pr-2.5 py-0.5">
            <AlertCircle className="w-3.5 h-3.5 fill-current opacity-80" />
            Inactive
          </Badge>
        )
      case 'deleted':
        return (
          <Badge variant="destructive" className="gap-1.5 pl-1.5 pr-2.5 py-0.5">
            <XCircle className="w-3.5 h-3.5 fill-current opacity-80" />
            Deleted
          </Badge>
        )
      default:
        return <Badge variant="secondary">{status}</Badge>
    }
  }

  return (
    <div className="page-container">
      <div className="page-content space-y-8">
        {/* Navigation */}
        <div className="flex items-center justify-between">
          <Button
            variant="ghost"
            onClick={() => router.push('/agents')}
            className="group pl-0 hover:pl-2 transition-all"
          >
            <ArrowLeft className="h-4 w-4 mr-2 group-hover:-translate-x-1 transition-transform" />
            Back to My Agents
          </Button>
          <Button variant="brandTint" size="sm" asChild>
            <a href={consumerUrl(`/agents/${agentId}`)} target="_blank" rel="noopener noreferrer">
              View as User
              <SquareArrowOutUpRight className="h-4 w-4 ml-2" />
            </a>
          </Button>
        </div>

        {/* Agent Summary */}
        <Card>
          <CardHeader>
            <div className="flex items-start gap-4">
              <Avatar className="h-16 w-16 border-2 border-background shadow-lg">
                <AvatarImage src={agent.agent_card.iconUrl || undefined} alt={agent.agent_card.name} />
                <AvatarFallback className="bg-primary/5 text-primary p-0 overflow-hidden">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={getAgentAvatarUri(agent.agent_id)} alt={agent.agent_card.name} className="h-full w-full" />
                </AvatarFallback>
              </Avatar>
              <div className="flex-1 space-y-1">
                <div className="flex items-center gap-3">
                  <CardTitle className="text-2xl">{agent.agent_card.name}</CardTitle>
                  {getStatusBadge(agent.agent_status)}
                  <AgentSourceBadge
                    source={agent.source}
                    isHubOnline={agent.is_hub_online}
                    className="h-5 w-5"
                  />
                </div>
                <div className="text-sm text-muted-foreground">
                  v{agent.agent_card.version} · Built by <span className="text-[hsl(var(--color-hybro-hy))]">{agent.agent_card.provider?.organization || agent.provider_name || "Unknown Provider"}</span>
                </div>
                <CardDescription className="mt-2">
                  {agent.agent_card.description}
                </CardDescription>
              </div>
            </div>
          </CardHeader>
        </Card>

        {/* Settings - Only visible to agent owner */}
        {isOwner && (
          <>
            <AgentSettingsCard
              values={settingsValues}
              onChange={setSettingsValues}
            />

            {/* Save & Delete Actions */}
            <div className="flex items-center justify-between">
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button
                    variant="destructive"
                    size="sm"
                    disabled={deleting}
                    className="bg-red-600 text-white hover:bg-red-700"
                  >
                    <Trash2 className="h-4 w-4 mr-2" />
                    {deleting ? "Deleting..." : "Delete Agent"}
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Delete this agent?</AlertDialogTitle>
                    <AlertDialogDescription>
                      This will permanently delete &quot;{agent.agent_card.name}&quot;. This
                      action cannot be undone.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <AlertDialogAction
                      onClick={handleDeleteAgent}
                      className="bg-red-600 text-white hover:bg-red-700"
                    >
                      Delete Agent
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>

              <Button 
                className={getStatusColor('active')}
                onClick={handleSaveSettings} 
                disabled={saving}
              >
                <Save className="h-4 w-4 mr-2" />
                {saving ? "Saving..." : "Save Settings"}
              </Button>
            </div>
          </>
        )}

        {!isOwner && (
          <Card className="border-dashed">
            <CardContent className="text-center py-8">
              {!userId ? (
                <>
                  <p className="text-muted-foreground">
                    Please sign in to manage this agent&apos;s settings.
                  </p>
                  <Button
                    variant="default"
                    className="mt-4"
                    onClick={() => router.push(`/sign-in?redirect_url=${encodeURIComponent(`/d/agents/${agentId}`)}`)}
                  >
                    Sign In
                  </Button>
                </>
              ) : (
                <p className="text-muted-foreground">
                  You do not own this agent. Only the agent owner can manage settings.
                </p>
              )}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}
