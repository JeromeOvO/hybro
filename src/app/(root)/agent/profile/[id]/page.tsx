"use client"

import { useState, useEffect, useCallback } from "react"
import { useParams, useRouter } from "next/navigation"
import { ArrowLeft, Bot, RefreshCw, ExternalLink, Trash2 } from "lucide-react"
import { useAuth } from "@clerk/nextjs"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Separator } from "@/components/ui/separator"
import { banner } from "@/components/ui/banner"
import { getAgent, deleteAgent } from "@/lib/api"
import type { Agent, AgentCenterResponse } from "@/lib/types"

export default function AgentProfilePage() {
  const params = useParams()
  const router = useRouter()
  const { userId, getToken } = useAuth()
  const agentId = params.id as string
  const [agentData, setAgentData] = useState<AgentCenterResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [deleting, setDeleting] = useState(false)

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

  const loadAgentDetail = useCallback(async () => {
    try {
      setLoading(true)
      const response = await getAgent(agentId)
      
      if (response.success && response.agent) {
        setAgentData(response)
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

  const handleDeleteAgent = async () => {
    if (!agentData?.agent) return

    if (!confirm(`Are you sure you want to delete "${agentData.agent.agent_card.name}"? This action cannot be undone.`)) {
      return
    }

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
              The agent you&nbsp;are looking for does&nbsp;not&nbsp;exist or has been removed.
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
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={loadAgentDetail}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
        </div>
      </div>

      <Card className="border-none shadow-none">
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
            </div>
          </div>
        </CardHeader>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Agent Details</CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          <div>
            <h3 className="text-lg font-semibold mb-3">Basic Information</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium">Provider</label>
                <p className="text-sm text-muted-foreground">{agent.agent_card.provider?.organization || "Unknown"}</p>
              </div>
              <div>
                <label className="text-sm font-medium">Version</label>
                <p className="text-sm text-muted-foreground">{agent.agent_card.version}</p>
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
            <div className="flex flex-wrap gap-2">
              {[
                { label: "Streaming", value: agent.agent_card.capabilities.streaming },
                { label: "Extensions", value: agent.agent_card.capabilities.extensions },
                { label: "Push Notifications", value: agent.agent_card.capabilities.pushNotifications },
                { label: "State Transition History", value: agent.agent_card.capabilities.stateTransitionHistory },
              ]
                .filter((cap) => cap.value)
                .map((cap) => (
                  <Button
                    key={cap.label}
                    variant="outline"
                    size="sm"
                    className="text-green-600 border-green-300 bg-green-50 hover:bg-green-100
                               dark:text-green-400 dark:border-green-700 dark:bg-green-950 dark:hover:bg-green-900"
                  >
                    {cap.label}
                  </Button>
                ))}
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
                      className="text-green-600 border-green-300 bg-green-50 hover:bg-green-100
                               dark:text-green-400 dark:border-green-700 dark:bg-green-950 dark:hover:bg-green-900"
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
                      className="text-green-600 border-green-300 bg-green-50 hover:bg-green-100
                               dark:text-green-400 dark:border-green-700 dark:bg-green-950 dark:hover:bg-green-900"
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
                <Card key={index} className="border-l-4 border-l-green-500">
                  <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                      <CardTitle className="text-lg">{skill.name}</CardTitle>
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
                                {example}
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
                              variant="secondary" 
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

      {userId && agentData?.agent?.provider_id === userId && (
        <div className="flex justify-center">
          <Button 
            className={getStatusColor('deleted')} 
            onClick={handleDeleteAgent}
            disabled={deleting}
          >
            <Trash2 className="h-4 w-4 mr-2" />
            {deleting ? "Deleting..." : "Delete Agent"}
          </Button>
        </div>
      )}
    </div>
  )
}
