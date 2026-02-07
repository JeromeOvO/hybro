"use client"

import { useState, useEffect, useCallback } from "react"
import { useUser, useAuth } from "@clerk/nextjs"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { VideoEmbed } from "@/components/video-embed"
import { FrameworkBadges } from "@/components/framework-badges"
import { Badge } from "@/components/ui/badge"
import {
  Github,
  ExternalLink,
  Copy,
  Check,
  ArrowRight,
  Package,
  Shield,
  Network,
  Terminal,
  Layers,
  Zap,
  BookOpen,
  Plus,
  Bot,
  RefreshCw,
} from "lucide-react"
import { useRouter } from "next/navigation"
import { getAgentsByProviderId } from "@/lib/api"
import type { Agent } from "@/lib/types"
import { consumerUrl } from "@/lib/urls"

function CopyButton({ text, className = "" }: { text: string; className?: string }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = async () => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }
  return (
    <button
      onClick={handleCopy}
      className={`inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors ${className}`}
      aria-label="Copy to clipboard"
    >
      {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      {copied ? "Copied" : "Copy"}
    </button>
  )
}

function CodeBlock({ code, language = "python" }: { code: string; language?: string }) {
  return (
    <div className="rounded-lg border border-border/50 bg-muted/30 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 bg-muted/50 border-b border-border/50">
        <span className="text-xs text-muted-foreground font-mono">{language}</span>
        <CopyButton text={code} />
      </div>
      <pre className="p-4 text-sm font-mono overflow-x-auto">
        <code>{code}</code>
      </pre>
    </div>
  )
}

const QUICK_START_CODE = `from a2a_adapter import A2AServer
from your_agent import YourAgent

# Wrap your existing agent
agent = YourAgent()
server = A2AServer(agent=agent)

# Start the A2A-compatible server
server.start()`

