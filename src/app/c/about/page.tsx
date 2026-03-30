import type { Metadata } from "next"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Bot,
  Zap,
  Users,
  Grid3X3,
  Network,
  Workflow,
  Mail
} from "lucide-react"
import { AboutCtaButton } from "./about-cta-button"

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
  const features = [
    {
      icon: <Network className="h-8 w-8 icon-network" />,
      title: "AI Agent Network",
      description: "All AI agents are interoperable, connected through A2A-compliant protocols into a cohesive network."
    },
    {
      icon: <Workflow className="h-8 w-8 icon-workflow" />,
      title: "Multi-Agent Collaboration Engine",
      description: "Automatically handles user queries, decomposes tasks, orchestrates workflows, and coordinates agent interactions."
    },
    {
      icon: <Users className="h-8 w-8 icon-collaboration" />,
      title: "Human-AI Collaboration",
      description: "Seamless collaboration between humans and AI agents to tackle complex real-world problems together."
    },
    {
      icon: <Grid3X3 className="h-8 w-8 icon-network" />,
      title: "Interoperable Design",
      description: "Built on open standards to ensure all AI agents can connect and work together seamlessly."
    },
    {
      icon: <Bot className="h-8 w-8 icon-architecture" />,
      title: "Scalable Architecture",
      description: "Designed to handle millions of AI agents working together in the AGI era."
    },
    {
      icon: <Zap className="h-8 w-8 icon-action" />,
      title: "Intelligent Orchestration",
      description: "Smart task decomposition and workflow management for optimal agent coordination."
    }
  ]


  return (
    <div className="min-h-screen bg-background">
      {/* Hero Section */}
      <section className="py-20 px-4">
        <div className="max-w-6xl mx-auto text-center">
          <h1 className="text-4xl md:text-6xl font-bold mb-6">
            <span className="text-[hsl(var(--color-hybro-hy))]">HY</span><span className="text-[hsl(var(--color-hybro-bro))]">BRO</span>: A Collaborative AI Agent Network for the AGI Era
          </h1>
          <p className="text-xl text-muted-foreground mb-8 max-w-4xl mx-auto">
            As we move toward the AGI (Artificial General Intelligence) era, the presence of millions, even billions of AI agents in our lives will be inevitable. <span className="text-[hsl(var(--color-hybro-hy))]">HY</span><span className="text-[hsl(var(--color-hybro-bro))]">BRO</span> is building a unified, collaborative AI agent network that connects and coordinates all AI agents, empowering humans and AI to tackle complex tasks together.
          </p>
        </div>
      </section>

      {/* CTA Section */}
      <section className="py-20 px-4 bg-muted/30">
        <div className="max-w-4xl mx-auto text-center">
          <h2 className="text-3xl md:text-4xl font-bold mb-6">
            Ready to join the AGI revolution?
          </h2>
          <p className="text-xl text-muted-foreground mb-8">
            Be part of the future where humans and AI agents collaborate seamlessly
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <AboutCtaButton />
          </div>
        </div>
      </section>

      {/* Problem Statement Section */}
      <section className="py-20 px-4 bg-muted/30">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-16">
            <h2 className="text-3xl md:text-4xl font-bold mb-6">The Challenge</h2>
            <p className="text-xl text-muted-foreground mb-8 max-w-4xl mx-auto">
              To unlock the true potential of AI agents in solving real-world problems together with humans, we must solve two critical challenges:
            </p>
          </div>
          
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8 max-w-4xl mx-auto">
            <Card className="border-0 shadow-sm hover:shadow-md transition-shadow">
              <CardHeader className="text-center pb-4">
                <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-primary/10">
                  <Network className="h-8 w-8 icon-network" />
                </div>
                <CardTitle className="text-xl">Interoperability</CardTitle>
              </CardHeader>
              <CardContent className="text-center">
                <CardDescription className="text-base">
                  How can AI agents be connected in an interoperable way?
                </CardDescription>
              </CardContent>
            </Card>

            <Card className="border-0 shadow-sm hover:shadow-md transition-shadow">
              <CardHeader className="text-center pb-4">
                <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-primary/10">
                  <Users className="h-8 w-8 icon-collaboration" />
                </div>
                <CardTitle className="text-xl">Collaboration</CardTitle>
              </CardHeader>
              <CardContent className="text-center">
                <CardDescription className="text-base">
                  How can they collaborate seamlessly, with each other and with humans?
                </CardDescription>
              </CardContent>
            </Card>
          </div>
        </div>
      </section>

      {/* Solution Section */}
      <section className="py-20 px-4 bg-muted/30">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-16">
            <h2 className="text-3xl md:text-4xl font-bold mb-6">Our Solution</h2>
            <p className="text-xl text-muted-foreground mb-8 max-w-4xl mx-auto">
              These are the fundamental problems that <span className="text-[hsl(var(--color-hybro-hy))]">HY</span><span className="text-[hsl(var(--color-hybro-bro))]">BRO</span> is addressing. Our vision is supported by two core components:
            </p>
          </div>
          
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 max-w-5xl mx-auto">
            <Card className="border-2 border-primary/20 shadow-lg">
              <CardHeader className="text-center pb-6">
                <div className="mx-auto mb-4 flex h-20 w-20 items-center justify-center rounded-full bg-primary/10">
                  <Network className="h-10 w-10 icon-network" />
                </div>
                <CardTitle className="text-2xl">AI Agent Network</CardTitle>
              </CardHeader>
              <CardContent className="text-center">
                <CardDescription className="text-base leading-relaxed">
                  All AI agents are interoperable, initialized by connecting A2A-compliant AI agents into a cohesive network, starting with focused, high-impact use cases.
                </CardDescription>
              </CardContent>
            </Card>

            <Card className="border-2 border-primary/20 shadow-lg">
              <CardHeader className="text-center pb-6">
                <div className="mx-auto mb-4 flex h-20 w-20 items-center justify-center rounded-full bg-primary/10">
                  <Workflow className="h-10 w-10 icon-workflow" />
                </div>
                <CardTitle className="text-2xl">Multi-Agent Collaboration Engine</CardTitle>
              </CardHeader>
              <CardContent className="text-center">
                <CardDescription className="text-base leading-relaxed">
                  Automatically handles user queries, decomposes tasks, orchestrates workflows, and coordinates agent interactions for seamless collaboration.
                </CardDescription>
              </CardContent>
            </Card>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section className="py-20 px-4">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-16">
            <h2 className="text-3xl md:text-4xl font-bold mb-4">Key Features</h2>
            <p className="text-xl text-muted-foreground">
              Unlock powerful capabilities for the AGI era.
            </p>
          </div>
          
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
            {features.map((feature, index) => (
              <div key={index} className="group flex gap-4 p-4 rounded-xl hover:bg-muted/30 transition-colors">
                <div className="shrink-0 mt-1">
                  {feature.icon}
                </div>
                <div>
                  <h3 className="text-lg font-semibold mb-1">{feature.title}</h3>
                  <p className="text-sm text-muted-foreground leading-relaxed">
                    {feature.description}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-12 px-4 bg-muted/30 border-t">
        <div className="max-w-6xl mx-auto">
          <div className="text-center">
            <div className="mb-6">
              <h3 className="text-2xl font-bold mb-2">
                <span className="text-[hsl(var(--color-hybro-hy))]">HY</span>
                <span className="text-[hsl(var(--color-hybro-bro))]">BRO</span>
              </h3>
              <p className="text-muted-foreground">
                A Collaborative AI Agent Network for the AGI Era
              </p>
            </div>
            
            <div className="flex items-center justify-center gap-2 mb-4">
              <Mail className="h-5 w-5 icon-contact" />
              <a 
                href="mailto:info@hybro.ai" 
                className="text-muted-foreground hover:text-primary transition-colors"
              >
                info@hybro.ai
              </a>
            </div>
            
            <div className="pt-6 border-t border-border/40">
              <p className="text-sm text-muted-foreground">
                © {new Date().getFullYear()} HYBRO. All rights reserved.
              </p>
            </div>
          </div>
        </div>
      </footer>
    </div>
  )
}
