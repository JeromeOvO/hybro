"use client"

import { useState, useEffect, useCallback } from "react"
import { useParams, useRouter } from "next/navigation"
import {
  ArrowLeft,
  Bot,
  SquareArrowOutUpRight,
  CheckCircle2,
  XCircle,
  AlertCircle,
  Lightbulb,
  Send,
  Reply,
  Zap,
  MessageCirclePlus,
  ShieldCheck,
} from "lucide-react"
import { useAuth } from "@clerk/nextjs"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardFooter, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Separator } from "@/components/ui/separator"
import { Badge } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { banner } from "@/components/ui/banner"
import { getAgent } from "@/lib/api"
import type { Agent, AgentCenterResponse, AgentCapabilities } from "@/lib/types"
import { developerUrl } from "@/lib/urls"
import { isSystemAgent, SYSTEM_AGENTS } from "@/lib/system-agents"
import { getModeIcon } from "@/lib/agent-icon-utils"
import { AgentSourceBadge } from "@/components/agent-source-badge"

function visibleCapabilities(caps: AgentCapabilities): string[] {
  const all: [string, unknown][] = [
    ["Streaming", caps.streaming],
    ["Push Notifications", caps.pushNotifications],
    ["State History", caps.stateTransitionHistory],
    ["Extensions", caps.extensions],
  ]
  return all
    .filter(([, v]) => (Array.isArray(v) ? v.length > 0 : !!v))
    .map(([label]) => label)
}

