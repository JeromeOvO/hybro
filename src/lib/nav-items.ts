import {
  ClipboardList,
  Code2,
  Globe,
  MessageCirclePlus,
} from "lucide-react"

export type NavAgentItem = {
  name: string
  url: string
  icon: typeof MessageCirclePlus
  colorClass: string
}

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

