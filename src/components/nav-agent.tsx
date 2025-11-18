"use client"

import { type LucideIcon } from "lucide-react"
import Link from "next/link"

import {
  SidebarGroup,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"

// Professional color mapping for navigation icons
const getIconColorClass = (itemName: string): string => {
  switch (itemName) {
    case "Start a new Chat":
      return "icon-create"
    case "Create a new Room":
      return "icon-create"
    case "Agent Network":
      return "icon-network"
    case "A2A Agent Inspector":
      return "icon-inspection"
    case "About HYBRO":
      return "icon-learn"
    default:
      return "icon-navigation"
  }
}

export function NavAgent({
  navAgents,
}: {
  navAgents: {
    name: string
    url: string
    icon: LucideIcon
  }[]
}) {

  return (
    <SidebarGroup>
      <SidebarMenu className="gap-1.5">
        {navAgents.map((item) => (
          <SidebarMenuItem key={item.name}>
            <SidebarMenuButton
              asChild
              size="lg"
              tooltip={item.name}
              className="text-base group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0"
            >
              <Link href={item.url} prefetch={false} scroll={false}>
                <item.icon
                  className={`${getIconColorClass(item.name)} group-data-[collapsible=icon]:mx-auto`}
                />
                <span className="leading-7 group-data-[collapsible=icon]:hidden">
                  {item.name}
                </span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        ))}
      </SidebarMenu>
    </SidebarGroup>
  )
} 