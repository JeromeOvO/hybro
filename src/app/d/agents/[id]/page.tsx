"use client"

import { useState, useEffect, useCallback } from "react"
import { useParams, useRouter } from "next/navigation"
import {
  ArrowLeft,
  Bot,
  ExternalLink,
  Trash2,
  CheckCircle2,
  XCircle,
  AlertCircle,
  Settings,
  Save,
  Globe,
  Lock,
} from "lucide-react"
import { useAuth } from "@clerk/nextjs"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Separator } from "@/components/ui/separator"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { banner } from "@/components/ui/banner"
import { getAgent, deleteAgent, updateAgent } from "@/lib/api"
import type { Agent, AgentCenterResponse } from "@/lib/types"
import { consumerUrl } from "@/lib/urls"

export default function DeveloperAgentManagePage() {
  const params = useParams()
  const router = useRouter()
  const { userId, getToken } = useAuth()
  const agentId = params.id as string
  const [agentData, setAgentData] = useState<AgentCenterResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [deleting, setDeleting] = useState(false)
  const [saving, setSaving] = useState(false)
  
  // Rate limit settings state
  const [enableUserLimit, setEnableUserLimit] = useState(false)
  const [userLimitValue, setUserLimitValue] = useState<string>("")
  const [enableSystemLimit, setEnableSystemLimit] = useState(false)
  const [systemLimitValue, setSystemLimitValue] = useState<string>("")
  
  // Visibility state
  const [isPublic, setIsPublic] = useState(true)

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
        if (agent.rate_limit_per_user_per_hour != null) {
          setEnableUserLimit(true)
          setUserLimitValue(agent.rate_limit_per_user_per_hour.toString())
        } else {
          setEnableUserLimit(false)
          setUserLimitValue("")
        }
        if (agent.rate_limit_system_per_hour != null) {
          setEnableSystemLimit(true)
          setSystemLimitValue(agent.rate_limit_system_per_hour.toString())
        } else {
          setEnableSystemLimit(false)
          setSystemLimitValue("")
        }
        setIsPublic(agent.is_public !== false)
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
      
      if (enableUserLimit && !userLimitValue) {
        banner.error("User rate limit required", {
          description: "Please enter a value or disable the limit"
        })
        setSaving(false)
        return
      }
      if (enableSystemLimit && !systemLimitValue) {
        banner.error("System rate limit required", {
          description: "Please enter a value or disable the limit"
        })
        setSaving(false)
        return
      }

      const userLimit = enableUserLimit && userLimitValue 
        ? parseInt(userLimitValue, 10) 
        : null
      const systemLimit = enableSystemLimit && systemLimitValue 
        ? parseInt(systemLimitValue, 10) 
        : null

      if (enableUserLimit && (isNaN(userLimit as number) || (userLimit as number) < 1)) {
        banner.error("Invalid user rate limit", {
          description: "Please enter a number greater than or equal to 1"
        })
        setSaving(false)
        return
      }
      if (enableSystemLimit && (isNaN(systemLimit as number) || (systemLimit as number) < 1)) {
        banner.error("Invalid system rate limit", {
          description: "Please enter a number greater than or equal to 1"
        })
        setSaving(false)
        return
      }

      const response = await updateAgent(
        agentId,
        {
          rate_limit_per_user_per_hour: userLimit,
          rate_limit_system_per_hour: systemLimit,
          is_public: isPublic,
        },
        getToken
      )

      if (response.success) {
        banner.success("Settings saved successfully")
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
      <div className="flex items-center justify-center min-h-[85vh]">
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
      <div className="flex items-center justify-center min-h-[85vh]">
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
          <Badge variant="outline" className="bg-green-50 text-green-700 border-green-200 dark:bg-green-900/30 dark:text-green-300 dark:border-green-800 gap-1.5 pl-1.5 pr-2.5 py-0.5">
            <CheckCircle2 className="w-3.5 h-3.5 fill-current opacity-80" />
            Active
          </Badge>
        )
      case 'inactive':
        return (
          <Badge variant="outline" className="bg-gray-50 text-gray-700 border-gray-200 dark:bg-gray-800/50 dark:text-gray-400 dark:border-gray-700 gap-1.5 pl-1.5 pr-2.5 py-0.5">
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
    <div className="px-4 sm:px-6 py-8">
      <div className="w-full max-w-4xl mx-auto space-y-8">
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
          <Button variant="outline" size="sm" asChild>
            <a href={consumerUrl(`/agents/${agentId}`)} target="_blank" rel="noopener noreferrer">
              View as User
              <ExternalLink className="h-4 w-4 ml-2" />
            </a>
          </Button>
        </div>

        {/* Agent Summary */}
        <Card>
          <CardHeader>
            <div className="flex items-start gap-4">
              <Avatar className="h-16 w-16 border-2 border-background shadow-lg">
                <AvatarImage src={agent.agent_card.iconUrl || undefined} alt={agent.agent_card.name} />
                <AvatarFallback className="bg-primary/5 text-primary">
                  <Bot className="h-8 w-8" />
                </AvatarFallback>
              </Avatar>
              <div className="flex-1 space-y-1">
                <div className="flex items-center gap-3">
                  <CardTitle className="text-2xl">{agent.agent_card.name}</CardTitle>
                  {getStatusBadge(agent.agent_status)}
                </div>
                <div className="text-sm text-muted-foreground">
                  v{agent.agent_card.version} · {agent.agent_card.provider?.organization || "Unknown Provider"}
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
            <Card>
              <CardHeader>
                <div className="flex items-center gap-2">
                  <Settings className="h-5 w-5" />
                  <CardTitle>Agent Settings</CardTitle>
                </div>
                <CardDescription>
                  Configure visibility and rate limits for your agent.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                {/* Visibility Toggle */}
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5">
                      <Label htmlFor="visibility-toggle" className="text-base font-medium flex items-center gap-2">
                        {isPublic ? <Globe className="h-4 w-4 text-green-500" /> : <Lock className="h-4 w-4 text-yellow-500" />}
                        Visibility
                      </Label>
                      <p className="text-sm text-muted-foreground">
                        {isPublic 
                          ? "Public - Everyone can discover and use this agent"
                          : "Private - Only you can see and use this agent"
                        }
                      </p>
                    </div>
                    <Switch
                      id="visibility-toggle"
                      checked={isPublic}
                      onCheckedChange={setIsPublic}
                    />
                  </div>
                </div>

                <Separator />

                {/* Per-User Rate Limit */}
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5">
                      <Label htmlFor="user-limit-toggle" className="text-base font-medium">
                        Per-User Limit
                      </Label>
                      <p className="text-sm text-muted-foreground">
                        Maximum requests each user can make per hour
                      </p>
                    </div>
                    <Switch
                      id="user-limit-toggle"
                      checked={enableUserLimit}
                      onCheckedChange={(checked) => {
                        setEnableUserLimit(checked)
                        if (!checked) setUserLimitValue("")
                      }}
                    />
                  </div>
                  {enableUserLimit && (
                    <div className="flex items-center gap-3 pl-4">
                      <Input
                        type="number"
                        min="1"
                        value={userLimitValue}
                        onChange={(e) => setUserLimitValue(e.target.value)}
                        placeholder="e.g., 10"
                        className="w-32"
                      />
                      <span className="text-sm text-muted-foreground">requests per hour</span>
                    </div>
                  )}
                </div>

                <Separator />

                {/* System-Wide Rate Limit */}
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5">
                      <Label htmlFor="system-limit-toggle" className="text-base font-medium">
                        System-Wide Limit
                      </Label>
                      <p className="text-sm text-muted-foreground">
                        Maximum total requests from all users per hour
                      </p>
                    </div>
                    <Switch
                      id="system-limit-toggle"
                      checked={enableSystemLimit}
                      onCheckedChange={(checked) => {
                        setEnableSystemLimit(checked)
                        if (!checked) setSystemLimitValue("")
                      }}
                    />
                  </div>
                  {enableSystemLimit && (
                    <div className="flex items-center gap-3 pl-4">
                      <Input
                        type="number"
                        min="1"
                        value={systemLimitValue}
                        onChange={(e) => setSystemLimitValue(e.target.value)}
                        placeholder="e.g., 100"
                        className="w-32"
                      />
                      <span className="text-sm text-muted-foreground">requests per hour</span>
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>

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
              <p className="text-muted-foreground">
                You do not own this agent. Only the agent owner can manage settings.
              </p>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}
