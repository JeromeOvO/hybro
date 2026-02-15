"use client"

import { useState, useRef, useEffect } from "react"
import { useRouter } from "next/navigation"
import { Bot, CheckCircle, Loader2, CloudDownload, Lightbulb, Shield, ShieldCheck, ShieldX, FileSearch, XCircle, ChevronDown } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { banner } from "@/components/ui/banner"
import { getAgentCardFromUrl, registerAgent, updateAgent, inspectAgentCard, inspectA2AConnection } from "@/lib/api"
import { ApiError } from "@/lib/api-client"
import type { 
  AgentCenterResponse,
  AgentCenterRequest,
  InspectionCenterResponse,
  InsepectionCenterConnectionValidationResponse
} from "@/lib/types"
import { useUser, useClerk} from "@clerk/nextjs"
import { isWaitlistEnabled } from "@/lib/utils"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { AgentSettingsCard, validateAgentSettings, settingsToUpdatePayload } from "@/components/developer/agent-settings-card"
import type { AgentSettingsValues } from "@/components/developer/agent-settings-card"
import { useMyAgents } from "@/hooks/useMyAgents"

export default function RegisterAgentPage() {
  const router = useRouter()
  const { user, isLoaded } = useUser()
  const { invalidate: refreshMyAgents } = useMyAgents()

  // Tailwind badge styles (replaces old CSS badge-* classes)
  const badgeSuccessInteractive = "bg-[rgb(240,253,244)] text-[rgb(22,163,74)] border-[rgb(134,239,172)] hover:bg-[rgb(220,252,231)] dark:bg-[rgb(4,47,46)] dark:text-[rgb(74,222,128)] dark:border-[rgb(21,128,61)] dark:hover:bg-[rgb(20,83,45)]"
  const badgeMuted = "text-muted-foreground border-muted bg-muted/50 hover:bg-muted/70"
  const badgeError = "bg-[rgb(254,242,242)] text-[rgb(220,38,38)] border-[rgb(252,165,165)] dark:bg-[rgb(69,10,10)] dark:text-[rgb(248,113,113)] dark:border-[rgb(185,28,28)]"
  const [url, setUrl] = useState("")
  const [loading, setLoading] = useState(false)
  const [inspecting, setInspecting] = useState(false)
  const [inspectingCard, setInspectingCard] = useState(false)
  const [registering, setRegistering] = useState(false)
  const [agentData, setAgentData] = useState<AgentCenterResponse | null>(null)
  const [cardInspectionData, setCardInspectionData] = useState<InspectionCenterResponse | null>(null)
  const [inspectionData, setInspectionData] = useState<InsepectionCenterConnectionValidationResponse | null>(null)
  const [urlError, setUrlError] = useState("")
  const { openWaitlist } = useClerk()
  const [inspectionOpen, setInspectionOpen] = useState(true)
  const [settingsValues, setSettingsValues] = useState<AgentSettingsValues>({
    isPublic: true,
    enableUserLimit: false,
    userLimitValue: "",
    enableSystemLimit: false,
    systemLimitValue: "",
  })
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

  // Track whether we should auto-inspect after loading
  const shouldAutoInspect = useRef(false)

  // Scroll refs
  const agentCardRef = useRef<HTMLDivElement>(null)
  const registerSectionRef = useRef<HTMLDivElement>(null)

  // Auto-trigger inspection when agentData is set and auto-inspect is requested
  useEffect(() => {
    if (agentData?.agent_card && shouldAutoInspect.current) {
      shouldAutoInspect.current = false
      inspectCard()
      inspectConnection()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentData])

  // Scroll to agent card when agent data loads
  useEffect(() => {
    if (agentData?.agent_card) {
      setTimeout(() => {
        agentCardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }, 100)
    }
  }, [agentData])

  // Derived: both inspections completed and passed
  const allInspectionsPassed = 
    cardInspectionData?.status_code === 200 && inspectionData?.status_code === 200

  // Auto-collapse inspection results when both pass
  useEffect(() => {
    if (allInspectionsPassed) {
      setInspectionOpen(false)
    }
  }, [allInspectionsPassed])

  // Scroll to register section when inspections complete
  useEffect(() => {
    if (allInspectionsPassed) {
      setTimeout(() => {
        registerSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }, 200)
    }
  }, [allInspectionsPassed])

  // Load Agent information
  const loadAgent = async () => {
    if (!url || !validateUrl(url)) {
      setUrlError("Please enter a valid URL")
      return
    }

    setLoading(true)
    setAgentData(null)
    setCardInspectionData(null) // Reset card inspection data when loading new agent
    setInspectionData(null) // Reset connection inspection data when loading new agent
    setInspectionOpen(true) // Re-expand when loading new agent

    try {
      const response = await getAgentCardFromUrl({ agent_url: url })
      
      if (response.agent_card) {
        shouldAutoInspect.current = true
        setAgentData(response)
      } else {
        banner.error("Failed to load agent", {
          description: "No agent card found at the provided URL"
        })
      }
    } catch (error) {
      banner.error("Failed to load agent", {
        description: error instanceof Error ? error.message : "Network error occurred"
      })
    } finally {
      setLoading(false)
    }
  }

  // Inspect Agent Card
  const inspectCard = async () => {
    if (!url || !validateUrl(url)) {
      banner.error("Please enter a valid URL")
      return
    }

    setInspectingCard(true)
    setCardInspectionData(null)

    try {
      const response = await inspectAgentCard({ agent_url: url })
      setCardInspectionData(response)
      
      if (response.status_code === 200) {
        banner.success("Agent card inspection completed successfully!")
      } else {
        banner.warning("Agent card inspection found issues")
      }
    } catch (error) {
      banner.error("Failed to inspect agent card", {
        description: error instanceof Error ? error.message : "Network error occurred"
      })
    } finally {
      setInspectingCard(false)
    }
  }

  // Inspect A2A Connection
  const inspectConnection = async () => {
    if (!url || !validateUrl(url)) {
      banner.error("Please enter a valid URL")
      return
    }

    setInspecting(true)
    setInspectionData(null)

    try {
      const response = await inspectA2AConnection({ agent_url: url })
      setInspectionData(response)
      
      if (response.status_code === 200) {
        banner.success("Connection inspection completed successfully!")
      } else {
        // Remove the warning banner - just show success that inspection completed
        banner.success("Connection inspection completed!")
      }
    } catch (error) {
      banner.error("Failed to inspect connection", {
        description: error instanceof Error ? error.message : "Network error occurred"
      })
    } finally {
      setInspecting(false)
    }
  }

  // Register Agent
  const registerAgentHandler = async () => {
    if (!agentData?.agent_card) {
      banner.error("Please load agent information first")
      return
    }

    if (!inspectionData || inspectionData.status_code !== 200) {
      banner.error("Please complete connection inspection first")
      return
    }

    // Validate settings before registering
    const validationError = validateAgentSettings(settingsValues)
    if (validationError) {
      banner.error("Invalid settings", { description: validationError })
      return
    }
    
    // Do nothing while Clerk is still loading to avoid unexpected waitlist popup
    if(!isLoaded){
      return
    }

    if (!user?.id) {
      if (isWaitlistEnabled()) {
        openWaitlist()
      } else {
        router.push("/sign-in")
      }
      return
    }

    setRegistering(true)

    // get provider id from clerk
    const providerId = user!.id

    try {
      const registerRequest: AgentCenterRequest = {
        agent_url: url,
        agent_card: agentData.agent_card,
        provider_id: providerId
      }

      const response = await registerAgent(registerRequest)
      
      if (response.success) {
        // Apply agent settings after successful registration
        const agentId = response.agent_id
        if (agentId) {
          try {
            await updateAgent(agentId, settingsToUpdatePayload(settingsValues))
          } catch {
            // Settings update failed but registration succeeded - warn the user
            banner.warning("Agent registered but settings could not be saved", {
              description: "You can update settings from the agent management page."
            })
            refreshMyAgents()
            setTimeout(() => {
              router.push("/agents")
            }, 1500)
            return
          }
        }

        banner.success("Agent registered successfully!", {
          description: "Redirecting to agents page..."
        })
        
        refreshMyAgents()

        // Delayed redirect
        setTimeout(() => {
          router.push("/agents")
        }, 1500)
      } else {
        banner.error("Registration failed", {
          description: response.error || "The agent URL is already registered or invalid."
        })
      }
    } catch (error) {
      if (error instanceof ApiError && error.isClientError) {
        banner.warning("Registration failed", {
          description: error.status === 400
            ? "The agent URL is already registered or invalid."
            : error.message
        })
      } else {
        banner.error("Registration failed", {
          description: error instanceof Error ? error.message : "An unexpected error occurred. Please try again."
        })
      }
    } finally {
      setRegistering(false)
    }
  }

  // Show loading state when loading
  if (loading) {
    return (
      <div className="page-loading">
        <div className="flex flex-col items-center justify-center gap-4">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
          <span className="text-base font-medium text-muted-foreground">Loading Agent...</span>
        </div>
      </div>
    )
  }

  return (
    <div className="page-container">
      <div className="page-content space-y-6">
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
              onKeyDown={(e) => {
                if (e.key === "Enter" && url && !urlError && !loading) {
                  loadAgent()
                }
              }}
              className={urlError ? "border-destructive" : ""}
            />
            {urlError && (
              <p className="text-sm text-destructive">{urlError}</p>
            )}
          </div>
          
          <Button 
            onClick={loadAgent}
            disabled={loading || !url || !!urlError}
            className={`w-full sm:w-auto ${
              agentData?.agent_card
                ? ""
                : "btn-brand-gradient"
            }`}
            variant={agentData?.agent_card ? "outline" : "default"}
          >
            {loading ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Loading Agent...
              </>
            ) : (
              <>
                <CloudDownload className="h-4 w-4 mr-2" />
                {agentData?.agent_card ? "Reload Agent" : "Load Agent"}
              </>
            )}
          </Button>

          {!agentData?.agent_card && (
          <div className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 dark:border-amber-900 dark:bg-amber-950/40">
            <Lightbulb className="h-4 w-4 mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" />
            <p className="text-sm text-amber-800 dark:text-amber-300">
              <span className="font-medium">Tip:</span> New to{" "}
              <a
                href="https://a2a-protocol.org/"
                target="_blank"
                rel="noopener noreferrer"
                className="tip-link"
              >
                A2A
              </a>
              ? Use{" "}
              <a
                href="https://github.com/hybroai/a2a-adapter"
                target="_blank"
                rel="noopener noreferrer"
                className="tip-link"
              >
                a2a-adapter
              </a>
              {" "}to convert any agent from{" "}
              <a
                href="https://www.openclaw.ai/"
                target="_blank"
                rel="noopener noreferrer"
                className="tip-link"
              >
                OpenClaw
              </a>
              ,{" "}
              <a
                href="https://n8n.io/"
                target="_blank"
                rel="noopener noreferrer"
                className="tip-link"
              >
                n8n
              </a>
              ,{" "}
              <a
                href="https://www.crewai.com/"
                target="_blank"
                rel="noopener noreferrer"
                className="tip-link"
              >
                CrewAI
              </a>
              ,{" "}
              <a
                href="https://www.langchain.com/"
                target="_blank"
                rel="noopener noreferrer"
                className="tip-link"
              >
                LangChain
              </a>
              ,{" "}
              <a
                href="https://www.langchain.com/langgraph"
                target="_blank"
                rel="noopener noreferrer"
                className="tip-link"
              >
                LangGraph
              </a>
              , or other frameworks.
            </p>
          </div>
          )}
        </CardContent>
      </Card>

      {/* Agent Card information display */}
      {agentData?.agent_card && (
        <>
          <Card ref={agentCardRef}>
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
                      ? badgeSuccessInteractive 
                      : badgeMuted
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
                      ? badgeSuccessInteractive 
                      : badgeMuted
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
                      ? badgeSuccessInteractive 
                      : badgeMuted
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
                      ? badgeSuccessInteractive 
                      : badgeMuted
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
                      className={badgeSuccessInteractive}
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
                      className={badgeSuccessInteractive}
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
                      className={badgeSuccessInteractive}
                    >
                      {mode}
                    </Button>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Inspection - Unified Collapsible */}
          <Collapsible open={inspectionOpen} onOpenChange={setInspectionOpen}>
            <Card>
              <CollapsibleTrigger asChild>
                <CardHeader className="cursor-pointer select-none">
                  <CardTitle className="flex items-center gap-2">
                    {(inspectingCard || inspecting) ? (
                      <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                    ) : allInspectionsPassed ? (
                      <ShieldCheck className="h-5 w-5 text-green-500" />
                    ) : (cardInspectionData && cardInspectionData.status_code !== 200) || (inspectionData && inspectionData.status_code !== 200) ? (
                      <ShieldX className="h-5 w-5 text-red-500" />
                    ) : (
                      <Shield className="h-5 w-5" />
                    )}
                    Inspection
                    {allInspectionsPassed && (
                      <Button 
                        variant="outline" 
                        size="sm"
                        className={`ml-auto pointer-events-none ${badgeSuccessInteractive}`}
                      >
                        All Passed
                      </Button>
                    )}
                    <ChevronDown className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200 ${inspectionOpen ? "rotate-180" : ""} ${allInspectionsPassed ? "" : "ml-auto"}`} />
                  </CardTitle>
                  <CardDescription>
                    {allInspectionsPassed
                      ? "Agent card and A2A connection validated successfully"
                      : (inspectingCard || inspecting)
                        ? "Running inspections..."
                        : "Validate agent card structure and A2A connection"
                    }
                  </CardDescription>
                </CardHeader>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <CardContent className="space-y-6">
                  {/* Agent Card Inspection */}
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <h4 className="text-sm font-semibold flex items-center gap-2">
                        <FileSearch className="h-4 w-4" />
                        Agent Card Validation
                        {cardInspectionData && (
                          <Button 
                            variant="outline" 
                            size="sm"
                            className={`pointer-events-none text-xs h-6 ${cardInspectionData.status_code === 200
                              ? badgeSuccessInteractive 
                              : badgeError
                            }`}
                          >
                            {cardInspectionData.status_code === 200 ? "Passed" : "Failed"}
                          </Button>
                        )}
                      </h4>
                      <Button 
                        onClick={(e) => { e.stopPropagation(); inspectCard(); }}
                        disabled={inspectingCard}
                        variant="outline"
                        size="sm"
                      >
                        {inspectingCard ? (
                          <>
                            <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                            Inspecting...
                          </>
                        ) : (
                          <>
                            <FileSearch className="h-3 w-3 mr-1" />
                            {cardInspectionData ? "Re-inspect" : "Inspect"}
                          </>
                        )}
                      </Button>
                    </div>

                    {cardInspectionData && cardInspectionData.result && cardInspectionData.result.length > 0 && (
                      <div className="space-y-1.5 pl-6">
                        {cardInspectionData.result.map((result, index) => (
                          <div key={index} className="flex items-center gap-2">
                            {cardInspectionData.status_code === 200 ? (
                              <CheckCircle className="h-3.5 w-3.5 text-green-500 shrink-0" />
                            ) : (
                              <XCircle className="h-3.5 w-3.5 text-red-500 shrink-0" />
                            )}
                            <span className="text-sm text-muted-foreground">{result}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  <Separator />

                  {/* A2A Connection Inspection */}
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <h4 className="text-sm font-semibold flex items-center gap-2">
                        <Shield className="h-4 w-4" />
                        A2A Connection
                        {inspectionData && (
                          <Button 
                            variant="outline" 
                            size="sm"
                            className={`pointer-events-none text-xs h-6 ${inspectionData.status_code === 200
                              ? badgeSuccessInteractive 
                              : badgeError
                            }`}
                          >
                            {inspectionData.status_code === 200 ? "Passed" : "Failed"}
                          </Button>
                        )}
                      </h4>
                      <Button 
                        onClick={(e) => { e.stopPropagation(); inspectConnection(); }}
                        disabled={inspecting}
                        variant="outline"
                        size="sm"
                      >
                        {inspecting ? (
                          <>
                            <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                            Inspecting...
                          </>
                        ) : (
                          <>
                            <Shield className="h-3 w-3 mr-1" />
                            {inspectionData ? "Re-inspect" : "Inspect"}
                          </>
                        )}
                      </Button>
                    </div>

                    {inspectionData && inspectionData.result && inspectionData.result.length > 0 && (
                      <div className="space-y-1.5 pl-6">
                        {inspectionData.result.map((result, index) => (
                          <div key={index} className="flex items-center gap-2">
                            {inspectionData.status_code === 200 ? (
                              <CheckCircle className="h-3.5 w-3.5 text-green-500 shrink-0" />
                            ) : (
                              <XCircle className="h-3.5 w-3.5 text-red-500 shrink-0" />
                            )}
                            <span className="text-sm text-muted-foreground">{result}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </CardContent>
              </CollapsibleContent>
            </Card>
          </Collapsible>
        </>
      )}

      {/* Agent Settings - Show after inspection passes */}
      {inspectionData?.status_code === 200 && (
        <div ref={registerSectionRef}>
        <AgentSettingsCard
          values={settingsValues}
          onChange={setSettingsValues}
        />
        </div>
      )}

      {/* Register button - only show after successful inspection */}
      {agentData?.agent_card && inspectionData?.status_code === 200 && (
        <div className="flex justify-end">
          <Button 
            onClick={registerAgentHandler}
            className="btn-brand-gradient"
            disabled={registering}
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