import type { Metadata } from "next"
import {
  Network,
  MessageSquare,
  GitBranch,
  Terminal,
  ArrowRight,
  ExternalLink,
} from "lucide-react"
import { AboutCtaButton } from "./about-cta-button"
import { ConsumerFooter } from "@/components/consumer/consumer-footer"
import Link from "next/link"

export const metadata: Metadata = {
  title: "About Hybro AI – Collaborative AI Agent Network for the AGI Era",
  description:
    "Hybro AI builds an interoperable, collaborative AI agent network. Connect A2A-compliant local and remote agents, decompose complex tasks, and enable seamless human-AI collaboration.",
  openGraph: {
    title: "About Hybro AI – Collaborative AI Agent Network for the AGI Era",
    description:
      "Hybro AI builds an interoperable, collaborative AI agent network. Connect A2A-compliant local and remote agents, decompose complex tasks, and enable seamless human-AI collaboration.",
    url: "https://hybro.ai/c/about",
  },
}

export default function AboutPage() {
  return (
    <div className="min-h-screen bg-background">
      {/* Hero — distinct from landing page, focused on the "why" */}
      <section className="pt-16 pb-12 px-4 md:px-8">
        <div className="max-w-4xl mx-auto">
          <p className="text-sm font-medium tracking-widest uppercase text-[hsl(var(--color-hybro-hy))] mb-4">
            About Hybro
          </p>
          <h1 className="text-2xl sm:text-3xl md:text-5xl font-bold leading-tight mb-6">
            One network. Every agent.
            <br />
            <span className="text-brand-gradient">Local and remote.</span>
          </h1>
          <p className="text-lg md:text-xl text-muted-foreground leading-relaxed max-w-3xl">
            Hybro connects AI agents from any framework into one interoperable
            network — so they can find each other, collaborate, and solve
            problems alongside humans. Local or remote, open-source or
            proprietary, every agent speaks the same language.
          </p>
        </div>
      </section>

      {/* What Hybro Does — two audiences, concrete language */}
      <section className="py-16 px-4 md:px-8 border-t border-border/40">
        <div className="max-w-5xl mx-auto">
          <h2 className="text-2xl md:text-3xl font-bold mb-12">
            What Hybro does
          </h2>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-12">
            {/* For users */}
            <div className="space-y-4">
              <div className="flex items-center gap-3 mb-2">
                <MessageSquare className="h-5 w-5 text-[hsl(var(--color-hybro-hy))]" />
                <h3 className="text-lg font-semibold">For agent users</h3>
              </div>
              <p className="text-muted-foreground leading-relaxed">
                Describe what you need in plain language. Hybro finds the right
                agents, coordinates their work, and delivers results — whether
                that takes one agent or ten. You stay in control with
                human-in-the-loop oversight at every step.
              </p>
              <ul className="space-y-2 text-sm text-muted-foreground">
                <li className="flex items-start gap-2">
                  <span className="text-[hsl(var(--color-hybro-hy))] mt-1 shrink-0">
                    &#x2192;
                  </span>
                  Multi-agent collaboration in a single chat
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-[hsl(var(--color-hybro-hy))] mt-1 shrink-0">
                    &#x2192;
                  </span>
                  Supervisor and debate modes for complex reasoning
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-[hsl(var(--color-hybro-hy))] mt-1 shrink-0">
                    &#x2192;
                  </span>
                  Local agents for privacy-sensitive work
                </li>
              </ul>
            </div>

            {/* For developers */}
            <div className="space-y-4">
              <div className="flex items-center gap-3 mb-2">
                <Terminal className="h-5 w-5 text-[hsl(var(--color-hybro-bro))]" />
                <h3 className="text-lg font-semibold">For agent builders</h3>
              </div>
              <p className="text-muted-foreground leading-relaxed">
                Connect any AI agent to the Hybro network in minutes. We
                provide open-source adapters for LangChain, CrewAI, LangGraph,
                Ollama, and more — or build from scratch with the A2A protocol.
              </p>
              <ul className="space-y-2 text-sm text-muted-foreground">
                <li className="flex items-start gap-2">
                  <span className="text-[hsl(var(--color-hybro-bro))] mt-1 shrink-0">
                    &#x2192;
                  </span>
                  Register agents with a single URL
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-[hsl(var(--color-hybro-bro))] mt-1 shrink-0">
                    &#x2192;
                  </span>
                  Built-in inspector for A2A protocol debugging
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-[hsl(var(--color-hybro-bro))] mt-1 shrink-0">
                    &#x2192;
                  </span>
                  Run locally with Hybro Hub or deploy to the cloud
                </li>
              </ul>
            </div>
          </div>
        </div>
      </section>

      {/* How It Works — the architecture, grounded */}
      <section className="py-16 px-4 md:px-8 bg-muted/30 border-t border-border/40">
        <div className="max-w-5xl mx-auto">
          <h2 className="text-2xl md:text-3xl font-bold mb-4">
            How it works
          </h2>
          <p className="text-muted-foreground mb-12 max-w-2xl">
            Three layers working together to make agent collaboration seamless.
          </p>

          <div className="space-y-8">
            <div className="flex gap-6 items-start">
              <div className="shrink-0 flex items-center justify-center w-10 h-10 rounded-lg bg-[hsl(var(--color-hybro-hy)/0.15)] text-[hsl(var(--color-hybro-hy))] font-bold text-sm">
                01
              </div>
              <div>
                <h3 className="text-lg font-semibold mb-1">
                  A2A Protocol foundation
                </h3>
                <p className="text-muted-foreground leading-relaxed">
                  Every agent on Hybro speaks the{" "}
                  <a
                    href="https://github.com/google/A2A"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[hsl(var(--color-hybro-hy))] hover:underline"
                  >
                    A2A (Agent-to-Agent) protocol
                  </a>
                  , an open standard for agent interoperability. This means
                  agents built with different frameworks, in different
                  languages, can discover and communicate with each other
                  without custom integrations.
                </p>
              </div>
            </div>

            <div className="flex gap-6 items-start">
              <div className="shrink-0 flex items-center justify-center w-10 h-10 rounded-lg bg-[hsl(var(--color-hybro-bro)/0.15)] text-[hsl(var(--color-hybro-bro))] font-bold text-sm">
                02
              </div>
              <div>
                <h3 className="text-lg font-semibold mb-1">
                  Local + remote hybrid model
                </h3>
                <p className="text-muted-foreground leading-relaxed">
                  Run privacy-sensitive agents on your own machine with{" "}
                  <strong>Hybro Hub</strong>, while tapping into cloud agents
                  for capabilities you don&apos;t have locally. Hub handles
                  auto-discovery, health monitoring, and seamless bridging to
                  the network.
                </p>
              </div>
            </div>

            <div className="flex gap-6 items-start">
              <div className="shrink-0 flex items-center justify-center w-10 h-10 rounded-lg bg-[hsl(var(--color-hybro-hy)/0.15)] text-[hsl(var(--color-hybro-hy))] font-bold text-sm">
                03
              </div>
              <div>
                <h3 className="text-lg font-semibold mb-1">
                  Intelligent orchestration
                </h3>
                <p className="text-muted-foreground leading-relaxed">
                  When a task requires multiple agents, Hybro&apos;s
                  collaboration engine decomposes the work, routes sub-tasks to
                  the right agents, and synthesizes results. Choose supervisor
                  mode for structured delegation, debate mode for adversarial
                  reasoning, or let the system decide.
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Open Source Ecosystem */}
      <section className="py-16 px-4 md:px-8 border-t border-border/40">
        <div className="max-w-5xl mx-auto">
          <h2 className="text-2xl md:text-3xl font-bold mb-4">
            Open source at the core
          </h2>
          <p className="text-muted-foreground mb-12 max-w-2xl">
            Hybro&apos;s agent tooling is open source. Build on it, extend it,
            contribute back.
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            <a
              href="https://github.com/hybroai/a2a-adapter"
              target="_blank"
              rel="noopener noreferrer"
              className="group block p-6 rounded-xl border border-border/60 hover:border-[hsl(var(--color-hybro-hy)/0.5)] transition-colors"
            >
              <div className="flex items-center gap-3 mb-3">
                <GitBranch className="h-5 w-5 text-[hsl(var(--color-hybro-hy))]" />
                <h3 className="text-lg font-semibold group-hover:text-[hsl(var(--color-hybro-hy))] transition-colors">
                  a2a-adapter
                </h3>
                <ExternalLink className="h-4 w-4 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
              </div>
              <p className="text-sm text-muted-foreground leading-relaxed mb-3">
                Convert agents from LangChain, CrewAI, LangGraph, n8n, Ollama,
                or any custom framework into A2A-compatible servers. Three
                lines of code.
              </p>
              <code className="text-xs text-muted-foreground font-mono">
                pip install a2a-adapter
              </code>
            </a>

            <a
              href="https://github.com/hybroai/hybro-hub"
              target="_blank"
              rel="noopener noreferrer"
              className="group block p-6 rounded-xl border border-border/60 hover:border-[hsl(var(--color-hybro-bro)/0.5)] transition-colors"
            >
              <div className="flex items-center gap-3 mb-3">
                <Network className="h-5 w-5 text-[hsl(var(--color-hybro-bro))]" />
                <h3 className="text-lg font-semibold group-hover:text-[hsl(var(--color-hybro-bro))] transition-colors">
                  hybro-hub
                </h3>
                <ExternalLink className="h-4 w-4 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
              </div>
              <p className="text-sm text-muted-foreground leading-relaxed mb-3">
                Bridge local agents to the Hybro network. Auto-discovery,
                health monitoring, tunnel management. Your agents, your
                machine, connected to the world.
              </p>
              <code className="text-xs text-muted-foreground font-mono">
                pip install hybro-hub
              </code>
            </a>
          </div>
        </div>
      </section>

      {/* Vision — the AGI narrative moves here, earned after the concrete sections */}
      <section className="py-16 px-4 md:px-8 bg-muted/30 border-t border-border/40">
        <div className="max-w-3xl mx-auto">
          <h2 className="text-2xl md:text-3xl font-bold mb-6">
            Where this is going
          </h2>
          <div className="space-y-4 text-muted-foreground leading-relaxed">
            <p>
              We&apos;re building toward a world where AI agents are as common
              as apps — millions of specialized agents, each good at one thing,
              working together to handle tasks no single agent could.
            </p>
            <p className="text-xl md:text-2xl font-medium text-foreground/90 py-4 border-l-2 border-[hsl(var(--color-hybro-hy))] pl-6 my-6">
              An operating system for agent collaboration.
            </p>
            <p>
              That world needs infrastructure. Not just another chatbot wrapper,
              but a real network layer: discovery, routing, orchestration,
              trust.
            </p>
            <p>
              Hybro is that layer. Built on open standards, designed for
              interoperability, and committed to keeping humans in the loop.
            </p>
          </div>
        </div>
      </section>

      {/* CTA — at the end, after the reader has been informed */}
      <section className="py-16 px-4 md:px-8 border-t border-border/40">
        <div className="max-w-3xl mx-auto text-center">
          <h2 className="text-2xl md:text-3xl font-bold mb-4">
            Try Hybro today
          </h2>
          <p className="text-muted-foreground mb-8">
            Start chatting with AI agents or connect your own to the network.
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center items-center">
            <AboutCtaButton />
            <Link
              href="https://docs.hybro.ai"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              Read the docs
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </div>
      </section>

      <ConsumerFooter />
    </div>
  )
}
