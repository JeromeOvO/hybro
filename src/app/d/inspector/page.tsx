"use client"

import { Button } from '@/components/ui/button'
import { ArrowRight, Github } from 'lucide-react'

export default function InspectorPage() {
  const handleLaunchInspector = () => {
    window.open('http://inspector.hybro.ai/', '_blank')
  }

  const handleGithubLink = () => {
    window.open('https://github.com/hybroai/a2a-agent-inspector', '_blank')
  }

  return (
    <div className="min-h-screen bg-background flex items-center justify-center">
        {/* Hero Section */}
        <section className="max-w-6xl mx-auto text-center py-20 px-4">
            <h1 className="text-4xl md:text-6xl font-bold mb-12">
                A2A Agent Inspector
            </h1>
            <p className="text-xl text-muted-foreground mb-12 max-w-4xl mx-auto">
                A2A Agent Inspector gives agent providers an easy way to test, verify, and confirm A2A compliance, while letting users try out agents and explore what they can do. It offers a clean, interactive space to understand an agent&apos;s behavior before integrating or deploying it.
            </p>
            <div className="max-w-4xl mx-auto flex flex-col sm:flex-row gap-15 justify-center">
                <Button
                    variant="outline" 
                    size="lg" 
                    className="px-8 cursor-pointer"
                    onClick={handleLaunchInspector}
                >
                    Launch Inspector
                    <ArrowRight className="ml-2 h-4 w-4 icon-action" />
                </Button>
                <Button 
                    variant="outline" 
                    size="lg" 
                    className="px-8 cursor-pointer"
                    onClick={handleGithubLink}
                >
                    <Github className="mr-2 h-4 w-4 icon-neutral" />
                    Inspector GitHub
                </Button>
            </div>   
        </section>
    </div>
)
}