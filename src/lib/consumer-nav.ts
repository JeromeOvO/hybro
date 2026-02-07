import {
  MessageCirclePlus,
  Globe,
} from "lucide-react"
import type { NavAgentItem } from "@/lib/nav-items"

export const CONSUMER_NAV: NavAgentItem[] = [
  {
    name: "New Chat",
    url: "/chat",
    icon: MessageCirclePlus,
    colorClass: "icon-create",
  },
  {
    name: "Explore Agents",
    url: "/agents",
    icon: Globe,
    colorClass: "icon-network",
  },
]
