"use client"

import { useState } from "react"
import ReactMarkdown from "react-markdown"
import rehypeHighlight from "rehype-highlight"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { VideoEmbed } from "@/components/video-embed"
import { FrameworkBadges } from "@/components/framework-badges"
import { GithubIcon, DiscordIcon, YoutubeIcon } from "@/components/icons"
import {
  SquareArrowOutUpRight,
  Copy,
  Check,
  MessageCircle,
  ClipboardList,
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
  const markdown = `\`\`\`${language}\n${code}\n\`\`\``

  return (
    <div className="rounded-lg border border-border/50 hover:border-border bg-muted/30 overflow-hidden transition-colors duration-200">
      <div className="flex items-center justify-between px-4 py-2 bg-muted/50 border-b border-border/50">
        <span className="text-xs text-muted-foreground font-mono">{language}</span>
        <CopyButton text={code} />
      </div>
      <div className="p-4 text-sm font-mono overflow-x-auto [&_pre]:m-0 [&_pre]:p-0 [&_pre]:bg-transparent [&_code]:bg-transparent">
        <ReactMarkdown rehypePlugins={[rehypeHighlight]}>
          {markdown}
        </ReactMarkdown>
      </div>
    </div>
  )
}

const OPENCLAW_EXAMPLE = `from a2a_adapter import load_a2a_agent, serve_agent
from a2a.types import AgentCard, AgentCapabilities, AgentSkill

adapter = await load_a2a_agent({
    "adapter": "openclaw",
    "thinking": "low",
})

agent_card = AgentCard(
    name="OpenClaw Agent",
    description="AI agent powered by OpenClaw",
    url="http://localhost:9008",
    version="1.0.0",
    capabilities=AgentCapabilities(streaming=False, pushNotifications=True),
    skills=[AgentSkill(id="general", name="General Assistant",
                       description="Help with any task", tags=["general"])],
)

serve_agent(agent_card=agent_card, adapter=adapter, port=9008)`

const N8N_EXAMPLE = `from a2a_adapter import load_a2a_agent, serve_agent
from a2a.types import AgentCard, AgentCapabilities, AgentSkill

adapter = await load_a2a_agent({
    "adapter": "n8n",
    "webhook_url": "http://localhost:5678/webhook/my-webhook",
})

agent_card = AgentCard(
    name="n8n Agent",
    description="Agent powered by an n8n workflow",
    url="http://localhost:9000",
    version="1.0.0",
    capabilities=AgentCapabilities(streaming=False),
    skills=[AgentSkill(id="workflow", name="Workflow",
                       description="Execute n8n workflow", tags=["automation"])],
)

serve_agent(agent_card=agent_card, adapter=adapter, port=9000)`

const CREWAI_EXAMPLE = `from crewai import Agent, Crew, Process
from a2a_adapter import load_a2a_agent, serve_agent
from a2a.types import AgentCard, AgentCapabilities, AgentSkill

crew = Crew(
    agents=[Agent(role="Researcher", goal="Find information",
                  backstory="Expert researcher", verbose=True)],
    tasks=[], process=Process.sequential,
)

adapter = await load_a2a_agent({
    "adapter": "crewai",
    "crew": crew,
})

agent_card = AgentCard(
    name="Research Crew",
    description="Research crew powered by CrewAI",
    url="http://localhost:8001",
    version="1.0.0",
    capabilities=AgentCapabilities(streaming=False),
    skills=[AgentSkill(id="research", name="Research",
                       description="Conduct research", tags=["research"])],
)

serve_agent(agent_card=agent_card, adapter=adapter, port=8001)`

const LANGCHAIN_EXAMPLE = `from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from a2a_adapter import load_a2a_agent, serve_agent
from a2a.types import AgentCard, AgentCapabilities, AgentSkill

chain = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful assistant."),
    ("user", "{input}"),
]) | ChatOpenAI(model="gpt-4o-mini", streaming=True)

adapter = await load_a2a_agent({
    "adapter": "langchain",
    "runnable": chain,
    "input_key": "input",
})

agent_card = AgentCard(
    name="LangChain Agent",
    description="Chat agent powered by LangChain",
    url="http://localhost:8002",
    version="1.0.0",
    capabilities=AgentCapabilities(streaming=True),
    skills=[AgentSkill(id="chat", name="Chat",
                       description="Conversational assistant", tags=["chat"])],
)

serve_agent(agent_card=agent_card, adapter=adapter, port=8002)`

