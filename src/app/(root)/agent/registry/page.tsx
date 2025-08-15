"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { Bot, CheckCircle, Loader2, ExternalLink, Shield, ShieldCheck, ShieldX } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { toast } from "sonner"
import { getAgentCardFromUrl, registerAgent, inspectA2AConnection } from "@/lib/api"
import type { 
  InspectionCenterResponse, 
  AgentCenterRequest,
  InsepectionCenterConnectionValidationResponse
} from "@/lib/types"
import { useUser } from "@clerk/nextjs"

export default function RegisterAgentPage() {
  const router = useRouter()
  const { user } = useUser()
  const [url, setUrl] = useState("")
  const [loading, setLoading] = useState(false)
  const [inspecting, setInspecting] = useState(false)
  const [registering, setRegistering] = useState(false)
  const [agentData, setAgentData] = useState<InspectionCenterResponse | null>(null)
  const [inspectionData, setInspectionData] = useState<InsepectionCenterConnectionValidationResponse | null>(null)
  const [urlError, setUrlError] = useState("")

  const validateUrl = (inputUrl: string): boolean => {
    try {
      const urlObj = new URL(inputUrl)
      return urlObj.protocol === 'http:' || urlObj.protocol === 'https:'
    } catch {
      return false
    }
  }

  // Handle URL input change
  const handleUrlChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newUrl = e.target.value
    setUrl(newUrl)
    
    if (newUrl && !validateUrl(newUrl)) {
      setUrlError("Please enter a valid URL (e.g., https://example.com:8080)")
    } else {
      setUrlError("")
    }
  }

  // Load Agent information
  const loadAgent = async () => {
    if (!url || !validateUrl(url)) {
      setUrlError("Please enter a valid URL")
      return
    }

    setLoading(true)
    setAgentData(null)
    setInspectionData(null) // Reset inspection data when loading new agent

    try {
      const response = await getAgentCardFromUrl({ agent_url: url })
      
      if (response.agent_card) {
        setAgentData(response)
      } else {
        toast.error("Failed to load agent", {
          description: "No agent card found at the provided URL"
        })
      }
    } catch (error) {
      toast.error("Failed to load agent", {
        description: error instanceof Error ? error.message : "Network error occurred"
      })
    } finally {
      setLoading(false)
    }
  }

  // Inspect A2A Connection
  const inspectConnection = async () => {
    if (!url || !validateUrl(url)) {
      toast.error("Please enter a valid URL")
      return
    }

    setInspecting(true)
    setInspectionData(null)

    try {
      const response = await inspectA2AConnection({ agent_url: url })
      setInspectionData(response)
      
      if (response.status_code === 200) {
        toast.success("Connection inspection completed successfully!")
      } else {
        // Remove the warning toast - just show success that inspection completed
        toast.success("Connection inspection completed!")
      }
    } catch (error) {
      toast.error("Failed to inspect connection", {
        description: error instanceof Error ? error.message : "Network error occurred"
      })
    } finally {
      setInspecting(false)
    }
  }

  // Register Agent
  const registerAgentHandler = async () => {
    if (!agentData?.agent_card) {
      toast.error("Please load agent information first")
      return
    }

    if (!inspectionData || inspectionData.status_code !== 200) {
      toast.error("Please complete connection inspection first")
      return
    }

    if (!user?.id) {
      toast.error("Please sign in to continue")
      router.push('/sign-in?redirect_url=/agent/registry')
      return
  }

    setRegistering(true)

    try {
      const registerRequest: AgentCenterRequest = {
        agent_url: url,
        agent_card: agentData.agent_card
      }

      const response = await registerAgent(registerRequest)
      
      if (response.success) {
        toast.success("Agent registered successfully!", {
          description: "Redirecting to agents page..."
        })
        
        // Delayed redirect
        setTimeout(() => {
          router.push("/agent")
        }, 1500)
      } else {
        toast.error("Registration failed", {
          description: response.error || "The agent URL is already registered or invalid."
        })
      }
    } catch (error) {
      toast.error("Registration failed", {
        description: error instanceof Error ? error.message : "An unexpected error occurred. Please try again."
      })
    } finally {
      setRegistering(false)
    }
  }

  // Show loading state when loading
  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[85vh]">
        <div className="flex flex-col items-center justify-center gap-4">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
          <span className="text-base font-medium text-muted-foreground">Loading Agent...</span>
        </div>
      </div>
    )
  }

  return (
    <div className="px-4 sm:px-6 py-8">
      <div className="w-full max-w-4xl mx-auto space-y-6">
      {/* Page title */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Register Agent</h1>
        </div>
      </div>

      {/* URL input area */}
      <Card>
        <CardHeader>
          <CardTitle>Agent URL</CardTitle>
          <CardDescription>
            Enter the URL of the agent you want to register
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="agent-url">Agent URL</Label>
            <Input
              id="agent-url"
              type="url"
              placeholder="https://<your-agent-url>:<port-number>"
              value={url}
              onChange={handleUrlChange}
              className={urlError ? "border-destructive" : ""}
            />
            {urlError && (
              <p className="text-sm text-destructive">{urlError}</p>
            )}
          </div>
          
          <Button 
            onClick={loadAgent}
            disabled={loading || !url || !!urlError}
            className="w-full sm:w-auto"
          >
            {loading ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Loading Agent...
              </>
            ) : (
              <>
                <ExternalLink className="h-4 w-4 mr-2" />
                Load Agent
              </>
            )}
          </Button>
        </CardContent>
      </Card>

      {/* Agent Card information display */}
      {agentData?.agent_card && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Bot className="h-5 w-5" />
                Agent Card
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <Label className="text-sm font-medium">Name</Label>
                  <p className="text-sm text-muted-foreground">{agentData.agent_card.name}</p>
                </div>
                <div>
                  <Label className="text-sm font-medium">Version</Label>
                  <p className="text-sm text-muted-foreground">{agentData.agent_card.version}</p>
                </div>
                <div>
                  <Label className="text-sm font-medium">Provider</Label>
                  <p className="text-sm text-muted-foreground">
                    {agentData.agent_card.provider?.organization || "Unknown"}
                  </p>
                </div>
                <div>
                  <Label className="text-sm font-medium">Streaming</Label>
                  <Button 
                    variant="outline" 
                    size="sm"
                    className={agentData.agent_card.capabilities.streaming 
                      ? "text-green-600 border-green-300 bg-green-50 hover:bg-green-100 dark:text-green-400 dark:border-green-700 dark:bg-green-950 dark:hover:bg-green-900" 
                      : "text-muted-foreground border-muted bg-muted/50 hover:bg-muted/70"
                    }
                  >
                    {agentData.agent_card.capabilities.streaming ? "Supported" : "Not Supported"}
                  </Button>
                </div>
                <div>
                  <Label className="text-sm font-medium">Extensions</Label>
                  <Button 
                    variant="outline" 
                    size="sm"
                    className={agentData.agent_card.capabilities.extensions 
                      ? "text-green-600 border-green-300 bg-green-50 hover:bg-green-100 dark:text-green-400 dark:border-green-700 dark:bg-green-950 dark:hover:bg-green-900" 
                      : "text-muted-foreground border-muted bg-muted/50 hover:bg-muted/70"
                    }
                  >
                    {agentData.agent_card.capabilities.extensions ? "Supported" : "Not Supported"}
                  </Button>
                </div>
                <div>
                  <Label className="text-sm font-medium">Push Notifications</Label>
                  <Button 
                    variant="outline" 
                    size="sm"
                    className={agentData.agent_card.capabilities.pushNotifications 
                      ? "text-green-600 border-green-300 bg-green-50 hover:bg-green-100 dark:text-green-400 dark:border-green-700 dark:bg-green-950 dark:hover:bg-green-900" 
                      : "text-muted-foreground border-muted bg-muted/50 hover:bg-muted/70"
                    }
                  >
                    {agentData.agent_card.capabilities.pushNotifications ? "Supported" : "Not Supported"}
                  </Button>
                </div>
                <div>
                  <Label className="text-sm font-medium">State Transition History</Label>
                  <Button 
                    variant="outline" 
                    size="sm"
                    className={agentData.agent_card.capabilities.stateTransitionHistory 
                      ? "text-green-600 border-green-300 bg-green-50 hover:bg-green-100 dark:text-green-400 dark:border-green-700 dark:bg-green-950 dark:hover:bg-green-900" 
                      : "text-muted-foreground border-muted bg-muted/50 hover:bg-muted/70"
                    }
                  >
                    {agentData.agent_card.capabilities.stateTransitionHistory ? "Supported" : "Not Supported"}
                  </Button>
                </div>
              </div>
              
              <div>
                <Label className="text-sm font-medium">Description</Label>
                <p className="text-sm text-muted-foreground">{agentData.agent_card.description}</p>
              </div>
              
              <div>
                <Label className="text-sm font-medium">URL</Label>
                <p className="text-sm text-muted-foreground">{agentData.agent_card.url}</p>
              </div>
              
              <div>
                <Label className="text-sm font-medium">Skills</Label>
                <div className="flex flex-wrap gap-2 mt-1">
                  {agentData.agent_card.skills.map((skill, index) => (
                    <Button 
                      key={index} 
                      variant="outline" 
                      size="sm"
                      className="text-green-600 border-green-300 bg-green-50 hover:bg-green-100 dark:text-green-400 dark:border-green-700 dark:bg-green-950 dark:hover:bg-green-900"
                    >
                      {skill.name}
                    </Button>
                  ))}
                </div>
              </div>

              <div>
                <Label className="text-sm font-medium">Input Modes</Label>
                <div className="flex flex-wrap gap-2 mt-1">
                  {agentData.agent_card.defaultInputModes.map((mode, index) => (
                    <Button 
                      key={index} 
                      variant="outline" 
                      size="sm"
                      className="text-green-600 border-green-300 bg-green-50 hover:bg-green-100 dark:text-green-400 dark:border-green-700 dark:bg-green-950 dark:hover:bg-green-900"
                    >
                      {mode}
                    </Button>
                  ))}
                </div>
              </div>

              <div>
                <Label className="text-sm font-medium">Output Modes</Label>
                <div className="flex flex-wrap gap-2 mt-1">
                  {agentData.agent_card.defaultOutputModes.map((mode, index) => (
                    <Button 
                      key={index} 
                      variant="outline" 
                      size="sm"
                      className="text-green-600 border-green-300 bg-green-50 hover:bg-green-100 dark:text-green-400 dark:border-green-700 dark:bg-green-950 dark:hover:bg-green-900"
                    >
                      {mode}
                    </Button>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Connection Inspection Button */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Shield className="h-5 w-5" />
                Connection Inspection
              </CardTitle>
              <CardDescription>
                Validate the A2A connection before registering the agent
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Button 
                onClick={inspectConnection}
                disabled={inspecting}
                className="w-full sm:w-auto"
                variant="outline"
              >
                {inspecting ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Inspecting Connection...
                  </>
                ) : (
                  <>
                    <Shield className="h-4 w-4 mr-2" />
                    Inspect A2A Connection
                  </>
                )}
              </Button>
            </CardContent>
          </Card>
        </>
      )}

      {/* Connection Inspection Results */}
      {inspectionData && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              {inspectionData.status_code === 200 ? (
                <ShieldCheck className="h-5 w-5 text-green-500" />
              ) : (
                <ShieldX className="h-5 w-5 text-red-500" />
              )}
              Connection Inspection Results
            </CardTitle>
            <CardDescription>
              {inspectionData.status_code === 200 
                ? "Connection validation passed" 
                : "Connection validation failed"
              }
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <Label className="text-sm font-medium">Status:</Label>
                <Button 
                  variant="outline" 
                  size="sm"
                  className={inspectionData.status_code === 200
                    ? "text-green-600 border-green-300 bg-green-50 hover:bg-green-100 dark:text-green-400 dark:border-green-700 dark:bg-green-950 dark:hover:bg-green-900" 
                    : "text-red-600 border-red-300 bg-red-50 hover:bg-red-100 dark:text-red-400 dark:border-red-700 dark:bg-red-950 dark:hover:bg-red-900"
                  }
                >
                  {inspectionData.status_code === 200 ? "Valid" : "Invalid"}
                </Button>
              </div>
              
              <div className="flex items-center gap-2">
                <Label className="text-sm font-medium">Agent URL:</Label>
                <p className="text-sm text-muted-foreground">{inspectionData.agent_url}</p>
              </div>

              {inspectionData.result && inspectionData.result.length > 0 && (
                <div>
                  <Label className="text-sm font-medium">Validation Results:</Label>
                  <div className="space-y-2 mt-2">
                    {inspectionData.result.map((result, index) => (
                      <div key={index} className="flex items-center gap-2">
                        <CheckCircle className="h-4 w-4 text-green-500" />
                        <span className="text-sm">{result}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Agent Inspection (existing inspection results) */}
      {agentData?.result && (
        <Card>
          <CardHeader>
            <CardTitle>Agent Inspection</CardTitle>
            <CardDescription>
              Validation results for the agent
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {agentData.result.map((result, index) => (
                <div key={index} className="flex items-center gap-2">
                  <CheckCircle className="h-4 w-4 text-green-500" />
                  <span className="text-sm">{result}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Register button */}
      {agentData?.agent_card && (
        <div className="flex justify-end">
          <Button 
            onClick={registerAgentHandler}
            variant="outline"
            disabled={registering || !inspectionData || inspectionData.status_code !== 200}
            size="lg"
          >
            {registering ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Registering...
              </>
            ) : (
              "Register"
            )}
          </Button>
        </div>
      )}
      </div>
    </div>
  )
} 