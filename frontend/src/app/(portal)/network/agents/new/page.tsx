'use client'

import { useState, useRef, useEffect } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { routes } from '@/lib/routes'
import { useRouter } from "next/navigation"
import { Bot, CheckCircle, Loader2, CloudDownload, Lightbulb, Shield, ShieldCheck, ShieldX, FileSearch, XCircle, ChevronDown } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { PageHeader } from "@/components/page-header"
import { Separator } from "@/components/ui/separator"
import { toast } from "sonner"
import { getAgentCardFromUrl, registerAgent, registrationConflictMessage } from "@/lib/api/agent"
import { inspectA2AConnection, inspectAgentCard } from "@/lib/api/inspection"
import { ApiError } from "@/lib/api-client"
import type {
  AgentRouteResponse,
  AgentRegisterRequest,
  InspectionCenterResponse,
  InsepectionCenterConnectionValidationResponse
} from "@/lib/types"
import { useUser } from "@/lib/auth"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"

const FRAMEWORK_LINKS = [
  { label: "OpenClaw", href: "https://www.openclaw.ai/" },
  { label: "n8n", href: "https://n8n.io/" },
  { label: "CrewAI", href: "https://www.crewai.com/" },
  { label: "LangChain", href: "https://www.langchain.com/" },
  { label: "LangGraph", href: "https://www.langchain.com/langgraph" },
]

