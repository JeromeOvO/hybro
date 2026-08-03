"use client"

import { type ReactNode } from "react"
import { OpenClawIcon, OllamaIcon, N8nIcon, LangChainIcon, LangGraphIcon, CrewAIIcon } from "@/components/icons"
import { Plus } from "lucide-react"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

export const FRAMEWORKS: { name: string; description: string; color: string; icon?: ReactNode; url?: string }[] = [
  { name: "OpenClaw", description: "Local standalone agents", color: "text-violet-500 dark:text-violet-400", icon: <OpenClawIcon className="h-10 w-10" />, url: "https://openclaw.ai/" },
  { name: "Ollama", description: "Run LLMs locally", color: "text-slate-600 dark:text-slate-300", icon: <OllamaIcon className="h-10 w-10" />, url: "https://ollama.com/" },
  { name: "n8n", description: "Workflow automation", color: "text-rose-500 dark:text-rose-400", icon: <N8nIcon className="h-10 w-10" />, url: "https://n8n.io/" },
  { name: "CrewAI", description: "Multi-agent orchestration", color: "text-orange-500 dark:text-orange-400", icon: <CrewAIIcon className="h-10 w-10" />, url: "https://www.crewai.com/" },
  { name: "LangChain", description: "LLM application framework", color: "text-emerald-500 dark:text-emerald-400", icon: <LangChainIcon className="h-10 w-10" />, url: "https://www.langchain.com/" },
  { name: "LangGraph", description: "Stateful agent workflows", color: "text-blue-500 dark:text-blue-400", icon: <LangGraphIcon className="h-10 w-10" />, url: "https://www.langchain.com/langgraph" },
  { name: "More ...", description: "Any agent that can receive input and return output", color: "text-muted-foreground", icon: <Plus className="h-10 w-10 text-muted-foreground" /> },
]

interface FrameworkBadgesProps {
  /** Compact mode - single row, no descriptions */
  compact?: boolean
  /** Optional className for the container */
  className?: string
}

/**
 * Reusable framework badges component.
 * Displays supported agent frameworks in a consistent style.
 */
export function FrameworkBadges({ compact = false, className = "" }: FrameworkBadgesProps) {
  if (compact) {
    return (
      <div className={`flex flex-wrap items-center gap-2 ${className}`}>
        {FRAMEWORKS.map((fw) => (
          <span
            key={fw.name}
            className="inline-flex items-center rounded-md border border-border/50 bg-muted/40 px-2.5 py-1 text-xs font-medium"
          >
            {fw.icon ? (
              <span className="mr-1.5">{fw.icon}</span>
            ) : (
              <span className={`mr-1.5 h-1.5 w-1.5 rounded-full bg-current ${fw.color}`} />
            )}
            {fw.name}
          </span>
        ))}
        <span className="text-xs text-muted-foreground">+ more</span>
      </div>
    )
  }

  return (
    <TooltipProvider delayDuration={0}>
      <div className={`grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-7 gap-3 ${className}`}>
        {FRAMEWORKS.map((fw) => {
          const tileContent = fw.icon ? (
            <div className="flex flex-col items-center gap-2">
              {fw.icon}
              <div className={`font-medium text-sm ${fw.color}`}>{fw.name}</div>
            </div>
          ) : (
            <div>
              <div className={`font-medium text-sm ${fw.color}`}>{fw.name}</div>
            </div>
          )

          const tileClass = "rounded-lg border border-border/50 bg-muted/30 px-6 py-5 text-center hover:bg-muted/50 transition-colors flex items-center justify-center card-lift"

          return (
            <Tooltip key={fw.name}>
              <TooltipTrigger asChild>
                {fw.url ? (
                  <a
                    href={fw.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={tileClass}
                  >
                    {tileContent}
                  </a>
                ) : (
                  <div className={`${tileClass} cursor-default`}>
                    {tileContent}
                  </div>
                )}
              </TooltipTrigger>
              {fw.description && (
                <TooltipContent>
                  {fw.description}
                </TooltipContent>
              )}
            </Tooltip>
          )
        })}
      </div>
    </TooltipProvider>
  )
}

/** Names-only export for inline text use */
export const FRAMEWORK_NAMES = FRAMEWORKS.map((fw) => fw.name)
