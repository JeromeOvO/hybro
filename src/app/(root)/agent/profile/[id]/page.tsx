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
  Cpu,
  Zap,
  MessageSquare,
  Terminal,
  ArrowRightLeft,
  Settings,
  Save,
  Globe,
  Lock
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

export default function AgentProfilePage() {
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
        // Initialize rate limit state from agent data
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
        // Initialize visibility state (default to true if not set)
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

  const handleSaveRateLimits = async () => {
    if (!agentData?.agent) return

    try {
      setSaving(true)
      
      // Validate that if toggle is enabled, a valid value is provided
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

      // Parse values - null means unlimited
      const userLimit = enableUserLimit && userLimitValue 
        ? parseInt(userLimitValue, 10) 
        : null
      const systemLimit = enableSystemLimit && systemLimitValue 
        ? parseInt(systemLimitValue, 10) 
        : null

      // Validate values must be >= 1
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
        // Reload agent data to reflect changes
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
        router.push('/agent')
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
          <span className="text-base font-medium text-muted-foreground animate-pulse">Loading Agent Profile...</span>
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
            <Button onClick={() => router.push('/agent')} variant="default">
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Registry
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
    <div className="container max-w-7xl mx-auto p-4 md:p-8 space-y-8 animate-in fade-in duration-500">
      {/* Navigation & Actions */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <Button
          variant="ghost"
          onClick={() => router.push('/agent')}
          className="group pl-0 hover:pl-2 transition-all"
        >
          <ArrowLeft className="h-4 w-4 mr-2 group-hover:-translate-x-1 transition-transform" />
          Back to Registry
        </Button>
        <div className="flex gap-2 w-full sm:w-auto">
          {isOwner && (
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
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        {/* Left Column: Identity & Meta */}
        <div className="lg:col-span-4 space-y-6">
          <Card className="overflow-hidden border-primary/10 shadow-lg bg-gradient-to-b from-card to-secondary/20">
            <div className="h-32 bg-linear-to-br from-primary/10 via-primary/5 to-transparent relative">
              <div className="absolute top-4 right-4">
                {getStatusBadge(agent.agent_status)}
              </div>
            </div>
            <CardHeader className="relative pb-2 -mt-16 text-center space-y-4">
              <div className="mx-auto relative group">
                <Avatar className="h-32 w-32 border-4 border-background shadow-xl mx-auto group-hover:scale-105 transition-transform duration-300">
                  <AvatarImage src={agent.agent_card.iconUrl || undefined} alt={agent.agent_card.name} />
                  <AvatarFallback className="bg-primary/5 text-primary">
                    <Bot className="h-12 w-12" />
                  </AvatarFallback>
                </Avatar>
              </div>
              <div className="space-y-1">
                <CardTitle className="text-2xl font-bold">{agent.agent_card.name}</CardTitle>
                <div className="text-sm text-muted-foreground font-medium flex items-center justify-center gap-2">
                  <span>v{agent.agent_card.version}</span>
                  <span className="text-border">•</span>
                  <span>{agent.agent_card.provider?.organization || "Unknown Provider"}</span>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-6 pt-4">
              <p className="text-muted-foreground text-center leading-relaxed">
                {agent.agent_card.description}
              </p>

              <Separator />

              <div className="space-y-3 text-sm">
                <div className="flex justify-between items-center py-1">
                  <span className="text-muted-foreground">Updated</span>
                  <span className="font-medium">
                    {new Date().toLocaleDateString()} {/* Using current date as placeholder */}
                  </span>
                </div>
                {agent.agent_card.documentationUrl && (
                  <Button variant="outline" className="w-full mt-4" asChild>
                    <a href={agent.agent_card.documentationUrl} target="_blank" rel="noopener noreferrer">
                      <ExternalLink className="h-4 w-4 mr-2" />
                      View Documentation
                    </a>
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Right Column: Details & Capabilities */}
        <div className="lg:col-span-8 space-y-6">
          {/* Capabilities Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Card className="h-full border-l-4 border-l-blue-500/50">
              <CardHeader className="pb-3">
                <CardTitle className="text-lg flex items-center gap-2">
                  <Zap className="h-5 w-5 text-blue-500" />
                  Core Capabilities
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex flex-wrap gap-2">
                  {[
                    { label: "Streaming", value: agent.agent_card.capabilities.streaming },
                    { label: "Extensions", value: agent.agent_card.capabilities.extensions },
                    { label: "Push Notifications", value: agent.agent_card.capabilities.pushNotifications },
                    { label: "State History", value: agent.agent_card.capabilities.stateTransitionHistory },
                  ]
                    .filter((cap) => cap.value)
                    .map((cap) => (
                      <Badge key={cap.label} variant="secondary" className="px-3 py-1 font-normal bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 hover:bg-blue-100">
                        {cap.label}
                      </Badge>
                    ))}
                  {[
                    { label: "Streaming", value: agent.agent_card.capabilities.streaming },
                    { label: "Extensions", value: agent.agent_card.capabilities.extensions },
                    { label: "Push Notifications", value: agent.agent_card.capabilities.pushNotifications },
                    { label: "State History", value: agent.agent_card.capabilities.stateTransitionHistory },
                  ].every(c => !c.value) && (
                      <span className="text-sm text-muted-foreground italic">No specific capabilities listed</span>
                    )}
                </div>
              </CardContent>
            </Card>

            <Card className="h-full border-l-4 border-l-purple-500/50">
              <CardHeader className="pb-3">
                <CardTitle className="text-lg flex items-center gap-2">
                  <MessageSquare className="h-5 w-5 text-purple-500" />
                  Interaction Modes
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2 block">Input</span>
                  <div className="flex flex-wrap gap-2">
                    {agent.agent_card.defaultInputModes.map((mode, i) => (
                      <Badge key={i} variant="outline" className="border-purple-200 text-purple-700 dark:border-purple-800 dark:text-purple-300">
                        {mode}
                      </Badge>
                    ))}
                  </div>
                </div>
                <div>
                  <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2 block">Output</span>
                  <div className="flex flex-wrap gap-2">
                    {agent.agent_card.defaultOutputModes.map((mode, i) => (
                      <Badge key={i} variant="outline" className="border-purple-200 text-purple-700 dark:border-purple-800 dark:text-purple-300">
                        {mode}
                      </Badge>
                    ))}
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Skills Section */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Cpu className="h-5 w-5 text-primary" />
                Skills & Functions
              </CardTitle>

            </CardHeader>
            <CardContent>
              {agent.agent_card.skills.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground bg-muted/30 rounded-lg border border-dashed">
                  <p>No specific skills defined for this agent.</p>
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-6">
                  {agent.agent_card.skills.map((skill, index) => (
                    <div
                      key={index}
                      className="group rounded-xl border border-white/20 bg-white/10 dark:bg-white/5 backdrop-blur-md text-card-foreground shadow-sm hover:shadow-lg hover:bg-white/15 dark:hover:bg-white/10 transition-all duration-200 overflow-hidden"
                    >
                      <div className="border-b border-white/10 bg-white/5 dark:bg-white/5 p-4 flex flex-col md:flex-row md:items-center justify-between gap-4">
                        <div className="flex items-center gap-3">
                          <div className="bg-primary/10 backdrop-blur-sm p-2 rounded-lg border border-primary/20">
                            <Terminal className="h-5 w-5 text-primary" />
                          </div>
                          <div>
                            <h4 className="font-semibold text-lg leading-none">{skill.name}</h4>
                            <p className="text-xs text-muted-foreground font-mono mt-1 opacity-70">
                              ID: {skill.id}
                            </p>
                          </div>
                        </div>
                        {skill.tags && skill.tags.length > 0 && (
                          <div className="flex flex-wrap gap-2">
                            {skill.tags.map((tag, i) => (
                              <Badge key={i} variant="outline" className="text-xs font-normal bg-background/50">
                                {tag}
                              </Badge>
                            ))}
                          </div>
                        )}
                      </div>

                      <div className="p-4 space-y-4">
                        <div className="space-y-1.5">
                          <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                            Description
                          </label>
                          <p className="text-sm leading-relaxed text-foreground/90 pl-1">
                            {skill.description}
                          </p>
                        </div>

                        {(skill.inputModes?.length ?? 0) > 0 || (skill.outputModes?.length ?? 0) > 0 ? (
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
                            {(skill.inputModes?.length ?? 0) > 0 && (
                              <div className="space-y-1.5">
                                <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                                  <ArrowRightLeft className="h-3 w-3 rotate-45" /> Input Modes
                                </label>
                                <div className="flex flex-wrap gap-1.5">
                                  {skill.inputModes!.map((mode, i) => (
                                    <code key={i} className="px-1.5 py-0.5 rounded-md bg-muted text-muted-foreground text-xs font-mono border">
                                      {mode}
                                    </code>
                                  ))}
                                </div>
                              </div>
                            )}
                            {(skill.outputModes?.length ?? 0) > 0 && (
                              <div className="space-y-1.5">
                                <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                                  <ArrowRightLeft className="h-3 w-3 -rotate-45" /> Output Modes
                                </label>
                                <div className="flex flex-wrap gap-1.5">
                                  {skill.outputModes!.map((mode, i) => (
                                    <code key={i} className="px-1.5 py-0.5 rounded-md bg-muted text-muted-foreground text-xs font-mono border">
                                      {mode}
                                    </code>
                                  ))}
                                </div>
                              </div>
                            )}
                          </div>
                        ) : null}

                        {skill.examples && skill.examples.length > 0 && (
                          <div className="pt-2">
                            <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2 block flex items-center gap-1.5">
                              <Terminal className="h-3 w-3" /> Usage Examples
                            </label>
                            <div className="space-y-2 bg-muted/30 rounded-lg p-3 border border-border/50">
                              {skill.examples.map((example, i) => (
                                <div key={i} className="font-mono text-xs text-foreground/80 break-words flex gap-2 items-start">
                                  <span className="text-muted-foreground select-none">$</span>
                                  <span>{example}</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

      {/* Agent Settings - Only visible to agent owner */}
      {userId && agentData?.agent?.provider_id === userId && (
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
      )}

      {userId && agentData?.agent?.provider_id === userId && (
        <div className="flex justify-center gap-20">
          <Button 
            className={getStatusColor('active')}
            onClick={handleSaveRateLimits} 
            disabled={saving}
          >
            <Save className="h-4 w-4 mr-2" />
            {saving ? "Saving..." : "Save"}
          </Button>
          <Button 
            className={getStatusColor('deleted')}
            onClick={handleDeleteAgent}
            disabled={deleting}
          >
            <Trash2 className="h-4 w-4 mr-2" />
            {deleting ? "Deleting..." : "Delete"}
          </Button>
        </div>
      )}
        </div>
      </div>
    </div>
  )
}
