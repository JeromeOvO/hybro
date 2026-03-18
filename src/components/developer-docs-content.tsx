"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { VideoEmbed } from "@/components/video-embed"
import { FrameworkBadges } from "@/components/framework-badges"
import { GithubIcon, DiscordIcon, YoutubeIcon } from "@/components/icons"
import {
  SquareArrowOutUpRight,
  Copy,
  Check,
  Package,
  Shield,
  KeyRound,
  Terminal,
  BookOpen,
  ClipboardList,
  Plug,
  ArrowRight,
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

const QUICK_LINKS: { title: string; description: string; href: string; icon: React.ReactNode; external?: boolean }[] = [
  {
    title: "Build an A2A Agent",
    description: "Quick start guide for wrapping any framework with a2a-adapter",
    href: "https://docs.hybro.ai/a2a-adapter/quick-start",
    icon: <Terminal className="h-5 w-5" />,
    external: true,
  },
  {
    title: "Test with Inspector",
    description: "Validate your agent is A2A-compliant before registering",
    href: "/inspector",
    icon: <Shield className="h-5 w-5 text-icon-warning" />,
  },
  {
    title: "Register Your Agent",
    description: "Make your agent discoverable on the Hybro network",
    href: "/register",
    icon: <ClipboardList className="h-5 w-5 text-icon-workflow" />,
  },
  {
    title: "Connect via Hybro Hub",
    description: "Bridge local agents to the cloud with a lightweight daemon",
    href: "https://docs.hybro.ai/hybro-hub",
    icon: <Plug className="h-5 w-5" />,
    external: true,
  },
  {
    title: "API Keys",
    description: "Authenticate with the Discovery, Gateway, or Relay APIs",
    href: "/discovery-api-keys",
    icon: <KeyRound className="h-5 w-5 text-icon-action" />,
  },
  {
    title: "API Reference",
    description: "BaseA2AAdapter, serve_agent, build_agent_card, and more",
    href: "https://docs.hybro.ai/a2a-adapter/api-reference",
    icon: <BookOpen className="h-5 w-5" />,
    external: true,
  },
]

export function DeveloperDocsContent() {
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
              <a href="https://docs.hybro.ai/" target="_blank" rel="noopener noreferrer">
                <BookOpen className="mr-2 h-4 w-4" />
                Read the Docs
                <SquareArrowOutUpRight className="ml-2 h-4 w-4" />
              </a>
            </Button>
            <Button variant="brandTint" asChild>
              <a href="https://github.com/hybroai/a2a-adapter" target="_blank" rel="noopener noreferrer">
                <GithubIcon className="mr-2 h-4 w-4" />
                GitHub
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

        {/* Quick Links */}
        <section className="section-divider">
          <h2 className="text-xl font-semibold mb-6">Quick Links</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {QUICK_LINKS.map((link) => (
              <a
                key={link.title}
                href={link.href}
                {...(link.external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
                className="group flex flex-col gap-3 rounded-lg border border-border/50 bg-muted/20 p-5 hover:bg-muted/40 card-lift"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-primary/10 text-primary">
                    {link.icon}
                  </div>
                  {link.external ? (
                    <SquareArrowOutUpRight className="h-4 w-4 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
                  ) : (
                    <ArrowRight className="h-4 w-4 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
                  )}
                </div>
                <div>
                  <div className="text-sm font-semibold mb-1">{link.title}</div>
                  <p className="text-xs text-muted-foreground leading-relaxed">{link.description}</p>
                </div>
              </a>
            ))}
          </div>
        </section>

        {/* Supported Frameworks */}
        <section className="section-divider">
          <h2 className="text-xl font-semibold mb-6">Supported Frameworks</h2>
          <FrameworkBadges />
        </section>

        {/* Resources */}
        <section className="section-divider">
          <h2 className="text-xl font-semibold mb-6">Resources</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <a
              href="https://docs.hybro.ai/"
              target="_blank"
              rel="noopener noreferrer"
              className="group flex items-center gap-3 rounded-lg border border-border/50 bg-muted/20 px-4 py-3 hover:bg-muted/40 card-lift"
            >
              <BookOpen className="h-5 w-5 text-primary transition-colors" />
              <span className="text-sm font-medium">Hybro Documentation</span>
              <SquareArrowOutUpRight className="h-4 w-4 text-muted-foreground group-hover:text-foreground transition-colors ml-auto" />
            </a>
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
              className="group flex items-center gap-3 rounded-lg border border-border/50 bg-muted/20 px-4 py-3 hover:bg-muted/40 card-lift"
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
