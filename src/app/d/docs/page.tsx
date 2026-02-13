"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { VideoEmbed } from "@/components/video-embed"
import { FrameworkBadges } from "@/components/framework-badges"
import { GithubIcon, DiscordIcon, YoutubeIcon } from "@/components/icons"
import {
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
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

function CopyButton({ text, className = "" }: { text: string; className?: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <TooltipProvider delayDuration={0}>
      <Tooltip open={copied || undefined}>
        <TooltipTrigger asChild>
          <button
            onClick={handleCopy}
            className={`inline-flex items-center p-1 rounded text-muted-foreground hover:text-foreground transition-colors ${className}`}
            aria-label="Copy to clipboard"
          >
            {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
          </button>
        </TooltipTrigger>
        <TooltipContent>
          <p>{copied ? "Copied" : "Copy"}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

function CodeBlock({ code, language = "python" }: { code: string; language?: string }) {
  return (
    <div className="rounded-lg border border-border/50 hover:border-border bg-muted/30 overflow-hidden transition-colors duration-200">
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
          <div className="inline-flex items-center gap-3 bg-muted/50 border border-border/50 border-l-2 border-l-primary rounded-lg px-5 py-3 mb-6">
            <Terminal className="h-4 w-4 text-muted-foreground" />
            <code className="font-mono text-sm font-medium">pip install a2a-adapter</code>
            <CopyButton text="pip install a2a-adapter" />
          </div>

          {/* Action buttons */}
          <div className="flex flex-wrap gap-3">
            <Button className="btn-brand-gradient" asChild>
              <a href="https://github.com/hybroai/a2a-adapter" target="_blank" rel="noopener noreferrer">
                <GithubIcon className="mr-2 h-4 w-4" />
                GitHub
              </a>
            </Button>
            <Button variant="outline" className="btn-brand-tint" asChild>
              <a href="https://github.com/hybroai/a2a-adapter#readme" target="_blank" rel="noopener noreferrer">
                Documentation
                <SquareArrowOutUpRight className="ml-2 h-4 w-4" />
              </a>
            </Button>
            <Button variant="outline" className="btn-brand-tint" asChild>
              <a href="https://pypi.org/project/a2a-adapter/" target="_blank" rel="noopener noreferrer">
                <Package className="mr-2 h-4 w-4" />
                PyPI
              </a>
            </Button>
          </div>
        </section>

        {/* Demo Video */}
        <section className="section-divider">
          <h2 className="text-xl font-semibold mb-6 border-l-2 border-primary pl-3">See it in action</h2>
          <VideoEmbed
            videoId="ZUQrnlBSsLg"
            title="HYBRO Demo - Multi-Agent Collaboration"
          />
        </section>

        {/* Architecture Overview */}
        <section className="section-divider">
          <h2 className="text-xl font-semibold mb-6 border-l-2 border-primary pl-3">How it works</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div className="rounded-lg border border-border/50 bg-muted/20 p-5 card-lift">
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
            <div className="rounded-lg border border-border/50 bg-muted/20 p-5 card-lift">
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
            <div className="rounded-lg border border-border/50 bg-muted/20 p-5 card-lift">
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
        <section className="section-divider">
          <h2 className="text-xl font-semibold mb-6 border-l-2 border-primary pl-3">Supported Frameworks</h2>
          <FrameworkBadges />
        </section>

        {/* Code Examples with Tabs */}
        <section className="section-divider">
          <h2 className="text-xl font-semibold mb-6 border-l-2 border-primary pl-3">Getting Started</h2>

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
        <section className="section-divider">
          <h2 className="text-xl font-semibold mb-6 border-l-2 border-primary pl-3">Next Steps</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <Card className="border-border/50 card-lift">
              <CardHeader className="pb-3">
                <div className="flex items-center gap-2 mb-1">
                  <div className="flex items-center justify-center w-8 h-8 rounded-full btn-brand-gradient text-sm font-bold">1</div>
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

            <Card className="border-border/50 card-lift">
              <CardHeader className="pb-3">
                <div className="flex items-center gap-2 mb-1">
                  <div className="flex items-center justify-center w-8 h-8 rounded-full btn-brand-gradient text-sm font-bold">2</div>
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

            <Card className="border-border/50 card-lift">
              <CardHeader className="pb-3">
                <div className="flex items-center gap-2 mb-1">
                  <div className="flex items-center justify-center w-8 h-8 rounded-full btn-brand-gradient text-sm font-bold">3</div>
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
        <section className="section-divider">
          <h2 className="text-xl font-semibold mb-6 border-l-2 border-primary pl-3">Resources</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <a
              href="https://github.com/hybroai/a2a-adapter"
              target="_blank"
              rel="noopener noreferrer"
              className="group flex items-center gap-3 rounded-lg border border-border/50 bg-muted/20 px-4 py-3 hover:bg-muted/40 card-lift"
            >
              <GithubIcon className="h-5 w-5 text-muted-foreground group-hover:text-foreground transition-colors" />
              <span className="text-sm font-medium">a2a-adapter</span>
              <SquareArrowOutUpRight className="h-4 w-4 text-muted-foreground group-hover:text-foreground transition-colors ml-auto" />
            </a>
            <a
              href="https://inspector.hybro.ai/"
              target="_blank"
              rel="noopener noreferrer"
              className="group flex items-center gap-3 rounded-lg border border-border/50 bg-muted/20 px-4 py-3 hover:bg-muted/40 card-lift"
            >
              <Shield className="h-5 w-5 text-muted-foreground group-hover:text-foreground transition-colors" />
              <span className="text-sm font-medium">A2A Agent Inspector</span>
              <SquareArrowOutUpRight className="h-4 w-4 text-muted-foreground group-hover:text-foreground transition-colors ml-auto" />
            </a>
            <a
              href="https://youtu.be/ZUQrnlBSsLg"
              target="_blank"
              rel="noopener noreferrer"
              className="group flex items-center gap-3 rounded-lg border border-border/50 bg-muted/20 px-4 py-3 hover:bg-muted/40 card-lift"
            >
              <YoutubeIcon className="h-5 w-5 text-muted-foreground group-hover:text-foreground transition-colors" />
              <span className="text-sm font-medium">Demo Video</span>
              <SquareArrowOutUpRight className="h-4 w-4 text-muted-foreground group-hover:text-foreground transition-colors ml-auto" />
            </a>
            <a
              href="https://discord.gg/2S5pCKzUmJ"
              target="_blank"
              rel="noopener noreferrer"
              className="group flex items-center gap-3 rounded-lg border border-border/50 bg-muted/20 px-4 py-3 hover:bg-muted/40 card-lift"
            >
              <DiscordIcon className="h-5 w-5 text-muted-foreground group-hover:text-foreground transition-colors" />
              <span className="text-sm font-medium">Discord Community</span>
              <SquareArrowOutUpRight className="h-4 w-4 text-muted-foreground group-hover:text-foreground transition-colors ml-auto" />
            </a>
            <a
              href="mailto:info@hybro.ai"
              className="group flex items-center gap-3 rounded-lg border border-border/50 bg-muted/20 px-4 py-3 hover:bg-muted/40 hover:shadow-sm hover:-translate-y-0.5 transition-all duration-200 sm:col-span-2"
            >
              <svg className="h-5 w-5 text-muted-foreground group-hover:text-foreground transition-colors" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect width="20" height="16" x="2" y="4" rx="2" />
                <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
              </svg>
              <div>
                <div className="text-sm font-medium">Contact Us</div>
                <div className="text-xs text-muted-foreground">info@hybro.ai</div>
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
