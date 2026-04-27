"use client"

import * as React from "react"
import Image from "next/image"
import Link from "next/link"
import { Bot, PanelLeftIcon } from "lucide-react"
import { useUser } from "@clerk/nextjs"

import { NavAgent } from "@/components/nav-agent"
import { NavHub } from "@/components/nav-hub"
import { NavMain } from "@/components/nav-main"
import { NavUser } from "@/components/nav-user"
import { Logo } from "@/components/logo"
import { DiscordButton } from "@/components/nav-discord-button"
import { DocsButton } from "@/components/nav-docs-button"
import { DEVELOPER_NAV } from "@/lib/developer-nav"
import { consumerUrl } from "@/lib/urls"
import { SIDEBAR_ICON_CENTER, SIDEBAR_ICON_HIDDEN, SIDEBAR_PORTAL_ICON, SIDEBAR_PORTAL_TEXT } from "@/lib/sidebar-styles"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar"
import { useMyAgents } from "@/hooks/useMyAgents"

export function DeveloperSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const { state, toggleSidebar } = useSidebar()
  const { isLoaded, isSignedIn } = useUser()
  const { agents: myAgents, isLoading: isLoadingAgents } = useMyAgents()

  // Build dynamic navigation data
  const navMainData = React.useMemo(() => {
    const agentItems = myAgents.map(agent => ({
      title: agent.agent_card.name || 'Unnamed Agent',
      url: `/agents/${agent.agent_id}`,
      id: agent.agent_id,
    }))

    return [
      {
        title: "My Agents",
        url: "#",
        icon: Bot,
        isActive: true,
        items: agentItems.length > 0 ? agentItems : [
          {
            title: isLoadingAgents ? "Loading..." : "No agents yet",
            url: "#",
            id: "no-agents",
          }
        ],
        isLoading: isLoadingAgents,
      },
    ]
  }, [myAgents, isLoadingAgents])

  const isCollapsed = state === "collapsed"

  if (isLoaded && !isSignedIn) return null

  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader className="py-0">
        <div className="flex h-14 items-center gap-2 px-2 group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:justify-center">
          <div className={`flex items-center gap-1.5 flex-1 ${SIDEBAR_ICON_HIDDEN}`}>
            <Logo className="flex-shrink-0" />
            <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Dev</span>
          </div>
          <button
            type="button"
            onClick={toggleSidebar}
            className={`hidden md:flex h-8 w-8 items-center justify-center rounded-md hover:bg-black/10 dark:hover:bg-white/15 transition-all duration-150 ease-out leading-none group ${isCollapsed ? "hover:cursor-e-resize" : "hover:cursor-w-resize"
              }`}
            aria-label={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {isCollapsed ? (
              <div className="relative h-5 w-5">
                <Image
                  src="/favicon.svg"
                  alt="Hybro"
                  width={20}
                  height={20}
                  className="absolute left-1/2 top-1/2 h-5 w-5 -translate-x-1/2 -translate-y-1/2 transition-opacity duration-150 group-hover:opacity-0"
                />
                <PanelLeftIcon className="absolute left-1/2 top-1/2 h-5 w-5 -translate-x-1/2 -translate-y-1/2 transition-opacity duration-150 opacity-0 group-hover:opacity-100" />
              </div>
            ) : (
              <PanelLeftIcon className="h-5 w-5" />
            )}
          </button>
        </div>
      </SidebarHeader>
      <SidebarContent>
        {isLoaded && (
          <>
            <NavAgent navAgents={DEVELOPER_NAV} />
            <NavHub basePath="/d" />
            <NavMain items={navMainData} />
          </>
        )}
      </SidebarContent>
      <SidebarFooter>
        <div className="border-t border-sidebar-border mx-2 mb-1" />
        {/* User Portal link */}
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              asChild
              size="default"
              tooltip="User Portal"
            >
              <Link href={consumerUrl("/chat")} prefetch={false}>
                <Bot className={`h-4 w-4 transition-colors ${SIDEBAR_PORTAL_ICON} ${SIDEBAR_ICON_CENTER}`} />
                <span className={`${SIDEBAR_PORTAL_TEXT} ${SIDEBAR_ICON_HIDDEN}`}>
                  User Portal →
                </span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>

        <DocsButton />
        <DiscordButton />
        <NavUser />
      </SidebarFooter>
    </Sidebar>
  )
}
