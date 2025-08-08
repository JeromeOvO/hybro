"use client"

import { type LucideIcon } from "lucide-react"

import {
  SidebarGroup,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"

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
              <a href={item.url}>
                <item.icon />
                <span className="leading-7">{item.name}</span>
              </a>
            </SidebarMenuButton>
          </SidebarMenuItem>
        ))}
      </SidebarMenu>
    </SidebarGroup>
  )
} 