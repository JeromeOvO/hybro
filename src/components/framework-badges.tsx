"use client"

import { type ReactNode } from "react"
import { OpenClawIcon, N8nIcon, LangChainIcon, LangGraphIcon, CrewAIIcon } from "@/components/icons"
import { Plus } from "lucide-react"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

const FRAMEWORKS: { name: string; description: string; color: string; icon?: ReactNode }[] = [
  { name: "OpenClaw", description: "Local standalone agents", color: "text-violet-500 dark:text-violet-400", icon: <OpenClawIcon className="h-10 w-10" /> },
  { name: "n8n", description: "Workflow automation", color: "text-rose-500 dark:text-rose-400", icon: <N8nIcon className="h-10 w-10" /> },
  { name: "CrewAI", description: "Multi-agent orchestration", color: "text-orange-500 dark:text-orange-400", icon: <CrewAIIcon className="h-10 w-10" /> },
  { name: "LangChain", description: "LLM application framework", color: "text-emerald-500 dark:text-emerald-400", icon: <LangChainIcon className="h-10 w-10" /> },
  { name: "LangGraph", description: "Stateful agent workflows", color: "text-blue-500 dark:text-blue-400", icon: <LangGraphIcon className="h-10 w-10" /> },
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
      <div className={`grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-3 ${className}`}>
        {FRAMEWORKS.map((fw) => (
          <Tooltip key={fw.name}>
            <TooltipTrigger asChild>
              <div
                className="rounded-lg border border-border/50 bg-muted/30 px-6 py-5 text-center hover:bg-muted/50 transition-colors flex items-center justify-center cursor-default"
              >
                {fw.icon ? (
                  <div className="flex flex-col items-center gap-2">
                    {fw.icon}
                    <div className={`font-medium text-sm ${fw.color}`}>{fw.name}</div>
                  </div>
                ) : (
                  <div>
                    <div className={`font-medium text-sm ${fw.color}`}>{fw.name}</div>
                  </div>
                )}
              </div>
            </TooltipTrigger>
            {fw.description && (
              <TooltipContent>
                <p>{fw.description}</p>
              </TooltipContent>
            )}
          </Tooltip>
        ))}
      </div>
    </TooltipProvider>
  )
}

/** Names-only export for inline text use */
export const FRAMEWORK_NAMES = FRAMEWORKS.map((fw) => fw.name)
