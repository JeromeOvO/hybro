import {
  ClipboardList,
  Code2,
  Globe,
  MessageCirclePlus,
} from "lucide-react"

export type NavAgentLink = {
  name: string
  url: string
  icon: typeof MessageCirclePlus
  colorClass: string
  activePaths?: string[]
}

export type NavAgentGroup = Omit<NavAgentLink, 'url'> & {
  items: NavAgentLink[]
}

export type NavAgentItem = NavAgentLink | NavAgentGroup

export const NAV_AGENTS: NavAgentItem[] = [
  {
    name: "New Chat",
    url: "/chat",
    icon: MessageCirclePlus,
    colorClass: "text-icon-create",
  },
  {
    name: "Developers",
    url: "/developers",
    icon: Code2,
    colorClass: "text-icon-workflow",
  },
  {
    name: "Agents",
    url: "/agent",
    icon: Globe,
    colorClass: "text-icon-network",
  },
  {
    name: "Register Agent",
    url: "/agent/registry",
    icon: ClipboardList,
    colorClass: "text-icon-workflow",
  },
]

