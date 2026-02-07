"use client"

const FRAMEWORKS = [
  { name: "CrewAI", description: "Multi-agent orchestration", color: "text-orange-500 dark:text-orange-400" },
  { name: "LangChain", description: "LLM application framework", color: "text-emerald-500 dark:text-emerald-400" },
  { name: "LangGraph", description: "Stateful agent workflows", color: "text-blue-500 dark:text-blue-400" },
  { name: "n8n", description: "Workflow automation", color: "text-rose-500 dark:text-rose-400" },
  { name: "OpenClaw", description: "Local standalone agents", color: "text-violet-500 dark:text-violet-400" },
]

interface FrameworkBadgesProps {
  /** Show description text below each framework name */
  showDescriptions?: boolean
  /** Compact mode - single row, no descriptions */
  compact?: boolean
  /** Optional className for the container */
  className?: string
}

/**
 * Reusable framework badges component.
 * Displays supported agent frameworks in a consistent style.
 */
export function FrameworkBadges({ showDescriptions = true, compact = false, className = "" }: FrameworkBadgesProps) {
  if (compact) {
    return (
      <div className={`flex flex-wrap items-center gap-2 ${className}`}>
        {FRAMEWORKS.map((fw) => (
          <span
            key={fw.name}
            className="inline-flex items-center rounded-md border border-border/50 bg-muted/40 px-2.5 py-1 text-xs font-medium"
          >
            <span className={`mr-1.5 h-1.5 w-1.5 rounded-full bg-current ${fw.color}`} />
            {fw.name}
          </span>
        ))}
        <span className="text-xs text-muted-foreground">+ more</span>
      </div>
    )
  }

  return (
    <div className={`grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-3 ${className}`}>
      {FRAMEWORKS.map((fw) => (
        <div
          key={fw.name}
          className="rounded-lg border border-border/50 bg-muted/30 px-4 py-3 text-center hover:bg-muted/50 transition-colors"
        >
          <div className={`font-medium text-sm ${fw.color}`}>{fw.name}</div>
          {showDescriptions && (
            <div className="text-xs text-muted-foreground mt-0.5">{fw.description}</div>
          )}
        </div>
      ))}
    </div>
  )
}

/** Names-only export for inline text use */
export const FRAMEWORK_NAMES = FRAMEWORKS.map((fw) => fw.name)
