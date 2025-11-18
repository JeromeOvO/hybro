"use client"

import * as React from "react"
import {
  BookOpen,
  VectorSquare,
  HousePlus,
  InspectionPanel,
  MessageCircle,
  History,
} from "lucide-react"
import { useUser } from "@clerk/nextjs"

import { NavMain } from "@/components/nav-main"
import { NavAgent } from "@/components/nav-agent"
import { NavUser } from "@/components/nav-user"
import { Logo } from '@/components/logo'
import { DiscordButton } from "@/components/nav-discord-button"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarRail,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { inquiryRoomsByRoomOwnerId } from "@/lib/api/room"
import type { Room } from "@/lib/types/room"

const staticNavAgents = [
  {
    name: "Start a new Chat",
    url: "/chat",
    icon: MessageCircle,
  },
  {
    name: "Create a new Room",
    url: "/room",
    icon: HousePlus,
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
  //const [chatSessions, setChatSessions] = React.useState<TaskSession[]>([])
  //const [isLoadingSessions, setIsLoadingSessions] = React.useState(false)
  const [rooms, setRooms] = React.useState<Room[]>([])
  const [isLoadingRooms, setIsLoadingRooms] = React.useState(false)
  
  // Get user's chat session list
  /**
  const loadChatSessions = React.useCallback(async () => {
    if (!isLoaded || !isSignedIn || !user?.id) return

    try {
      setIsLoadingSessions(true)
      const response: TaskCenterResponse = await getAllSessions(user.id)
      
      if (response.success && response.task_sessions) {
        setChatSessions(response.task_sessions)
      } else {
        console.error('Failed to load chat sessions:', response.error)
        setChatSessions([])
      }
    } catch (error) {
      console.error('Error loading chat sessions:', error)
      setChatSessions([])
    } finally {
      setIsLoadingSessions(false)
    }
  }, [isLoaded, isSignedIn, user?.id])
  */

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

  // Build dynamic navigation data
  const navMainData = React.useMemo(() => {
    // const chatHistoryItems = [...chatSessions].reverse().map(session => ({
    //   title: session.session_name,
    //   url: `/chat/${session.session_id}`,
    //   id: session.session_id,
    // }))

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
      // {
      //   title: "Chat Sessions",
      //   url: "#",
      //   icon: History,
      //   isActive: true,
      //   items: chatHistoryItems.length > 0 ? chatHistoryItems : [
      //     {
      //       title: isLoadingSessions ? "Loading..." : "No sessions yet",
      //       url: "#",
      //       id: "no-sessions",
      //     }
      //   ],
      //   isLoading: isLoadingSessions,
      // },
    ]
  }, [rooms, isLoadingRooms])

  return (
      <Sidebar collapsible="icon" {...props}>
        <SidebarHeader>
          <div className="flex items-center gap-2 px-2">
            <Logo className="flex-1 group-data-[collapsible=icon]:hidden" />
            <SidebarTrigger className="hidden md:block" />
          </div>
        </SidebarHeader>
        <SidebarContent>
          <NavAgent navAgents={staticNavAgents} />
          <NavMain 
            items={navMainData} 
            //onRefreshSessions={loadChatSessions}
            onRefreshRooms={loadRooms}
          />
        </SidebarContent>
        <SidebarFooter>
          <DiscordButton />
            <NavUser />
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
  )
}
