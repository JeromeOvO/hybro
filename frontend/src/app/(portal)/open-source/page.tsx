'use client'

import React, { useState } from "react"
import Link from "next/link"
import {
  ArrowRight,
  Check,
  Copy,
  Terminal,
  Shield,
  Zap,
  Network,
  Code2,
  Sparkles,
  GitBranch,
  ExternalLink,
  BookOpen,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { FadeInSection } from "@/components/fade-in-section"
import { VideoEmbed } from "@/components/video-embed"
import { GithubIcon, DiscordIcon } from "@/components/icons"
import { PortalFooter } from "@/components/portal/portal-footer"
import { routes } from "@/lib/routes"

const QUICK_START_COMMANDS = {
  script: "curl -fsSL https://raw.githubusercontent.com/hybroai/hybro/main/install.sh | sh",
  docker: "git clone https://github.com/hybroai/hybro.git && cd hybro && docker compose up -d --build",
}

export default function OpenSourcePage() {
  const [activeTab, setActiveTab] = useState<"script" | "docker">("script")
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    navigator.clipboard.writeText(QUICK_START_COMMANDS[activeTab])
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="relative min-h-screen bg-background text-foreground overflow-x-hidden">
      {/* Background Decorators */}
      <div aria-hidden="true" className="pointer-events-none fixed inset-0 z-0">
        <div className="absolute inset-x-0 top-0 h-[560px] bg-[radial-gradient(ellipse_at_50%_0%,hsl(var(--color-hybro-hy)/0.15),transparent_38%),radial-gradient(ellipse_at_80%_10%,hsl(var(--color-hybro-bro)/0.12),transparent_35%)]" />
        <div className="absolute left-[-14rem] top-[24rem] h-[32rem] w-[32rem] rounded-full bg-[hsl(var(--color-hybro-hy)/0.06)] blur-2xl md:blur-3xl" />
        <div className="absolute right-[-16rem] top-[44rem] h-[36rem] w-[36rem] rounded-full bg-[hsl(var(--color-hybro-bro)/0.06)] blur-2xl md:blur-3xl" />
        <div
          className="absolute inset-0 opacity-[0.12]"
          style={{
            backgroundImage:
              "linear-gradient(hsl(var(--color-border) / 0.3) 1px, transparent 1px), linear-gradient(90deg, hsl(var(--color-border) / 0.3) 1px, transparent 1px)",
            backgroundSize: "48px 48px",
            maskImage: "linear-gradient(to bottom, black 0%, black 20%, transparent 60%, black 80%, transparent 100%)",
          }}
        />
      </div>

      <div className="relative z-10 max-w-6xl mx-auto px-4 sm:px-8">
        {/* Hero Section */}
        <section className="pt-16 md:pt-24 pb-12 text-center animate-fade-up">
          {/* Badge */}
          <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs font-semibold bg-muted/60 border border-border/60 text-muted-foreground mb-6 shadow-xs backdrop-blur-md">
            <span className="w-2 h-2 rounded-full bg-[hsl(var(--color-hybro-hy))] animate-pulse" />
            <span className="text-[hsl(var(--color-hybro-hy))] font-bold">HYBRO CORE v0.2</span>
            <span className="text-border">|</span>
            <span>OPEN SOURCE RELEASE</span>
          </div>

          <h1 className="text-4xl sm:text-5xl md:text-6xl font-bold tracking-tight mb-6 max-w-4xl mx-auto leading-[1.15]">
            Local-First Multi-Agent
            <br />
            <span className="text-brand-gradient">Orchestration Engine</span>
          </h1>

          <p className="text-lg md:text-xl text-muted-foreground max-w-2xl mx-auto mb-10 leading-relaxed text-balance">
            Run, manage, and coordinate intelligent AI agents locally with complete data privacy, zero configuration overhead, and native A2A protocol support.
          </p>

          {/* Action CTAs */}
          <div className="flex flex-wrap items-center justify-center gap-4 mb-12">
            <Button size="lg" asChild className="btn-brand-gradient shadow-md px-6">
              <Link href={routes.chat}>
                Launch App
                <ArrowRight className="ml-2 h-4 w-4" />
              </Link>
            </Button>
            <Button size="lg" variant="outline" asChild className="px-6 border-border/80">
              <a href="https://github.com/hybroai/hybro" target="_blank" rel="noopener noreferrer">
                <GithubIcon className="mr-2 h-4 w-4" />
                GitHub Repo
                <ExternalLink className="ml-1.5 h-3.5 w-3.5 opacity-60" />
              </a>
            </Button>
            <Button size="lg" variant="brandTint" asChild className="px-6">
              <a href="https://docs.hybro.ai" target="_blank" rel="noopener noreferrer">
                <BookOpen className="mr-2 h-4 w-4" />
                Docs
              </a>
            </Button>
          </div>

          {/* Quick Start Terminal Widget */}
          <div className="max-w-2xl mx-auto rounded-xl border border-border/60 bg-card/80 backdrop-blur-md overflow-hidden shadow-xl text-left">
            <div className="flex items-center justify-between px-4 py-2.5 bg-muted/40 border-b border-border/50">
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 rounded-full bg-red-500/80" />
                <div className="w-3 h-3 rounded-full bg-yellow-500/80" />
                <div className="w-3 h-3 rounded-full bg-green-500/80" />
                <span className="ml-2 text-xs font-mono text-muted-foreground flex items-center gap-1.5">
                  <Terminal className="h-3.5 w-3.5 text-[hsl(var(--color-hybro-hy))]" />
                  Quick Start
                </span>
              </div>
              <div className="flex items-center gap-1 bg-background/60 p-0.5 rounded-md border border-border/40 text-xs">
                <button
                  onClick={() => setActiveTab("script")}
                  className={`px-2.5 py-1 rounded transition-colors ${
                    activeTab === "script"
                      ? "bg-muted text-foreground font-medium"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  install.sh
                </button>
                <button
                  onClick={() => setActiveTab("docker")}
                  className={`px-2.5 py-1 rounded transition-colors ${
                    activeTab === "docker"
                      ? "bg-muted text-foreground font-medium"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  Docker Compose
                </button>
              </div>
            </div>
            <div className="p-4 flex items-center justify-between gap-3 bg-black/40 font-mono text-xs md:text-sm text-foreground overflow-x-auto">
              <code className="text-[hsl(var(--color-hybro-hy))] whitespace-nowrap">
                <span className="text-muted-foreground select-none">$ </span>
                {QUICK_START_COMMANDS[activeTab]}
              </code>
              <Button
                size="sm"
                variant="ghost"
                onClick={handleCopy}
                className="h-8 px-2.5 text-muted-foreground hover:text-foreground shrink-0"
              >
                {copied ? <Check className="h-4 w-4 text-green-400" /> : <Copy className="h-4 w-4" />}
                <span className="sr-only">Copy command</span>
              </Button>
            </div>
          </div>
        </section>

        {/* Feature Grid */}
        <section className="py-16 border-t border-border/40">
          <FadeInSection>
            <div className="text-center mb-12">
              <h2 className="text-2xl md:text-4xl font-bold mb-3 tracking-tight">
                Designed for Developer Autonomy
              </h2>
              <p className="text-muted-foreground max-w-xl mx-auto">
                Everything you need to orchestrate autonomous AI agent workflows locally or at scale.
              </p>
            </div>
          </FadeInSection>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            <FadeInSection delay={100}>
              <div className="h-full p-6 rounded-2xl border border-border/50 bg-gradient-to-b from-card/80 to-card/40 hover:border-[hsl(var(--color-hybro-hy)/0.4)] transition-all duration-300 shadow-xs flex flex-col">
                <div className="w-10 h-10 rounded-xl bg-[hsl(var(--color-hybro-hy)/0.12)] flex items-center justify-center mb-4">
                  <Shield className="h-5 w-5 text-[hsl(var(--color-hybro-hy))]" />
                </div>
                <h3 className="text-lg font-semibold mb-2">Local-First & Private</h3>
                <p className="text-sm text-muted-foreground leading-relaxed flex-1">
                  Run completely offline with 100% data sovereignty. No telemetry, external tracking, or mandatory cloud lock-in.
                </p>
              </div>
            </FadeInSection>

            <FadeInSection delay={150}>
              <div className="h-full p-6 rounded-2xl border border-border/50 bg-gradient-to-b from-card/80 to-card/40 hover:border-[hsl(var(--color-hybro-hy)/0.4)] transition-all duration-300 shadow-xs flex flex-col">
                <div className="w-10 h-10 rounded-xl bg-[hsl(var(--color-hybro-bro)/0.12)] flex items-center justify-center mb-4">
                  <Terminal className="h-5 w-5 text-[hsl(var(--color-hybro-bro))]" />
                </div>
                <h3 className="text-lg font-semibold mb-2">Zero-Config Dev Mode</h3>
                <p className="text-sm text-muted-foreground leading-relaxed flex-1">
                  Get up and running instantly with mocked developer credentials, pre-configured environment templates, and zero hassle.
                </p>
              </div>
            </FadeInSection>

            <FadeInSection delay={200}>
              <div className="h-full p-6 rounded-2xl border border-border/50 bg-gradient-to-b from-card/80 to-card/40 hover:border-[hsl(var(--color-hybro-hy)/0.4)] transition-all duration-300 shadow-xs flex flex-col">
                <div className="w-10 h-10 rounded-xl bg-[hsl(var(--color-hybro-hy)/0.12)] flex items-center justify-center mb-4">
                  <Network className="h-5 w-5 text-[hsl(var(--color-hybro-hy))]" />
                </div>
                <h3 className="text-lg font-semibold mb-2">Multi-Agent Rooms</h3>
                <p className="text-sm text-muted-foreground leading-relaxed flex-1">
                  Create execution rooms where multiple specialized agents collaborate, debate, and solve multi-step problems together.
                </p>
              </div>
            </FadeInSection>

            <FadeInSection delay={250}>
              <div className="h-full p-6 rounded-2xl border border-border/50 bg-gradient-to-b from-card/80 to-card/40 hover:border-[hsl(var(--color-hybro-hy)/0.4)] transition-all duration-300 shadow-xs flex flex-col">
                <div className="w-10 h-10 rounded-xl bg-[hsl(var(--color-hybro-bro)/0.12)] flex items-center justify-center mb-4">
                  <GitBranch className="h-5 w-5 text-[hsl(var(--color-hybro-bro))]" />
                </div>
                <h3 className="text-lg font-semibold mb-2">A2A Protocol Standard</h3>
                <p className="text-sm text-muted-foreground leading-relaxed flex-1">
                  Connect agents built in any framework (LangChain, AutoGen, CrewAI, or custom code) via the Agent2Agent specification.
                </p>
              </div>
            </FadeInSection>

            <FadeInSection delay={300}>
              <div className="h-full p-6 rounded-2xl border border-border/50 bg-gradient-to-b from-card/80 to-card/40 hover:border-[hsl(var(--color-hybro-hy)/0.4)] transition-all duration-300 shadow-xs flex flex-col">
                <div className="w-10 h-10 rounded-xl bg-[hsl(var(--color-hybro-hy)/0.12)] flex items-center justify-center mb-4">
                  <Code2 className="h-5 w-5 text-[hsl(var(--color-hybro-hy))]" />
                </div>
                <h3 className="text-lg font-semibold mb-2">Modular Core Architecture</h3>
                <p className="text-sm text-muted-foreground leading-relaxed flex-1">
                  Powered by a FastAPI orchestration engine, Redis event pub/sub, MongoDB persistence, and a sleek Next.js 16 UI.
                </p>
              </div>
            </FadeInSection>

            <FadeInSection delay={350}>
              <div className="h-full p-6 rounded-2xl border border-border/50 bg-gradient-to-b from-card/80 to-card/40 hover:border-[hsl(var(--color-hybro-hy)/0.4)] transition-all duration-300 shadow-xs flex flex-col">
                <div className="w-10 h-10 rounded-xl bg-[hsl(var(--color-hybro-bro)/0.12)] flex items-center justify-center mb-4">
                  <Sparkles className="h-5 w-5 text-[hsl(var(--color-hybro-bro))]" />
                </div>
                <h3 className="text-lg font-semibold mb-2">Human-in-the-Loop</h3>
                <p className="text-sm text-muted-foreground leading-relaxed flex-1">
                  Interactive steering with task approval checkpoints, streaming agent logs, state inspection, and manual overrides.
                </p>
              </div>
            </FadeInSection>
          </div>
        </section>

        {/* 3-Step Workflow */}
        <section className="py-16 border-t border-border/40">
          <FadeInSection>
            <h2 className="text-2xl md:text-3xl font-bold text-center mb-12">
              How to Get Started
            </h2>
          </FadeInSection>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-8 max-w-4xl mx-auto">
            <FadeInSection delay={100}>
              <div className="flex flex-col items-center text-center p-6 rounded-2xl border border-border/40 bg-card/40">
                <div className="w-12 h-12 rounded-full btn-brand-gradient flex items-center justify-center text-base font-bold mb-4 shadow-sm">
                  1
                </div>
                <h3 className="text-lg font-semibold mb-2">Spin Up Engine</h3>
                <p className="text-sm text-muted-foreground">
                  Run the 1-liner script or Docker Compose to start the local backend and frontend services.
                </p>
              </div>
            </FadeInSection>

            <FadeInSection delay={200}>
              <div className="flex flex-col items-center text-center p-6 rounded-2xl border border-border/40 bg-card/40">
                <div className="w-12 h-12 rounded-full btn-brand-gradient flex items-center justify-center text-base font-bold mb-4 shadow-sm">
                  2
                </div>
                <h3 className="text-lg font-semibold mb-2">Register Agents</h3>
                <p className="text-sm text-muted-foreground">
                  Connect your local Python or remote A2A agents with the Agent2Agent adapter SDK.
                </p>
              </div>
            </FadeInSection>

            <FadeInSection delay={300}>
              <div className="flex flex-col items-center text-center p-6 rounded-2xl border border-border/40 bg-card/40">
                <div className="w-12 h-12 rounded-full btn-brand-gradient flex items-center justify-center text-base font-bold mb-4 shadow-sm">
                  3
                </div>
                <h3 className="text-lg font-semibold mb-2">Orchestrate Rooms</h3>
                <p className="text-sm text-muted-foreground">
                  Create multi-agent rooms, prompt your cluster, and monitor live task collaboration.
                </p>
              </div>
            </FadeInSection>
          </div>
        </section>

        {/* Demo Video Section */}
        <section className="py-16 border-t border-border/40 animate-fade-up">
          <h2 className="text-xl font-semibold text-muted-foreground uppercase tracking-wider mb-8 text-center">
            See Hybro in Action
          </h2>
          <VideoEmbed
            videoId="P0kyUQAxnZg"
            title="HYBRO Core Demo - Multi-Agent Collaboration Engine"
          />
        </section>

        {/* Open Source License & Community Footer CTA */}
        <section className="py-16 border-t border-border/40">
          <FadeInSection>
            <div className="rounded-3xl border border-border/60 bg-gradient-to-br from-card via-secondary/20 to-card p-8 md:p-12 text-center max-w-4xl mx-auto shadow-lg relative overflow-hidden">
              <div className="absolute top-0 right-0 p-8 opacity-10 pointer-events-none">
                <Zap className="w-48 h-48 text-[hsl(var(--color-hybro-hy))]" />
              </div>
              <h2 className="text-3xl font-bold mb-4">Join the Open Source Agent Community</h2>
              <p className="text-muted-foreground max-w-xl mx-auto mb-8">
                Hybro Core is released under the permissive Apache License 2.0. We welcome contributions, agent integrations, and feedback.
              </p>
              <div className="flex flex-wrap items-center justify-center gap-4">
                <Button size="lg" asChild className="btn-brand-gradient">
                  <a href="https://github.com/hybroai/hybro" target="_blank" rel="noopener noreferrer">
                    <GithubIcon className="mr-2 h-4 w-4" />
                    Star on GitHub
                  </a>
                </Button>
                <Button size="lg" variant="outline" asChild>
                  <a href="https://discord.gg/2S5pCKzUmJ" target="_blank" rel="noopener noreferrer">
                    <DiscordIcon className="mr-2 h-4 w-4 text-[#7289DA]" />
                    Join Discord
                  </a>
                </Button>
              </div>
            </div>
          </FadeInSection>
        </section>

        <PortalFooter />
      </div>
    </div>
  )
}
