"use client"

import Link from "next/link"

import {
  SidebarGroup,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import type { NavAgentItem } from "@/lib/nav-items"

export function NavAgent({
  navAgents,
}: {
  navAgents: NavAgentItem[]
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
              className="text-base group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:h-12! group-data-[collapsible=icon]:w-full!"
            >
              <Link href={item.url} prefetch={false} scroll={false}>
                <item.icon
                  className={`${item.colorClass ?? "icon-navigation"} group-data-[collapsible=icon]:mx-auto`}
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