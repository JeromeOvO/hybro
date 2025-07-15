"use client"

import { useState, useEffect } from "react"
import { useParams, useRouter } from "next/navigation"
import { ArrowLeft, Bot, CheckCircle, RefreshCw, ExternalLink, Calendar, Users, Star } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Separator } from "@/components/ui/separator"
import { toast } from "sonner"
import { getAgent } from "@/lib/api"
import type { Agent, AgentCenterResponse } from "@/lib/types"

export default function AgentProfilePage() {
  const params = useParams()
  const router = useRouter()
  const agentId = params.id as string
  
  const [agentData, setAgentData] = useState<AgentCenterResponse | null>(null)
  const [loading, setLoading] = useState(true)

  // Get status color
  const getStatusColor = (status: Agent['agent_status']) => {
    switch (status) {
      case 'active':
        return 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300'
      case 'inactive':
        return 'bg-gray-100 text-gray-800 dark:bg-gray-900 dark:text-gray-300'
      case 'deleted':
        return 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300'
      default:
        return 'bg-gray-100 text-gray-800 dark:bg-gray-900 dark:text-gray-300'
    }
  }

  const getStatusText = (status: Agent['agent_status']) => {
    switch (status) {
      case 'active':
        return 'Active'
      case 'inactive':
        return 'Inactive'
      case 'deleted':
        return 'Deleted'
      default:
        return 'Unknown'
    }
  }

  const loadAgentDetail = async () => {
    try {
      setLoading(true)
      const response = await getAgent(agentId)
      
      if (response.success && response.agent) {
        setAgentData(response)
      } else {
        const errorMessage = response.error || "Failed to load agent details"
        toast.error("Failed to load agent details", {
          description: errorMessage
        })
      }
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "Network error occurred"
      toast.error("Failed to load agent details", {
        description: errorMessage
      })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (agentId) {
      loadAgentDetail()
    }
  }, [agentId])

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[85vh]">
        <div className="flex flex-col items-center justify-center gap-4">
          <RefreshCw className="h-8 w-8 animate-spin text-primary" />
          <span className="text-base font-medium text-muted-foreground">Loading Agent...</span>
        </div>
      </div>
    )
  }

  if (!agentData?.success || !agentData.agent) {
    return (
      <div className="flex items-center justify-center min-h-[85vh]">
        <div className="flex flex-col items-center justify-center gap-4">
          <div className="text-center">
            <h2 className="text-lg font-semibold mb-2">Agent not found</h2>
            <p className="text-muted-foreground mb-4">
              The agent you're looking for doesn't exist or has been removed.
            </p>
          </div>
          <Button onClick={() => router.push('/agent')} variant="outline">
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to Agents
          </Button>
        </div>
      </div>
    )
  }

  const agent = agentData.agent
  const successRate = agent.call_count && agent.call_count > 0 
    ? (((agent.call_success_count || 0) / agent.call_count) * 100).toFixed(1)
    : '0'

  return (
    <div className="p-8 space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Button 
            variant="outline" 
            size="sm"
            onClick={() => router.push('/agent')}
          >
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back
          </Button>
          <div>
            <h1 className="text-3xl font-bold">Agent Profile</h1>
            <p className="text-muted-foreground mt-1">
              Detailed information about this agent
            </p>
          </div>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={loadAgentDetail}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-start gap-4">
            <Avatar className="h-16 w-16">
              <AvatarImage src={agent.agent_card.iconUrl || undefined} alt={agent.agent_card.name} />
              <AvatarFallback>
                <Bot className="h-8 w-8" />
              </AvatarFallback>
            </Avatar>
            <div className="flex-1">
              <div className="flex items-center gap-3 mb-2">
                <CardTitle className="text-2xl">{agent.agent_card.name}</CardTitle>
                <Button variant="outline" className={getStatusColor(agent.agent_status)}>
                  {getStatusText(agent.agent_status)}
                </Button>
              </div>
              <CardDescription className="text-base mb-3">
                {agent.agent_card.description}
              </CardDescription>
              <div className="flex items-center gap-6 text-sm text-muted-foreground">
                <div className="flex items-center gap-1">
                  <Calendar className="h-4 w-4" />
                  <span>v{agent.agent_card.version}</span>
                </div>
                <div className="flex items-center gap-1">
                  <Users className="h-4 w-4" />
                  <span>{agent.call_count || 0} calls</span>
                </div>
                <div className="flex items-center gap-1">
                  <Star className="h-4 w-4" />
                  <span>{agent.like_count || 0} likes</span>
                </div>
                <div className="flex items-center gap-1">
                  <CheckCircle className="h-4 w-4" />
                  <span>{successRate}% success rate</span>
                </div>
              </div>
            </div>
          </div>
        </CardHeader>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="pb-3">
            <CardDescription>Total Calls</CardDescription>
            <CardTitle className="text-2xl">{agent.call_count || 0}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-3">
            <CardDescription>Success Rate</CardDescription>
            <CardTitle className="text-2xl">{successRate}%</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-3">
            <CardDescription>Likes</CardDescription>
            <CardTitle className="text-2xl">{agent.like_count || 0}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-3">
            <CardDescription>Provider</CardDescription>
            <CardTitle className="text-lg">{agent.agent_card.provider?.organization || "Unknown"}</CardTitle>
          </CardHeader>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Agent Details</CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          <div>
            <h3 className="text-lg font-semibold mb-3">Basic Information</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium">URL</label>
                <p className="text-sm text-muted-foreground">{agent.agent_card.url}</p>
              </div>
              <div>
                <label className="text-sm font-medium">Documentation</label>
                <p className="text-sm text-muted-foreground">
                  {agent.agent_card.documentationUrl ? (
                    <a href={agent.agent_card.documentationUrl} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline flex items-center gap-1">
                      View Documentation <ExternalLink className="h-3 w-3" />
                    </a>
                  ) : (
                    "Not available"
                  )}
                </p>
              </div>
            </div>
          </div>

          <Separator />

          <div>
            <h3 className="text-lg font-semibold mb-3">Capabilities</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium">Streaming</label>
                <div className="mt-1">
                  <Button 
                    variant="outline" 
                    size="sm"
                    className={agent.agent_card.capabilities.streaming 
                      ? "text-green-600 border-green-300 bg-green-50 hover:bg-green-100 dark:text-green-400 dark:border-green-700 dark:bg-green-950 dark:hover:bg-green-900" 
                      : "text-muted-foreground border-muted bg-muted/50 hover:bg-muted/70"
                    }
                  >
                    {agent.agent_card.capabilities.streaming ? "Supported" : "Not Supported"}
                  </Button>
                </div>
              </div>
              <div>
                <label className="text-sm font-medium">Extensions</label>
                <div className="mt-1">
                  <Button 
                    variant="outline" 
                    size="sm"
                    className={agent.agent_card.capabilities.extensions 
                      ? "text-green-600 border-green-300 bg-green-50 hover:bg-green-100 dark:text-green-400 dark:border-green-700 dark:bg-green-950 dark:hover:bg-green-900" 
                      : "text-muted-foreground border-muted bg-muted/50 hover:bg-muted/70"
                    }
                  >
                    {agent.agent_card.capabilities.extensions ? "Supported" : "Not Supported"}
                  </Button>
                </div>
              </div>
              <div>
                <label className="text-sm font-medium">Push Notifications</label>
                <div className="mt-1">
                  <Button 
                    variant="outline" 
                    size="sm"
                    className={agent.agent_card.capabilities.pushNotifications 
                      ? "text-green-600 border-green-300 bg-green-50 hover:bg-green-100 dark:text-green-400 dark:border-green-700 dark:bg-green-950 dark:hover:bg-green-900" 
                      : "text-muted-foreground border-muted bg-muted/50 hover:bg-muted/70"
                    }
                  >
                    {agent.agent_card.capabilities.pushNotifications ? "Supported" : "Not Supported"}
                  </Button>
                </div>
              </div>
              <div>
                <label className="text-sm font-medium">State Transition History</label>
                <div className="mt-1">
                  <Button 
                    variant="outline" 
                    size="sm"
                    className={agent.agent_card.capabilities.stateTransitionHistory 
                      ? "text-green-600 border-green-300 bg-green-50 hover:bg-green-100 dark:text-green-400 dark:border-green-700 dark:bg-green-950 dark:hover:bg-green-900" 
                      : "text-muted-foreground border-muted bg-muted/50 hover:bg-muted/70"
                    }
                  >
                    {agent.agent_card.capabilities.stateTransitionHistory ? "Supported" : "Not Supported"}
                  </Button>
                </div>
              </div>
            </div>
          </div>

          <Separator />

          <div>
            <h3 className="text-lg font-semibold mb-3">Input/Output Modes</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium">Input Modes</label>
                <div className="flex flex-wrap gap-2 mt-1">
                  {agent.agent_card.defaultInputModes.map((mode, index) => (
                    <Button 
                      key={index} 
                      variant="outline" 
                      size="sm"
                      className="text-purple-600 border-purple-300 bg-purple-50 hover:bg-purple-100 dark:text-purple-400 dark:border-purple-700 dark:bg-purple-950 dark:hover:bg-purple-900"
                    >
                      {mode}
                    </Button>
                  ))}
                </div>
              </div>
              <div>
                <label className="text-sm font-medium">Output Modes</label>
                <div className="flex flex-wrap gap-2 mt-1">
                  {agent.agent_card.defaultOutputModes.map((mode, index) => (
                    <Button 
                      key={index} 
                      variant="outline" 
                      size="sm"
                      className="text-orange-600 border-orange-300 bg-orange-50 hover:bg-orange-100 dark:text-orange-400 dark:border-orange-700 dark:bg-orange-950 dark:hover:bg-orange-900"
                    >
                      {mode}
                    </Button>
                  ))}
                </div>
              </div>
            </div>
          </div>

          <Separator />

          <div>
            <h3 className="text-lg font-semibold mb-3">Skills</h3>
            <div className="space-y-4">
              {agent.agent_card.skills.map((skill, index) => (
                <Card key={index} className="border-l-4 border-l-blue-500">
                  <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                      <CardTitle className="text-lg">{skill.name}</CardTitle>
                      <Button 
                        variant="outline" 
                        size="sm"
                        className="text-blue-600 border-blue-300 bg-blue-50 hover:bg-blue-100 dark:text-blue-400 dark:border-blue-700 dark:bg-blue-950 dark:hover:bg-blue-900"
                      >
                        {skill.id}
                      </Button>
                    </div>
                    <CardDescription>{skill.description}</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-3">
                      {skill.examples && skill.examples.length > 0 && (
                        <div>
                          <label className="text-sm font-medium">Examples</label>
                          <div className="flex flex-wrap gap-2 mt-1">
                            {skill.examples.map((example, exampleIndex) => (
                              <Button 
                                key={exampleIndex} 
                                variant="secondary" 
                                size="sm"
                                className="text-xs h-6 px-2"
                              >
                                "{example}"
                              </Button>
                            ))}
                          </div>
                        </div>
                      )}
                      <div>
                        <label className="text-sm font-medium">Tags</label>
                        <div className="flex flex-wrap gap-2 mt-1">
                          {skill.tags.map((tag, tagIndex) => (
                            <Button 
                              key={tagIndex} 
                              variant="outline" 
                              size="sm"
                              className="text-xs h-6 px-2"
                            >
                              {tag}
                            </Button>
                          ))}
                        </div>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
