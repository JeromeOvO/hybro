import {
  LayoutDashboard,
  ClipboardList,
  Shield,
  Code2,
} from "lucide-react"
import type { NavAgentItem } from "@/lib/nav-items"

export const DEVELOPER_NAV: NavAgentItem[] = [
  {
    name: "Dashboard",
    url: "/",
    icon: LayoutDashboard,
    colorClass: "icon-navigation",
  },
  {
    name: "Register Agent",
    url: "/register",
    icon: ClipboardList,
    colorClass: "icon-workflow",
  },
  {
    name: "Inspector",
    url: "/inspector",
    icon: Shield,
    colorClass: "icon-warning",
  },
  {
    name: "Docs & SDK",
    url: "/docs",
    icon: Code2,
    colorClass: "icon-workflow",
  },
]
