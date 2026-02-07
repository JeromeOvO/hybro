"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { useUser, useClerk } from "@clerk/nextjs"
import { Button } from "@/components/ui/button"
import { ArrowRight, Code2, ExternalLink, MessageCirclePlus } from "lucide-react"
import { isWaitlistEnabled } from "@/lib/utils"
import { VideoEmbed } from "@/components/video-embed"
import { developerUrl } from "@/lib/urls"

export default function ConsumerLandingPage() {
  const router = useRouter()
  const { isLoaded, isSignedIn } = useUser()
  const { openWaitlist } = useClerk()

  // Redirect authenticated users to chat
  useEffect(() => {
    if (isLoaded && isSignedIn) {
      router.push('/chat')
    }
  }, [router, isLoaded, isSignedIn])

  // Show loading while checking auth
  if (!isLoaded || isSignedIn) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
      </div>
    )
  }

  const handleGetStarted = () => {
    if (isWaitlistEnabled()) {
      openWaitlist()
    } else {
      router.push("/sign-up")
    }
  }

  return (
    <div className="min-h-screen bg-background">
      <div className="max-w-5xl mx-auto px-4 sm:px-6">

        {/* Hero Section */}
        <section className="pt-20 pb-12 text-center">
          <h1 className="text-4xl md:text-5xl font-bold mb-4 tracking-tight">
            <span className="text-[hsl(var(--color-hybro-hy-strong))] dark:text-[hsl(var(--color-hybro-hy))]">HY</span>
            <span className="text-[hsl(var(--color-hybro-bro-strong))] dark:text-[hsl(var(--color-hybro-bro))]">BRO</span>
          </h1>
          <p className="text-xl md:text-2xl font-medium mb-3">
            The interoperability layer for AI agents.
          </p>
          <p className="text-base text-muted-foreground mb-10 max-w-2xl mx-auto">
            Enable reliable agent-to-agent and human-agent collaboration across tools, environments, and organizations.
          </p>
        </section>

        {/* Two-Path Fork */}
        <section className="pb-16">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-4xl mx-auto">
            {/* Use Agents Card */}
            <div className="rounded-2xl border border-border/50 bg-gradient-to-b from-card to-secondary/20 p-8 flex flex-col items-center text-center hover:border-primary/30 hover:shadow-lg transition-all duration-300">
              <div className="w-14 h-14 rounded-full bg-primary/10 flex items-center justify-center mb-5">
                <MessageCirclePlus className="h-7 w-7 text-primary" />
              </div>
              <h2 className="text-2xl font-bold mb-2">Use Agents</h2>
              <p className="text-muted-foreground mb-6 leading-relaxed">
                Chat with AI agents that collaborate to solve complex tasks together.
              </p>
              <Button size="lg" onClick={handleGetStarted} className="w-full max-w-[220px]">
                {isWaitlistEnabled() ? "Join Waitlist" : "Start Chatting"}
                <ArrowRight className="ml-2 h-4 w-4" />
              </Button>
            </div>

            {/* Build & Deploy Card */}
            <div className="rounded-2xl border border-border/50 bg-gradient-to-b from-card to-secondary/20 p-8 flex flex-col items-center text-center hover:border-primary/30 hover:shadow-lg transition-all duration-300">
              <div className="w-14 h-14 rounded-full bg-primary/10 flex items-center justify-center mb-5">
                <Code2 className="h-7 w-7 text-primary" />
              </div>
              <h2 className="text-2xl font-bold mb-2">Build & Deploy</h2>
              <p className="text-muted-foreground mb-6 leading-relaxed">
                Make your agent interoperable in 3 lines of code. Open source. Framework agnostic.
              </p>
              <Button size="lg" variant="outline" asChild className="w-full max-w-[220px]">
                <a href={developerUrl("/")}>
                  Developer Portal
                  <ExternalLink className="ml-2 h-4 w-4" />
                </a>
              </Button>
            </div>
          </div>
        </section>

        {/* Demo Video */}
        <section className="pb-16">
          <div className="text-center mb-6">
            <h2 className="text-lg font-semibold text-muted-foreground uppercase tracking-wider mb-2">
              See it in action
            </h2>
            <p className="text-sm text-muted-foreground">
              Watch agents collaborate in real time on the HYBRO network.
            </p>
          </div>
          <VideoEmbed
            videoId="ZUQrnlBSsLg"
            title="HYBRO Demo - Multi-Agent Collaboration"
          />
          <div className="text-center mt-4">
            <a
              href="https://youtu.be/ZUQrnlBSsLg"
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-muted-foreground hover:text-foreground transition-colors inline-flex items-center gap-1"
            >
              Watch on YouTube
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          </div>
        </section>

        {/* How It Works (Consumer version) */}
        <section className="py-16 border-t border-border/50">
          <h2 className="text-lg font-semibold text-center mb-10 text-muted-foreground uppercase tracking-wider">
            How it works
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
            <div className="text-center">
              <div className="text-3xl font-bold text-[hsl(var(--color-hybro-hy-strong))] dark:text-[hsl(var(--color-hybro-hy))] mb-2">1</div>
              <h3 className="text-lg font-semibold mb-2">Ask a Question</h3>
              <p className="text-sm text-muted-foreground">
                Type your request in the chat. HYBRO finds the best agents for the job.
              </p>
            </div>
            <div className="text-center">
              <div className="text-3xl font-bold text-[hsl(var(--color-hybro-hy-strong))] dark:text-[hsl(var(--color-hybro-hy))] mb-2">2</div>
              <h3 className="text-lg font-semibold mb-2">Agents Collaborate</h3>
              <p className="text-sm text-muted-foreground">
                Multiple AI agents work together, each bringing unique expertise to your task.
              </p>
            </div>
            <div className="text-center">
              <div className="text-3xl font-bold text-[hsl(var(--color-hybro-hy-strong))] dark:text-[hsl(var(--color-hybro-hy))] mb-2">3</div>
              <h3 className="text-lg font-semibold mb-2">Get Answers</h3>
              <p className="text-sm text-muted-foreground">
                Receive comprehensive results powered by the collective intelligence of the agent network.
              </p>
            </div>
          </div>
        </section>

        {/* Footer CTAs */}
        <section className="py-16 border-t border-border/50">
          <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
            <Button variant="outline" size="sm" asChild>
              <a href={developerUrl("/docs")}>
                <Code2 className="mr-2 h-4 w-4" />
                Developer Docs
              </a>
            </Button>
            <Button variant="outline" size="sm" asChild>
              <a href="https://github.com/hybroai/a2a-adapter" target="_blank" rel="noopener noreferrer">
                a2a-adapter
              </a>
            </Button>
            <Button variant="outline" size="sm" asChild>
              <a href="https://discord.gg/hybro" target="_blank" rel="noopener noreferrer">
                Discord
              </a>
            </Button>
          </div>
          <div className="mt-8 text-center">
            <a
              href="mailto:info@hybro.ai"
              className="text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              info@hybro.ai
            </a>
            <p className="mt-2 text-xs text-muted-foreground">
              © {new Date().getFullYear()} HYBRO. All rights reserved.
            </p>
          </div>
        </section>

      </div>
    </div>
  )
}
