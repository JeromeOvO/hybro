"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

import {
  SidebarGroup,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import type { NavAgentItem } from "@/lib/nav-items"
import { SIDEBAR_ICON_CENTER, SIDEBAR_ICON_HIDDEN } from "@/lib/sidebar-styles"

export function NavAgent({
  navAgents,
}: {
  navAgents: NavAgentItem[]
}) {
  const pathname = usePathname()

  return (
    <SidebarGroup>
      <SidebarMenu className="gap-1.5">
        {navAgents.map((item) => {
          const isActive = pathname === item.url || pathname?.startsWith(item.url + "/")

          return (
            <SidebarMenuItem key={item.name}>
              <SidebarMenuButton
                asChild
                isActive={isActive}
                size="default"
                tooltip={item.name}
              >
                <Link href={item.url} prefetch={false} scroll={false}>
                  <item.icon
                    className={`transition-colors ${item.colorClass ?? "text-icon-navigation"} ${SIDEBAR_ICON_CENTER}`}
                  />
                  <span
                    className={`leading-7 ${SIDEBAR_ICON_HIDDEN}`}
                    style={{ wordSpacing: item.name === "New Chat" ? "0.16em" : undefined }}
                  >
                    {item.name}
                  </span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          )
        })}
      </SidebarMenu>
    </SidebarGroup>
  )
}
