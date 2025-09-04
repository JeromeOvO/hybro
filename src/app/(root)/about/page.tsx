"use client"

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { 
  Bot, 
  Zap, 
  Users, 
  Grid3X3,
  ArrowRight,
  Network,
  Workflow,
  Mail
} from "lucide-react"
import { useRouter } from "next/navigation"

export default function AboutPage() {
  const router = useRouter()

  const features = [
    {
      icon: <Network className="h-8 w-8" />,
      title: "AI Agent Network",
      description: "All AI agents are interoperable, connected through A2A-compliant protocols into a cohesive network."
    },
    {
      icon: <Workflow className="h-8 w-8" />,
      title: "Multi-Agent Collaboration Engine",
      description: "Automatically handles user queries, decomposes tasks, orchestrates workflows, and coordinates agent interactions."
    },
    {
      icon: <Users className="h-8 w-8" />,
      title: "Human-AI Collaboration",
      description: "Seamless collaboration between humans and AI agents to tackle complex real-world problems together."
    },
    {
      icon: <Grid3X3 className="h-8 w-8" />,
      title: "Interoperable Design",
      description: "Built on open standards to ensure all AI agents can connect and work together seamlessly."
    },
    {
      icon: <Bot className="h-8 w-8" />,
      title: "Scalable Architecture",
      description: "Designed to handle millions of AI agents working together in the AGI era."
    },
    {
      icon: <Zap className="h-8 w-8" />,
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
            Hybro: A Collaborative AI Agent Network for the AGI Era
          </h1>
          <p className="text-xl text-muted-foreground mb-8 max-w-4xl mx-auto">
            As we move toward the AGI (Artificial General Intelligence) era, the presence of millions, even billions of AI agents in our lives will be inevitable. Hybro is building a unified, collaborative AI agent network that connects and coordinates all AI agents, empowering humans and AI to tackle complex tasks together.
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
            <Button 
              variant="outline" 
              size="lg" 
              className="px-8"
              onClick={() => router.push('/room')}
            >
              Get Started Free
              <ArrowRight className="ml-2 h-4 w-4" />
            </Button>
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
                  <Network className="h-8 w-8" />
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
                  <Users className="h-8 w-8" />
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
              These are the fundamental problems that Hybro is addressing. Our vision is supported by two core components:
            </p>
          </div>
          
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 max-w-5xl mx-auto">
            <Card className="border-2 border-primary/20 shadow-lg">
              <CardHeader className="text-center pb-6">
                <div className="mx-auto mb-4 flex h-20 w-20 items-center justify-center rounded-full bg-primary/10">
                  <Network className="h-10 w-10" />
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
                  <Workflow className="h-10 w-10" />
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
              <Card key={index} className="border-0 shadow-sm hover:shadow-md transition-shadow">
                <CardHeader className="text-center pb-4">
                  <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-primary/10">
                    {feature.icon}
                  </div>
                  <CardTitle className="text-xl">{feature.title}</CardTitle>
                </CardHeader>
                <CardContent className="text-center">
                  <CardDescription className="text-base">
                    {feature.description}
                  </CardDescription>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-12 px-4 bg-muted/30 border-t">
        <div className="max-w-6xl mx-auto">
          <div className="text-center">
            <div className="mb-6">
              <h3 className="text-2xl font-bold mb-2">HYBRO</h3>
              <p className="text-muted-foreground">
                A Collaborative AI Agent Network for the AGI Era
              </p>
            </div>
            
            <div className="flex items-center justify-center gap-2 mb-4">
              <Mail className="h-5 w-5 text-muted-foreground" />
              <a 
                href="mailto:info@hybro.ai" 
                className="text-muted-foreground hover:text-primary transition-colors"
              >
                info@hybro.ai
              </a>
            </div>
            
            <div className="pt-6 border-t border-border/40">
              <p className="text-sm text-muted-foreground">
                © 2025 HYBRO. All rights reserved.
              </p>
            </div>
          </div>
        </div>
      </footer>
    </div>
  )
}
