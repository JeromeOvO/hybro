'use client'

import React, { useState, useEffect } from "react"
import Link from "next/link"
import {
  ArrowRight,
  ArrowUp,
  AtSign,
  Check,
  Copy,
  Loader2,
  Paperclip,
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
import { useUser, useAuth } from "@/lib/auth"
import { useChatRoomCreation } from "@/hooks/useChatRoomCreation"
import { FRAMEWORKS } from "@/components/framework-badges"

const QUICK_START_COMMANDS = {
  script: "curl -fsSL https://raw.githubusercontent.com/hybroai/hybro/main/install.sh | sh",
  docker: "git clone https://github.com/hybroai/hybro.git && cd hybro && docker compose up -d --build",
}

const HERO_EXAMPLE_PROMPTS = [
  "Plan a travel and calculate the budget for me",
  "Search creators across all social media platform",
]

const marqueeFrameworks = FRAMEWORKS.filter((fw) => fw.name !== "More ...")

export default function OpenSourcePage() {
  const [activeTab, setActiveTab] = useState<"script" | "docker">("script")
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    navigator.clipboard.writeText(QUICK_START_COMMANDS[activeTab])
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  // Hero prompt input + typewriter placeholder animation
  const [heroInput, setHeroInput] = useState("")
  const [promptIndex, setPromptIndex] = useState(0)
  const [charIndex, setCharIndex] = useState(0)
  const [isDeleting, setIsDeleting] = useState(false)

  useEffect(() => {
    if (heroInput) return // pause animation while the user has typed something

    const current = HERO_EXAMPLE_PROMPTS[promptIndex]
    let delay = isDeleting ? 28 : 55
    if (!isDeleting && charIndex === current.length) delay = 1500
    if (isDeleting && charIndex === 0) delay = 300

    const timer = setTimeout(() => {
      if (!isDeleting && charIndex === current.length) {
        setIsDeleting(true)
      } else if (isDeleting && charIndex === 0) {
        setIsDeleting(false)
        setPromptIndex((p) => (p + 1) % HERO_EXAMPLE_PROMPTS.length)
      } else {
        setCharIndex((c) => c + (isDeleting ? -1 : 1))
      }
    }, delay)

    return () => clearTimeout(timer)
  }, [charIndex, isDeleting, promptIndex, heroInput])

  const { user } = useUser()
  const { getToken } = useAuth()

  const handleRequireAuth = () => {
    window.location.href = `/sign-in?redirect_url=${encodeURIComponent(window.location.pathname)}`
  }

  const { creating, createAndNavigate } = useChatRoomCreation({
    userId: user?.id,
    userName: user?.firstName || user?.username || "User",
    getToken,
    onRequireAuth: handleRequireAuth,
  })

  const handleHeroSend = async () => {
    if (creating) return
    const text = heroInput.trim() || HERO_EXAMPLE_PROMPTS[promptIndex]
    await createAndNavigate(text)
  }

  const handleHeroKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault()
      handleHeroSend()
    }
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
            Launch <span className="text-brand-gradient">a local meeting room</span>
            <br />
            for your agents
          </h1>

          <p className="text-lg md:text-xl text-muted-foreground max-w-2xl mx-auto mb-10 leading-relaxed text-balance">
            Complete data privacy, zero configuration, and native A2A protocol support. All running on your machine.
          </p>

          {/* Hero Prompt Input */}
          <div
            className="max-w-xl mx-auto mb-8 rounded-xl bg-muted shadow-sm overflow-hidden text-left"
            style={{ border: "1px solid var(--conversation-border-light)" }}
          >
            {/* Fake mention chip — decorative only */}
            <div className="flex items-center px-4 pt-3">
              <span className="inline-flex items-center gap-1 text-xs font-medium px-2 py-1 rounded-md bg-primary/10 text-primary">
                <AtSign className="h-3 w-3" />
                Manager Agent
              </span>
            </div>

            <input
              type="text"
              value={heroInput}
              onChange={(e) => setHeroInput(e.target.value)}
              onKeyDown={handleHeroKeyDown}
              placeholder={`${HERO_EXAMPLE_PROMPTS[promptIndex].slice(0, charIndex)}▍`}
              aria-label="Ask Hybro to do something"
              className="w-full min-w-0 bg-transparent border-0 text-[15px] text-foreground placeholder:text-muted-foreground focus:outline-none px-4 py-2"
            />

            {/* Decorative toolbar — visual only, mirrors the real chat composer */}
            <div className="flex items-center justify-between px-3 py-2 border-t border-border/40">
              <div className="flex items-center gap-1">
                <span className="flex items-center justify-center size-8 rounded-md text-muted-foreground/70 cursor-default">
                  <Paperclip className="h-4 w-4" />
                </span>
                <span className="flex items-center justify-center size-8 rounded-md text-muted-foreground/70 cursor-default">
                  <AtSign className="h-4 w-4" />
                </span>
              </div>
              <button
                onClick={handleHeroSend}
                disabled={creating}
                aria-label="Send"
                className={`flex items-center justify-center size-8 rounded-full shrink-0 transition-colors ${
                  creating
                    ? "bg-primary/40 text-primary-foreground/70"
                    : "bg-primary text-primary-foreground hover:bg-primary/90"
                }`}
              >
                {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowUp className="h-4 w-4" />}
              </button>
            </div>
          </div>

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
          <div className="max-w-3xl mx-auto rounded-xl border border-border/60 bg-card/80 backdrop-blur-md overflow-hidden shadow-xl text-left">
            <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 bg-muted/40 border-b border-border/50">
              <span className="text-xs font-mono text-muted-foreground flex items-center gap-1.5">
                <Terminal className="h-3.5 w-3.5 text-[hsl(var(--color-hybro-hy))]" />
                Quick Start
              </span>
              <div className="flex items-center gap-1 bg-background/60 p-1 rounded-lg border border-border/40 text-xs">
                <button
                  onClick={() => setActiveTab("script")}
                  className={`px-3 py-1.5 rounded-md transition-all ${
                    activeTab === "script"
                      ? "bg-[hsl(var(--color-hybro-hy))]/15 text-[hsl(var(--color-hybro-hy))] font-semibold shadow-sm ring-1 ring-[hsl(var(--color-hybro-hy))]/30"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  install.sh
                </button>
                <button
                  onClick={() => setActiveTab("docker")}
                  className={`px-3 py-1.5 rounded-md transition-all ${
                    activeTab === "docker"
                      ? "bg-[hsl(var(--color-hybro-hy))]/15 text-[hsl(var(--color-hybro-hy))] font-semibold shadow-sm ring-1 ring-[hsl(var(--color-hybro-hy))]/30"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  Docker Compose
                </button>
              </div>
            </div>
            <div className="flex items-center gap-2 p-4 bg-black/40 font-mono text-xs md:text-sm text-foreground">
              <div className="flex-1 min-w-0 overflow-x-auto">
                <code className="text-[hsl(var(--color-hybro-hy))] whitespace-nowrap">
                  <span className="text-muted-foreground select-none">$ </span>
                  {QUICK_START_COMMANDS[activeTab]}
                </code>
              </div>
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

          {/* Featured pillars */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
            <FadeInSection delay={100}>
              <div className="h-full p-8 rounded-2xl border border-border/50 bg-gradient-to-br from-card/90 to-card/40 hover:border-[hsl(var(--color-hybro-hy)/0.4)] transition-all duration-300 shadow-xs flex flex-col justify-between">
                <div>
                  <div className="w-11 h-11 rounded-xl bg-[hsl(var(--color-hybro-hy)/0.12)] flex items-center justify-center mb-4">
                    <Shield className="h-5 w-5 text-[hsl(var(--color-hybro-hy))]" />
                  </div>
                  <h3 className="text-xl font-semibold mb-2">Local-First & Private</h3>
                  <p className="text-sm text-muted-foreground leading-relaxed mb-5">
                    100% data sovereignty. Nothing leaves your machine.
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {["Offline-capable", "No telemetry", "Self-hosted"].map((tag) => (
                    <span
                      key={tag}
                      className="text-xs font-medium px-2.5 py-1 rounded-full bg-[hsl(var(--color-hybro-hy)/0.1)] text-[hsl(var(--color-hybro-hy))] border border-[hsl(var(--color-hybro-hy)/0.2)]"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              </div>
            </FadeInSection>

            <FadeInSection delay={150}>
              <div className="h-full p-8 rounded-2xl border border-border/50 bg-gradient-to-br from-card/90 to-card/40 hover:border-[hsl(var(--color-hybro-bro)/0.4)] transition-all duration-300 shadow-xs flex flex-col justify-between">
                <div>
                  <div className="w-11 h-11 rounded-xl bg-[hsl(var(--color-hybro-bro)/0.12)] flex items-center justify-center mb-4">
                    <GitBranch className="h-5 w-5 text-[hsl(var(--color-hybro-bro))]" />
                  </div>
                  <h3 className="text-xl font-semibold mb-2">A2A Protocol Standard</h3>
                  <p className="text-sm text-muted-foreground leading-relaxed mb-5">
                    Any framework, one wire format via the Agent2Agent spec.
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {["LangChain", "AutoGen", "CrewAI", "Custom"].map((tag) => (
                    <span
                      key={tag}
                      className="text-xs font-medium px-2.5 py-1 rounded-full bg-[hsl(var(--color-hybro-bro)/0.1)] text-[hsl(var(--color-hybro-bro))] border border-[hsl(var(--color-hybro-bro)/0.2)]"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              </div>
            </FadeInSection>
          </div>

          {/* Supporting capabilities */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <FadeInSection delay={200}>
              <div className="h-full p-5 rounded-xl border border-border/40 bg-card/40 hover:border-[hsl(var(--color-hybro-bro)/0.3)] transition-all duration-300">
                <div className="w-8 h-8 rounded-lg bg-[hsl(var(--color-hybro-bro)/0.12)] flex items-center justify-center mb-3">
                  <Terminal className="h-4 w-4 text-[hsl(var(--color-hybro-bro))]" />
                </div>
                <h3 className="text-sm font-semibold mb-1">Zero-Config Dev Mode</h3>
                <p className="text-xs text-muted-foreground leading-relaxed">
                  Mocked credentials, ready-made templates. Just run it.
                </p>
              </div>
            </FadeInSection>

            <FadeInSection delay={250}>
              <div className="h-full p-5 rounded-xl border border-border/40 bg-card/40 hover:border-[hsl(var(--color-hybro-hy)/0.3)] transition-all duration-300">
                <div className="w-8 h-8 rounded-lg bg-[hsl(var(--color-hybro-hy)/0.12)] flex items-center justify-center mb-3">
                  <Network className="h-4 w-4 text-[hsl(var(--color-hybro-hy))]" />
                </div>
                <h3 className="text-sm font-semibold mb-1">Multi-Agent Rooms</h3>
                <p className="text-xs text-muted-foreground leading-relaxed">
                  Agents collaborate, debate, and solve problems together.
                </p>
              </div>
            </FadeInSection>

            <FadeInSection delay={300}>
              <div className="h-full p-5 rounded-xl border border-border/40 bg-card/40 hover:border-[hsl(var(--color-hybro-bro)/0.3)] transition-all duration-300">
                <div className="w-8 h-8 rounded-lg bg-[hsl(var(--color-hybro-bro)/0.12)] flex items-center justify-center mb-3">
                  <Code2 className="h-4 w-4 text-[hsl(var(--color-hybro-bro))]" />
                </div>
                <h3 className="text-sm font-semibold mb-1">Modular Core Architecture</h3>
                <p className="text-xs text-muted-foreground leading-relaxed">
                  FastAPI, Redis, MongoDB, Next.js. Swap any layer.
                </p>
              </div>
            </FadeInSection>

            <FadeInSection delay={350}>
              <div className="h-full p-5 rounded-xl border border-border/40 bg-card/40 hover:border-[hsl(var(--color-hybro-hy)/0.3)] transition-all duration-300">
                <div className="w-8 h-8 rounded-lg bg-[hsl(var(--color-hybro-hy)/0.12)] flex items-center justify-center mb-3">
                  <Sparkles className="h-4 w-4 text-[hsl(var(--color-hybro-hy))]" />
                </div>
                <h3 className="text-sm font-semibold mb-1">Human-in-the-Loop</h3>
                <p className="text-xs text-muted-foreground leading-relaxed">
                  Approve, inspect, and override agents mid-task.
                </p>
              </div>
            </FadeInSection>
          </div>

          {/* Supported frameworks */}
          <FadeInSection delay={400}>
            <div className="mt-14">
              <p className="text-center text-xs font-semibold tracking-wider uppercase text-muted-foreground mb-6">
                Works with agents built anywhere
              </p>
              <div className="flex flex-wrap items-center justify-center gap-x-10 gap-y-6">
                {marqueeFrameworks.map((fw) => (
                  <div key={fw.name} className="flex items-center gap-2.5 opacity-70 hover:opacity-100 transition-opacity">
                    <span className={fw.color}>{fw.icon}</span>
                    <span className="text-sm font-medium text-muted-foreground whitespace-nowrap">{fw.name}</span>
                  </div>
                ))}
              </div>
            </div>
          </FadeInSection>
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