function getStatusBadge(status: Agent["agent_status"]) {
  switch (status) {
    case "active":
      return (
        <Badge variant="success" className="gap-1.5 pl-1.5 pr-2.5 py-0.5">
          <CheckCircle2 className="w-3.5 h-3.5 fill-current opacity-80" />
          Active
        </Badge>
      )
    case "inactive":
      return (
        <Badge variant="inactive" className="gap-1.5 pl-1.5 pr-2.5 py-0.5">
          <AlertCircle className="w-3.5 h-3.5 fill-current opacity-80" />
          Inactive
        </Badge>
      )
    case "deleted":
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

export default function ConsumerAgentProfilePage() {
  const params = useParams()
  const router = useRouter()
  const { userId } = useAuth()
  const agentId = params.id as string
  const [agentData, setAgentData] = useState<AgentCenterResponse | null>(null)
  const [loading, setLoading] = useState(true)

  const loadAgentDetail = useCallback(async () => {
    if (isSystemAgent(agentId)) {
      setLoading(false)
      return
    }
    try {
      setLoading(true)
      const response = await getAgent(agentId)

      if (response.success && response.agent) {
        setAgentData(response)
      } else {
        const errorMessage = response.error || "Failed to load agent details"
        banner.error("Failed to load agent details", {
          description: errorMessage,
        })
      }
    } catch {
      banner.error("Failed to load agent details")
    } finally {
      setLoading(false)
    }
  }, [agentId])

  useEffect(() => {
    if (agentId) {
      loadAgentDetail()
    }
  }, [agentId, loadAgentDetail])

  /* ───────── Loading ───────── */

  if (loading) {
    return (
      <div className="page-container">
        <div className="page-content flex items-center justify-center min-h-[60vh]">
          <div className="flex flex-col items-center justify-center gap-4">
            <div className="relative">
              <div className="h-12 w-12 rounded-full border-4 border-primary/20 animate-spin border-t-primary" />
              <Bot className="h-6 w-6 absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-primary/60" />
            </div>
            <span className="text-base font-medium text-muted-foreground animate-pulse">
              Loading Agent Profile...
            </span>
          </div>
        </div>
      </div>
    )
  }

  /* ───────── System agent ───────── */

  if (!agentData?.success || !agentData.agent) {
    if (isSystemAgent(agentId)) {
      const info = SYSTEM_AGENTS[agentId]
      return (
        <div className="page-container animate-in fade-in duration-500">
          <div className="page-content space-y-8">
            <Button
              variant="ghost"
              onClick={() => router.push("/agents")}
              className="group pl-0 hover:pl-2 transition-all"
            >
              <ArrowLeft className="h-4 w-4 mr-2 group-hover:-translate-x-1 transition-transform" />
              Back to Agents
            </Button>

            <Card className="overflow-hidden border-primary/10 shadow-lg">
              <CardHeader className="text-center space-y-4 pt-10 pb-6">
                <div className="mx-auto">
                  <Avatar className="h-24 w-24 border-4 border-background shadow-xl mx-auto">
                    <AvatarFallback className="bg-primary/5 text-primary">
                      <ShieldCheck className="h-10 w-10" />
                    </AvatarFallback>
                  </Avatar>
                </div>
                <div className="space-y-2">
                  <CardTitle className="text-2xl font-bold">{info.name}</CardTitle>
                  <Badge variant="secondary" className="gap-1.5">
                    <ShieldCheck className="h-3 w-3" />
                    Built-in System Agent
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="text-center pb-10 max-w-lg mx-auto space-y-4">
                <p className="text-muted-foreground leading-relaxed">{info.description}</p>
                <Separator />
                <p className="text-sm text-muted-foreground">
                  This is a built-in agent managed by Hybro. It does not have a
                  configurable profile, skills, or capabilities — it activates
                  automatically when a room has multiple agent responses.
                </p>
              </CardContent>
            </Card>
          </div>
        </div>
      )
    }

    /* ───────── Not found ───────── */

    return (
      <div className="page-container">
        <div className="page-content flex items-center justify-center min-h-[60vh]">
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
              <Button onClick={() => router.push("/agents")} variant="default">
                <ArrowLeft className="h-4 w-4 mr-2" />
                Back to Agents
              </Button>
            </CardFooter>
          </Card>
        </div>
      </div>
    )
  }

  /* ───────── Main profile ───────── */

  const agent = agentData.agent
  const card = agent.agent_card
  const isOwner = userId && agent.provider_id === userId
  const enabledCaps = visibleCapabilities(card.capabilities)

  return (
    <div className="page-container animate-in fade-in duration-500">
      <div className="page-content space-y-10">
        {/* ── Top nav ── */}
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <Button
            variant="ghost"
            onClick={() => router.push("/agents")}
            className="group pl-0 hover:pl-2 transition-all"
          >
            <ArrowLeft className="h-4 w-4 mr-2 group-hover:-translate-x-1 transition-transform" />
            Back to Agents
          </Button>
          {isOwner && (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="brandTint" size="sm" asChild>
                    <a
                      href={developerUrl(`/agents/${agentId}`)}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Manage
                      <SquareArrowOutUpRight className="h-4 w-4 ml-2" />
                    </a>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Manage on Developer Portal</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
        </div>

        {/* ── Hero ── */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
          {/* Left: Identity */}
          <div className="lg:col-span-7 space-y-5">
            <div className="flex items-start gap-5">
              <Avatar className="h-24 w-24 shrink-0 border-4 border-background shadow-xl">
                <AvatarImage src={card.iconUrl || undefined} alt={card.name} />
                <AvatarFallback className="bg-primary/5 text-primary">
                  <Bot className="h-10 w-10" />
                </AvatarFallback>
              </Avatar>

              <div className="space-y-2 min-w-0">
                <div className="flex flex-wrap items-center gap-3">
                  <h1 className="text-2xl font-bold tracking-tight">{card.name}</h1>
                  {getStatusBadge(agent.agent_status)}
                  <AgentSourceBadge
                    source={agent.source}
                    isHubOnline={agent.is_hub_online}
                    className="h-5 w-5"
                  />
                </div>
                <p className="text-sm text-muted-foreground font-medium">
                  v{card.version}
                  <span className="mx-1.5 text-border">·</span>
                  Built by {card.provider?.organization || "Unknown Provider"}
                </p>
              </div>
            </div>

            <p className="text-muted-foreground leading-relaxed">{card.description}</p>
          </div>

          {/* Right: Action card */}
          <div className="lg:col-span-5">
            <div className="space-y-3">
                <Button
                  className="w-full btn-brand-gradient"
                  onClick={() => router.push(`/chat?agentId=${agentId}`)}
                >
                  <MessageCirclePlus className="h-4 w-4 mr-2" />
                  Chat with this agent
                </Button>

                {card.documentationUrl && (
                  <Button variant="outline" className="w-full" asChild>
                    <a
                      href={card.documentationUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      <SquareArrowOutUpRight className="h-4 w-4 mr-2" />
                      View Documentation
                    </a>
                  </Button>
                )}
            </div>
          </div>
        </div>

        {/* ── What this agent can help with ── */}
        {card.skills.some((s) => s.examples && s.examples.length > 0) && (
          <section className="space-y-4" data-testid="skills-section">
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <Lightbulb className="h-5 w-5 text-primary" />
              What this agent can help with
            </h2>

            <div className="space-y-1.5 rounded-lg bg-muted/30 border border-border/50 p-3">
              {card.skills.flatMap((skill) =>
                (skill.examples ?? []).slice(0, 2).map((example) => (
                  <p
                    key={`${skill.id}-${example}`}
                    className="text-xs text-foreground/80 font-mono break-words"
                  >
                    {example}
                  </p>
                )),
              )}
            </div>
          </section>
        )}

        {/* ── How this agent works ── */}
        <section className="space-y-4" data-testid="technical-section">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Zap className="h-5 w-5 text-primary" />
            How this agent works
          </h2>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* You can send */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-semibold flex items-center gap-2">
                  <Send className="h-4 w-4 text-muted-foreground" />
                  You can send
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex flex-wrap gap-2">
                  {card.defaultInputModes.map((mode) => {
                    const Icon = getModeIcon(mode)
                    return (
                      <Badge
                        key={mode}
                        variant="outline"
                        className="gap-1.5 font-normal"
                      >
                        <Icon className="h-3 w-3" />
                        {mode}
                      </Badge>
                    )
                  })}
                </div>
              </CardContent>
            </Card>

            {/* It returns */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-semibold flex items-center gap-2">
                  <Reply className="h-4 w-4 text-muted-foreground" />
                  It returns
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex flex-wrap gap-2">
                  {card.defaultOutputModes.map((mode) => {
                    const Icon = getModeIcon(mode)
                    return (
                      <Badge
                        key={mode}
                        variant="outline"
                        className="gap-1.5 font-normal"
                      >
                        <Icon className="h-3 w-3" />
                        {mode}
                      </Badge>
                    )
                  })}
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Capabilities */}
          {enabledCaps.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 pt-2" data-testid="capabilities">
              <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                Capabilities
              </span>
              {enabledCaps.map((cap) => (
                <Badge
                  key={cap}
                  variant="secondary"
                  className="font-normal"
                >
                  {cap}
                </Badge>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
