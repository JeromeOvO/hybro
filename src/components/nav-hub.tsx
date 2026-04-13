"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { House } from "lucide-react"

import {
  SidebarGroup,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { useHubStatus } from "@/hooks/useHubStatus"
import { SIDEBAR_ICON_CENTER, SIDEBAR_ICON_HIDDEN } from "@/lib/sidebar-styles"

export function NavHub({ basePath = "" }: { basePath?: string }) {
  const pathname = usePathname()
  const { hasHub, isOnline, isLoading } = useHubStatus()
  const hubUrl = `${basePath}/hub`
  const isActive = pathname === hubUrl || pathname?.startsWith(hubUrl + "/")

  return (
    <SidebarGroup className="py-0">
      <div className="mx-3 border-t border-sidebar-border" />
      <SidebarMenu className="gap-1.5 pt-2">
        <SidebarMenuItem>
          <SidebarMenuButton
            asChild
            isActive={isActive}
            size="default"
            tooltip="My Hub"
          >
            <Link href={hubUrl} prefetch={false} scroll={false}>
              <span className={`relative inline-flex ${SIDEBAR_ICON_CENTER}`}>
                <House
                  className={`h-4 w-4 transition-colors ${
                    hasHub && isOnline
                      ? "text-emerald-500"
                      : hasHub
                        ? "text-amber-500"
                        : "text-muted-foreground/50"
                  }`}
                />
                {!isLoading && hasHub && (
                  <span
                    className={`absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full border border-sidebar-background ${
                      isOnline ? "bg-emerald-500" : "bg-amber-500"
                    }`}
                  />
                )}
              </span>
              <span className={`leading-7 ${SIDEBAR_ICON_HIDDEN}`}>
                My Hub
              </span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarGroup>
  )
}
