"use client"

import { type ReactNode } from "react"
import { OpenClawIcon, OllamaIcon, N8nIcon, LangChainIcon, LangGraphIcon, CrewAIIcon, HermesIcon, PiIcon } from "@/components/icons"
import { Plus } from "lucide-react"

export const FRAMEWORKS: {
  name: string
  description: string
  color: string
  /** Static Tailwind classes — tints the logo on hover of an ancestor marked `group/tile`. */
  tileHoverColor: string
  icon?: ReactNode
  url?: string
}[] = [
  { name: "Hermes", description: "Self-improving agent with tools & memory", color: "text-foreground", tileHoverColor: "group-hover/tile:text-foreground", icon: <HermesIcon className="h-10 w-10" />, url: "https://github.com/NousResearch/hermes-agent" },
  { name: "OpenClaw", description: "Local standalone agents", color: "text-violet-500 dark:text-violet-400", tileHoverColor: "group-hover/tile:text-violet-500 dark:group-hover/tile:text-violet-400", icon: <OpenClawIcon className="h-10 w-10" />, url: "https://openclaw.ai/" },
  { name: "Pi", description: "Minimal terminal coding agent", color: "text-foreground", tileHoverColor: "group-hover/tile:text-foreground", icon: <PiIcon className="h-10 w-10" />, url: "https://pi.dev" },
  { name: "Ollama", description: "Run LLMs locally", color: "text-slate-600 dark:text-slate-300", tileHoverColor: "group-hover/tile:text-slate-600 dark:group-hover/tile:text-slate-300", icon: <OllamaIcon className="h-10 w-10" />, url: "https://ollama.com/" },
  { name: "n8n", description: "Workflow automation", color: "text-rose-500 dark:text-rose-400", tileHoverColor: "group-hover/tile:text-rose-500 dark:group-hover/tile:text-rose-400", icon: <N8nIcon className="h-10 w-10" />, url: "https://n8n.io/" },
  { name: "CrewAI", description: "Multi-agent orchestration", color: "text-orange-500 dark:text-orange-400", tileHoverColor: "group-hover/tile:text-orange-500 dark:group-hover/tile:text-orange-400", icon: <CrewAIIcon className="h-10 w-10" />, url: "https://www.crewai.com/" },
  { name: "LangChain", description: "LLM application framework", color: "text-emerald-500 dark:text-emerald-400", tileHoverColor: "group-hover/tile:text-emerald-500 dark:group-hover/tile:text-emerald-400", icon: <LangChainIcon className="h-10 w-10" />, url: "https://www.langchain.com/" },
  { name: "LangGraph", description: "Stateful agent workflows", color: "text-blue-500 dark:text-blue-400", tileHoverColor: "group-hover/tile:text-blue-500 dark:group-hover/tile:text-blue-400", icon: <LangGraphIcon className="h-10 w-10" />, url: "https://www.langchain.com/langgraph" },
  { name: "More ...", description: "Any agent that can receive input and return output", color: "text-muted-foreground", tileHoverColor: "group-hover/tile:text-foreground", icon: <Plus className="h-10 w-10 text-muted-foreground" /> },
]