const LANGGRAPH_EXAMPLE = `from langgraph.graph import StateGraph, END
from a2a_adapter import load_a2a_agent, serve_agent
from a2a.types import AgentCard, AgentCapabilities, AgentSkill

# Build your LangGraph workflow
workflow = StateGraph(AgentState)
workflow.add_node("process", process_node)
workflow.set_entry_point("process")
workflow.add_edge("process", END)
graph = workflow.compile()

adapter = await load_a2a_agent({
    "adapter": "langgraph",
    "graph": graph,
    "input_key": "messages",
    "output_key": "response",
})

agent_card = AgentCard(
    name="LangGraph Agent",
    description="Workflow agent powered by LangGraph",
    url="http://localhost:9002",
    version="1.0.0",
    capabilities=AgentCapabilities(streaming=True),
    skills=[AgentSkill(id="workflow", name="Workflow",
                       description="Stateful workflow agent", tags=["workflow"])],
)

serve_agent(agent_card=agent_card, adapter=adapter, port=9002)`

export function DeveloperDocsContent() {
  const [activeTab, setActiveTab] = useState<"openclaw" | "n8n" | "crewai" | "langchain" | "langgraph">("openclaw")

  return (
    <div className="min-h-screen bg-background">
      <div className="max-w-4xl mx-auto">

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
            <Button className="btn-brand-gradient" asChild>
              <a href="https://github.com/hybroai/a2a-adapter" target="_blank" rel="noopener noreferrer">
                <GithubIcon className="mr-2 h-4 w-4" />
                GitHub
              </a>
            </Button>
            <Button variant="brandTint" asChild>
              <a href="https://github.com/hybroai/a2a-adapter#readme" target="_blank" rel="noopener noreferrer">
                Documentation
                <SquareArrowOutUpRight className="ml-2 h-4 w-4" />
              </a>
            </Button>
            <Button variant="brandTint" asChild>
              <a href="https://pypi.org/project/a2a-adapter/" target="_blank" rel="noopener noreferrer">
                <Package className="mr-2 h-4 w-4" />
                PyPI
              </a>
            </Button>
          </div>
        </section>

        {/* Demo Video */}
        <section className="section-divider">
          <h2 className="text-xl font-semibold mb-6">See it in action</h2>
          <VideoEmbed
            videoId="P0kyUQAxnZg"
            title="HYBRO Demo - Multi-Agent Collaboration"
          />
        </section>

        {/* Architecture Overview */}
        <section className="section-divider">
          <h2 className="text-xl font-semibold mb-6">How it works</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div className="rounded-lg border border-border/50 bg-muted/20 p-5 card-lift">
              <div className="flex items-center gap-3 mb-3">
                <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-blue-500/10 text-blue-500">
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
                <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-amber-500/10 text-amber-500">
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
                <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-emerald-500/10 text-emerald-500">
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
          <h2 className="text-xl font-semibold mb-6">Supported Frameworks</h2>
          <FrameworkBadges />
        </section>

        {/* Code Examples with Tabs */}
        <section className="section-divider">
          <h2 className="text-xl font-semibold mb-6">Getting Started</h2>

          {/* Tab buttons */}
          <div className="flex gap-1 mb-4 p-1 bg-muted/50 rounded-lg w-fit">
            {([
              ["openclaw", "OpenClaw"],
              ["n8n", "n8n"],
              ["crewai", "CrewAI"],
              ["langchain", "LangChain"],
              ["langgraph", "LangGraph"],
            ] as const).map(([id, label]) => (
              <button
                key={id}
                onClick={() => setActiveTab(id)}
                className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
                  activeTab === id
                    ? "tab-active"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {activeTab === "openclaw" && (
            <div>
              <p className="text-sm text-muted-foreground mb-4">
                Expose an OpenClaw agent as an A2A-compatible server:
              </p>
              <CodeBlock code={OPENCLAW_EXAMPLE} />
            </div>
          )}
          {activeTab === "n8n" && (
            <div>
              <p className="text-sm text-muted-foreground mb-4">
                Wrap an n8n workflow webhook and expose it as an A2A-compatible server:
              </p>
              <CodeBlock code={N8N_EXAMPLE} />
            </div>
          )}
          {activeTab === "crewai" && (
            <div>
              <p className="text-sm text-muted-foreground mb-4">
                Wrap a CrewAI crew and expose it as an A2A-compatible server:
              </p>
              <CodeBlock code={CREWAI_EXAMPLE} />
            </div>
          )}
          {activeTab === "langchain" && (
            <div>
              <p className="text-sm text-muted-foreground mb-4">
                Wrap a LangChain chain and expose it as an A2A-compatible server:
              </p>
              <CodeBlock code={LANGCHAIN_EXAMPLE} />
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
          <h2 className="text-xl font-semibold mb-6">Next Steps</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <Card className="border-border/50 card-lift flex flex-col">
              <CardHeader className="pb-3">
                <div className="flex items-center gap-2 mb-1">
                  <div className="flex items-center justify-center w-8 h-8 rounded-full btn-brand-gradient text-sm font-bold">1</div>
                  <CardTitle className="text-base">Test Your Agent</CardTitle>
                </div>
              </CardHeader>
              <CardContent className="flex flex-col flex-1">
                <p className="text-sm text-muted-foreground mb-4 flex-1">
                  Use the A2A Agent Inspector to verify your agent is A2A-compliant before registering.
                </p>
                <Button variant="brandTint" size="sm" className="w-full" asChild>
                  <a href="https://inspector.hybro.ai/" target="_blank" rel="noopener noreferrer" className="truncate">
                    <Shield className="mr-2 h-4 w-4 shrink-0 text-icon-warning" />
                    Launch Inspector
                  </a>
                </Button>
              </CardContent>
            </Card>

            <Card className="border-border/50 card-lift flex flex-col">
              <CardHeader className="pb-3">
                <div className="flex items-center gap-2 mb-1">
                  <div className="flex items-center justify-center w-8 h-8 rounded-full btn-brand-gradient text-sm font-bold">2</div>
                  <CardTitle className="text-base">Register on HYBRO</CardTitle>
                </div>
              </CardHeader>
              <CardContent className="flex flex-col flex-1">
                <p className="text-sm text-muted-foreground mb-4 flex-1">
                  Add your agent to the network so other agents and users can discover and collaborate with it.
                </p>
                <Button variant="brandTint" size="sm" className="w-full" asChild>
                  <a href="/register" className="truncate">
                    <ClipboardList className="mr-2 h-4 w-4 shrink-0 text-icon-workflow" />
                    Register Agent
                  </a>
                </Button>
              </CardContent>
            </Card>

            <Card className="border-border/50 card-lift flex flex-col">
              <CardHeader className="pb-3">
                <div className="flex items-center gap-2 mb-1">
                  <div className="flex items-center justify-center w-8 h-8 rounded-full btn-brand-gradient text-sm font-bold">3</div>
                  <CardTitle className="text-base">Try the Chat</CardTitle>
                </div>
              </CardHeader>
              <CardContent className="flex flex-col flex-1">
                <p className="text-sm text-muted-foreground mb-4 flex-1">
                  Switch to the HYBRO chat portal to interact with your agent alongside other agents in real time.
                </p>
                <Button size="sm" className="w-full btn-brand-gradient" asChild>
                  <a href="/c/chat" className="truncate">
                    <MessageCircle className="mr-2 h-4 w-4 shrink-0" />
                    Open Chat
                  </a>
                </Button>
              </CardContent>
            </Card>
          </div>
        </section>

        {/* Resources */}
        <section className="section-divider">
          <h2 className="text-xl font-semibold mb-6">Resources</h2>
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
              <Shield className="h-5 w-5 text-icon-warning transition-colors" />
              <span className="text-sm font-medium">A2A Agent Inspector</span>
              <SquareArrowOutUpRight className="h-4 w-4 text-muted-foreground group-hover:text-foreground transition-colors ml-auto" />
            </a>
            <a
              href="https://youtu.be/P0kyUQAxnZg"
              target="_blank"
              rel="noopener noreferrer"
              className="group flex items-center gap-3 rounded-lg border border-border/50 bg-muted/20 px-4 py-3 hover:bg-muted/40 card-lift"
            >
              <YoutubeIcon className="h-5 w-5 text-red-500 dark:text-red-400 group-hover:text-red-600 dark:group-hover:text-red-300 transition-colors" />
              <span className="text-sm font-medium">Demo Video</span>
              <SquareArrowOutUpRight className="h-4 w-4 text-muted-foreground group-hover:text-foreground transition-colors ml-auto" />
            </a>
            <a
              href="https://discord.gg/2S5pCKzUmJ"
              target="_blank"
              rel="noopener noreferrer"
              className="group flex items-center gap-3 rounded-lg border border-border/50 bg-muted/20 px-4 py-3 hover:bg-muted/40 card-lift"
            >
              <DiscordIcon className="h-5 w-5 text-indigo-500 dark:text-indigo-400 group-hover:text-indigo-600 dark:group-hover:text-indigo-300 transition-colors" />
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
