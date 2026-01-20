"use client"

import * as React from "react"
import Image from "next/image"
import { History, PanelLeftIcon } from "lucide-react"
import { useUser } from "@clerk/nextjs"

import { NavMain } from "@/components/nav-main"
import { NavAgent } from "@/components/nav-agent"
import { NavUser } from "@/components/nav-user"
import { Logo } from "@/components/logo"
import { DiscordButton } from "@/components/nav-discord-button"
import { NAV_AGENTS } from "@/lib/nav-items"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  useSidebar,
} from "@/components/ui/sidebar"
import { inquiryRoomsByRoomOwnerId } from "@/lib/api/room"
import type { Room } from "@/lib/types/room"
import { UpgradeButton } from "./upgrade-button"

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const { user, isLoaded, isSignedIn } = useUser()
  const { state, toggleSidebar } = useSidebar()
  const [rooms, setRooms] = React.useState<Room[]>([])
  const [isLoadingRooms, setIsLoadingRooms] = React.useState(false)

  // Get user's room list
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

  // Load sessions and rooms when user login status changes
  React.useEffect(() => {
    if (isLoaded && isSignedIn && user?.id) {
      loadRooms()
    }
  }, [isLoaded, isSignedIn, user?.id, loadRooms])

  // Refresh rooms when a new room is created elsewhere
  React.useEffect(() => {
    const handleRefresh = () => loadRooms()
    window.addEventListener("rooms:refresh", handleRefresh)
    return () => window.removeEventListener("rooms:refresh", handleRefresh)
  }, [loadRooms])

  // Build dynamic navigation data
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

  const isCollapsed = state === "collapsed"

  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader>
        <div className="flex h-12 items-center gap-2 px-2">
          <Logo className="flex-1 group-data-[collapsible=icon]:hidden" />
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
        <NavAgent navAgents={NAV_AGENTS} />
        <NavMain items={navMainData} />
      </SidebarContent>
      <SidebarFooter>
        <DiscordButton />
        <UpgradeButton />
        <NavUser />
      </SidebarFooter>
    </Sidebar>
  )
}
