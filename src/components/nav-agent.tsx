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
    case "Create a new Room":
      return "icon-create"
    case "Agent Network":
      return "icon-network"
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
    <SidebarGroup className="group-data-[collapsible=icon]:hidden">
      <SidebarMenu className="gap-1.5">
        {navAgents.map((item) => (
          <SidebarMenuItem key={item.name}>
            <SidebarMenuButton asChild size="lg" className="text-base">
              <Link href={item.url} prefetch={false} scroll={false}>
                <item.icon className={getIconColorClass(item.name)} />
                <span className="leading-7">{item.name}</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        ))}
      </SidebarMenu>
    </SidebarGroup>
  )
} 