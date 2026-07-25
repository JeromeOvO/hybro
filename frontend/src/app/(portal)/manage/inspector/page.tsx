'use client'

import { Button } from "@/components/ui/button"
import { GithubIcon } from "@/components/icons"
import {
  ArrowRight,
  Shield,
  SquareArrowOutUpRight,
} from "lucide-react"

export default function InspectorPage() {
  return (
    <div className="min-h-screen bg-background">
      <div className="max-w-4xl mx-auto">
        {/* Hero */}
        <section className="pt-16 pb-12">
          <div className="flex items-center gap-2 mb-4">
            <Shield className="h-5 w-5 text-icon-warning" />
            <span className="text-sm font-medium text-primary uppercase tracking-wider">Agent Testing</span>
          </div>
          <h1 className="text-3xl md:text-4xl font-bold mb-3 tracking-tight">
            A2A Agent Inspector
          </h1>
          <p className="text-lg text-muted-foreground mb-8 max-w-2xl">
            Test, verify, and confirm A2A compliance for your agents. Explore what an agent can do in a clean, interactive space before integrating or deploying it.
          </p>

          {/* Action buttons */}
          <div className="flex flex-wrap gap-3">
            <Button className="btn-brand-gradient" asChild>
              <a href="https://inspector.hybro.ai/" target="_blank" rel="noopener noreferrer">
                Launch Inspector
                <ArrowRight className="ml-2 h-4 w-4" />
              </a>
            </Button>
            <Button variant="brandTint" asChild>
              <a href="https://github.com/hybroai/a2a-agent-inspector" target="_blank" rel="noopener noreferrer">
                <GithubIcon className="mr-2 h-4 w-4" />
                Inspector GitHub
                <SquareArrowOutUpRight className="ml-2 h-4 w-4" />
              </a>
            </Button>
          </div>
        </section>
      </div>
    </div>
  )
}
