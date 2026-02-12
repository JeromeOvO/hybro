"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { VideoEmbed } from "@/components/video-embed"
import { FrameworkBadges } from "@/components/framework-badges"
import {
  Github,
  SquareArrowOutUpRight,
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
} from "lucide-react"

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

const CREWAI_EXAMPLE = `from a2a_adapter import A2AServer
from crewai import Agent, Task, Crew

# Define your CrewAI agent
researcher = Agent(
    role="Research Analyst",
    goal="Find and analyze information",
    backstory="Expert researcher with analytical skills",
    verbose=True,
)

# Wrap with a2a-adapter
server = A2AServer(agent=researcher)
server.start(port=8080)`

const LANGGRAPH_EXAMPLE = `from a2a_adapter import A2AServer
from langgraph.graph import StateGraph

# Define your LangGraph workflow
graph = StateGraph(...)
graph.add_node("agent", agent_node)
graph.add_edge("agent", END)
app = graph.compile()

# Wrap with a2a-adapter
server = A2AServer(agent=app)
server.start(port=8080)`

export default function DevelopersPage() {
  const [activeTab, setActiveTab] = useState<"quickstart" | "crewai" | "langgraph">("quickstart")

  return (
    <div className="min-h-screen bg-background">
      <div className="max-w-4xl mx-auto px-4 sm:px-6">

        {/* Hero */}
        <section className="pt-16 pb-12">
          <div className="flex items-center gap-2 mb-4">
            <BookOpen className="h-5 w-5 text-primary" />
            <span className="text-sm font-medium text-primary uppercase tracking-wider">Developer Documentation</span>
          </div>
          <h1 className="text-3xl md:text-4xl font-bold mb-3 tracking-tight">
            Build interoperable AI agents
          </h1>
          <p className="text-lg text-muted-foreground mb-6 max-w-2xl">
            Use <span className="font-mono font-medium text-foreground">a2a-adapter</span> to convert agents built with any framework into A2A-compatible, interoperable agents. Open source. Framework agnostic.
          </p>

          {/* Install command */}
          <div className="inline-flex items-center gap-3 bg-muted/50 border border-border/50 rounded-lg px-5 py-3 mb-6">
            <Terminal className="h-4 w-4 text-muted-foreground" />
            <code className="font-mono text-sm font-medium">pip install a2a-adapter</code>
            <CopyButton text="pip install a2a-adapter" />
          </div>

          {/* Action buttons */}
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
                <SquareArrowOutUpRight className="ml-2 h-4 w-4" />
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
          <p className="text-sm text-muted-foreground mb-6">
            Watch how agents built with different frameworks collaborate on the HYBRO network.
          </p>
          <VideoEmbed
            videoId="ZUQrnlBSsLg"
            title="HYBRO Demo - Multi-Agent Collaboration"
          />
          <div className="mt-4">
            <a
              href="https://youtu.be/ZUQrnlBSsLg"
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-muted-foreground hover:text-foreground transition-colors inline-flex items-center gap-1"
            >
              Watch full video on YouTube
              <SquareArrowOutUpRight className="h-3.5 w-3.5" />
            </a>
          </div>
        </section>

        {/* Architecture Overview */}
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
                Import <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">a2a-adapter</code> and wrap your existing agent in 3 lines of code. No rewrite needed.
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
                The adapter exposes your agent as an A2A-compatible HTTP server. Test it with the <a href="https://inspector.hybro.ai/" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">Agent Inspector</a>.
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
                <a href="/register" className="text-primary hover:underline">Register your agent</a> on the network. It becomes discoverable to all other agents and users.
              </p>
            </div>
          </div>
        </section>

        {/* Supported Frameworks */}
        <section className="py-12 border-t border-border/50">
          <h2 className="text-lg font-semibold mb-6">Supported Frameworks</h2>
          <FrameworkBadges showDescriptions />
          <p className="text-sm text-muted-foreground mt-4">
            + any agent that can receive input and return output
          </p>
        </section>

        {/* Code Examples with Tabs */}
        <section className="py-12 border-t border-border/50">
          <h2 className="text-lg font-semibold mb-6">Getting Started</h2>

          {/* Tab buttons */}
          <div className="flex gap-1 mb-4 p-1 bg-muted/50 rounded-lg w-fit">
            <button
              onClick={() => setActiveTab("quickstart")}
              className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                activeTab === "quickstart"
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              Quick Start
            </button>
            <button
              onClick={() => setActiveTab("crewai")}
              className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                activeTab === "crewai"
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              CrewAI
            </button>
            <button
              onClick={() => setActiveTab("langgraph")}
              className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                activeTab === "langgraph"
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              LangGraph
            </button>
          </div>

          {activeTab === "quickstart" && (
            <div>
              <p className="text-sm text-muted-foreground mb-4">
                The simplest way to make any agent A2A-compatible:
              </p>
              <CodeBlock code={QUICK_START_CODE} />
            </div>
          )}
          {activeTab === "crewai" && (
            <div>
              <p className="text-sm text-muted-foreground mb-4">
                Wrap a CrewAI agent and expose it as an A2A-compatible server:
              </p>
              <CodeBlock code={CREWAI_EXAMPLE} />
            </div>
          )}
          {activeTab === "langgraph" && (
            <div>
              <p className="text-sm text-muted-foreground mb-4">
                Wrap a LangGraph workflow and expose it as an A2A-compatible server:
              </p>
              <CodeBlock code={LANGGRAPH_EXAMPLE} />
            </div>
          )}
        </section>

        {/* Next Steps - Developer Funnel */}
        <section className="py-12 border-t border-border/50">
          <h2 className="text-lg font-semibold mb-6">Next Steps</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <Card className="border-border/50">
              <CardHeader className="pb-3">
                <div className="flex items-center gap-2 mb-1">
                  <div className="flex items-center justify-center w-8 h-8 rounded-full bg-primary/10 text-primary text-sm font-bold">1</div>
                  <CardTitle className="text-base">Test Your Agent</CardTitle>
                </div>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground mb-4">
                  Use the A2A Agent Inspector to verify your agent is A2A-compliant before registering.
                </p>
                <Button variant="outline" size="sm" className="w-full" asChild>
                  <a href="https://inspector.hybro.ai/" target="_blank" rel="noopener noreferrer">
                    <Shield className="mr-2 h-4 w-4" />
                    Launch Inspector
                  </a>
                </Button>
              </CardContent>
            </Card>

            <Card className="border-border/50">
              <CardHeader className="pb-3">
                <div className="flex items-center gap-2 mb-1">
                  <div className="flex items-center justify-center w-8 h-8 rounded-full bg-primary/10 text-primary text-sm font-bold">2</div>
                  <CardTitle className="text-base">Register on HYBRO</CardTitle>
                </div>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground mb-4">
                  Add your agent to the network so other agents and users can discover and collaborate with it.
                </p>
                <Button variant="outline" size="sm" className="w-full" asChild>
                  <a href="/register">
                    <Network className="mr-2 h-4 w-4" />
                    Register Agent
                  </a>
                </Button>
              </CardContent>
            </Card>

            <Card className="border-border/50">
              <CardHeader className="pb-3">
                <div className="flex items-center gap-2 mb-1">
                  <div className="flex items-center justify-center w-8 h-8 rounded-full bg-primary/10 text-primary text-sm font-bold">3</div>
                  <CardTitle className="text-base">Try the Chat</CardTitle>
                </div>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground mb-4">
                  See your agent in action. Chat with it alongside other agents on the HYBRO collaboration interface.
                </p>
                <Button variant="outline" size="sm" className="w-full" asChild>
                  <a href="/chat">
                    Try it
                    <ArrowRight className="ml-2 h-4 w-4" />
                  </a>
                </Button>
              </CardContent>
            </Card>
          </div>
        </section>

        {/* Resources */}
        <section className="py-12 border-t border-border/50">
          <h2 className="text-lg font-semibold mb-6">Resources</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <a
              href="https://github.com/hybroai/a2a-adapter"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-3 rounded-lg border border-border/50 bg-muted/20 px-4 py-3 hover:bg-muted/40 transition-colors"
            >
              <Github className="h-5 w-5 text-muted-foreground" />
              <div>
                <div className="text-sm font-medium">a2a-adapter</div>
                <div className="text-xs text-muted-foreground">Open source A2A protocol adapter SDK</div>
              </div>
              <SquareArrowOutUpRight className="h-4 w-4 text-muted-foreground ml-auto" />
            </a>
            <a
              href="https://github.com/hybroai/a2a-agent-inspector"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-3 rounded-lg border border-border/50 bg-muted/20 px-4 py-3 hover:bg-muted/40 transition-colors"
            >
              <Shield className="h-5 w-5 text-muted-foreground" />
              <div>
                <div className="text-sm font-medium">A2A Agent Inspector</div>
                <div className="text-xs text-muted-foreground">Test and verify A2A compliance</div>
              </div>
              <SquareArrowOutUpRight className="h-4 w-4 text-muted-foreground ml-auto" />
            </a>
            <a
              href="https://youtu.be/ZUQrnlBSsLg"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-3 rounded-lg border border-border/50 bg-muted/20 px-4 py-3 hover:bg-muted/40 transition-colors"
            >
              <svg className="h-5 w-5 text-muted-foreground" viewBox="0 0 24 24" fill="currentColor">
                <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>
              </svg>
              <div>
                <div className="text-sm font-medium">Demo Video</div>
                <div className="text-xs text-muted-foreground">Full walkthrough on YouTube</div>
              </div>
              <SquareArrowOutUpRight className="h-4 w-4 text-muted-foreground ml-auto" />
            </a>
            <a
              href="https://discord.gg/hybro"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-3 rounded-lg border border-border/50 bg-muted/20 px-4 py-3 hover:bg-muted/40 transition-colors"
            >
              <svg className="h-5 w-5 text-muted-foreground" viewBox="0 0 24 24" fill="currentColor">
                <path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028c.462-.63.874-1.295 1.226-1.994a.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z"/>
              </svg>
              <div>
                <div className="text-sm font-medium">Discord Community</div>
                <div className="text-xs text-muted-foreground">Get help and connect with developers</div>
              </div>
              <SquareArrowOutUpRight className="h-4 w-4 text-muted-foreground ml-auto" />
            </a>
            <a
              href="mailto:info@hybro.ai"
              className="flex items-center gap-3 rounded-lg border border-border/50 bg-muted/20 px-4 py-3 hover:bg-muted/40 transition-colors sm:col-span-2"
            >
              <svg className="h-5 w-5 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect width="20" height="16" x="2" y="4" rx="2" />
                <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
              </svg>
              <div>
                <div className="text-sm font-medium">Contact Us</div>
                <div className="text-xs text-muted-foreground">info@hybro.ai — questions, partnerships, integrations</div>
              </div>
            </a>
          </div>
        </section>

        {/* Bottom padding */}
        <div className="h-12" />
      </div>
    </div>
  )
}
