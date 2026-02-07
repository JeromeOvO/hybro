"use client"

import * as React from "react"
import Image from "next/image"
import Link from "next/link"
import { Bot, PanelLeftIcon, ExternalLink } from "lucide-react"
import { useUser, useAuth } from "@clerk/nextjs"

import { NavAgent } from "@/components/nav-agent"
import { NavMain } from "@/components/nav-main"
import { NavUser } from "@/components/nav-user"
import { Logo } from "@/components/logo"
import { DiscordButton } from "@/components/nav-discord-button"
import { DEVELOPER_NAV } from "@/lib/developer-nav"
import { consumerUrl } from "@/lib/urls"
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
import { getAgentsByProviderId } from "@/lib/api"
import type { Agent } from "@/lib/types"

export function DeveloperSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const { user, isLoaded, isSignedIn } = useUser()
  const { getToken } = useAuth()
  const { state, toggleSidebar } = useSidebar()
  const [myAgents, setMyAgents] = React.useState<Agent[]>([])
  const [isLoadingAgents, setIsLoadingAgents] = React.useState(false)

  // Get user's agents list
  const loadAgents = React.useCallback(async () => {
    if (!isLoaded || !isSignedIn || !user?.id) return

    try {
      setIsLoadingAgents(true)
      const response = await getAgentsByProviderId(getToken)

      if (response.success && response.agents) {
        setMyAgents(response.agents)
      } else {
        console.error('Failed to load agents:', response.error)
        setMyAgents([])
      }
    } catch (error) {
      console.error('Error loading agents:', error)
      setMyAgents([])
    } finally {
      setIsLoadingAgents(false)
    }
  }, [isLoaded, isSignedIn, user?.id, getToken])

  // Load agents when user login status changes
  React.useEffect(() => {
    if (isLoaded && isSignedIn && user?.id) {
      loadAgents()
    }
  }, [isLoaded, isSignedIn, user?.id, loadAgents])

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

  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader>
        <div className="flex h-12 items-center gap-2 px-2">
          <div className="flex items-center gap-1.5 flex-1 group-data-[collapsible=icon]:hidden">
            <Logo className="flex-shrink-0" />
            <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Dev</span>
          </div>
          <button
            type="button"
            onClick={toggleSidebar}
            className={`hidden md:flex h-8 w-8 items-center justify-center rounded-md hover:bg-white/10 dark:hover:bg-white/15 transition-all duration-150 ease-out leading-none group ${isCollapsed ? "hover:cursor-e-resize" : "hover:cursor-w-resize"
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
        <NavAgent navAgents={DEVELOPER_NAV} />
        <NavMain items={navMainData} />
      </SidebarContent>
      <SidebarFooter>
        <DiscordButton />

        {/* Try Agents link */}
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              asChild
              size="default"
              tooltip="Try Agents"
              className="group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:h-12! group-data-[collapsible=icon]:w-full!"
            >
              <Link href={consumerUrl("/chat")} prefetch={false}>
                <ExternalLink className="h-4 w-4 group-data-[collapsible=icon]:mx-auto" />
                <span className="group-data-[collapsible=icon]:hidden">
                  Try Agents →
                </span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>

        <NavUser />
      </SidebarFooter>
    </Sidebar>
  )
}
