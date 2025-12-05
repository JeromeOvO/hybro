"use client"

import * as React from "react"
import Image from "next/image"
import {
  BookOpen,
  VectorSquare,
  InspectionPanel,
  MessageCirclePlus,
  History,
  PanelLeftIcon,
} from "lucide-react"
import { useUser } from "@clerk/nextjs"

import { NavMain } from "@/components/nav-main"
import { NavAgent } from "@/components/nav-agent"
import { NavUser } from "@/components/nav-user"
import { Logo } from "@/components/logo"
import { DiscordButton } from "@/components/nav-discord-button"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarRail,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar"
import { inquiryRoomsByRoomOwnerId } from "@/lib/api/room"
import type { Room } from "@/lib/types/room"

const staticNavAgents = [
  {
    name: "New Chat",
    url: "/chat",
    icon: MessageCirclePlus,
  },
  {
    name: "Agent Network",
    url: "/agent",
    icon: VectorSquare,
  },
  {
    name: "A2A Agent Inspector",
    url: "/inspector",
    icon: InspectionPanel,
  },
  {
    name: "About HYBRO",
    url: "/about",
    icon: BookOpen,
  }
]

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
          <div className="flex items-center gap-2 px-2">
            <Logo className="flex-1 group-data-[collapsible=icon]:hidden" />
            {isCollapsed ? (
              <button
                type="button"
                onClick={toggleSidebar}
                className="hidden md:flex h-8 w-8 items-center justify-center rounded-md hover:bg-sidebar-accent transition-colors group"
                aria-label="Expand sidebar"
              >
                <Image
                  src="/favicon.svg"
                  alt="Hybro"
                  width={20}
                  height={20}
                  className="h-5 w-5 group-hover:hidden"
                />
                <PanelLeftIcon className="h-5 w-5 hidden group-hover:block" />
              </button>
            ) : (
              <SidebarTrigger className="hidden md:block" />
            )}
          </div>
        </SidebarHeader>
        <SidebarContent>
          <NavAgent navAgents={staticNavAgents} />
          <NavMain items={navMainData} />
        </SidebarContent>
        <SidebarFooter>
          <DiscordButton />
            <NavUser />
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
  )
}
