"use client"

import * as React from "react"
import Image from "next/image"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { Code, History, PanelLeftIcon } from "lucide-react"
import { useUser } from "@clerk/nextjs"

import { NavAgent } from "@/components/nav-agent"
import { NavMain } from "@/components/nav-main"
import { NavUser } from "@/components/nav-user"
import { Logo } from "@/components/logo"
import { DiscordButton } from "@/components/nav-discord-button"
import { DocsButton } from "@/components/nav-docs-button"
import { CONSUMER_NAV } from "@/lib/consumer-nav"
import { developerUrl } from "@/lib/urls"
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
import { inquiryRoomsByRoomOwnerId } from "@/lib/api/room"
import type { Room } from "@/lib/types/room"

const MARKETING_PAGES = ['/', '/about', '/pricing', '/agents', '/c', '/c/about', '/c/pricing', '/c/agents']

export function ConsumerSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const { user, isLoaded, isSignedIn } = useUser()
  const { state, toggleSidebar } = useSidebar()
  const pathname = usePathname()
  const [rooms, setRooms] = React.useState<Room[]>([])
  const [isLoadingRooms, setIsLoadingRooms] = React.useState(false)

  const loadRooms = React.useCallback(async () => {
    if (!isLoaded || !isSignedIn || !user?.id) return

    try {
      setIsLoadingRooms(true)
      const response = await inquiryRoomsByRoomOwnerId(user.id)

      if (response.success && response.room_list) {
        setRooms(response.room_list)
      } else {
        console.error('Failed to load rooms:', response.error)
        setRooms([])
      }
    } catch (error) {
      console.error('Error loading rooms:', error)
      setRooms([])
    } finally {
      setIsLoadingRooms(false)
    }
  }, [isLoaded, isSignedIn, user?.id])

  React.useEffect(() => {
    if (isLoaded && isSignedIn && user?.id) {
      loadRooms()
    }
  }, [isLoaded, isSignedIn, user?.id, loadRooms])

  React.useEffect(() => {
    const handleRefresh = () => loadRooms()
    window.addEventListener("rooms:refresh", handleRefresh)
    return () => window.removeEventListener("rooms:refresh", handleRefresh)
  }, [loadRooms])

  const navMainData = React.useMemo(() => {
    const roomItems = [...rooms].reverse().map(room => ({
      title: room.room_name || 'Unnamed Room',
      url: `/room/${room.room_id}`,
      id: room.room_id,
    }))

    return [
      {
        title: "History",
        url: "#",
        icon: History,
        isActive: true,
        items: roomItems.length > 0 ? roomItems : [
          {
            title: isLoadingRooms ? "Loading..." : "No history yet",
            url: "#",
            id: "no-history",
          }
        ],
        isLoading: isLoadingRooms,
      },
    ]
  }, [rooms, isLoadingRooms])

  const isMarketingPage = MARKETING_PAGES.includes(pathname)
  const hideSidebar = isMarketingPage && (!isLoaded || !isSignedIn)

  if (hideSidebar) {
    return null
  }

  const isCollapsed = state === "collapsed"

  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader>
        <div className="flex h-12 items-center gap-2 px-2 group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:justify-center">
          <Logo className={`flex-1 ${SIDEBAR_ICON_HIDDEN}`} />
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
        <NavAgent navAgents={CONSUMER_NAV} />
        <NavMain items={navMainData} />
      </SidebarContent>
      <SidebarFooter>
        <div className="border-t border-sidebar-border mx-2 mb-1" />
        {/* Developer Portal link */}
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              asChild
              size="default"
              tooltip="Developer Portal"
            >
              <Link href={developerUrl("/")} prefetch={false}>
                <Code className={`h-4 w-4 transition-colors ${SIDEBAR_PORTAL_ICON} ${SIDEBAR_ICON_CENTER}`} />
                <span className={`${SIDEBAR_PORTAL_TEXT} ${SIDEBAR_ICON_HIDDEN}`}>
                  Developer Portal →
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
