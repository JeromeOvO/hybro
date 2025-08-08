"use client"

import * as React from "react"
import {
  BookOpen,
  VectorSquare,
  MessageCircle,
  History
} from "lucide-react"
import { useUser } from "@clerk/nextjs"

import { NavMain } from "@/components/nav-main"
import { NavAgent } from "@/components/nav-agent"
import { NavUser } from "@/components/nav-user"
import { Logo } from '@/components/logo'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarRail,
} from "@/components/ui/sidebar"
import { ThemeToggle } from "./theme-toggle"
import { getAllSessions } from "@/lib/api"
import type { TaskSession, TaskCenterResponse } from "@/lib/types"

const staticNavAgents = [
  {
    name: "Start a new Chat",
    url: "/chat",
    icon: MessageCircle,
  },
  {
    name: "Agent Network",
    url: "/agent",
    icon: VectorSquare,
  },
  {
    name: "About Hybro",
    url: "/about",
    icon: BookOpen,
  }
]

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const { user, isLoaded, isSignedIn } = useUser()
  const [chatSessions, setChatSessions] = React.useState<TaskSession[]>([])
  const [isLoadingSessions, setIsLoadingSessions] = React.useState(false)

  // Get user's chat session list
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

  // Load sessions when user login status changes
  React.useEffect(() => {
    if (isLoaded && isSignedIn && user?.id) {
      loadChatSessions()
    }
  }, [isLoaded, isSignedIn, user?.id, loadChatSessions])

  // Build dynamic navigation data
  const navMainData = React.useMemo(() => {
    const chatHistoryItems = chatSessions.map(session => ({
      title: session.session_name,
      url: `/chat/${session.session_id}`,
      id: session.session_id, // Add id field
    }))

    return [
      {
        title: "Chat History",
        url: "#",
        icon: History,
        isActive: true,
        items: chatHistoryItems.length > 0 ? chatHistoryItems : [
          {
            title: isLoadingSessions ? "Loading..." : "No sessions yet",
            url: "#",
            id: "no-sessions", // Add unique id for default item
          }
        ],
        isLoading: isLoadingSessions,
      },
    ]
  }, [chatSessions, isLoadingSessions])

  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader className="group-data-[collapsible=icon]:hidden">
        <div className="flex items-center gap-2 px-2">
          <Logo className="flex-1" />
          <ThemeToggle />
        </div>
      </SidebarHeader>
      <SidebarContent>
        <NavAgent navAgents={staticNavAgents} />
        <NavMain items={navMainData} onRefreshSessions={loadChatSessions} />
      </SidebarContent>
      <SidebarFooter>
        <NavUser />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
