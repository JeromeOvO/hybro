import {
  MessageCirclePlus,
  Globe,
  Network,
} from "lucide-react"
import type { NavAgentItem } from "@/lib/nav-items"
import { routes } from '@/lib/routes'

export const CONSUMER_NAV: NavAgentItem[] = [
  {
    name: "New Chat",
    url: "/chat",
    icon: MessageCirclePlus,
    colorClass: "text-icon-create",
  },
  {
    name: "Agents",
    url: "/agents",
    icon: Globe,
    colorClass: "text-icon-network",
  },
  {
    name: 'Networks',
    url: routes.networks,
    icon: Network,
    colorClass: 'text-icon-network',
  },
]
