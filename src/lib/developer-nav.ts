import {
  LayoutDashboard,
  ClipboardList,
  Shield,
  Code2,
  KeyRound,
} from "lucide-react"
import type { NavAgentItem } from "@/lib/nav-items"

export const DEVELOPER_NAV: NavAgentItem[] = [
  {
    name: "Dashboard",
    url: "/",
    icon: LayoutDashboard,
    colorClass: "text-icon-navigation",
  },
  {
    name: "Register Agent",
    url: "/register",
    icon: ClipboardList,
    colorClass: "text-icon-workflow",
  },
  {
    name: "Inspector",
    url: "/inspector",
    icon: Shield,
    colorClass: "text-icon-warning",
  },
  {
    name: "API Keys",
    url: "/discovery-api-keys",
    icon: KeyRound,
    colorClass: "text-icon-action",
  },
  {
    name: "Docs & SDK",
    url: "/docs",
    icon: Code2,
    colorClass: "text-icon-workflow",
  },
]
