import {
  BookOpen,
  Globe,
  InspectionPanel,
  MessageCirclePlus,
  MonitorCog,
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
    colorClass: "icon-create",
  },
  {
    name: "Agent Network",
    url: "/agent",
    icon: Globe,
    colorClass: "icon-network",
  },
  {
    name: "Agent Inspector",
    url: "/inspector",
    icon: InspectionPanel,
    colorClass: "icon-inspection",
  },
  {
    name: "About HYBRO",
    url: "/about",
    icon: BookOpen,
    colorClass: "icon-learn",
  },
  {
    name: "Agent Workspace",
    url: "/workspace",
    icon: MonitorCog,
    colorClass: "icon-create",
  },
]