// Authenticated dashboard view
function DeveloperDashboard() {
  const router = useRouter()
  const { user } = useUser()
  const { getToken } = useAuth()
  const [myAgents, setMyAgents] = useState<Agent[]>([])
  const [loading, setLoading] = useState(true)

  const loadMyAgents = useCallback(async () => {
    try {
      setLoading(true)
      const response = await getAgentsByProviderId(getToken)
      if (response.success && response.agents) {
        setMyAgents(response.agents)
      }
    } catch {
      // silent
    } finally {
      setLoading(false)
    }
  }, [getToken])

  useEffect(() => {
    loadMyAgents()
  }, [loadMyAgents])

  const activeCount = myAgents.filter(a => a.agent_status === 'active').length

  return (
    <div className="px-4 sm:px-6 py-8">
      <div className="w-full max-w-4xl mx-auto space-y-8">
        {/* Welcome */}
        <div>
          <h1 className="text-3xl font-bold">
            Welcome back{user?.firstName ? `, ${user.firstName}` : ''}
          </h1>
          <p className="text-muted-foreground mt-1">
            Manage your agents and build on the HYBRO network.
          </p>
        </div>

        {/* Quick Stats */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <Card>
            <CardContent className="pt-6">
              <div className="text-sm text-muted-foreground">Total Agents</div>
              <div className="text-3xl font-bold text-[hsl(var(--color-hybro-hy))]">
                {loading ? <RefreshCw className="h-6 w-6 animate-spin" /> : myAgents.length}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <div className="text-sm text-muted-foreground">Active</div>
              <div className="text-3xl font-bold text-green-600 dark:text-green-400">
                {loading ? <RefreshCw className="h-6 w-6 animate-spin" /> : activeCount}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <div className="text-sm text-muted-foreground">Inactive</div>
              <div className="text-3xl font-bold text-yellow-600 dark:text-yellow-400">
                {loading ? <RefreshCw className="h-6 w-6 animate-spin" /> : myAgents.length - activeCount}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Quick Actions */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <Button variant="outline" className="h-auto py-4 flex flex-col gap-2" onClick={() => router.push('/register')}>
            <Plus className="h-5 w-5" />
            <span>Register New Agent</span>
          </Button>
          <Button variant="outline" className="h-auto py-4 flex flex-col gap-2" onClick={() => router.push('/inspector')}>
            <Shield className="h-5 w-5" />
            <span>Open Inspector</span>
          </Button>
          <Button variant="outline" className="h-auto py-4 flex flex-col gap-2" onClick={() => router.push('/docs')}>
            <BookOpen className="h-5 w-5" />
            <span>View Docs</span>
          </Button>
        </div>

        {/* My Agents Summary */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <Bot className="h-5 w-5" />
              My Agents
            </CardTitle>
            <Button variant="ghost" size="sm" onClick={() => router.push('/agents')}>
              View All <ArrowRight className="ml-1 h-3.5 w-3.5" />
            </Button>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="flex items-center justify-center py-8">
                <RefreshCw className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : myAgents.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                <p className="mb-4">No agents registered yet.</p>
                <Button onClick={() => router.push('/register')}>
                  <Plus className="h-4 w-4 mr-2" />
                  Register Your First Agent
                </Button>
              </div>
            ) : (
              <div className="space-y-3">
                {myAgents.slice(0, 5).map((agent) => (
                  <div
                    key={agent.agent_id}
                    className="flex items-center justify-between rounded-lg border border-border/50 p-3 hover:bg-muted/30 cursor-pointer transition-colors"
                    onClick={() => router.push(`/agents/${agent.agent_id}`)}
                  >
                    <div className="flex items-center gap-3">
                      <div className="w-2 h-2 rounded-full flex-shrink-0" style={{
                        backgroundColor: agent.agent_status === 'active' ? '#22c55e' : '#eab308'
                      }} />
                      <div>
                        <div className="font-medium text-sm">{agent.agent_card.name}</div>
                        <div className="text-xs text-muted-foreground">{agent.agent_card.provider?.organization}</div>
                      </div>
                    </div>
                    <Badge variant="outline" className="text-xs">
                      {agent.agent_status}
                    </Badge>
                  </div>
                ))}
                {myAgents.length > 5 && (
                  <p className="text-xs text-muted-foreground text-center pt-2">
                    + {myAgents.length - 5} more agents
                  </p>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

// Unauthenticated hero view
function DeveloperHero() {
  const [activeTab, setActiveTab] = useState<"quickstart" | "crewai" | "langgraph">("quickstart")

  const CREWAI_EXAMPLE = `from a2a_adapter import A2AServer
from crewai import Agent, Task, Crew

researcher = Agent(
    role="Research Analyst",
    goal="Find and analyze information",
    backstory="Expert researcher",
    verbose=True,
)

server = A2AServer(agent=researcher)
server.start(port=8080)`

  const LANGGRAPH_EXAMPLE = `from a2a_adapter import A2AServer
from langgraph.graph import StateGraph

graph = StateGraph(...)
graph.add_node("agent", agent_node)
graph.add_edge("agent", END)
app = graph.compile()

server = A2AServer(agent=app)
server.start(port=8080)`

  return (
    <div className="min-h-screen bg-background">
      <div className="max-w-4xl mx-auto px-4 sm:px-6">
        {/* Hero */}
        <section className="pt-16 pb-12">
          <div className="flex items-center gap-2 mb-4">
            <BookOpen className="h-5 w-5 text-primary" />
            <span className="text-sm font-medium text-primary uppercase tracking-wider">Developer Portal</span>
          </div>
          <h1 className="text-3xl md:text-4xl font-bold mb-3 tracking-tight">
            Build interoperable AI agents
          </h1>
          <p className="text-lg text-muted-foreground mb-6 max-w-2xl">
            Use <span className="font-mono font-medium text-foreground">a2a-adapter</span> to convert agents built with any framework into A2A-compatible, interoperable agents.
          </p>
          <div className="inline-flex items-center gap-3 bg-muted/50 border border-border/50 rounded-lg px-5 py-3 mb-6">
            <Terminal className="h-4 w-4 text-muted-foreground" />
            <code className="font-mono text-sm font-medium">pip install a2a-adapter</code>
            <CopyButton text="pip install a2a-adapter" />
          </div>
          <div className="flex flex-wrap gap-3">
            <Button asChild>
              <a href="https://github.com/hybroai/a2a-adapter" target="_blank" rel="noopener noreferrer">
                <Github className="mr-2 h-4 w-4" />
                GitHub
              </a>
            </Button>
            <Button variant="outline" asChild>
              <a href="https://github.com/hybroai/a2a-adapter#readme" target="_blank" rel="noopener noreferrer">
                Documentation
                <ExternalLink className="ml-2 h-4 w-4" />
              </a>
            </Button>
            <Button variant="outline" asChild>
              <a href="https://pypi.org/project/a2a-adapter/" target="_blank" rel="noopener noreferrer">
                <Package className="mr-2 h-4 w-4" />
                PyPI
              </a>
            </Button>
          </div>
        </section>

        {/* Demo Video */}
        <section className="py-12 border-t border-border/50">
          <h2 className="text-lg font-semibold mb-2">See it in action</h2>
          <p className="text-sm text-muted-foreground mb-6">Watch how agents built with different frameworks collaborate.</p>
          <VideoEmbed videoId="ZUQrnlBSsLg" title="HYBRO Demo" />
        </section>

        {/* How It Works */}
        <section className="py-12 border-t border-border/50">
          <h2 className="text-lg font-semibold mb-6">How it works</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div className="rounded-lg border border-border/50 bg-muted/20 p-5">
              <div className="flex items-center gap-3 mb-3">
                <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-primary/10 text-primary">
                  <Layers className="h-5 w-5" />
                </div>
                <h3 className="font-semibold">1. Wrap your agent</h3>
              </div>
              <p className="text-sm text-muted-foreground">
                Import <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">a2a-adapter</code> and wrap your existing agent in 3 lines.
              </p>
            </div>
            <div className="rounded-lg border border-border/50 bg-muted/20 p-5">
              <div className="flex items-center gap-3 mb-3">
                <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-primary/10 text-primary">
                  <Zap className="h-5 w-5" />
                </div>
                <h3 className="font-semibold">2. Start the server</h3>
              </div>
              <p className="text-sm text-muted-foreground">
                The adapter exposes your agent as an A2A-compatible HTTP server.
              </p>
            </div>
            <div className="rounded-lg border border-border/50 bg-muted/20 p-5">
              <div className="flex items-center gap-3 mb-3">
                <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-primary/10 text-primary">
                  <Network className="h-5 w-5" />
                </div>
                <h3 className="font-semibold">3. Register on HYBRO</h3>
              </div>
              <p className="text-sm text-muted-foreground">
                <a href="/register" className="text-primary hover:underline">Register your agent</a> on the network.
              </p>
            </div>
          </div>
        </section>

        {/* Supported Frameworks */}
        <section className="py-12 border-t border-border/50">
          <h2 className="text-lg font-semibold mb-6">Supported Frameworks</h2>
          <FrameworkBadges showDescriptions />
          <p className="text-sm text-muted-foreground mt-4">+ any agent that can receive input and return output</p>
        </section>

        {/* Code Examples */}
        <section className="py-12 border-t border-border/50">
          <h2 className="text-lg font-semibold mb-6">Getting Started</h2>
          <div className="flex gap-1 mb-4 p-1 bg-muted/50 rounded-lg w-fit">
            {(["quickstart", "crewai", "langgraph"] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                  activeTab === tab ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {tab === "quickstart" ? "Quick Start" : tab === "crewai" ? "CrewAI" : "LangGraph"}
              </button>
            ))}
          </div>
          {activeTab === "quickstart" && <CodeBlock code={QUICK_START_CODE} />}
          {activeTab === "crewai" && <CodeBlock code={CREWAI_EXAMPLE} />}
          {activeTab === "langgraph" && <CodeBlock code={LANGGRAPH_EXAMPLE} />}
        </section>

        {/* Next Steps */}
        <section className="py-12 border-t border-border/50">
          <h2 className="text-lg font-semibold mb-6">Next Steps</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <Card className="border-border/50">
              <CardHeader className="pb-3">
                <CardTitle className="text-base flex items-center gap-2">
                  <div className="w-8 h-8 rounded-full bg-primary/10 text-primary flex items-center justify-center text-sm font-bold">1</div>
                  Test Your Agent
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground mb-4">Use the Inspector to verify A2A compliance.</p>
                <Button variant="outline" size="sm" className="w-full" asChild>
                  <a href="/inspector"><Shield className="mr-2 h-4 w-4" />Launch Inspector</a>
                </Button>
              </CardContent>
            </Card>
            <Card className="border-border/50">
              <CardHeader className="pb-3">
                <CardTitle className="text-base flex items-center gap-2">
                  <div className="w-8 h-8 rounded-full bg-primary/10 text-primary flex items-center justify-center text-sm font-bold">2</div>
                  Register on HYBRO
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground mb-4">Add your agent to the network.</p>
                <Button variant="outline" size="sm" className="w-full" asChild>
                  <a href="/register"><Network className="mr-2 h-4 w-4" />Register Agent</a>
                </Button>
              </CardContent>
            </Card>
            <Card className="border-border/50">
              <CardHeader className="pb-3">
                <CardTitle className="text-base flex items-center gap-2">
                  <div className="w-8 h-8 rounded-full bg-primary/10 text-primary flex items-center justify-center text-sm font-bold">3</div>
                  Try the Chat
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground mb-4">See your agent in action.</p>
                <Button variant="outline" size="sm" className="w-full" asChild>
                  <a href={consumerUrl("/chat")}>Try it<ArrowRight className="ml-2 h-4 w-4" /></a>
                </Button>
              </CardContent>
            </Card>
          </div>
        </section>

        <div className="h-12" />
      </div>
    </div>
  )
}

export default function DeveloperLandingPage() {
  const { isLoaded, isSignedIn } = useUser()

  if (!isLoaded) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
      </div>
    )
  }

  return isSignedIn ? <DeveloperDashboard /> : <DeveloperHero />
}
