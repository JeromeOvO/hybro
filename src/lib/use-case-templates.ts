import { Youtube, Palmtree, ImageIcon } from "lucide-react"
import type { LucideIcon } from "lucide-react"
import type { Agent } from "@/lib/types/agent"

export interface UseCaseAgent {
  agentId: string
  agentName: string
  iconUrl?: string
}

export interface UseCaseTemplate {
  id: string
  icon: LucideIcon
  iconGradient: [string, string]
  title: string
  description: string
  agents: UseCaseAgent[]
  prefillMessage: string
  tag?: "new" | null
}

/**
 * Resolve template agents against the live agent catalog.
 * ID-first, name-fallback (case-insensitive). Throws if any agent unresolved.
 */
export function resolveTemplateAgents(
  templateAgents: UseCaseAgent[],
  catalog: Agent[],
): Agent[] {
  const idMap = new Map(catalog.map((a) => [a.agent_id, a]))
  const nameMap = new Map(
    catalog.map((a) => [a.agent_card.name.toLowerCase(), a]),
  )

  const resolved: Agent[] = []
  for (const ta of templateAgents) {
    const byId = idMap.get(ta.agentId)
    if (byId) {
      resolved.push(byId)
      continue
    }
    const byName = nameMap.get(ta.agentName.toLowerCase())
    if (byName) {
      resolved.push(byName)
      continue
    }
    throw new Error("Some agents in this template are unavailable")
  }
  return resolved
}

export const useCaseTemplates: UseCaseTemplate[] = [
  {
    id: "youtube-creator-finder",
    icon: Youtube,
    iconGradient: ["#ff0050", "#ff4080"],
    title: "YouTube Creator Finder",
    description: "Find YouTuber contact info by topic with multi-agent research",
    agents: [
      {
        agentId: "YouTube Creator Finder Agent",
        agentName: "YouTube Creator Finder Agent",
      },
      {
        agentId: "GPT-5-mini Agent",
        agentName: "GPT-5-mini Agent",
      },
    ],
    prefillMessage:
      "Find top AI agent YouTubers and their contact info",
    tag: "new",
  },
  {
    id: "travel-planner",
    icon: Palmtree,
    iconGradient: ["#00c9a7", "#00b4d8"],
    title: "Travel Planner",
    description:
      "Get a complete travel itinerary with flights, hotels, and activities",
    agents: [
      {
        agentId: "travel planner Agent",
        agentName: "travel planner Agent",
      },
    ],
    prefillMessage: "Give me a travel plan to Hawaii",
    tag: "new",
  },
  {
    id: "image-generator",
    icon: ImageIcon,
    iconGradient: ["#f59e0b", "#ef4444"],
    title: "Image Generator",
    description: "Generate stunning AI images from natural language prompts",
    agents: [
      {
        agentId: "Image Generator Agent",
        agentName: "Image Generator Agent",
      },
    ],
    prefillMessage:
      "Generate an image of a futuristic city at sunset",
    tag: null,
  },
]
