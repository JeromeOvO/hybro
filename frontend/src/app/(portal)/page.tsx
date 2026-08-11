'use client'

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { useUser } from "@/lib/auth"
import { Button } from "@/components/ui/button"
import { ArrowRight, Rocket, SquareArrowOutUpRight, Sparkles } from "lucide-react"
import { VideoEmbed } from "@/components/video-embed"
import Link from 'next/link'
import { PortalFooter } from '@/components/portal/portal-footer'
import { routes } from '@/lib/routes'
import { FadeInSection } from "@/components/fade-in-section"

export default function ConsumerLandingPage() {
  const router = useRouter()
  const { isLoaded, isSignedIn } = useUser()

  // Redirect authenticated users to chat
  useEffect(() => {
    if (isLoaded && isSignedIn) {
      router.push(routes.chat)
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
    router.push("/sign-up")
  }

  return (
    <div className="relative min-h-screen bg-background">
      <div aria-hidden="true" className="pointer-events-none fixed inset-0 z-0">
        <div className="absolute inset-x-0 top-0 h-[520px] bg-[radial-gradient(ellipse_at_50%_0%,hsl(var(--color-hybro-hy)/0.14),transparent_36%),radial-gradient(ellipse_at_78%_8%,hsl(var(--color-hybro-bro)/0.11),transparent_34%)]" />
        <div className="absolute left-[-14rem] top-[28rem] h-[34rem] w-[34rem] rounded-full bg-[hsl(var(--color-hybro-hy)/0.055)] blur-xl md:blur-3xl" />
        <div className="absolute right-[-16rem] top-[48rem] h-[38rem] w-[38rem] rounded-full bg-[hsl(var(--color-hybro-bro)/0.06)] blur-xl md:blur-3xl" />
        <div className="absolute left-1/2 top-[38rem] h-[24rem] w-[min(64rem,100vw)] -translate-x-1/2 rounded-full bg-[radial-gradient(ellipse_at_center,hsl(var(--color-primary)/0.07),transparent_62%)] blur-lg md:blur-2xl" />
        <div
          className="absolute inset-0 opacity-[0.13]"
          style={{
            backgroundImage:
              "linear-gradient(hsl(var(--color-border) / 0.3) 1px, transparent 1px), linear-gradient(90deg, hsl(var(--color-border) / 0.3) 1px, transparent 1px)",
            backgroundSize: "52px 52px",
            maskImage: "linear-gradient(to bottom, black 0%, black 18%, transparent 56%, black 78%, transparent 100%)",
          }}
        />
        <div className="absolute inset-x-0 top-[42rem] h-px bg-gradient-to-r from-transparent via-border/50 to-transparent" />
      </div>

      <div className="relative z-10 max-w-6xl mx-auto px-4 sm:px-8">

        {/* Hero Section */}
        <section className="relative pt-20 pb-12 text-center animate-fade-up">
          <h1 className="text-4xl md:text-5xl lg:text-6xl font-bold mb-4 tracking-tight">
            <span className="text-[hsl(var(--color-hybro-hy))]">HY</span>
            <span className="text-[hsl(var(--color-hybro-bro))]">BRO</span>
          </h1>
          <p className="text-xl md:text-2xl font-medium mb-3 text-balance">
            Unify all AI agents &mdash; local &amp; remote
          </p>
          <p className="text-base text-muted-foreground mb-10 max-w-2xl mx-auto text-pretty">
            Local and remote AI agents that collaborate seamlessly.<br />
            Your data, your privacy, your control.
          </p>
        </section>

        {/* Two-Path Fork */}
        <section className="pb-16 animate-fade-up animate-fade-up-delay-1">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-4xl mx-auto">
            {/* Use Agents Card */}
            <div className="rounded-2xl border border-border/50 bg-gradient-to-b from-card to-secondary/20 p-8 flex flex-col items-start text-left hover:border-primary/30 hover:shadow-lg transition-all duration-300">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-lg bg-[hsl(var(--color-hybro-bro)/0.1)] flex items-center justify-center">
                  <Sparkles className="h-5 w-5 text-[hsl(var(--color-hybro-bro))]" />
                </div>
                <h2 className="text-2xl font-bold">Use Agents</h2>
              </div>
              <p className="text-muted-foreground mb-6 leading-relaxed">
                Work with AI agents that collaborate to solve complex tasks together.
              </p>
              <Button size="lg" onClick={handleGetStarted} className="btn-brand-gradient w-full max-w-[220px]">
                Start Chatting
                <ArrowRight className="ml-2 h-4 w-4" />
              </Button>
            </div>

            {/* Build & Deploy Card */}
            <div className="rounded-2xl border border-border/50 bg-gradient-to-b from-card to-secondary/20 p-8 flex flex-col items-start text-left hover:border-primary/30 hover:shadow-lg transition-all duration-300">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-lg bg-[hsl(var(--color-hybro-hy)/0.1)] flex items-center justify-center">
                  <Rocket className="h-5 w-5 text-[hsl(var(--color-hybro-hy))]" />
                </div>
                <h2 className="text-2xl font-bold">Build & Connect</h2>
              </div>
              <p className="text-muted-foreground mb-6 leading-relaxed">
                Connect any agent to the Hybro network. One install, any framework, open source.
              </p>
              <Button size="lg" variant="brandTint" asChild className="w-full max-w-[220px]">
                <Link href={routes.agents}>
                  View Agents
                  <SquareArrowOutUpRight className="ml-2 h-4 w-4" />
                </Link>
              </Button>
            </div>
          </div>
        </section>

        {/* Demo Video */}
        <section className="pb-16 animate-fade-up animate-fade-up-delay-2">
          <h2 className="text-lg font-semibold text-muted-foreground uppercase tracking-wider mb-6 text-center">
            See it in action
          </h2>
          <VideoEmbed
            videoId="P0kyUQAxnZg"
            title="HYBRO Demo - Multi-Agent Collaboration"
          />
        </section>

        {/* How It Works (Consumer version) */}
        <section className="py-16 border-t border-border/50">
          <FadeInSection>
            <h2 className="text-lg font-semibold text-center mb-10 text-muted-foreground uppercase tracking-wider">
              How it works
            </h2>
          </FadeInSection>
          <div className="max-w-2xl mx-auto space-y-0">
            <FadeInSection delay={100}>
              <div className="flex gap-5 items-start relative pb-8">
                <div className="flex flex-col items-center shrink-0">
                  <div className="flex items-center justify-center w-10 h-10 rounded-full btn-brand-gradient text-sm font-bold">1</div>
                  <div className="w-px h-full bg-border/50 mt-2" />
                </div>
                <div className="pt-1.5">
                  <h3 className="text-lg font-semibold mb-1">Ask a Question</h3>
                  <p className="text-sm text-muted-foreground">
                    Type your request in the chat. HYBRO finds the best agents for the job.
                  </p>
                </div>
              </div>
            </FadeInSection>
            <FadeInSection delay={200}>
              <div className="flex gap-5 items-start relative pb-8">
                <div className="flex flex-col items-center shrink-0">
                  <div className="flex items-center justify-center w-10 h-10 rounded-full btn-brand-gradient text-sm font-bold">2</div>
                  <div className="w-px h-full bg-border/50 mt-2" />
                </div>
                <div className="pt-1.5">
                  <h3 className="text-lg font-semibold mb-1">Agents Collaborate</h3>
                  <p className="text-sm text-muted-foreground">
                    Multiple AI agents work together, each bringing unique expertise to your task.
                  </p>
                </div>
              </div>
            </FadeInSection>
            <FadeInSection delay={300}>
              <div className="flex gap-5 items-start relative">
                <div className="flex flex-col items-center shrink-0">
                  <div className="flex items-center justify-center w-10 h-10 rounded-full btn-brand-gradient text-sm font-bold">3</div>
                </div>
                <div className="pt-1.5">
                  <h3 className="text-lg font-semibold mb-1">Get Answers</h3>
                  <p className="text-sm text-muted-foreground">
                    Results powered by the collective intelligence of the agent network.
                  </p>
                </div>
              </div>
            </FadeInSection>
          </div>
        </section>

        <PortalFooter />

      </div>
    </div>
  )
}
