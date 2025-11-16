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
    <div className="min-h-screen bg-background">
      {/* Hero Section */}
      <section className="py-20 px-4">
        <div className="max-w-4xl mx-auto text-center">
          <h1 className="text-4xl md:text-6xl font-bold mb-6">
            A2A Agent Inspector
          </h1>
          <p className="text-xl text-muted-foreground mb-8 max-w-3xl mx-auto leading-relaxed">
            [Introduction content will be provided later]
          </p>
        </div>
      </section>

      {/* Action Buttons Section */}
      <section className="py-12 px-4">
        <div className="max-w-4xl mx-auto">
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
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
        </div>
      </section>
    </div>
   
  )
}