export default function RegisterAgentPage() {
  const router = useRouter()
  const queryClient = useQueryClient()
  const { user, isLoaded } = useUser()

  const [url, setUrl] = useState("")
  const [loading, setLoading] = useState(false)
  const [inspecting, setInspecting] = useState(false)
  const [inspectingCard, setInspectingCard] = useState(false)
  const [registering, setRegistering] = useState(false)
  const [agentData, setAgentData] = useState<AgentRouteResponse | null>(null)
  const [cardInspectionData, setCardInspectionData] = useState<InspectionCenterResponse | null>(null)
  const [inspectionData, setInspectionData] = useState<InsepectionCenterConnectionValidationResponse | null>(null)
  const [urlError, setUrlError] = useState("")
  const [inspectionOpen, setInspectionOpen] = useState(true)
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
    if (agentData?.card && shouldAutoInspect.current) {
      shouldAutoInspect.current = false
      inspectCard()
      inspectConnection()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentData])

  // Scroll to agent card when agent data loads
  useEffect(() => {
    if (agentData?.card) {
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

      if (response.card) {
        shouldAutoInspect.current = true
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

  // Inspect Agent Card
  const inspectCard = async () => {
    if (!url || !validateUrl(url)) {
      toast.error("Please enter a valid URL")
      return
    }

    setInspectingCard(true)
    setCardInspectionData(null)

    try {
      const response = await inspectAgentCard({ agent_url: url })
      setCardInspectionData(response)

      if (response.status_code === 200) {
        toast.success("Agent card inspection completed successfully!")
      } else {
        toast.warning("Agent card inspection found issues")
      }
    } catch (error) {
      toast.error("Failed to inspect agent card", {
        description: error instanceof Error ? error.message : "Network error occurred"
      })
    } finally {
      setInspectingCard(false)
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
        // Remove the warning banner - just show success that inspection completed
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
    if (!agentData?.card) {
      toast.error("Please load agent information first")
      return
    }

    if (!inspectionData || inspectionData.status_code !== 200) {
      toast.error("Please complete connection inspection first")
      return
    }

    // Do nothing while the auth adapter is still loading.
    if (!isLoaded) {
      return
    }

    if (!user?.id) {
      router.push(`/sign-in?redirect_url=${encodeURIComponent(window.location.pathname + window.location.search)}`)
      return
    }

    setRegistering(true)

    try {
      const registerRequest: AgentRegisterRequest = { agent_url: url }

      const response = await registerAgent(registerRequest)

      if (response.success) {
        toast.success("Agent registered successfully!", {
          description: "Redirecting to the network…"
        })

        await queryClient.invalidateQueries({ queryKey: ["agents"] })

        // Delayed redirect
        setTimeout(() => {
          router.push(routes.network)
        }, 1500)
      } else {
        toast.error("Registration failed", {
          description: response.error || "The agent URL could not be registered."
        })
      }
    } catch (error) {
      if (error instanceof ApiError && error.isClientError) {
        toast.warning("Registration failed", {
          description: registrationConflictMessage(error) ?? (
            error.status === 400 ? "The agent URL is invalid." : error.message
          )
        })
      } else {
        toast.error("Registration failed", {
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
          <Loader2 className="h-8 w-8 motion-safe:animate-spin text-muted-foreground" />
          <span className="text-base font-medium text-muted-foreground">Loading agent…</span>
        </div>
      </div>
    )
  }

  return (
    <div className="page-container">
      <div className="page-content space-y-6">
      <PageHeader title="Register an agent" description="Add a remote A2A agent by its Agent Card URL." />

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
            className="w-full sm:w-auto"
            variant={agentData?.card ? "secondary" : "default"}
          >
            {loading ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 motion-safe:animate-spin" />
                Loading agent…
              </>
            ) : (
              <>
                <CloudDownload className="h-4 w-4 mr-2" />
                {agentData?.card ? "Reload agent" : "Load agent"}
              </>
            )}
          </Button>

          {!agentData?.card && (
          <div className="flex flex-col gap-2 rounded-lg border bg-muted/40 p-3">
            <p className="flex items-start gap-2 text-sm text-muted-foreground">
              <Lightbulb className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <span>
                New to{" "}
                <a href="https://a2a-protocol.org/" target="_blank" rel="noopener noreferrer" className="font-medium text-foreground underline underline-offset-2">A2A</a>
                ? Use{" "}
                <a href="https://github.com/hybroai/a2a-adapter" target="_blank" rel="noopener noreferrer" className="font-medium text-foreground underline underline-offset-2">a2a-adapter</a>
                {" "}to convert an agent from one of these frameworks.
              </span>
            </p>
            <div className="flex flex-wrap gap-1">
              {FRAMEWORK_LINKS.map((link) => (
                <Button key={link.href} variant="ghost" size="sm" asChild>
                  <a href={link.href} target="_blank" rel="noopener noreferrer">{link.label}</a>
                </Button>
              ))}
            </div>
          </div>
          )}
        </CardContent>
      </Card>

      {/* Agent Card information display */}
      {agentData?.card && (
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
                  <p className="text-sm text-muted-foreground">{agentData.card.name}</p>
                </div>
                <div>
                  <Label className="text-sm font-medium">Version</Label>
                  <p className="text-sm text-muted-foreground">{agentData.card.version}</p>
                </div>
                <div>
                  <Label className="text-sm font-medium">Provider</Label>
                  <p className="text-sm text-muted-foreground">
                    {agentData.card.provider_organization || "Unknown"}
                  </p>
                </div>
                <div>
                  <Label className="text-sm font-medium">Streaming</Label>
                  <Badge variant="badgeMuted" className={agentData.card.capabilities.streaming ? "text-status-online" : undefined}>
                    {agentData.card.capabilities.streaming ? "Supported" : "Not supported"}
                  </Badge>
                </div>
                <div>
                  <Label className="text-sm font-medium">Extensions</Label>
                  <Badge variant="badgeMuted" className={agentData.card.capabilities.extensions ? "text-status-online" : undefined}>
                    {agentData.card.capabilities.extensions ? "Supported" : "Not supported"}
                  </Badge>
                </div>
                <div>
                  <Label className="text-sm font-medium">Push Notifications</Label>
                  <Badge variant="badgeMuted" className={agentData.card.capabilities.push_notifications ? "text-status-online" : undefined}>
                    {agentData.card.capabilities.push_notifications ? "Supported" : "Not supported"}
                  </Badge>
                </div>
                <div>
                  <Label className="text-sm font-medium">State Transition History</Label>
                  <Badge variant="badgeMuted" className={agentData.card.capabilities.state_transition_history ? "text-status-online" : undefined}>
                    {agentData.card.capabilities.state_transition_history ? "Supported" : "Not supported"}
                  </Badge>
                </div>
              </div>

              <div>
                <Label className="text-sm font-medium">Description</Label>
                <p className="text-sm text-muted-foreground">{agentData.card.description}</p>
              </div>

              <div>
                <Label className="text-sm font-medium">URL</Label>
                <p className="text-sm text-muted-foreground">{url}</p>
              </div>

              <div>
                <Label className="text-sm font-medium">Skills</Label>
                <div className="flex flex-wrap gap-2 mt-1">
                  {agentData.card.skills.map((skill, index) => (
                    <Badge key={index} variant="badgeMuted">
                      {skill.name}
                    </Badge>
                  ))}
                </div>
              </div>

              <div>
                <Label className="text-sm font-medium">Input Modes</Label>
                <div className="flex flex-wrap gap-2 mt-1">
                  {agentData.card.default_input_modes.map((mode, index) => (
                    <Badge key={index} variant="badgeMuted">
                      {mode}
                    </Badge>
                  ))}
                </div>
              </div>

              <div>
                <Label className="text-sm font-medium">Output Modes</Label>
                <div className="flex flex-wrap gap-2 mt-1">
                  {agentData.card.default_output_modes.map((mode, index) => (
                    <Badge key={index} variant="badgeMuted">
                      {mode}
                    </Badge>
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
                      <Loader2 className="h-5 w-5 motion-safe:animate-spin text-muted-foreground" />
                    ) : allInspectionsPassed ? (
                      <ShieldCheck className="h-5 w-5 text-status-online" />
                    ) : (cardInspectionData && cardInspectionData.status_code !== 200) || (inspectionData && inspectionData.status_code !== 200) ? (
                      <ShieldX className="h-5 w-5 text-status-error" />
                    ) : (
                      <Shield className="h-5 w-5" />
                    )}
                    Inspection
                    {allInspectionsPassed && (
                      <Badge variant="badgeMuted" className="ml-auto text-status-online">
                        All passed
                      </Badge>
                    )}
                    <ChevronDown className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200 ${inspectionOpen ? "rotate-180" : ""} ${allInspectionsPassed ? "" : "ml-auto"}`} />
                  </CardTitle>
                  <CardDescription>
                    {allInspectionsPassed
                      ? "Agent card and A2A connection validated successfully"
                      : (inspectingCard || inspecting)
                        ? "Running inspections…"
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
                          <Badge variant="badgeMuted" className={cardInspectionData.status_code === 200 ? "text-status-online" : "text-status-error"}>
                            {cardInspectionData.status_code === 200 ? "Passed" : "Failed"}
                          </Badge>
                        )}
                      </h4>
                      <Button
                        onClick={(e) => { e.stopPropagation(); inspectCard(); }}
                        disabled={inspectingCard}
                        variant="secondary"
                        size="sm"
                      >
                        {inspectingCard ? (
                          <>
                            <Loader2 className="h-3 w-3 mr-1 motion-safe:animate-spin" />
                            Inspecting…
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
                              <CheckCircle className="h-3.5 w-3.5 text-status-online shrink-0" />
                            ) : (
                              <XCircle className="h-3.5 w-3.5 text-status-error shrink-0" />
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
                          <Badge variant="badgeMuted" className={inspectionData.status_code === 200 ? "text-status-online" : "text-status-error"}>
                            {inspectionData.status_code === 200 ? "Passed" : "Failed"}
                          </Badge>
                        )}
                      </h4>
                      <Button
                        onClick={(e) => { e.stopPropagation(); inspectConnection(); }}
                        disabled={inspecting}
                        variant="secondary"
                        size="sm"
                      >
                        {inspecting ? (
                          <>
                            <Loader2 className="h-3 w-3 mr-1 motion-safe:animate-spin" />
                            Inspecting…
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
                              <CheckCircle className="h-3.5 w-3.5 text-status-online shrink-0" />
                            ) : (
                              <XCircle className="h-3.5 w-3.5 text-status-error shrink-0" />
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

      {/* Register button - only show after successful inspection */}
      {agentData?.card && inspectionData?.status_code === 200 && (
        <div ref={registerSectionRef} className="flex justify-end">
          <Button
            onClick={registerAgentHandler}
            disabled={registering}
            size="lg"
          >
            {registering ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 motion-safe:animate-spin" />
                Registering…
              </>
            ) : (
              "Register agent"
            )}
          </Button>
        </div>
      )}
      </div>
    </div>
  )
}
